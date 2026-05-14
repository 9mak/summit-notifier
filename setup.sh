#!/bin/bash
# ============================================================
# サミット特売OCR & LINE通知 — セットアップスクリプト
# 実行方法: bash setup.sh
# ============================================================
set -e

PROJ_DIR="$HOME/Desktop/summit-ocr"
echo "📁 プロジェクトディレクトリ: $PROJ_DIR"

# ディレクトリ作成
mkdir -p "$PROJ_DIR/src"
mkdir -p "$PROJ_DIR/data/flyers"
mkdir -p "$PROJ_DIR/logs"
mkdir -p "$PROJ_DIR/launchd"

# ── requirements.txt ──────────────────────────────────────────
cat > "$PROJ_DIR/requirements.txt" << 'EOF'
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
python-dotenv>=1.0.0
EOF

# ── .env ──────────────────────────────────────────────────────
cat > "$PROJ_DIR/.env" << 'EOF'
SUMMIT_STORE_ID=151
SUMMIT_WP_USER=preview
SUMMIT_WP_PASS=xxxx xxxx xxxx xxxx xxxx xxxx
OLLAMA_MODEL=gemma4
OLLAMA_URL=http://localhost:11434
LINE_CHANNEL_ACCESS_TOKEN=your_channel_access_token_here
LINE_USER_ID=Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF

# ── src/scraper.py ────────────────────────────────────────────
cat > "$PROJ_DIR/src/scraper.py" << 'PYEOF'
"""
scraper.py — Summit公式 WP REST API から店舗別バナー画像を取得する
"""
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)
WP_API_BASE = "https://mng.content-smt-site.net/wp-json/wp/v2"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


class SummitStoreScraper:
    def __init__(self, store_id, wp_user, wp_pass, save_dir="data/flyers", request_interval=1.0):
        self.store_id = str(store_id)
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.interval = request_interval
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.auth = (wp_user, wp_pass)

    def fetch_today_flyer_images(self):
        today = date.today().isoformat()
        cached = sorted(self.save_dir.glob(f"{today}_*.jpg"))
        if cached:
            logger.info(f"[scraper] キャッシュ使用: {len(cached)} 枚")
            return cached

        banner = self._get_latest_banner()
        if not banner:
            logger.warning("[scraper] バナーが見つかりませんでした")
            return []

        logger.info(f"[scraper] バナーID={banner['id']} ({banner['date'][:10]})")
        saved_paths = []
        for slot_idx in range(1, 5):
            img_id = banner.get("acf", {}).get(f"banner{slot_idx}_img", "")
            if not img_id:
                continue
            img_url = self._resolve_media_url(img_id)
            if not img_url:
                continue
            dest = self.save_dir / f"{today}_slot{slot_idx:02d}.jpg"
            path = self._download_image(img_url, dest)
            if path:
                saved_paths.append(path)
            time.sleep(self.interval)

        logger.info(f"[scraper] 取得完了: {len(saved_paths)} 枚")
        return saved_paths

    def get_store_info(self):
        try:
            r = self.session.get(f"{WP_API_BASE}/store", params={"slug": self.store_id}, timeout=10)
            r.raise_for_status()
            stores = r.json()
            if stores:
                s = stores[0]
                return {"id": self.store_id, "name": s.get("title", {}).get("rendered", ""), "wp_post_id": s.get("id")}
        except Exception as e:
            logger.warning(f"[scraper] 店舗情報取得失敗: {e}")
        return {}

    def _get_latest_banner(self):
        logger.info(f"[scraper] store-banner 検索中 (store_id={self.store_id})")
        page = 1
        while True:
            try:
                r = self.session.get(f"{WP_API_BASE}/store-banner",
                    params={"per_page": 100, "page": page, "orderby": "date", "order": "desc"}, timeout=15)
                r.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"[scraper] API取得失敗: {e}")
                return None
            banners = r.json()
            if not banners:
                break
            for b in banners:
                acf = b.get("acf", {})
                if str(acf.get("banner_store", "")) == self.store_id:
                    return b
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages:
                break
            page += 1
        logger.warning(f"[scraper] store_id={self.store_id} のバナーが見つかりません")
        return None

    def _resolve_media_url(self, media_id):
        try:
            r = self.session.get(f"{WP_API_BASE}/media/{media_id}", timeout=10)
            r.raise_for_status()
            return r.json().get("source_url", "")
        except Exception as e:
            logger.warning(f"[scraper] メディア解決失敗 id={media_id}: {e}")
            return None

    def _download_image(self, url, dest):
        if dest.exists():
            logger.info(f"[scraper] スキップ（キャッシュ済み）: {dest.name}")
            return dest
        try:
            r = self.session.get(url, timeout=30, stream=True)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"[scraper] 保存: {dest.name} ({dest.stat().st_size // 1024} KB)")
            return dest
        except Exception as e:
            logger.error(f"[scraper] ダウンロード失敗 {url}: {e}")
            return None
