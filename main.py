import urllib.request
import urllib.parse
import urllib.error
import json
import re
import os
import sys
import base64
import subprocess
import traceback

# 実ブラウザに寄せたUA。短いUAだとtokubai等のCDNで弾かれることがある
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
}

# Geminiのinline_dataは1リクエスト合計 ~20MB が上限。base64で約4/3に膨らむため
# 安全のためバイト換算で14MBを上限に枚数を絞る
MAX_INLINE_BYTES = 14 * 1024 * 1024


def _http_get(url, headers=None, timeout=30):
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    return urllib.request.urlopen(req, timeout=timeout)

# ==========================================
# 設定エリア
# ==========================================
# トクバイのサミット ミナノ分倍河原店URL（教えていただいたもの）
STORE_URL = "https://tokubai.co.jp/%E3%82%B5%E3%83%9F%E3%83%83%E3%83%88/7221" 

# Google AI StudioのAPIキー (Gemini API)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "ここにGeminiのAPIキーを入れてください")
# LINE Messaging API（LINE Developersコンソールから取得）
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# ==========================================

def fetch_flyer_images(url):
    """トクバイからチラシ画像をダウンロードし、ローカルパスと元URLのペアを返す"""
    print(f"📥 {url} からチラシ情報を取得中...")
    downloaded = []  # [(local_path, original_url), ...]
    try:
        html = _http_get(url).read().decode('utf-8')

        # leafletページのリンクを1つ取得してアクセス
        leaflet_matches = re.findall(r'href="(/[^/]+/\d+/leaflets/\d+)[^"]*"', html)
        if leaflet_matches:
            leaflet_url = "https://tokubai.co.jp" + leaflet_matches[0]
            print(f"📄 チラシ詳細ページにアクセス中: {leaflet_url}")
            html_leaf = _http_get(leaflet_url, headers={"Referer": url}).read().decode('utf-8')
        else:
            html_leaf = html

        # ページ内のJSONデータから high_resolution_image_url を全て抽出
        img_urls = re.findall(r'high_resolution_image_url(?:&quot;|")\s*:\s*(?:&quot;|")(https?://[^"\\]+)', html_leaf)

        # 重複を排除しつつ順序を保持
        seen = set()
        unique_urls = []
        for u in img_urls:
            u = u.replace('\\u0026', '&').replace('\\/', '/')
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        print(f"📋 {len(unique_urls)} 枚のチラシ画像が見つかりました。全て取得します。")

        img_headers = {"Referer": "https://tokubai.co.jp/"}
        for i, img_url in enumerate(unique_urls):
            save_path = f"today_flyer_{i}.jpg"
            print(f"🖼️ チラシ {i+1}/{len(unique_urls)}: {img_url[:80]}...")
            try:
                with _http_get(img_url, headers=img_headers) as resp, open(save_path, "wb") as f:
                    f.write(resp.read())
                downloaded.append((save_path, img_url))
            except Exception as e:
                print(f"  ⚠️ チラシ {i+1} ダウンロード失敗: {e}")

        print(f"✅ 合計 {len(downloaded)} 枚のチラシ画像を保存しました。")
        return downloaded
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        print(f"❌ 画像取得HTTPエラー: {e.code} {e.reason} body={body}")
        traceback.print_exc()
        return downloaded
    except Exception as e:
        print(f"❌ 画像取得エラー: {e}")
        traceback.print_exc()
        return downloaded

