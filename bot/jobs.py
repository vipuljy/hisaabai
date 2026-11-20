"""Daily jobs. Run once a day, around 10 am IST:

  python -m bot.jobs            real run (sends WhatsApp templates, deletes old photos)
  python -m bot.jobs --dry-run  shows what would happen, changes nothing

On a host, the same jobs run when the scheduler calls POST /jobs/daily (see bot/app.py, docs/11_setup_and_hosting.md).

1. Photo clean-up (all shops): bill photos (and proof photos sent for voice / typed bills) older than 30 days are
   deleted, unless the owner agreed to keep them for the reading test (shops.eval_consent). The numbers read from
   them stay. This is the privacy promise.

2. Nudges (the experiment):
What it sends, only to active shops in the TREATMENT group (control gets nothing: that is the A/B test):
  - every day: a reminder for each confirmed bill due in 2 days (once per bill)
  - Sundays:   the weekly summary (bills, payments, total due, biggest due)
Shops not yet assigned a group (friendly testers before the pilot) are treated as treatment.

3. Owner reminders (BOTH groups: the owner asked for them, so they are a feature, not an experiment nudge):
   each reminder the owner set ("Sharma ko Friday yaad dilana") is sent once on its day (or the next run if a day
   was missed), with the supplier's current baaki. Logged as owner_reminder_sent (NOT reminder_sent, which is the
   treatment-only nudge the experiment counts), with the shop's experiment group.

These go outside the 24-hour reply window, so WhatsApp requires pre-approved templates:
see docs/10_templates.md for the exact text to submit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

from bot import messages as M
from bot.photos import SB_PREFIX, SupabasePhotos

log = logging.getLogger("hisaabai")

IST = timezone(timedelta(hours=5, minutes=30))
REMINDER_DAYS_AHEAD = 2
TEMPLATE_REMINDER = "due_reminder"
TEMPLATE_SUMMARY = "weekly_summary"
TEMPLATE_OWNER_REMINDER = "owner_reminder"
PHOTO_KEEP_DAYS = 30
# Spend guard (20 Nov): Meta charges each template (about Rs 0.115). At most this many go out per calendar month
# (IST), whatever happens; 300 = about Rs 35. Change with TEMPLATE_MONTHLY_CAP; 0 sends none.
TEMPLATE_MONTHLY_CAP = 300
SENT_EVENTS = ("reminder_sent", "summary_sent", "owner_reminder_sent")


class TemplateBudget:
    """How many more templates may go out this month; logs 'template_cap_hit' once when the cap stops a send."""

    def __init__(self, store, today: date, cap: int | None = None):
        self.store = store
        self.cap = int(os.getenv("TEMPLATE_MONTHLY_CAP", TEMPLATE_MONTHLY_CAP)) if cap is None else cap
        month_start = datetime(today.year, today.month, 1, tzinfo=IST)
        self.sent = sum(store.count_events(e, month_start) for e in SENT_EVENTS)
        self.blocked = 0

    def take(self) -> bool:
        if self.sent >= self.cap:
            if self.blocked == 0:
                self.store.log_event(None, "template_cap_hit", {"cap": self.cap, "sent": self.sent})
            self.blocked += 1
            return False
        self.sent += 1
        return True


def purge_old_photos(store, photos, now: datetime | None = None, keep_days: int = PHOTO_KEEP_DAYS,
                     dry_run: bool = False) -> dict:
    """Deletes photos older than keep_days (except eval-consented shops). Returns counts."""
    before = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    counts = {"photos_deleted": 0, "photos_unreachable": 0}
    remote = isinstance(photos, SupabasePhotos)
    for row in store.old_photos(before):
        if row["image_path"].startswith(SB_PREFIX) != remote:
            counts["photos_unreachable"] += 1  # e.g. a laptop file while running on the host: delete it there
            continue
        if not dry_run:
            try:
                photos.delete(row["image_path"])
            except Exception:
                log.exception("photo delete failed")
                continue
            store.clear_photo(row["bill_id"], row.get("kind", "bill"))  # a bill photo or a proof photo
        counts["photos_deleted"] += 1
    if counts["photos_deleted"] and not dry_run:
        store.log_event(None, "photos_purged", {"count": counts["photos_deleted"]})
    return counts


async def run_all(store, wa, photos, today: date, dry_run: bool = False, language: str = "en") -> dict:
    counts = purge_old_photos(store, photos, dry_run=dry_run)
    counts |= await run_daily(store, wa, today, dry_run=dry_run, language=language)
    return counts


def gets_nudges(shop) -> bool:
    return shop.get("experiment_group") in ("treatment", None)


async def run_daily(store, wa, today: date, dry_run: bool = False, language: str = "en") -> dict:
    counts = {"reminders": 0, "summaries": 0, "skipped_control": 0, "owner_reminders": 0}
    budget = TemplateBudget(store, today)
    due_day = today + timedelta(days=REMINDER_DAYS_AHEAD)
    for shop in store.list_shops(status="active"):
        if shop.get("consent_at") is None:
            continue
        counts["owner_reminders"] += await send_owner_reminders(store, wa, shop, today, dry_run, language, budget)
        if not gets_nudges(shop):
            counts["skipped_control"] += 1
            continue

        for bill in store.bills_due_on(shop["id"], due_day):
            if store.event_exists(shop["id"], "reminder_sent", "bill_id", bill["id"]):
                continue
            due_now = next((r["due"] for r in store.dues(shop["id"]) if r["supplier"] == bill["supplier"]), 0)
            if due_now <= 0:
                continue  # already paid off: no nagging
            params = [bill["supplier"], M.inr(bill["total_amount"]), M.nice_date(bill["due_date"]), M.inr(due_now)]
            if not dry_run and not budget.take():
                continue
            if not dry_run:
                mid = await wa.send_template(shop["wa_id"], TEMPLATE_REMINDER, params, language)
                store.record_message(shop["id"], "out", mid, "template", f"{TEMPLATE_REMINDER}: {params}")
                store.log_event(shop["id"], "reminder_sent" if mid else "send_failed",
                                {"bill_id": bill["id"], "template": TEMPLATE_REMINDER})
            counts["reminders"] += 1

        if today.weekday() == 6:  # Sunday
            s = store.week_stats(shop["id"], today)
            if s["bills"] == 0 and s["total_due"] <= 0:
                continue  # nothing to say
            week = f"{M.nice_date(today - timedelta(days=6))} - {M.nice_date(today)}"
            params = [week, str(s["bills"]), M.inr(s["billed"]), M.inr(s["paid"]), M.inr(s["total_due"]),
                      f"{s['top_supplier']} ({M.inr(s['top_due'])})" if s["top_supplier"] else "koi nahi"]
            if not dry_run and not budget.take():
                continue
            if not dry_run:
                mid = await wa.send_template(shop["wa_id"], TEMPLATE_SUMMARY, params, language)
                store.record_message(shop["id"], "out", mid, "template", f"{TEMPLATE_SUMMARY}: {params}")
                store.log_event(shop["id"], "summary_sent" if mid else "send_failed",
                                {"week_end": today.isoformat(), "template": TEMPLATE_SUMMARY})
            counts["summaries"] += 1
    return counts


async def send_owner_reminders(store, wa, shop, today: date, dry_run: bool = False, language: str = "en",
                               budget: TemplateBudget | None = None) -> int:
    """Reminders the owner set, due today or earlier and not yet sent. Each is sent once (sent_at)."""
    n = 0
    for r in store.reminders_due(shop["id"], today):
        dues = store.dues(shop["id"])
        if r.get("supplier_id"):
            due = next((d["due"] for d in dues if str(d["supplier_id"]) == str(r["supplier_id"])), 0)
        else:
            due = sum(d["due"] for d in dues)
        params = [r["text"], M.inr(due)]
        if not dry_run and budget is not None and not budget.take():
            continue   # stays pending: sent on a later day, or next month
        if not dry_run:
            mid = await wa.send_template(shop["wa_id"], TEMPLATE_OWNER_REMINDER, params, language)
            store.record_message(shop["id"], "out", mid, "template", f"{TEMPLATE_OWNER_REMINDER}: {params}")
            if mid:
                store.mark_reminder_sent(r["id"])  # failed sends stay pending and are retried on the next run
            store.log_event(shop["id"], "owner_reminder_sent" if mid else "send_failed",
                            {"reminder_id": r["id"], "template": TEMPLATE_OWNER_REMINDER,
                             "group": shop.get("experiment_group") or "unassigned",
                             "days_late": (today - r["remind_on"]).days})
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", help="pretend today is YYYY-MM-DD (for testing)")
    args = ap.parse_args(argv)
    load_dotenv()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set in .env (jobs need the real database).")
        return 1
    from bot.photos import photos_from_env
    from bot.store import PgStore
    from bot.whatsapp import WhatsAppClient
    today = date.fromisoformat(args.date) if args.date else datetime.now(IST).date()
    wa = WhatsAppClient(os.getenv("WHATSAPP_TOKEN", ""), os.getenv("PHONE_NUMBER_ID", ""),
                        os.getenv("GRAPH_VERSION", "v23.0"))
    counts = asyncio.run(run_all(PgStore(url), wa, photos_from_env(), today, dry_run=args.dry_run,
                                 language=os.getenv("TEMPLATE_LANGUAGE", "en")))
    print(f"{today} ({'dry run' if args.dry_run else 'sent'}): {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
