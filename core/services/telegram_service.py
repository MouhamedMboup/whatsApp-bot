"""
Service for Telegram Bot API messaging.

We use Telegram chat_id as the routing identifier and store users as:
  tg:<chat_id>
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Dict, Any, List

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class TelegramService:
    API_BASE_URL = "https://api.telegram.org"

    @staticmethod
    def _get_token() -> str:
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN not configured")
        return token

    @staticmethod
    def _api_url(method: str) -> str:
        return f"{TelegramService.API_BASE_URL}/bot{TelegramService._get_token()}/{method}"

    @staticmethod
    def parse_update(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns: (tg_identifier, message_text)
        tg_identifier format: "tg:<chat_id>"
        """
        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict):
            return None, None

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")

        if chat_id is None or not isinstance(text, str):
            return None, None

        return f"tg:{chat_id}", text

    @staticmethod
    def format_menu_message(title: Optional[str], body: str, options: Optional[List[Tuple[str, str]]] = None, footer: Optional[str] = None) -> str:
        parts: List[str] = []
        if title:
            parts.append(str(title).strip())
        if body:
            parts.append(str(body).strip())

        if options:
            parts.append("")
            for num, text in options:
                parts.append(f"{num}. {text}")

        if footer:
            parts.append("")
            parts.append(str(footer).strip())

        # Telegram is fine with \n; keep it simple.
        return "\n".join([p for p in parts if p is not None])

    @staticmethod
    def send_message(tg_identifier: str, text: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        tg_identifier: "tg:<chat_id>"
        """
        if not tg_identifier.startswith("tg:"):
            return False, "Invalid telegram identifier", None

        chat_id = tg_identifier.split(":", 1)[1]
        url = TelegramService._api_url("sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("ok") is True:
                logger.info("Telegram message sent | chat_id=%s | message_id=%s", chat_id, (data.get("result") or {}).get("message_id"))
                return True, None, data
            logger.error("Telegram send failed | status=%s | response=%s", resp.status_code, data)
            return False, str(data), data
        except Exception as e:
            logger.error("Telegram send exception: %s", str(e), exc_info=True)
            return False, str(e), None

