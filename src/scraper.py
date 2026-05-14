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
