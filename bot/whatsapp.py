"""Talks to Meta's WhatsApp Cloud API: send text, send buttons, download a photo."""

from __future__ import annotations

import asyncio
import contextvars
import logging

import httpx

log = logging.getLogger("hisaabai")

# Meta's error for the last failed send in THIS task (each incoming message is its own task, so shops never mix).
# The bot logs it with the send_failed event; the health report prints the code and what it means (21 Nov).
_LAST_ERROR: contextvars.ContextVar = contextvars.ContextVar("wa_last_error", default=None)


def last_error() -> dict | None:
    """{'code': Meta's error code or None, 'status': HTTP status} for the last failed send here, else None."""
    return _LAST_ERROR.get()


def _error_of(resp) -> dict:
    try:
        code = ((resp.json() or {}).get("error") or {}).get("code")
    except Exception:  # noqa: BLE001  not JSON
        code = None
    return {"code": code if isinstance(code, int) else None, "status": resp.status_code}


class WhatsAppClient:
    def __init__(self, token: str, phone_number_id: str, version: str = "v23.0"):
        self.token = token
        self.base = f"https://graph.facebook.com/{version}"
        self.phone_number_id = phone_number_id
        self._client: httpx.AsyncClient | None = None
        self._client_loop = None

    def _http(self) -> httpx.AsyncClient:
        """One kept-open connection to Meta, instead of a new one (TLS handshake included) for every reply. A
        connection belongs to one event loop, so a new loop (the daily job) gets its own client."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop or self._client.is_closed:
            # keep the connection open 2 minutes between messages (httpx closes it after 5 idle seconds by default,
            # and a new one costs 0.4-0.8 s: measured 16 Nov)
            self._client = httpx.AsyncClient(timeout=20, limits=httpx.Limits(keepalive_expiry=120))
            self._client_loop = loop
        return self._client

    @property
    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def _send(self, payload: dict) -> str | None:
        """Returns Meta's message id, or None if sending failed (the failure is logged)."""
        url = f"{self.base}/{self.phone_number_id}/messages"
        resp = await self._http().post(url, json={"messaging_product": "whatsapp", **payload}, headers=self._auth)
        if resp.status_code >= 400:
            log.error("WhatsApp send failed (%s): %s", resp.status_code, resp.text[:500])
            _LAST_ERROR.set(_error_of(resp))
            return None
        _LAST_ERROR.set(None)
        return (resp.json().get("messages") or [{}])[0].get("id")

    async def typing(self, message_id: str) -> None:
        """Blue ticks and "typing..." on the owner's phone at once, while the reply is being made. WhatsApp hides
        it when the reply arrives (or after 25 s). Never raises: it is only a courtesy."""
        try:
            resp = await self._http().post(f"{self.base}/{self.phone_number_id}/messages", headers=self._auth, json={
                "messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                "typing_indicator": {"type": "text"}})
            if resp.status_code >= 400:
                log.warning("typing indicator refused (%s): %s", resp.status_code, resp.text[:200])
        except Exception as err:  # noqa: BLE001
            log.warning("typing indicator failed: %s", type(err).__name__)

    async def send_text(self, to: str, body: str) -> str | None:
        return await self._send({"to": to, "type": "text", "text": {"body": body}})

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]], header: str | None = None,
                           footer: str | None = None) -> str | None:
        """Up to 3 reply buttons: (id, title). Titles over 20 characters are cut by WhatsApp's rule.
        Optional header (bold line on top, <= 60 chars) and footer (small grey line, <= 60 chars)."""
        if not 1 <= len(buttons) <= 3:
            raise ValueError("WhatsApp allows 1 to 3 reply buttons")
        interactive = {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                                   for bid, title in buttons]},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}
        return await self._send({"to": to, "type": "interactive", "interactive": interactive})

    async def send_list(self, to: str, body: str, button: str, rows: list[tuple[str, str, str]],
                        section: str = "Chuniye") -> str | None:
        """Interactive LIST: a button that opens up to 10 rows (id, title <= 24 chars, description <= 72)."""
        if not 1 <= len(rows) <= 10:
            raise ValueError("WhatsApp allows 1 to 10 list rows")
        return await self._send({
            "to": to, "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {"button": button[:20], "sections": [{"title": section[:24], "rows": [
                    {"id": rid[:200], "title": title[:24], **({"description": desc[:72]} if desc else {})}
                    for rid, title, desc in rows]}]},
            },
        })

    async def send_template(self, to: str, name: str, params: list[str], language: str = "en") -> str | None:
        """Pre-approved template: the only way to message an owner who has not written in 24 hours."""
        return await self._send({
            "to": to, "type": "template",
            "template": {"name": name, "language": {"code": language},
                         "components": [{"type": "body",
                                         "parameters": [{"type": "text", "text": str(p)} for p in params]}]},
        })

    async def download_media(self, media_id: str) -> bytes:
        """Two steps: ask Meta for a short-lived URL, then fetch the file with the same token."""
        client = self._http()
        meta = await client.get(f"{self.base}/{media_id}", headers=self._auth, timeout=30)
        meta.raise_for_status()
        file = await client.get(meta.json()["url"], headers=self._auth, timeout=30)
        file.raise_for_status()
        return file.content