PYEOF

# ── src/ocr.py ────────────────────────────────────────────────
cat > "$PROJ_DIR/src/ocr.py" << 'PYEOF'
"""
ocr.py — Gemma 4 Vision (Ollama) でチラシ画像から特売情報を抽出する
"""
import base64, json, logging, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)

@dataclass
class SaleItem:
    category: str
    name: str
    price: str
    unit: str = ""
    note: str = ""

@dataclass
class FlyerOCRResult:
    image_path: str
    items: list = field(default_factory=list)
    raw_response: str = ""
    success: bool = True
    error: str = ""

SYSTEM_PROMPT = "あなたはスーパーマーケットのチラシ画像から特売情報を抽出する専門家です。"

EXTRACTION_PROMPT = """このチラシ画像から「肉類」「野菜」「魚介類」のセール・特売商品を抽出してください。

以下の JSON 配列のみで出力してください（説明文不要）:
[
  {"category": "肉", "name": "国産豚バラ肉", "price": "100g 78円", "unit": "100gあたり", "note": "本日限り"},
  {"category": "野菜", "name": "大根", "price": "1本 98円", "unit": "1本", "note": ""}
]

ルール:
- category は "肉" / "野菜" / "魚" / "その他" のいずれか
- 価格が読み取れない場合は price を "価格不明" に
- 掲載されていない商品は追加しない
- JSON のみを返す（前置き・コードブロック記号不要）
"""

