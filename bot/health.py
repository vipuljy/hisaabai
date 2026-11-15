"""The pilot health report: one page per day, from the events table (no message text, no phone numbers).

  python -m bot.admin health          today and the last 7 days (IST), printed and saved to outputs/pilot/
  python -m bot.admin health 14       the last 14 days

What it answers, in order: are shops using it, do cards get saved, does the bot understand, are the guards
firing (and are they right), do owners trust it (STOP, delete), is it fast and cheap enough, what broke.
Every line is a count of events the bot already logs; nothing here is estimated except the AI cost line, which
uses the measured bill-reading cost (docs/13_cascade_spec.md, policy P5 without OCR) and says so.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BILL_COST_RS = 0.205   # measured: average AI cost of reading one bill photo with the live policy (P5 without OCR)
SYSTEM = {"reminder_sent", "summary_sent", "owner_reminder_sent", "photos_purged", "group_assigned", "eval_consent",
          "setup_check", "unknown_sender", "slow_reply", "reply_timed", "shop_data_deleted"}   # not something the owner did


def _day(e) -> date:
    return e["created_at"].astimezone(IST).date()


def _pct(a, b) -> str:
    return f"{round(100 * a / b)}%" if b else "-"


def build(shops: list[dict], events: list[dict], today: date, days: int = 7) -> str:
    """Markdown. `events` = everything since the start of the window (store.events_since)."""
    start = today - timedelta(days=days - 1)
    win = [e for e in events if start <= _day(e) <= today]
    now = [e for e in win if _day(e) == today]
    code = {str(s["id"]): s["shop_code"] for s in shops}

    def c(evs, name, **props):
        return sum(1 for e in evs if e["event"] == name and all((e["props"] or {}).get(k) == v for k, v in props.items()))

    def both(name, **props):
        return f"{c(now, name, **props)} / {c(win, name, **props)}"

    owner = [e for e in win if e["shop_id"] and e["event"] not in SYSTEM]
    active_today = {e["shop_id"] for e in owner if _day(e) == today}
    active_week = {e["shop_id"] for e in owner}
    status = Counter(s["status"] for s in shops)

    saved = ("bill_confirmed", "bill_corrected", "payment_logged", "refund_logged")
    made = c(win, "bill_received") + c(win, "payment_proposed")
    kept = sum(c(win, n) for n in saved)
    dropped = c(win, "bill_cancelled") + c(win, "payment_cancelled")
    warned = [e for e in win if e["event"] == "card_warned"]
    kinds = Counter(k for e in warned for k in (e["props"] or {}).get("kinds", []))
    dup_kept = sum(1 for e in win if e["event"] in saved and (e["props"] or {}).get("dup_ok"))
    dup_dropped = sum(1 for e in win if e["event"] in ("bill_cancelled", "payment_cancelled")
                      and (e["props"] or {}).get("duplicate"))
    sources = Counter((e["props"] or {}).get("source") or "photo" for e in win if e["event"] == "bill_received")
    pay_sources = Counter((e["props"] or {}).get("source") or "?" for e in win if e["event"] == "payment_proposed")
    by = Counter((e["props"] or {}).get("understood_by") for e in win if e["event"] == "entry_parsed")
    q_unclear = sum(1 for e in win if e["event"] == "question_asked" and (e["props"] or {}).get("unclear"))
    ai = Counter((e["props"] or {}).get("kind") for e in win if e["event"] == "ai_call")
    slow = [e["props"].get("ms", 0) for e in win if e["event"] == "slow_reply"]
    timed = [e["props"] or {} for e in win if e["event"] == "reply_timed"]

    L = [f"# Pilot health, {today:%d %b %Y}", "",
         f"Window: {start:%d %b} to {today:%d %b} ({days} days, IST). Pairs read *today / {days} days*.", "",
         "## Shops",
         f"- {len(shops)} shops: {status.get('active', 0)} active, {status.get('invited', 0)} invited, "
         f"{status.get('opted_out', 0)} stopped",
         f"- Used the bot: {len(active_today)} today, {len(active_week)} in {days} days",
         f"- New sign-ups (consent): {both('opt_in')}; declined: {both('consent_declined')}; "
         f"example viewed: {both('example_viewed')}", "",
         "## Entries (the core loop)",
         f"- Cards made: {made} ({', '.join(f'{k} {v}' for k, v in sources.most_common())} bills; "
         f"{', '.join(f'{k} {v}' for k, v in pay_sources.most_common())} payments)",
         f"- Saved: {kept} ({_pct(kept, made)}); cancelled: {dropped} ({_pct(dropped, made)}); the rest still open",
         f"- Fixed after saving: {c(win, 'entry_edited')} edits, {c(win, 'entry_deleted')} removals, "
         f"{c(win, 'entry_adjusted')} dated corrections (a fix soon after a save = a card that was wrong)", "",
         "## Understanding",
         f"- Entries read by rules / by the AI fallback: {by.get('rules', 0)} / {by.get('llm', 0)}",
         f"- Asked to choose (bill or payment, what came in, direction): {c(win, 'kind_asked') + c(win, 'received_asked')}",
         f"- Questions: {c(win, 'question_asked') + c(win, 'dues_asked')} asked, {q_unclear} needed a list",
         f"- Not understood: {c(win, 'off_topic')} off-topic, {c(win, 'voice_not_understood')} voice notes, "
         f"{c(win, 'entry_part_skipped')} skipped pieces",
         f"- Hindi script messages: {c(win, 'hindi_script')}; spelling slips fixed: {c(win, 'typo_fixed')} "
         "(check a few: a wrong fix is worse than none)", "",
         "## Guards (are they firing, and are owners agreeing?)",
         f"- Cards with a warning: {len(warned)} (duplicate {kinds.get('duplicate', 0)}, unusual amount "
         f"{kinds.get('unusual', 0)}, above the baaki {kinds.get('advance', 0)}, date ahead {kinds.get('date_ahead', 0)})",
         f"- Duplicates: {dup_dropped} dropped, {dup_kept} kept as new (many kept = the 7-day window is too wide)",
         f"- Dues from before the bot: {c(win, 'old_dues_asked')} asked, {c(win, 'old_dues_added')} added, "
         f"{c(win, 'old_dues_none')} none",
         f"- Money back: {c(win, 'refund_why_asked')} asked why; saved {c(win, 'refund_logged', why='goods')} goods back, "
         f"{c(win, 'refund_logged', why='over')} overpaid",
         f"- Photos: {c(win, 'not_a_bill')} not a bill; proof asked {c(win, 'proof_asked')}, attached "
         f"{c(win, 'photo_used_as_proof')}, declined {c(win, 'proof_declined')}", "",
         "## Trust",
         f"- STOP {both('opt_out')}; came back {both('opt_back_in')}; delete asked {both('delete_asked')}, "
         f"done {both('shop_data_deleted')}, kept {both('delete_declined')}", "",
         "## Speed and cost",
         speed_line(timed),
         f"- Replies over 5 s: {len(slow)}" + (f" (slowest {max(slow) / 1000:.1f} s)" if slow else ""),
         f"- AI calls: {sum(ai.values())} ({', '.join(f'{k} {v}' for k, v in ai.most_common()) or 'none'}); "
         f"bill reading about Rs {(ai.get('bill', 0) * BILL_COST_RS):.0f} at the measured Rs {BILL_COST_RS} a bill",
         f"- Daily AI limit hit: {c(win, 'limit_hit')}", "",
         "## Broke",
         f"- Sends failed {c(win, 'send_failed')}; bill reading failed {c(win, 'extract_failed')}; voice failed "
         f"{c(win, 'voice_failed')}; PDFs refused {c(win, 'pdf_rejected')}", ""]

    per = defaultdict(Counter)
    last = {}
    for e in owner:
        sid = e["shop_id"]
        per[sid][e["event"]] += 1
        last[sid] = max(last.get(sid, e["created_at"]), e["created_at"])
    rows = sorted(per, key=lambda sid: code.get(sid, "?"))
    if rows:
        L += ["## Per shop (codes only)", "",
              "| Shop | Last active | Saved | Cancelled | Questions | Warnings |", "| --- | --- | --- | --- | --- | --- |"]
        for sid in rows:
            n = per[sid]
            L.append(f"| {code.get(sid, '?')} | {last[sid].astimezone(IST):%d %b %H:%M} | {sum(n[x] for x in saved)} | "
                     f"{n['bill_cancelled'] + n['payment_cancelled']} | {n['question_asked'] + n['dues_asked']} | "
                     f"{n['card_warned']} |")
        quiet = sorted(code[str(s["id"])] for s in shops if s["status"] == "active" and str(s["id"]) not in active_week)
        if quiet:
            L += ["", f"Active but silent for {days} days: {', '.join(quiet)} (a call or a nudge?)"]
    return "\n".join(L) + "\n"


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def speed_line(timed: list[dict]) -> str:
    """'- Reply time: median 1.4 s over 52 replies (text 1.2 s, image 4.8 s); a typical reply: database 0.5 s ...'"""
    if not timed:
        return "- Reply time: no timed replies yet"
    by = defaultdict(list)
    for t in timed:
        by[t.get("type", "?")].append(t.get("ms", 0))
    kinds = ", ".join(f"{k} {_median(v) / 1000:.1f} s" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])))
    parts = ", ".join(f"{label} {_median([t.get(key, 0) for t in timed]) / 1000:.1f} s"
                      for label, key in (("database", "db_ms"), ("WhatsApp", "wa_ms"), ("other (AI, voice)", "other_ms")))
    return (f"- Reply time: median {_median([t.get('ms', 0) for t in timed]) / 1000:.1f} s over {len(timed)} replies "
            f"({kinds}); median parts: {parts}")


def window_start(today: date, days: int) -> datetime:
    return datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=IST)
