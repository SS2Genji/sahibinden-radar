"""
notifier.py: Telegram Bot Alert Dispatcher with Rich Markdown Formatting.
Uses Python standard library (urllib.request, json) with zero dependencies.
Includes terminal console fallback when Telegram credentials are not yet set.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class Notifier:
    """Formats and dispatches opportunity alerts to Telegram or console."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token) and bool(chat_id) and bot_token != "YOUR_TELEGRAM_BOT_TOKEN_HERE"

    def format_alert_message(
        self,
        item: Dict[str, Any],
        decision: Dict[str, Any]
    ) -> str:
        """Constructs an eye-catching Telegram Markdown alert."""
        alert_type = decision.get("alert_type", "NEW_OPPORTUNITY")
        title = item.get("title", "İsimsiz İlan")
        price = item.get("price", 0)
        url = item.get("url", "")
        location = item.get("location", "Belirtilmemiş")

        variant = decision.get("detected_variant")
        battery = decision.get("battery_health")
        conf = decision.get("confidence_score")
        flaws = decision.get("detected_flaws", [])
        is_clean = decision.get("is_clean", len(flaws) == 0)

        extra_info_lines = []
        if variant and variant != "Standard":
            extra_info_lines.append(f"📱 *Model:* `{variant}`")
        if battery:
            extra_info_lines.append(f"🔋 *Pil Sağlığı:* `%{battery}`")
        if flaws:
            extra_info_lines.append(f"🔍 *Durum:* ⚠️ Kusurlar: {', '.join(flaws)}")
        elif is_clean:
            extra_info_lines.append(f"🔍 *Durum:* 🛡️ Temiz")
        if conf is not None:
            extra_info_lines.append(f"🎯 *Güven Skoru:* `%{conf}`")
        extra_str = "\n".join(extra_info_lines)
        if extra_str:
            extra_str = "\n" + extra_str

        if alert_type == "PRICE_DROP":
            old_p = decision.get("old_price", 0)
            drop_amt = decision.get("drop_amount", 0)
            drop_pct = decision.get("drop_pct", 0.0)
            clean_med = decision.get("clean_median", 0)

            msg = (
                f"📉 *FİYAT DÜŞÜŞÜ YAKALANDI!*\n\n"
                f"📌 *İlan:* {title}"
                f"{extra_str}\n"
                f"💰 *Yeni Fiyat:* `{price:,} TL`\n"
                f"🏷 *Eski Fiyat:* ~{old_p:,} TL~ (\\-%{drop_pct} / \\-{drop_amt:,} TL)\n"
                f"📊 *Piyasa Değeri:* `{int(clean_med):,} TL`\n"
                f"📍 *Konum:* {location}\n\n"
                f"🔗 [İlan Sayfasına Git]({url})"
            )
        else:
            clean_med = decision.get("clean_median", 0)
            disc_pct = decision.get("discount_pct", 0.0)
            advantage = decision.get("price_advantage", 0)

            msg = (
                f"🚨 *ARBİTRAJ / KELEPİR FIRSAT!*\n\n"
                f"📌 *İlan:* {title}"
                f"{extra_str}\n"
                f"💰 *Fiyat:* `{price:,} TL`\n"
                f"📊 *Piyasa Değeri:* `{int(clean_med):,} TL`\n"
                f"🔥 *Fiyat Avantajı:* `%{disc_pct}` daha ucuz (\\~{advantage:,} TL kâr marjı)\n"
                f"📍 *Konum:* {location}\n\n"
                f"🔗 [İlan Sayfasına Git]({url})"
            )

        return msg

    def send_alert(
        self,
        item: Dict[str, Any],
        decision: Dict[str, Any]
    ) -> bool:
        """Dispatches alert to Telegram or logs to console."""
        message = self.format_alert_message(item, decision)

        if not self.enabled:
            # Console fallback
            print("\n" + "=" * 50)
            print("[RADAR NOTIFICATION TRIGGERED]")
            print(message)
            print("=" * 50 + "\n")
            return True

        # Send via Telegram Bot API
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }

        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[Notifier Error] Telegram send failed: {e}")
            print(message)
            return False