def analyze_with_gemini(image_paths):
    """複数のチラシ画像をGeminiに投げて、特売情報＋献立提案を抽出する"""
    print(f"🤖 Google Gemini API に {len(image_paths)} 枚の画像を同時に投げて特売情報を抽出中...")

    api_key = GEMINI_API_KEY.strip() if GEMINI_API_KEY else ""
    if api_key == "ここにGeminiのAPIキーを入れてください" or not api_key:
        raise RuntimeError("Gemini APIキーが設定されていません (GEMINI_API_KEY)")

    # inline_data の合計サイズが Gemini の上限を超えると 400 になる。
    # サイズ順ではなく元の順序を維持しつつ、合計が閾値を超えない枚数だけ送る
    selected = []
    total = 0
    for p in image_paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if total + size > MAX_INLINE_BYTES and selected:
            print(f"⚠️ サイズ上限({MAX_INLINE_BYTES//1024//1024}MB)に達したため "
                  f"{len(image_paths)}枚中{len(selected)}枚のみGeminiに送信します")
            break
        selected.append(p)
        total += size
    image_paths = selected
    if not image_paths:
        raise RuntimeError("Geminiに送信できる画像がありません")

    prompt = """あなたはスーパーのチラシを読み取る専門AIです。

【ステップ1】まず、全てのチラシ画像に掲載されている商品と価格を漏れなく全て読み取ってください。
小さい文字や端に書かれた商品も見逃さないでください。

【ステップ2】読み取った全商品の中から、以下の基準で「本当にお得な目玉商品」だけを厳選してください。
・通常価格より明らかに安いもの（半額、割引、特価の表示があるもの）
・数量限定やタイムセール品
・「お買い得」「おすすめ」等の目立つ表示があるもの
・各カテゴリ最大5品まで（多すぎると読みにくいため）

【ステップ3】厳選した商品だけを、以下のフォーマットで出力してください。
ステップ1・2の思考過程は出力しないでください。
不要な挨拶や説明も一切不要です。

【出力フォーマット】

🥩 お肉
・商品名 → ○○円（税込○○円）
...

🐟 魚・海鮮
・商品名 → ○○円（税込○○円）
...

🥬 野菜
・商品名 → ○○円（税込○○円）
...

🍎 果物
・商品名 → ○○円（税込○○円）
...

🍞 パン・たまご・乳製品
・商品名 → ○○円（税込○○円）
...

🍱 惣菜・お弁当
・商品名 → ○○円（税込○○円）
...

🍺 飲料・お酒
・商品名 → ○○円（税込○○円）
...

📦 その他
・商品名 → ○○円（税込○○円）
...

🍳 今日の献立提案（特売品を活用して2品）
①（メイン料理名）
　使う特売品：○○、○○
　ざっくり作り方を1行で

②（副菜・汁物）
　使う特売品：○○
　ざっくり作り方を1行で
"""

    # Geminiに渡すデータパーツ（プロンプト＋複数画像）
    parts = [{"text": prompt}]
    
    for img_path in image_paths:
        with open(img_path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64_image
            }
        })

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }

    data = {
        "contents": [{
            "parts": parts
        }]
    }

    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method="POST")

    try:
        response = urllib.request.urlopen(req, timeout=120)
        result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        print(f"❌ Gemini API HTTPエラー: {e.code} {e.reason}\n--- response body ---\n{body}\n---------------------")
        raise
    except Exception as e:
        print(f"❌ Gemini API エラー: {e}")
        traceback.print_exc()
        raise

    candidates = result.get('candidates') or []
    if not candidates:
        feedback = result.get('promptFeedback') or {}
        print(f"❌ Gemini候補なし: promptFeedback={json.dumps(feedback, ensure_ascii=False)}")
        print(f"--- full response ---\n{json.dumps(result, ensure_ascii=False)[:2000]}\n---------------------")
        raise RuntimeError(f"Gemini returned no candidates (finishReason={feedback.get('blockReason', 'unknown')})")

    cand = candidates[0]
    parts = (cand.get('content') or {}).get('parts') or []
    text_parts = [p.get('text', '') for p in parts if 'text' in p]
    if not text_parts:
        print(f"❌ Gemini候補にtext無し: {json.dumps(cand, ensure_ascii=False)[:1000]}")
        raise RuntimeError(f"Gemini candidate had no text (finishReason={cand.get('finishReason')})")

    return "".join(text_parts).strip()

def notify_line(message):
    """LINE Messaging APIで友だち全員に特売情報をブロードキャスト送信する"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("⚠️ LINE_CHANNEL_ACCESS_TOKEN が設定されていないため、LINE通知をスキップします。")
        return

    print("🟢 LINEに通知をブロードキャスト中...")
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    # テキストメッセージ（特売情報＋献立＋チラシリンク）
    text = f"🛒 サミット ミナノ分倍河原店\n📅 本日の特売情報\n\n{message}\n\n📎 チラシを見る👇\n{STORE_URL}"
    if len(text) > 5000:
        text = text[:4997] + "..."

    data = json.dumps({
        "messages": [{"type": "text", "text": text}]
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req)
        print("✅ LINE通知完了")
    except Exception as e:
        print(f"❌ LINE通知エラー: {e}")

def notify_mac(title, message):
    if sys.platform != "darwin":
        return
    escaped_msg = message.replace('"', '\\"').replace('\n', '\\n')
    script = f'display notification "{escaped_msg}" with title "{title}"'
    subprocess.run(["osascript", "-e", script])

if __name__ == "__main__":
    print("🚀 サミット特売通知ツールを開始します")

    # 1. チラシ画像取得（ローカルパスと元URLのペアで返る）
    flyer_data = fetch_flyer_images(STORE_URL)
    if not flyer_data:
        print("❌ チラシ画像を1枚も取得できませんでした。終了します。")
        sys.exit(1)

    local_paths = [d[0] for d in flyer_data]
    original_urls = [d[1] for d in flyer_data]

    # 2. Gemini でOCR＋献立提案
    try:
        deals = analyze_with_gemini(local_paths)
    except Exception as e:
        print(f"❌ Geminiでの解析に失敗したため処理を中断します: {e}")
        sys.exit(1)

    print("\n--- 抽出結果 ---")
    print(deals)
    print("----------------\n")

    # 3. 通知（テキスト＋チラシ画像＋リンク）
    notify_mac("サミット 今日の特売！", deals)
    notify_line(deals)

    print("🎉 完了しました！")
