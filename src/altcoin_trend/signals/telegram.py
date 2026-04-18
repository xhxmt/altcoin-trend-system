from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class TelegramResult:
    ok: bool
    error: str = ""


@dataclass
class TelegramClient:
    bot_token: str
    chat_id: str
    timeout_seconds: float = 10.0

    def send_message(self, text: str) -> TelegramResult:
        if not self.bot_token or not self.chat_id:
            return TelegramResult(ok=False, error="missing Telegram bot token or chat id")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}

        try:
            response = httpx.post(url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            return TelegramResult(ok=False, error=str(exc))
        except ValueError as exc:
            return TelegramResult(ok=False, error=str(exc))

        if isinstance(data, dict) and data.get("ok"):
            return TelegramResult(ok=True, error="")

        description = "telegram send failed"
        if isinstance(data, dict):
            description = str(data.get("description") or description)
        return TelegramResult(ok=False, error=description)
