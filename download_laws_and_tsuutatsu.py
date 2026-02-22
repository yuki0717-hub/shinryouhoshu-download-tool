#!/usr/bin/env python3
"""e-Gov法令APIから指定法令の条文本文を取得し、テキスト化して保存するスクリプト。"""

from __future__ import annotations

import csv
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from urllib.request import Request, urlopen

BASE_DIR = Path("output") / "ai-hourei-db"
TEXT_DIR = BASE_DIR / "text"
DATA_DIR = BASE_DIR / "data"
INDEX_CSV_PATH = DATA_DIR / "laws_index.csv"
LOG_PATH = DATA_DIR / "download.log"

TIMEOUT_SECONDS = 30
USER_AGENT = "shinryouhoshu-downloader/1.0"

# e-Gov 法令API エンドポイント
EGOV_API_BASE = "https://laws.e-gov.go.jp/api/1/lawdata"


class LawTarget:
    def __init__(self, name: str, category: str, law_id: str, output_filename: str):
        self.name = name
        self.category = category
        self.law_id = law_id
        self.output_filename = output_filename


TARGETS: List[LawTarget] = [
    LawTarget("健康保険法", "法律", "211AC0000000070", "kenko-hoken-hou.txt"),
    LawTarget("健康保険法施行令", "施行令", "211IO0000000243", "kenko-hoken-seirei.txt"),
    LawTarget("健康保険法施行規則", "施行規則", "211M10000008036", "kenko-hoken-kisoku.txt"),
    LawTarget("保険医療機関及び保険医療養担当規則", "省令", "332M50000100015", "ryoutan-kisoku.txt"),
    LawTarget("医療法", "法律", "323AC0000000205", "iryo-hou.txt"),
    LawTarget("医療法施行令", "施行令", "323IO0000000326", "iryo-seirei.txt"),
    LawTarget("医療法施行規則", "施行規則", "323M40000100050", "iryo-kisoku.txt"),
    LawTarget("医師法", "法律", "323AC0000000201", "ishi-hou.txt"),
    LawTarget("高齢者の医療の確保に関する法律", "法律", "357AC0000000080", "koureisha-iryo-hou.txt"),
    LawTarget("介護保険法", "法律", "409AC0000000123", "kaigo-hoken-hou.txt"),
]


def ensure_directories() -> None:
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def configure_logger() -> logging.Logger:
    logger = logging.getLogger("download_laws")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def fetch_law_xml(law_id: str) -> str:
    """e-Gov法令APIからXMLを取得"""
    url = f"{EGOV_API_BASE}/{law_id}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8")


def xml_to_text(xml_str: str) -> str:
    """法令XMLからテキストを抽出"""
    root = ET.fromstring(xml_str)

    # ApplData/LawFullText 以下を探す
    law_full_text = root.find(".//LawFullText")
    if law_full_text is None:
        law_full_text = root.find(".//LawBody")
    if law_full_text is None:
        # フォールバック: 全テキスト
        return "\n".join(root.itertext()).strip()

    lines = []
    for elem in law_full_text.iter():
        if elem.text and elem.text.strip():
            lines.append(elem.text.strip())
        if elem.tail and elem.tail.strip():
            lines.append(elem.tail.strip())
    return "\n".join(lines).strip()


def save_text(file_path: Path, header: str, text: str) -> float:
    content = f"# {header}\n# 取得日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n# ソース: e-Gov法令API\n\n{text}"
    file_path.write_text(content, encoding="utf-8")
    return round(file_path.stat().st_size / 1024, 1)


def process_targets(logger: logging.Logger) -> List[Tuple[str, str, str, str, str, float]]:
    rows = []
    for target in TARGETS:
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output_path = TEXT_DIR / target.output_filename
        logger.info("Downloading: %s (law_id=%s)", target.name, target.law_id)
        print(f"[INFO] Downloading: {target.name}")

        try:
            xml_str = fetch_law_xml(target.law_id)
            text = xml_to_text(xml_str)
            if len(text) < 100:
                raise ValueError(f"取得テキストが短すぎます ({len(text)} chars)")
            size_kb = save_text(output_path, target.name, text)
            status = "成功"
            logger.info("Saved: %s (%.1f KB)", output_path.as_posix(), size_kb)
            print(f"[INFO] Saved: {output_path.as_posix()} ({size_kb} KB)")
        except Exception as exc:
            size_kb = 0.0
            status = f"失敗: {type(exc).__name__}: {exc}"
            logger.exception("Error: %s: %s", target.name, exc)
            print(f"[ERROR] {target.name}: {status}")

        rows.append((target.name, target.category, f"{EGOV_API_BASE}/{target.law_id}", fetched_at, status, size_kb))
        time.sleep(1)  # API負荷軽減

    return rows


def save_index(rows: List[Tuple[str, str, str, str, str, float]]) -> None:
    with INDEX_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["法令名", "種類", "URL", "取得日時", "ステータス", "ファイルサイズ(KB)"])
        for row in rows:
            writer.writerow(row)


def main() -> None:
    ensure_directories()
    logger = configure_logger()
    logger.info("=== 法令ダウンロード開始 ===")
    rows = process_targets(logger)
    save_index(rows)
    success_count = sum(1 for r in rows if r[4] == "成功")
    logger.info("完了: %d/%d 成功", success_count, len(rows))
    print(f"[INFO] CSV: {INDEX_CSV_PATH.as_posix()}")


if __name__ == "__main__":
    main()
