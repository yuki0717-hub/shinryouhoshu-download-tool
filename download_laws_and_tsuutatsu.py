diff --git a/download_laws_and_tsuutatsu.py b/download_laws_and_tsuutatsu.py
new file mode 100644
index 0000000000000000000000000000000000000000..4352fe951599903ad3c6af750322b90466912673
--- /dev/null
+++ b/download_laws_and_tsuutatsu.py
@@ -0,0 +1,208 @@
+#!/usr/bin/env python3
+"""e-Gov法令検索から指定法令の条文本文を取得し、テキスト化して保存するスクリプト。"""
+
+from __future__ import annotations
+
+import logging
+import time
+from dataclasses import dataclass
+from datetime import datetime
+from pathlib import Path
+from typing import List, Tuple
+
+import pandas as pd
+import requests
+from bs4 import BeautifulSoup
+from requests import Response
+from requests.adapters import HTTPAdapter
+from urllib3.util.retry import Retry
+
+
+BASE_DIR = Path("output") / "ai-hourei-db"
+TEXT_DIR = BASE_DIR / "text"
+DATA_DIR = BASE_DIR / "data"
+INDEX_CSV_PATH = DATA_DIR / "laws_index.csv"
+LOG_PATH = DATA_DIR / "download.log"
+
+TIMEOUT_SECONDS = 30
+MAX_RETRIES = 3
+USER_AGENT = (
+    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
+    "AppleWebKit/537.36 (KHTML, like Gecko) "
+    "Chrome/124.0.0.0 Safari/537.36"
+)
+
+
+@dataclass
+class LawTarget:
+    name: str
+    category: str
+    url: str
+    output_filename: str
+
+
+TARGETS: List[LawTarget] = [
+    LawTarget("健康保険法", "法律", "https://laws.e-gov.go.jp/law/211AC0000000070", "kenko-hoken-hou.txt"),
+    LawTarget("健康保険法施行令", "施行令", "https://laws.e-gov.go.jp/law/215IO0000000243", "kenko-hoken-seirei.txt"),
+    LawTarget("健康保険法施行規則", "施行規則", "https://laws.e-gov.go.jp/law/215M10000008036", "kenko-hoken-kisoku.txt"),
+    LawTarget("医療法", "法律", "https://laws.e-gov.go.jp/law/223AC0000000205", "iryo-hou.txt"),
+    LawTarget("医療法施行令", "施行令", "https://laws.e-gov.go.jp/law/223IO0000000305", "iryo-seirei.txt"),
+    LawTarget("医療法施行規則", "施行規則", "https://laws.e-gov.go.jp/law/223M10000008050", "iryo-kisoku.txt"),
+    LawTarget("医師法", "法律", "https://laws.e-gov.go.jp/law/223AC0000000201", "ishi-hou.txt"),
+    LawTarget("医師法施行規則", "施行規則", "https://laws.e-gov.go.jp/law/224M10000008004", "ishi-kisoku.txt"),
+    LawTarget("高齢者医療確保法", "法律", "https://laws.e-gov.go.jp/law/357AC0000000080", "kouseikyoku-hou.txt"),
+    LawTarget("診療報酬の算定方法", "告示", "https://laws.e-gov.go.jp/law/H20M10000008059", "sangyou-hoken-hou.txt"),
+]
+
+
+def ensure_directories() -> None:
+    TEXT_DIR.mkdir(parents=True, exist_ok=True)
+    DATA_DIR.mkdir(parents=True, exist_ok=True)
+
+
+def configure_logger() -> logging.Logger:
+    logger = logging.getLogger("download_laws_and_tsuutatsu")
+    logger.setLevel(logging.INFO)
+    logger.handlers.clear()
+
+    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
+
+    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
+    file_handler.setFormatter(formatter)
+
+    stream_handler = logging.StreamHandler()
+    stream_handler.setFormatter(formatter)
+
+    logger.addHandler(file_handler)
+    logger.addHandler(stream_handler)
+    return logger
+
+
+def build_session() -> requests.Session:
+    session = requests.Session()
+    retry_policy = Retry(
+        total=MAX_RETRIES,
+        read=MAX_RETRIES,
+        connect=MAX_RETRIES,
+        backoff_factor=1.0,
+        status_forcelist=(429, 500, 502, 503, 504),
+        allowed_methods=("GET",),
+        raise_on_status=False,
+    )
+    adapter = HTTPAdapter(max_retries=retry_policy)
+    session.mount("http://", adapter)
+    session.mount("https://", adapter)
+    session.headers.update({"User-Agent": USER_AGENT})
+    return session
+
+
+def fetch_html(session: requests.Session, url: str) -> Response:
+    last_exception: Exception | None = None
+    for attempt in range(1, MAX_RETRIES + 1):
+        try:
+            response = session.get(url, timeout=TIMEOUT_SECONDS)
+            response.raise_for_status()
+            return response
+        except requests.RequestException as exc:
+            last_exception = exc
+            if attempt == MAX_RETRIES:
+                break
+            time.sleep(attempt)
+    raise RuntimeError(f"最大リトライ回数({MAX_RETRIES})に到達: {url}") from last_exception
+
+
+def extract_law_text(html: str) -> str:
+    soup = BeautifulSoup(html, "lxml")
+
+    selectors = [
+        "#lawBody",
+        "#lawContent",
+        ".law-content",
+        ".lawtext",
+        "main",
+        "body",
+    ]
+
+    container = None
+    for selector in selectors:
+        container = soup.select_one(selector)
+        if container:
+            break
+
+    if container is None:
+        raise ValueError("本文コンテナが見つかりませんでした。")
+
+    for tag in container.select("script, style, noscript"):
+        tag.decompose()
+
+    lines = []
+    for raw_line in container.get_text("\n", strip=True).splitlines():
+        line = " ".join(raw_line.split())
+        if line:
+            lines.append(line)
+
+    text = "\n".join(lines).strip()
+    if not text:
+        raise ValueError("本文抽出結果が空です。")
+
+    return text
+
+
+def save_text(file_path: Path, text: str) -> float:
+    file_path.write_text(text, encoding="utf-8")
+    return round(file_path.stat().st_size / 1024, 1)
+
+
+def process_targets() -> List[Tuple[str, str, str, str, str, float]]:
+    ensure_directories()
+    logger = configure_logger()
+    session = build_session()
+
+    logger.info("Starting download_laws_and_tsuutatsu.py")
+    rows: List[Tuple[str, str, str, str, str, float]] = []
+
+    for target in TARGETS:
+        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
+        output_path = TEXT_DIR / target.output_filename
+        print(f"[INFO] Downloading: {target.name} ({target.url})")
+        logger.info("Downloading: %s", target.name)
+
+        try:
+            response = fetch_html(session, target.url)
+            extracted_text = extract_law_text(response.text)
+            size_kb = save_text(output_path, extracted_text)
+            status = "成功"
+            logger.info("Saved: %s (%.1f KB)", output_path.as_posix(), size_kb)
+            print(f"[INFO] Saved: {output_path.as_posix()} ({size_kb} KB)")
+        except Exception as exc:  # pylint: disable=broad-except
+            size_kb = 0.0
+            status = f"失敗: {type(exc).__name__}: {exc}"
+            logger.exception("Error while processing %s: %s", target.name, exc)
+            print(f"[ERROR] {target.name} の取得に失敗: {status}")
+
+        rows.append((target.name, target.category, target.url, fetched_at, status, size_kb))
+
+    success_count = sum(1 for row in rows if row[4] == "成功")
+    if success_count == len(TARGETS):
+        logger.info("All downloads completed successfully.")
+    else:
+        logger.info("Completed with failures: %d/%d succeeded.", success_count, len(TARGETS))
+
+    return rows
+
+
+def save_index(rows: List[Tuple[str, str, str, str, str, float]]) -> None:
+    columns = ["法令名", "種類", "URL", "取得日時", "ステータス", "ファイルサイズ(KB)"]
+    df = pd.DataFrame(rows, columns=columns)
+    df.to_csv(INDEX_CSV_PATH, index=False, encoding="utf-8-sig")
+
+
+def main() -> None:
+    rows = process_targets()
+    save_index(rows)
+    print(f"[INFO] CSV index saved: {INDEX_CSV_PATH.as_posix()}")
+    print(f"[INFO] Log saved: {LOG_PATH.as_posix()}")
+
+
+if __name__ == "__main__":
+    main()