class GemmaVisionOCR:
    def __init__(self, model="gemma4", ollama_url="http://localhost:11434", timeout=120):
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self.timeout = timeout
        self._api_endpoint = f"{self.ollama_url}/api/generate"

    def extract_sale_items(self, image_path):
        result = FlyerOCRResult(image_path=str(image_path))
        try:
            img_b64 = base64.b64encode(open(image_path, "rb").read()).decode("utf-8")
        except Exception as e:
            result.success = False
            result.error = f"画像読み込みエラー: {e}"
            return result

        raw_text = self._call_ollama(img_b64)
        if raw_text is None:
            result.success = False
            result.error = "Ollama API 呼び出し失敗"
            return result

        result.raw_response = raw_text
        result.items = self._parse_json_response(raw_text)
        logger.info(f"[ocr] {Path(image_path).name}: {len(result.items)} 件抽出")
        return result

    def extract_from_multiple(self, image_paths):
        return [self.extract_sale_items(p) for p in image_paths]

    def _call_ollama(self, img_b64):
        payload = {"model": self.model, "system": SYSTEM_PROMPT, "prompt": EXTRACTION_PROMPT,
                   "images": [img_b64], "stream": False, "options": {"temperature": 0.1}}
        try:
            resp = requests.post(self._api_endpoint, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.ConnectionError:
            logger.error("[ocr] Ollama に接続できません。`ollama serve` を実行してください。")
            return None
        except Exception as e:
            logger.error(f"[ocr] Ollama APIエラー: {e}")
            return None

    def _parse_json_response(self, raw_text):
        cleaned = re.sub(r"```(?:json)?", "", raw_text).strip()
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        items = []
        for entry in data:
            if isinstance(entry, dict) and entry.get("name"):
                items.append(SaleItem(
                    category=entry.get("category", "その他"),
                    name=entry.get("name", ""),
                    price=entry.get("price", "価格不明"),
                    unit=entry.get("unit", ""),
                    note=entry.get("note", ""),
                ))
        return items

    def check_ollama_health(self):
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            logger.info(f"[ocr] Ollama OK。モデル: {models}")
            if not any(self.model in m for m in models):
                logger.warning(f"[ocr] モデル '{self.model}' が見つかりません。`ollama pull {self.model}` を実行してください。")
                return False
            return True
        except Exception:
            logger.error("[ocr] Ollama が起動していません。`ollama serve` を実行してください。")
            return False
PYEOF

# ── src/notifier.py ───────────────────────────────────────────
cat > "$PROJ_DIR/src/notifier.py" << 'PYEOF'
"""
notifier.py — LINE Messaging API (Push Message) で特売情報を通知する
"""
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional
import requests

logger = logging.getLogger(__name__)
LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"
CATEGORY_EMOJI = {"肉": "🥩", "野菜": "🥬", "魚": "🐟", "その他": "🛒"}

@dataclass
class NotifyResult:
    success: bool
    status_code: Optional[int] = None
    error: str = ""

class LineNotifier:
    def __init__(self, channel_access_token, user_id):
        self.token = channel_access_token
        self.user_id = user_id
        self._headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}

    def notify_sale_items(self, results, store_name="サミット"):
        all_items = self._aggregate_items(results)
        if not all_items:
            return self._push([{"type": "text", "text": f"📋 {store_name} 本日の特売品は見つかりませんでした。"}])
        messages = self._build_messages(all_items, store_name)
        return self._push(messages)

    def notify_error(self, error_message):
        return self._push([{"type": "text", "text": f"⚠️ サミット特売通知エラー\n\n{error_message}"}])

    def _aggregate_items(self, results):
        seen, items = set(), []
        for result in results:
            for item in result.items:
                key = f"{item.name}_{item.price}"
                if key not in seen:
                    seen.add(key)
                    items.append(item)
        order = {"肉": 0, "野菜": 1, "魚": 2, "その他": 3}
        items.sort(key=lambda x: order.get(x.category, 9))
        return items

    def _build_messages(self, items, store_name):
        today_str = date.today().strftime("%m月%d日")
        header = f"🛍️ {store_name} 本日の特売情報\n📅 {today_str}\n"
        by_cat = {}
        for item in items:
            by_cat.setdefault(item.category, []).append(item)
        lines = [header]
        for cat, cat_items in by_cat.items():
            emoji = CATEGORY_EMOJI.get(cat, "🛒")
            lines.append(f"\n{emoji} ─── {cat} ───")
            for item in cat_items:
                line = f"• {item.name}  {item.price}"
                if item.note:
                    line += f"  ⚡{item.note}"
                lines.append(line)
        lines.append("\n🔗 詳細はサミット公式サイトで確認できます")
        return [{"type": "text", "text": "\n".join(lines)}]

    def _push(self, messages):
        try:
            resp = requests.post(LINE_PUSH_API, headers=self._headers,
                json={"to": self.user_id, "messages": messages[:5]}, timeout=15)
            if resp.status_code == 200:
                logger.info(f"[notifier] LINE 送信成功")
                return NotifyResult(success=True, status_code=200)
            else:
                logger.error(f"[notifier] LINE API エラー {resp.status_code}: {resp.text[:200]}")
                return NotifyResult(success=False, status_code=resp.status_code, error=resp.text[:200])
        except Exception as e:
            logger.error(f"[notifier] LINE 送信失敗: {e}")
            return NotifyResult(success=False, error=str(e))
PYEOF

# ── src/main.py ───────────────────────────────────────────────
cat > "$PROJ_DIR/src/main.py" << 'PYEOF'
"""
main.py — サミット特売OCR & LINE通知 メインオーケストレーター
実行: python src/main.py [--dry-run] [--force]
"""
import argparse, logging, os, sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from scraper import SummitStoreScraper
from ocr import GemmaVisionOCR, FlyerOCRResult
from notifier import LineNotifier

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_DIR / "summit_ocr.log", encoding="utf-8")],
)
logger = logging.getLogger("main")


