
import os, json
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv(Path(__file__).parent.parent / ".env")
WP_BASE = "https://mng.content-smt-site.net/wp-json"
WP_V2   = f"{WP_BASE}/wp/v2"
AUTH    = (os.getenv("SUMMIT_WP_USER","preview"), os.getenv("SUMMIT_WP_PASS",""))
STORE_ID = os.getenv("SUMMIT_STORE_ID","151")
s = requests.Session()
s.auth = AUTH
s.headers["User-Agent"] = "Mozilla/5.0"

def get(url, params=None):
    try:
        r = s.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json(), r.headers
    except Exception as e:
        print(f"  ERROR {url}: {e}")
        return None, {}

print("\n=== API routes (store/banner/flyer) ===")
routes, _ = get(f"{WP_BASE}/")
if routes:
    for route in sorted(routes.get("routes",{}).keys()):
        if any(k in route.lower() for k in ["store","flyer","sale","banner","tokubai","weekly"]):
            print(f"  {route}")

print(f"\n=== store_id={STORE_ID} banner slots ===")
found = None
page = 1
while True:
    data, hdrs = get(f"{WP_V2}/store-banner", {"per_page":100,"page":page,"orderby":"date","order":"desc"})
    if not data: break
    for b in data:
        if str(b.get("acf",{}).get("banner_store","")) == STORE_ID:
            found = b; break
    if found or page >= int(hdrs.get("X-WP-TotalPages",1)): break
    page += 1

if found:
    acf = found.get("acf",{})
    print(f"ID={found['id']}  date={found['date'][:10]}")
    for slot in range(1,5):
        img_id = acf.get(f"banner{slot}_img","")
        if img_id:
            media, _ = get(f"{WP_V2}/media/{img_id}")
            url = media.get("source_url","") if media else ""
            print(f"  slot{slot}: {url.split('/')[-1]}")
            print(f"    {url}")
        else:
            print(f"  slot{slot}: (empty)")
    print("\n=== ACF fields ===")
    print(json.dumps(acf, ensure_ascii=False, indent=2)[:2000])
else:
    print("banner not found")
