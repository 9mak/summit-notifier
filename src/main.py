"""
main.py — サミット特売OCR & LINE通知 メインオーケストレーター
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
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
    store_id     = os.getenv("SUMMIT_STORE_ID", "151")
    wp_user      = os.getenv("SUMMIT_WP_USER", "preview")
    wp_pass      = os.getenv("SUMMIT_WP_PASS", "")
    line_token   = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    line_user    = os.getenv("LINE_USER_ID", "")
    ollama_model = os.getenv("OLLAMA_MODEL", "gemma4")
    ollama_url   = os.getenv("OLLAMA_URL", "http://localhost:11434")

    if not wp_pass:
        logger.error("SUMMIT_WP_PASS が未設定"); return 1
    if not dry_run and (not line_token or not line_user):
        logger.error("LINE設定が未設定"); return 1

    logger.info("=" * 50)
    logger.info(f"開始 store_id={store_id} model={ollama_model} dry_run={dry_run}")
    logger.info("=" * 50)

    flyer_dir = Path(__file__).parent.parent / "data" / "flyers"
    if force:
        for f in flyer_dir.glob("*.jpg"): f.unlink()
        logger.info("[main] キャッシュクリア")

    scraper = SummitStoreScraper(store_id=store_id, wp_user=wp_user, wp_pass=wp_pass, save_dir=str(flyer_dir))
    info = scraper.get_store_info()
    if info: logger.info(f"[main] 店舗: {info.get('name','不明')}")

    image_paths = scraper.fetch_today_flyer_images()
    if not image_paths:
        logger.warning("[main] 画像取得失敗"); return 1
    logger.info(f"[main] {len(image_paths)} 枚取得")

    ocr = GemmaVisionOCR(model=ollama_model, ollama_url=ollama_url)
    if not ocr.check_ollama_health():
        logger.error("[main] Ollama 未起動"); return 1

    ocr_results = ocr.extract_from_multiple(image_paths)
    total_items = sum(len(r.items) for r in ocr_results)
    logger.info(f"[main] OCR完了: {total_items} 件抽出")

    if dry_run:
        logger.info("[main] dry-run: LINE送信スキップ")
        print("\n" + "=" * 50)
        print("【DRY-RUN】抽出結果")
        print("=" * 50)
        for result in ocr_results:
            page = Path(result.image_path).name
            print(f"\n📄 {page} — {len(result.items)} 件")
            for item in result.items:
                emoji = {"肉":"🥩","野菜":"🥬","魚":"🐟"}.get(item.category,"🛒")
                print(f"  {emoji} {item.name}  {item.price}  {item.note}")
        if total_items == 0:
            print("\n【DEBUG】Gemma 生レスポンス")
            print("=" * 50)
            for r in ocr_results:
                print(f"\n--- {Path(r.image_path).name} ---")
                raw = r.raw_response.strip()
                print(raw[:3000] if raw else "(レスポンスなし)")
        print("=" * 50)
        return 0

    notifier = LineNotifier(line_token, line_user)
    res = notifier.notify_sale_items(ocr_results, store_name="サミット")
    if res.success: logger.info("[main] LINE送信完了 ✅")
    else: logger.error(f"[main] LINE送信失敗: {res.error}"); return 1
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, force=args.force))
