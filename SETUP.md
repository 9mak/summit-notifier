# サミット特売OCR & LINE通知 — セットアップ手順

## 0. 前提環境

| 項目 | 内容 |
|------|------|
| OS | macOS (Apple Silicon / Intel 両対応) |
| Python | 3.11 以上 |
| Ollama | インストール済み |
| Gemma 4 | `ollama pull gemma4` 済み |

---

## 1. Ollama & Gemma 4 の準備

```bash
# Ollama インストール（未インストールの場合）
brew install ollama

# Gemma 4 モデルを取得（マルチモーダル対応版）
ollama pull gemma4

# 起動確認
ollama list   # → gemma4 が表示されれば OK

# 常時起動させる場合
brew services start ollama
```

> **補足:** Ollama のモデル名は `ollama list` で確認してください。
> `gemma4` でない場合は `.env` の `OLLAMA_MODEL` を合わせて変更します。

---

## 2. Summit 店舗IDの確認

1. `https://www.summitstore.co.jp/store/` にアクセス
2. 最寄り店舗をクリック
3. URLを確認: `https://www.summitstore.co.jp/store/tokyo/post/?id=XXX`
4. `XXX` が **SUMMIT_STORE_ID**（例: `151` = ミナノ分倍河原店）

---

## 3. LINE Messaging API の設定

### 3-1. チャンネル作成
1. `https://developers.line.biz/` にアクセスしてログイン
2. **新規プロバイダー作成** → **Messaging API チャンネル作成**
3. チャンネル名: 任意（例: "サミット特売通知"）
4. チャンネルアクセストークン（長期）を発行

### 3-2. User ID の確認
- LINE アプリでボットに1通メッセージを送る
- Webhook に届く JSON の `source.userId` が **LINE_USER_ID**
- または LINE Official Account Manager で確認

---

## 4. プロジェクトのセットアップ

```bash
cd summit-ocr

# 仮想環境を作成
python3 -m venv .venv
source .venv/bin/activate

# 依存パッケージをインストール
pip install -r requirements.txt

# .env を作成
cp .env.example .env
nano .env   # 必要な値を書き込む
```

`.env` の最低限の設定:

```
SUMMIT_STORE_ID=151              # 手順2で調べたID
SUMMIT_WP_USER=preview           # そのまま使用可
SUMMIT_WP_PASS=xxxx xxxx xxxx xxxx xxxx xxxx   # WP管理画面で発行したアプリパスワード
OLLAMA_MODEL=gemma4
LINE_CHANNEL_ACCESS_TOKEN=xxx    # 手順3-1で取得
LINE_USER_ID=Uxxxxxxxxx          # 手順3-2で確認
```

---

## 5. 動作確認

```bash
# dry-run で LINE送信なしにコンソール出力確認
python src/main.py --dry-run

# 問題なければ本番実行
python src/main.py
```

---

## 6. launchd で毎朝自動実行（macOS）

```bash
# plist のパスを実際の絶対パスに書き換える
PROJ=$(pwd)   # summit-ocr ディレクトリで実行

sed -i '' "s|/path/to/your/summit-ocr|$PROJ|g" \
  launchd/com.user.summit-ocr.plist

# LaunchAgents にコピー
cp launchd/com.user.summit-ocr.plist \
  ~/Library/LaunchAgents/com.user.summit-ocr.plist

# launchd に登録
launchctl load ~/Library/LaunchAgents/com.user.summit-ocr.plist

# 登録確認
launchctl list | grep summit
```

### よく使うコマンド

```bash
# 即時実行テスト
launchctl start com.user.summit-ocr

# 停止・登録解除
launchctl unload ~/Library/LaunchAgents/com.user.summit-ocr.plist

# ログ確認
tail -f logs/summit_ocr.log
```

---

## 7. アーキテクチャ

```
summitstore.co.jp (公式)
  └─ WP REST API (mng.content-smt-site.net)
       └─ /wp/v2/store-banner   ← 認証付きで店舗バナー画像を取得
            │
            ▼
         scraper.py
         （画像ダウンロード・日付キャッシュ）
            │
            ▼
         ocr.py
         （Gemma 4 Vision @Ollama でチラシOCR）
            │
            ▼
         notifier.py
         （LINE Push Messaging API）
```

---

## 8. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `バナーが見つかりません` | 店舗IDの誤り | URLを確認し `SUMMIT_STORE_ID` を修正 |
| `Ollama に接続できません` | ollama 未起動 | `ollama serve` または `brew services start ollama` |
| `モデルが見つかりません` | モデル名の不一致 | `ollama list` で確認し `.env` を修正 |
| `LINE API エラー 401` | トークンが無効 | LINE Developers で新しいトークンを発行 |
| `画像は取得できるが価格が抽出されない` | チラシが価格表示でない | Gemmaプロンプトを調整（ocr.py内） |

---

## 9. ディレクトリ構成

```
summit-ocr/
├── src/
│   ├── main.py        # オーケストレーター
│   ├── scraper.py     # Summit WP API スクレイピング
│   ├── ocr.py         # Gemma 4 Vision OCR
│   └── notifier.py    # LINE 通知
├── launchd/
│   └── com.user.summit-ocr.plist
├── data/
│   └── flyers/        # 取得したチラシ画像（日付キャッシュ）
├── logs/
│   └── summit_ocr.log
├── .env               # ★ 実際の設定値（git管理外）
├── .env.example       # 設定テンプレート
└── requirements.txt
```
