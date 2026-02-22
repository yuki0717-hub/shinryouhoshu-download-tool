#!/usr/bin/env python3
"""診療報酬関連データ完全網羅型自動ダウンロードツール（標準ライブラリ版）"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SUPPORTED_EXTENSIONS = {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".txt", ".csv", ".zip"}


@dataclass
class SourceConfig:
    name: str
    category: str
    stage: str
    url: str
    include_keywords: list[str]
    exclude_keywords: list[str]


@dataclass
class DownloadRecord:
    file_name: str
    category: str
    source_page: str
    file_url: str
    downloaded_at: str
    file_size: int
    status: str


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.current_href = ""
        self.current_text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_dict = dict(attrs)
        href = (attr_dict.get("href") or "").strip()
        self.current_href = urljoin(self.base_url, href) if href else ""
        self.current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self.current_href:
            self.current_text_parts.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href:
            text = " ".join(t for t in self.current_text_parts if t)
            self.links.append((self.current_href, text))
            self.current_href = ""
            self.current_text_parts = []


class MedicalFeeDownloader:
    def __init__(self, config_path: Path, output_dir: Path, timeout: int = 30, user_agent: str = "shinryouhoshu-downloader/1.0"):
        self.config_path = config_path
        self.output_dir = output_dir
        self.timeout = timeout
        self.user_agent = user_agent
        self.records: list[DownloadRecord] = []
        self.downloaded_keys: set[str] = set()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()

    def _setup_logging(self) -> None:
        self.log_file = self.output_dir / "download.log"
        self.error_file = self.output_dir / "error.log"

        self.logger = logging.getLogger("download_logger")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(self.log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)

        eh = logging.FileHandler(self.error_file, encoding="utf-8")
        eh.setLevel(logging.ERROR)
        eh.setFormatter(fmt)
        self.logger.addHandler(eh)

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        self.logger.addHandler(sh)

    def load_config(self) -> list[SourceConfig]:
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        return [
            SourceConfig(
                name=item["name"],
                category=item["category"],
                stage=item.get("stage", "unknown"),
                url=item["url"],
                include_keywords=item.get("include_keywords", []),
                exclude_keywords=item.get("exclude_keywords", []),
            )
            for item in data.get("sources", [])
        ]

    def run(self, dry_run: bool = False, sleep_sec: float = 0.5) -> None:
        self.logger.info("=== ダウンロード処理開始 ===")
        sources = self.load_config()

        for idx, source in enumerate(sources, start=1):
            self.logger.info("[%s/%s] 処理中: %s", idx, len(sources), source.name)
            self.process_source(source, dry_run=dry_run)
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        self.write_csv()
        self.logger.info("=== ダウンロード処理終了 (件数: %s) ===", len(self.records))

    def process_source(self, source: SourceConfig, dry_run: bool = False) -> None:
        try:
            html = self.fetch_text(source.url)
            links = self.extract_links(html, source.url)
            targets = self.filter_links(links, source)
            if not targets:
                self.logger.warning("対象ファイルが見つかりません: %s", source.name)
                return
            for link_url, text in targets:
                self.handle_link(source, link_url, text, dry_run)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("ソース処理エラー [%s]: %s", source.name, exc)

    def fetch_text(self, url: str) -> str:
        req = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

    def extract_links(self, html: str, base_url: str) -> list[tuple[str, str]]:
        parser = LinkExtractor(base_url)
        parser.feed(html)
        return parser.links

    def filter_links(self, links: Iterable[tuple[str, str]], source: SourceConfig) -> list[tuple[str, str]]:
        include_set = [k.lower() for k in source.include_keywords]
        exclude_set = [k.lower() for k in source.exclude_keywords]
        targets: list[tuple[str, str]] = []

        for url, text in links:
            ext = Path(urlparse(url).path).suffix.lower()
            searchable = f"{url} {text}".lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            if include_set and not any(k in searchable for k in include_set):
                continue
            if exclude_set and any(k in searchable for k in exclude_set):
                continue
            targets.append((url, text))

        return list(dict.fromkeys(targets))

    def handle_link(self, source: SourceConfig, file_url: str, text: str, dry_run: bool) -> None:
        file_key = hashlib.sha256(file_url.encode("utf-8")).hexdigest()
        if file_key in self.downloaded_keys:
            self.records.append(self._make_record("", source, source.url, file_url, 0, "skipped_duplicate_url"))
            return
        self.downloaded_keys.add(file_key)

        year = self.extract_year(source.name + " " + text + " " + file_url)
        date_str = self.extract_date(source.name + " " + text + " " + file_url)
        ext = Path(urlparse(file_url).path).suffix.lower() or ".bin"
        title = self.slugify(text) or self.slugify(Path(urlparse(file_url).path).stem) or "document"
        category = self.slugify(source.category)
        final_name = f"{category}_{year}_{date_str}_{title}{ext}"
        path = self.output_dir / final_name

        if path.exists():
            self.records.append(self._make_record(final_name, source, source.url, file_url, path.stat().st_size, "skipped_existing_file"))
            return

        if dry_run:
            self.records.append(self._make_record(final_name, source, source.url, file_url, 0, "dry_run"))
            return

        try:
            size = self.download_file(file_url, path)
            self.records.append(self._make_record(final_name, source, source.url, file_url, size, "downloaded"))
            self.logger.info("保存完了: %s (%s bytes)", final_name, size)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("ダウンロード失敗 [%s]: %s", file_url, exc)
            self.records.append(self._make_record(final_name, source, source.url, file_url, 0, f"error: {exc}"))

    def download_file(self, url: str, path: Path) -> int:
        req = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            data = resp.read()
        path.write_bytes(data)
        return len(data)

    def write_csv(self) -> None:
        csv_path = self.output_dir / "files_list.csv"
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["file_name", "category", "source_page", "url", "downloaded_at", "file_size", "status"])
            for r in self.records:
                writer.writerow([r.file_name, r.category, r.source_page, r.file_url, r.downloaded_at, r.file_size, r.status])

    @staticmethod
    def slugify(value: str) -> str:
        value = re.sub(r"\s+", "_", value)
        value = re.sub(r"[^\w\-ぁ-んァ-ン一-龥ー_]", "", value)
        return value.strip("_")[:80]

    @staticmethod
    def extract_year(text: str) -> str:
        for p, r in [
            (r"令和\s*([0-9]{1,2})\s*年度", lambda m: f"R{m.group(1)}"),
            (r"(20[0-9]{2})\s*年度", lambda m: m.group(1)),
            (r"(20[0-9]{2})", lambda m: m.group(1)),
        ]:
            m = re.search(p, text)
            if m:
                return r(m)
        return "unknownYear"

    @staticmethod
    def extract_date(text: str) -> str:
        m = re.search(r"(20[0-9]{2})[\-/年](\d{1,2})[\-/月](\d{1,2})", text)
        if m:
            return f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        m = re.search(r"令和\s*([0-9]{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
        if m:
            year = 2018 + int(m.group(1))
            return f"{year:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        return datetime.now().strftime("%Y%m%d")

    @staticmethod
    def _make_record(file_name: str, source: SourceConfig, source_page: str, file_url: str, file_size: int, status: str) -> DownloadRecord:
        return DownloadRecord(
            file_name=file_name,
            category=source.category,
            source_page=source_page,
            file_url=file_url,
            downloaded_at=datetime.now().isoformat(timespec="seconds"),
            file_size=file_size,
            status=status,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="診療報酬関連資料自動ダウンロードツール")
    p.add_argument("--config", default="urls.json")
    p.add_argument("--output-dir", default="./downloads")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sleep", type=float, default=0.5)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    dl = MedicalFeeDownloader(Path(args.config), Path(args.output_dir), timeout=args.timeout)
    dl.run(dry_run=args.dry_run, sleep_sec=args.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
