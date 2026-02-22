#!/usr/bin/env python3
"""厚労省ポータルから診療報酬関連資料を網羅取得するスクリプト。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# 定数
# ============================================================
PORTAL_URL = "https://www.mhlw.go.jp/stf/newpage_67729.html"
BASE_OUTPUT = Path("output") / "ai-shinryou-db"
TEXT_ROOT = BASE_OUTPUT / "text"
DATA_DIR = BASE_OUTPUT / "data"
METADATA_DIR = BASE_OUTPUT / "metadata"

INDEX_CSV = DATA_DIR / "comprehensive_index.csv"
LINKS_JSON = DATA_DIR / "portalpage_links.json"
LOG_FILE = DATA_DIR / "download.log"
STRUCTURE_JSON = METADATA_DIR / "portalpage_structure.json"

TIMEOUT_SECONDS = 60
MAX_RETRIES = 5
CHUNK_SIZE = 1024 * 512
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

YEAR_PATTERNS = [
    ("2026", ["令和8", "R8", "2026"]),
    ("2025", ["令和7", "R7", "2025", "薬価"]),
    ("2024", ["令和6", "R6", "2024"]),
]

CATEGORY_RULES = [
    ("kihon-hoshishin", "基本方針", ["基本方針"]),
    ("hosei-tsuuchi-i", "改定通知", ["改定通知", "医科", "歯科", "調剤"]),
    ("shisetsu-kijun", "施設基準", ["施設基準"]),
    ("gisoku-kaishaku", "疑義解釈", ["疑義", "Q&A", "問", "答"]),
    ("kobetsu-kaitei", "個別改定項目説明", ["個別改定", "改定項目"]),
    ("dpc-tsuutatsu", "DPC/PDPS関連", ["DPC", "PDPS"]),
    ("iryo-kiki-tsuutatsu", "医療機器保険適用通知", ["医療機器", "保険適用"]),
    ("zairyo-kakaku-tsuutatsu", "材料価格改定通知", ["材料価格", "特定保険医療材料"]),
    ("yakka-kaitei", "薬価改定通知", ["薬価改定", "薬価基準"]),
    ("iyakuhin-list", "医薬品リスト", ["医薬品", "収載", "リスト"]),
    ("tokurei-rinji", "特例措置・臨時改定", ["特例", "臨時", "経過措置"]),
    ("chiho-kouseikyoku", "地方厚生局別通知", ["地方厚生局", "厚生局"]),
]

RELEVANT_KEYWORDS = {
    "診療報酬", "改定", "通知", "施設基準", "疑義", "DPC", "PDPS",
    "薬価", "医療機器", "材料価格", "地方厚生局", "特例", "臨時",
    "調剤", "医科", "歯科", "点数表", "告示", "省令", "答申",
    "諮問", "基本方針", "中医協", "公聴会", "パブリックコメント",
    "個別改定", "ベースアップ", "届出", "報酬",
}


# ============================================================
# データクラス
# ============================================================
@dataclass
class LinkItem:
    text: str
    url: str


@dataclass
class DownloadRecord:
    year: str
    category: str
    file_name: str
    url: str
    downloaded_at: str
    file_size_kb: float
    status: str
    note: str


# ============================================================
# ユーティリティ
# ============================================================
def ensure_directories() -> None:
    for directory in (TEXT_ROOT, DATA_DIR, METADATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def configure_logger() -> logging.Logger:
    logger = logging.getLogger("comprehensive_shinryohoshu")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES, connect=MAX_RETRIES, read=MAX_RETRIES,
        backoff_factor=1.0, allowed_methods=("GET", "HEAD"),
        status_forcelist=(429, 500, 502, 503, 504), raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def sanitize_for_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", "", value)
    return value[:120] or "no-title"


def detect_year(text: str) -> str:
    for year, markers in YEAR_PATTERNS:
        if any(marker.lower() in text.lower() for marker in markers):
            return year
    return "2026"


def detect_category(text: str) -> Tuple[str, str]:
    for slug, label, keywords in CATEGORY_RULES:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return slug, label
    return "other", "その他関連資料"


def is_relevant_link(text: str, url: str) -> bool:
    haystack = f"{text} {url}".lower()
    return any(kw.lower() in haystack for kw in RELEVANT_KEYWORDS)


# ============================================================
# リンク抽出（★ エンコーディング修正済み）
# ============================================================
def extract_links(session: requests.Session, logger: logging.Logger) -> List[LinkItem]:
    response = session.get(PORTAL_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    # ★ 厚労省サイトのエンコーディングを正しく検出
    response.encoding = response.apparent_encoding

    soup = BeautifulSoup(response.text, "lxml")
    links: List[LinkItem] = []
    seen: Set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        absolute_url = urljoin(PORTAL_URL, href)
        text = normalize_text(anchor.get_text(" ", strip=True)) or normalize_text(href)
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(LinkItem(text=text, url=absolute_url))
    logger.info("ポータルページリンク抽出完了: total=%d", len(links))
    return links


def save_link_snapshot(links: Iterable[LinkItem]) -> None:
    payload = [{"text": link.text, "url": link.url} for link in links]
    LINKS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# ダウンロード処理
# ============================================================
def choose_extension(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".csv", ".txt", ".zip"}:
        return suffix
    if "pdf" in content_type:
        return ".pdf"
    if "excel" in content_type or "spreadsheet" in content_type:
        return ".xlsx"
    if "word" in content_type:
        return ".docx"
    if "text/plain" in content_type:
        return ".txt"
    if "html" in content_type:
        return ".txt"
    return ".bin"


def stream_download_with_hash(
    session: requests.Session, url: str, output_path: Path
) -> Tuple[float, str, str]:
    with session.get(url, timeout=TIMEOUT_SECONDS, stream=True) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()
        hasher = hashlib.sha256()
        with output_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                hasher.update(chunk)
    size_kb = round(output_path.stat().st_size / 1024, 1)
    return size_kb, hasher.hexdigest(), content_type


def html_to_text(
    session: requests.Session, url: str, output_path: Path
) -> Tuple[float, str]:
    resp = session.get(url, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding  # ★ ここも修正
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup.select("script, style, noscript"):
        tag.decompose()
    text = normalize_text(soup.get_text("\n", strip=True))
    output_path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    size_kb = round(output_path.stat().st_size / 1024, 1)
    return size_kb, digest


# ============================================================
# メタデータ・CSV 出力
# ============================================================
def write_structure_metadata(records: List[DownloadRecord]) -> None:
    structure: Dict[str, Dict[str, int]] = {}
    for rec in records:
        structure.setdefault(rec.year, {})
        structure[rec.year].setdefault(rec.category, 0)
        structure[rec.year][rec.category] += 1
    payload = {
        "portal_url": PORTAL_URL,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "counts": structure,
        "total_records": len(records),
    }
    STRUCTURE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_records(records: List[DownloadRecord]) -> None:
    if not records:
        INDEX_CSV.write_text("", encoding="utf-8")
        return
    df = pd.DataFrame([r.__dict__ for r in records])
    rename_map = {
        "year": "年度",
        "category": "カテゴリ",
        "file_name": "ファイル名",
        "url": "URL",
        "downloaded_at": "ダウンロード日時",
        "file_size_kb": "ファイルサイズ(KB)",
        "status": "ステータス",
        "note": "備考",
    }
    df.rename(columns=rename_map, inplace=True)
    df.to_csv(INDEX_CSV, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


# ============================================================
# リンク処理ループ
# ============================================================
def process_links(
    session: requests.Session,
    links: List[LinkItem],
    logger: logging.Logger,
    limit: Optional[int],
) -> List[DownloadRecord]:
    relevant = [lnk for lnk in links if is_relevant_link(lnk.text, lnk.url)]
    if limit is not None:
        relevant = relevant[:limit]

    records: List[DownloadRecord] = []
    seen_names: Set[str] = set()
    seen_hashes: Set[str] = set()
    total = len(relevant)
    logger.info("対象リンク数: %d (全リンク %d 中)", total, len(links))

    for idx, link in enumerate(relevant, start=1):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_token = datetime.now().strftime("%Y%m%d")
        descriptor = normalize_text(
            link.text or Path(urlparse(link.url).path).name
        )
        year = detect_year(descriptor)
        cat_slug, cat_label = detect_category(descriptor)
        out_dir = TEXT_ROOT / year / cat_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{idx}/{total}] {descriptor[:80]}")
        logger.info("[%d/%d] start: %s", idx, total, link.url)

        # HEAD でコンテンツタイプを取得
        try:
            head = session.head(link.url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
            ctype = head.headers.get("Content-Type", "").lower()
        except requests.RequestException:
            ctype = ""

        ext = choose_extension(link.url, ctype)
        base = sanitize_for_filename(f"{year}_{descriptor}_{date_token}")
        fname = f"{base}{ext}"

        if fname in seen_names:
            records.append(
                DownloadRecord(year, cat_label, fname, link.url, now, 0.0, "スキップ", "ファイル名重複")
            )
            continue

        path = out_dir / fname
        try:
            if ext == ".txt" and ("html" in ctype or not Path(urlparse(link.url).path).suffix):
                size_kb, fhash = html_to_text(session, link.url, path)
            else:
                size_kb, fhash, detected = stream_download_with_hash(session, link.url, path)
                if ext == ".bin" and "html" in detected:
                    path.unlink(missing_ok=True)
                    path = path.with_suffix(".txt")
                    fname = path.name
                    size_kb, fhash = html_to_text(session, link.url, path)

            if fhash in seen_hashes:
                path.unlink(missing_ok=True)
                records.append(
                    DownloadRecord(year, cat_label, fname, link.url, now, 0.0, "スキップ", "ハッシュ重複")
                )
                continue

            seen_names.add(fname)
            seen_hashes.add(fhash)
            records.append(
                DownloadRecord(year, cat_label, fname, link.url, now, size_kb, "成功", descriptor[:200])
            )
            logger.info("saved: %s (%.1f KB)", path.as_posix(), size_kb)

        except Exception as exc:
            logger.exception("download failed: %s", link.url)
            records.append(
                DownloadRecord(year, cat_label, fname, link.url, now, 0.0, "失敗", f"{type(exc).__name__}: {exc}")
            )

        time.sleep(1)  # サーバー負荷軽減

    return records


# ============================================================
# CLI エントリポイント
# ============================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="診療報酬関連資料の包括ダウンローダー")
    parser.add_argument("--limit", type=int, default=None, help="処理リンク数上限（テスト用）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_directories()
    logger = configure_logger()
    session = build_session()

    logger.info("=== Start comprehensive downloader ===")
    logger.info("Portal: %s", PORTAL_URL)

    links = extract_links(session, logger)
    save_link_snapshot(links)

    records = process_links(session, links, logger, args.limit)
    save_records(records)
    write_structure_metadata(records)

    success = sum(1 for r in records if r.status == "成功")
    skip = sum(1 for r in records if r.status == "スキップ")
    fail = sum(1 for r in records if r.status == "失敗")

    logger.info("=== 完了: 成功=%d スキップ=%d 失敗=%d 合計=%d ===", success, skip, fail, len(records))
    print(f"\nDone. 成功={success} スキップ={skip} 失敗={fail} / 合計={len(records)}")
    print(f"CSV: {INDEX_CSV.as_posix()}")


if __name__ == "__main__":
    main()
