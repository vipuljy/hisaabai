"""Links the WhatsApp Business Account (WABA_ID) to this app, so Meta forwards REAL incoming messages
to the webhook. Without this link, the dashboard "Test" button works but messages from phones never arrive.

  python -m bot.link_whatsapp        (or double-click RUN_link_whatsapp.bat)

Safe to run again. Never prints the token.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main(out=print, client: httpx.Client | None = None) -> int:
    load_dotenv(ROOT / ".env")
    token = os.getenv("WHATSAPP_TOKEN", "").strip()
    waba = os.getenv("WABA_ID", "").strip()
    version = os.getenv("GRAPH_VERSION", "v23.0").strip() or "v23.0"
    if not token or not waba:
        out("FAIL  WHATSAPP_TOKEN or WABA_ID is empty in .env")
        return 1
    client = client or httpx.Client(timeout=20)
    url = f"https://graph.facebook.com/{version}/{waba}/subscribed_apps"
    auth = {"Authorization": f"Bearer {token}"}

    before = client.get(url, headers=auth)
    if before.status_code != 200:
        out(f"FAIL  Could not read linked apps ({before.status_code}): {before.text[:200]}")
        return 1
    names = [a.get("whatsapp_business_api_data", {}).get("name", "?") for a in before.json().get("data", [])]
    out(f"Linked apps before: {', '.join(names) or 'none'}")

    resp = client.post(url, headers=auth)
    if resp.status_code != 200 or not resp.json().get("success"):
        out(f"FAIL  Could not link the app ({resp.status_code}): {resp.text[:200]}")
        return 1

    after = client.get(url, headers=auth)
    names = [a.get("whatsapp_business_api_data", {}).get("name", "?") for a in after.json().get("data", [])]
    out(f"OK    Linked apps now: {', '.join(names)}")
    out("Now send 'hi' from your phone to the test number and watch the bot window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
