"""Telegram Bot API channel: send messages (with optional buttons) and long-poll for replies.

Long polling is an outbound HTTPS request that Telegram holds open until a message arrives, so nothing on the
Mac is exposed to the internet: no webhook, no tunnel, no open port.
"""
import httpx

API = "https://api.telegram.org"


class Telegram:
    def __init__(self, token: str) -> None:
        self._base = f"{API}/bot{token}"  # the token is part of the URL; never log it

    def _call(self, method: str, params: dict | None = None, http_timeout: float = 20) -> dict | list:
        r = httpx.post(f"{self._base}/{method}", json=params or {}, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', r.status_code)}")
        return data["result"]

    def me(self) -> dict:
        return self._call("getMe")

    def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
        params = {"chat_id": chat_id, "text": text[:4000], "link_preview_options": {"is_disabled": True}}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}
        self._call("sendMessage", params)

    def answer_button(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})

    def updates(self, offset: int, wait: int = 50) -> list[dict]:
        """Waits up to `wait` seconds for new messages or button presses."""
        return self._call("getUpdates", {"offset": offset, "timeout": wait,
                                         "allowed_updates": ["message", "callback_query"]}, http_timeout=wait + 15)
