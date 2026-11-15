"""Pilot admin, run from the project root (needs DATABASE_URL in .env).

  python -m bot.admin add-shop S01 kirana 9876543210 "Gupta General Store"
                                                       register an invited shop (shop name optional)
  python -m bot.admin set-name S01 "Gupta General Store"   name printed on the shop's own bills
  python -m bot.admin eval-consent S01 yes             owner agreed photos may be kept for the reading test
                                                       (no = deleted after 30 days, the default)
  python -m bot.admin list-shops                       codes, segments, status, group (no phone numbers)
  python -m bot.admin assign-groups                    random treatment / control split, balanced by segment
  python -m bot.admin delete-shop S01                  delete a shop, ALL its data and photos (privacy promise)
  python -m bot.admin health [days]                    the pilot health report (default: today + 7 days), also
                                                       saved to outputs/pilot/health_<date>.md
"""

from __future__ import annotations

import os
import re
import sys

from dotenv import load_dotenv

from bot.experiment import assign_groups

SEGMENTS = ("kirana", "medical", "hardware", "other")


def main(argv=None, store=None, out=print, photos=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if photos is None:
        from bot.photos import photos_from_env
        load_dotenv()
        photos = photos_from_env()
    if store is None:
        load_dotenv()
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            out("DATABASE_URL is not set in .env")
            return 1
        from bot.store import PgStore
        store = PgStore(url)
    if not argv:
        out(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]

    if cmd == "add-shop" and len(args) in (3, 4):
        code, segment, number = args[0].upper(), args[1].lower(), re.sub(r"\D", "", args[2])
        name = args[3].strip()[:60] if len(args) == 4 else None
        if len(number) == 10:
            number = "91" + number
        if not re.fullmatch(r"91\d{10}", number):
            out("Phone number must be 10 digits, or 91 + 10 digits")
            return 1
        if segment not in SEGMENTS:
            out("Segment must be one of: " + ", ".join(SEGMENTS))
            return 1
        if any(s["shop_code"] == code for s in store.list_shops()):
            out(f"{code} already exists")
            return 1
        store.create_shop(number, shop_code=code, segment=segment, status="invited", shop_name=name)
        out(f"Added {code} ({segment}), number ending {number[-4:]}. Ask the owner to send 'hi'.")
        return 0

    if cmd in ("set-name", "eval-consent") and len(args) == 2:
        code = args[0].upper()
        shop = next((s for s in store.list_shops() if s["shop_code"] == code), None)
        if shop is None:
            out(f"No shop {code}.")
            return 1
        if cmd == "set-name":
            store.update_shop(shop["id"], shop_name=args[1].strip()[:60])
            out(f"{code} name set.")
            return 0
        answer = args[1].lower()
        if answer not in ("yes", "no"):
            out("Use: eval-consent S01 yes  (or no)")
            return 1
        store.update_shop(shop["id"], eval_consent=answer == "yes")
        store.log_event(shop["id"], "eval_consent", {"consent": answer == "yes"})
        out(f"{code}: photos {'kept for the reading test' if answer == 'yes' else 'deleted after 30 days'}.")
        return 0

    if cmd == "list-shops":
        for s in store.list_shops():
            onboarded = s["onboarded_at"].strftime("%d %b") if s.get("onboarded_at") else "not yet"
            out(f"{s['shop_code']:<5} {s.get('segment') or '-':<9} {s['status']:<10} "
                f"{s.get('experiment_group') or '-':<10} {onboarded}")
        return 0

    if cmd == "assign-groups":
        todo = [s for s in store.list_shops() if s["status"] != "opted_out" and not s.get("experiment_group")]
        if not todo:
            out("Every shop already has a group.")
            return 0
        groups = assign_groups(todo)
        for s in todo:
            g = groups[str(s["id"])]
            store.update_shop(s["id"], experiment_group=g)
            store.log_event(s["id"], "group_assigned", {"group": g})
            out(f"{s['shop_code']:<5} {s.get('segment') or '-':<9} {g}")
        t = sum(g == "treatment" for g in groups.values())
        out(f"\n{t} treatment, {len(groups) - t} control. Groups never change after this.")
        return 0

    if cmd == "delete-shop" and len(args) == 1:
        code = args[0].upper()
        if store.delete_shop(code):
            try:
                photos.delete_shop(code)
                out(f"Deleted {code}, all its data and its photos.")
            except Exception as err:
                out(f"Deleted {code} and all its data, but its photos could not be deleted ({type(err).__name__}). "
                    "Run the same command again later.")
        else:
            out(f"No shop {code}.")
        return 0

    if cmd == "health" and len(args) <= 1 and (not args or args[0].isdigit()):
        from datetime import datetime
        from pathlib import Path

        from bot import health
        days = max(1, min(int(args[0]) if args else 7, 60))
        today = datetime.now(health.IST).date()
        report = health.build(store.list_shops(), store.events_since(health.window_start(today, days)), today, days)
        out(report)
        folder = Path("outputs") / "pilot"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"health_{today:%Y-%m-%d}.md").write_text(report, encoding="utf-8")
        out(f"Saved: outputs/pilot/health_{today:%Y-%m-%d}.md")
        return 0

    out(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
