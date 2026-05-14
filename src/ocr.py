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
