# 診療報酬データベースミラー

> 厚生労働省の診療報酬関連資料・法令を自動収集・構造化し、AI LLM から参照可能にするプロジェクト

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-success)](https://yuki0717-hub.github.io/shinryouhoshu-download-tool/)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## 📊 プロジェクト概要

本リポジトリは、診療報酬改定に関連する以下のデータを自動ダウンロード・構造化・公開します：

- **診療報酬改定資料**：令和8年度の改定通知、施設基準、疑義解釈など
- **関連法令**：健康保険法、医療法、医師法などの条文
- **中医協資料**：中央社会保険医療協議会の答申・議事録
- **点数表マスター**：診療報酬点数表（医科・歯科・調剤）

## 🌐 公開 URL

**ホームページ**：https://yuki0717-hub.github.io/shinryouhoshu-download-tool/

**API エンドポイント**：
- `/ai-shinryou-db/` - 診療報酬改定資料
- `/ai-hourei-db/` - 関連法令
- `/ai-chiikyou-db/` - 中医協資料
- `/ai-tensuhyo-db/` - 点数表マスター

## 🤖 AI LLM からの参照

ChatGPT、Claude、Google Gemini などから参照可能です。

詳細は [USAGE_FOR_LLMS.txt](USAGE_FOR_LLMS.txt) を参照してください。

## 🔄 自動更新スケジュール

- **診療報酬改定資料**：毎日 18:00 UTC（日本時間 03:00）
- **法令・通達**：毎日 13:00 UTC（日本時間 22:00）
- **中医協資料**：会議後随時

## 📁 ファイル構成

