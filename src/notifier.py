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