def run(dry_run=False, force=False):
    load_dotenv(Path(__file__).parent.parent / ".env")
    store_id     = os.getenv("SUMMIT_STORE_ID", "151")
    wp_user      = os.getenv("SUMMIT_WP_USER", "preview")
    wp_pass      = os.getenv("SUMMIT_WP_PASS", "")
    line_token   = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_user    = os.getenv("LINE_USER_ID", "")
    ollama_model = os.getenv("OLLAMA_MODEL", "gemma4")
    ollama_url   = os.getenv("OLLAMA_URL", "http://localhost:11434")

    if not wp_pass:
        logger.error("SUMMIT_WP_PASS が未設定です")
        return 1
    if not dry_run and (not line_token or not line_user):
        logger.error("LINE設定が未設定です")
        return 1

    logger.info("=" * 50)
    logger.info(f"サミット特売OCR 開始  store_id={store_id}  model={ollama_model}  dry_run={dry_run}")
    logger.info("=" * 50)

    # Step 1: 画像取得
    flyer_dir = Path(__file__).parent.parent / "data" / "flyers"
    if force:
        for f in flyer_dir.glob("*.jpg"):
            f.unlink()
        logger.info("[main] キャッシュクリア完了")

    scraper = SummitStoreScraper(store_id=store_id, wp_user=wp_user, wp_pass=wp_pass, save_dir=str(flyer_dir))
    info = scraper.get_store_info()
    if info:
        logger.info(f"[main] 対象店舗: {info.get('name', '不明')} (id={store_id})")

    image_paths = scraper.fetch_today_flyer_images()
    if not image_paths:
        msg = "チラシ画像を取得できませんでした"
        logger.warning(f"[main] {msg}")
        if not dry_run:
            LineNotifier(line_token, line_user).notify_error(msg)
        return 1

    logger.info(f"[main] {len(image_paths)} 枚取得")

    # Step 2: Gemma 4 OCR
    ocr = GemmaVisionOCR(model=ollama_model, ollama_url=ollama_url)
    if not ocr.check_ollama_health():
        msg = f"Ollama ({ollama_url}) が起動していないか、モデル '{ollama_model}' が未取得です"
        logger.error(f"[main] {msg}")
        if not dry_run:
            LineNotifier(line_token, line_user).notify_error(msg)
        return 1

    ocr_results = ocr.extract_from_multiple(image_paths)
    total = sum(len(r.items) for r in ocr_results)
    logger.info(f"[main] OCR完了: {total} 件抽出")

    # Step 3: LINE通知
    if dry_run:
        logger.info("[main] dry-run: LINE送信スキップ")
        print("\n" + "=" * 50)
        print("【DRY-RUN】抽出結果")
        print("=" * 50)
        for result in ocr_results:
            page = Path(result.image_path).name
            print(f"\n📄 {page} — {len(result.items)} 件")
            for item in result.items:
                emoji = {"肉": "🥩", "野菜": "🥬", "魚": "🐟"}.get(item.category, "🛒")
                print(f"  {emoji} {item.name}  {item.price}" + (f"  ⚡{item.note}" if item.note else ""))
        print("=" * 50 + "\n")
        return 0

    notifier = LineNotifier(line_token, line_user)
    result = notifier.notify_sale_items(ocr_results)
    if result.success:
        logger.info("[main] LINE通知 完了 ✅")
    else:
        logger.error(f"[main] LINE通知 失敗: {result.error}")
        return 1

    logger.info("完了")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, force=args.force))
PYEOF

# ── launchd plist ─────────────────────────────────────────────
cat > "$PROJ_DIR/launchd/com.user.summit-ocr.plist" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.summit-ocr</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJ_DIR/.venv/bin/python</string>
    <string>$PROJ_DIR/src/main.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>WorkingDirectory</key>
  <string>$PROJ_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$PROJ_DIR/logs/launchd_stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJ_DIR/logs/launchd_stderr.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLISTEOF

echo ""
echo "✅ ファイル生成完了！"
echo ""
echo "次のステップ:"
echo "  cd $PROJ_DIR"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python src/main.py --dry-run"
PYEOF