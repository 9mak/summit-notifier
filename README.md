# summit-notifier

サミット ミナノ分倍河原店のチラシをトクバイから取得し、Geminiで特売情報＋献立提案を抽出してLINE公式アカウントの友だち全員にブロードキャスト通知するツール。

GitHub Actions により毎日 12:00 JST に自動実行。

## セットアップ

GitHub リポジトリの Settings → Secrets and variables → Actions に以下を登録：

- `GEMINI_API_KEY` — Google AI Studio の Gemini API キー
- `LINE_CHANNEL_ACCESS_TOKEN` — LINE Developers の Messaging API チャネルアクセストークン

## ローカル実行

```bash
cp .env.example .env  # 値を埋める
export $(cat .env | xargs)
python3 main.py
```
