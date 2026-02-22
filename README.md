# shinryouhoshu-download-tool

診療報酬・薬価改定・関連通知・法令・中医協資料を横断して自動収集する Python ツールです。  
厚生労働省サイトおよび e-Gov 法令検索を対象に、リンク抽出〜ダウンロード〜CSV台帳化までを一括実行します。

## 機能

- 複数ページ（`urls.json`）から自動で資料リンクを抽出
- 対応形式: PDF / Excel / Word / TXT / CSV / ZIP
- 保存ファイル名へ「分類・年度・日付・元タイトル」を自動付与
- 出力フォルダを指定可能（デフォルト: `./downloads`）
- 実行結果を `files_list.csv` と `download.log` / `error.log` に出力
- URL重複・同名ファイル重複を自動スキップ
- 月1回の定期実行（cron / タスクスケジューラ）に対応しやすい構成

---

## ファイル構成

1. `main.py` - メインスクリプト
2. `requirements.txt` - 依存ライブラリ
3. `README.md` - 本ドキュメント
4. `urls.json` - ダウンロード対象URLリスト（保守用）

---

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 実行方法

### 通常実行

```bash
python main.py
```

### 出力先を指定

```bash
python main.py --output-dir /path/to/downloads
```

### 実ファイルを保存しない確認実行（Dry Run）

```bash
python main.py --dry-run
```

### オプション

- `--config` : URL定義JSON（既定: `urls.json`）
- `--output-dir` : 出力先フォルダ（既定: `./downloads`）
- `--timeout` : HTTPタイムアウト秒（既定: `30`）
- `--dry-run` : ダウンロードを行わず対象一覧のみ出力
- `--sleep` : 各ソース処理の待機秒（既定: `0.5`）

---

## 出力ファイル

### 1) `files_list.csv`

列:

- `file_name`
- `category`
- `source_page`
- `url`
- `downloaded_at`
- `file_size`
- `status`

### 2) `download.log`

- 処理開始・終了
- ソース単位の進捗
- 各ファイルの保存結果

### 3) `error.log`

- HTTPエラー
- パース失敗
- ダウンロード失敗

---

## 月1回の定期実行例（Linux cron）

毎月1日 04:00 実行:

```bash
0 4 1 * * cd /path/to/shinryouhoshu-download-tool && /usr/bin/python3 main.py >> cron.log 2>&1
```

---

## 運用メモ

- `urls.json` の `include_keywords` を調整すると、対象資料の絞り込み精度を改善できます。
- 厚労省のページ構造変更時は `url` とキーワードを更新してください。
- 同一URLは自動スキップされるため、定期実行に向いています。

