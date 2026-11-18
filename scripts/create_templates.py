"""Submit the 3 WhatsApp templates (bot/wa_templates.py) to Meta for approval.

    python scripts/create_templates.py            show what will be submitted, ask, then submit
    python scripts/create_templates.py --show     only show the messages (nothing is sent to Meta)
    python scripts/create_templates.py --yes      submit without asking (used by nobody yet)

Reads WHATSAPP_TOKEN and WABA_ID from .env and never prints them. A template already approved or waiting is
left alone; a rejected one is resubmitted with the current text. Safe to run again.
Then RUN_check_setup.bat shows each template's status (approval: minutes to a day).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import wa_templates as W  # noqa: E402
from bot.redact import redact  # noqa: E402


def _error(resp: httpx.Response, env: dict) -> str:
    try:
        err = resp.json().get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or ""
        title = err.get("error_user_title") or ""
        text = f"{title}: {msg}" if title else msg
    except ValueError:
        text = resp.text
    return redact(str(text)[:300], env)


def submit(env: dict, http: httpx.Client, language: str, out=print) -> int:
    token, waba = (env.get("WHATSAPP_TOKEN") or "").strip(), (env.get("WABA_ID") or "").strip()
    if not token or not waba:
        out("FAIL  WHATSAPP_TOKEN or WABA_ID is empty in .env")
        return 1
    graph = f"https://graph.facebook.com/{(env.get('GRAPH_VERSION') or 'v23.0').strip()}"
    auth = {"Authorization": f"Bearer {token}"}

    resp = http.get(f"{graph}/{waba}/message_templates", headers=auth,
                    params={"fields": "id,name,status,language", "limit": 200})
    if resp.status_code != 200:
        out(f"FAIL  could not list templates. Meta said: {_error(resp, env)}")
        return 1
    found = {t.get("name"): t for t in resp.json().get("data", [])}

    failed = 0
    for name in W.SPECS:
        have = found.get(name)
        if have and have.get("status") != "REJECTED":
            out(f"SKIP  {name}: already in Meta ({have.get('status', '?')}, {have.get('language', '?')})")
            continue
        if have:   # rejected: edit it in place with the current text (a rejected name cannot be created again)
            resp = http.post(f"{graph}/{have['id']}", headers=auth,
                             json={"category": W.CATEGORY, "components": W.components(name)})
        else:
            resp = http.post(f"{graph}/{waba}/message_templates", headers=auth, json=W.payload(name, language))
        if resp.status_code == 200:
            status = resp.json().get("status") or ("PENDING" if have else "submitted")
            out(f"OK    {name}: {'resubmitted' if have else 'submitted'} ({status})")
        else:
            failed += 1
            out(f"FAIL  {name}: Meta said: {_error(resp, env)}")
    out("")
    out("Done. Paste these lines to Claude. Tomorrow RUN_check_setup.bat shows approved / rejected."
        if not failed else "Some failed: paste these lines to Claude (they contain no token).")
    return 1 if failed else 0


def main() -> int:
    from dotenv import dotenv_values

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="only show the messages")
    ap.add_argument("--yes", action="store_true", help="submit without asking")
    ap.add_argument("--language", default=None, help="template language code (default TEMPLATE_LANGUAGE or en)")
    args = ap.parse_args()

    for name in W.SPECS:
        print(f"----- {name} (as an owner sees it, with sample values) -----")
        print(W.preview(name))
        print()
    if args.show:
        return 0

    env_file = ROOT / ".env"
    env = {k: (v or "") for k, v in dotenv_values(env_file).items()} if env_file.exists() else {}
    language = args.language or (env.get("TEMPLATE_LANGUAGE") or os.getenv("TEMPLATE_LANGUAGE") or "en").strip()
    if not args.yes:
        answer = input(f"Submit these 3 to Meta now (category Utility, language {language})? Type Y and Enter: ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing sent.")
            return 0
    with httpx.Client(timeout=30) as http:
        return submit(env, http, language)


if __name__ == "__main__":
    sys.exit(main())
