"""The pilot health report: one page per day, from the events table (no phone numbers; the only message text is
what an owner sends AS feedback, listed first, with long numbers masked).

  python -m bot.admin health          today and the last 7 days (IST), printed and saved to outputs/pilot/
  python -m bot.admin health 14       the last 14 days

What it answers, in order: are shops using it, do cards get saved, does the bot understand, are the guards
firing (and are they right), do owners trust it (STOP, delete), is it fast and cheap enough, what broke.
Every line is a count of events the bot already logs; nothing here is estimated except the AI cost line, which
uses the measured bill-reading cost (docs/13_cascade_spec.md, policy P5 without OCR) and says so.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from bot.feedback_flow import mask_numbers

IST = timezone(timedelta(hours=5, minutes=30))
BILL_COST_RS = 0.205   # measured: average AI cost of reading one bill photo with the live policy (P5 without OCR)
SYSTEM = {"reminder_sent", "summary_sent", "owner_reminder_sent", "photos_purged", "group_assigned", "eval_consent",
          "setup_check", "unknown_sender", "slow_reply", "reply_timed", "shop_data_deleted"}   # not something the owner did


def _day(e) -> date:
    return e["created_at"].astimezone(IST).date()


def _pct(a, b) -> str:
    return f"{round(100 * a / b)}%" if b else "-"


FREE_REPLIES_A_MONTH = 1000   # Meta, from 1 Oct 2026: service replies free up to about 1,000 a month per number (then
                              # Rs 0.115 each). Without a payment method, replies past that may stop being delivered.


def month_line(month_replies: int | None) -> str:
    """'- Replies this month: 412 of about 1,000 free (Meta counts per number per month)'."""
    if month_replies is None:
        return ""
    warn = (" **WARNING: near the free limit. Add a payment method, or replies may stop**"
            if month_replies >= 0.8 * FREE_REPLIES_A_MONTH else "")
    return (f"- Replies this month: {month_replies} of about {FREE_REPLIES_A_MONTH:,} free "
            f"(Meta counts per number per calendar month){warn}")


def build(shops: list[dict], events: list[dict], today: date, days: int = 7, month_replies: int | None = None) -> str:
    """Markdown. `events` = everything since the start of the window (store.events_since). `month_replies` =
    bot messages Meta accepted since the 1st of this month (store.replies_sent_since), for the free-limit line."""
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
         f"Window: {start:%d %b} to {today:%d %b} ({days} days, IST). Pairs read *today / {days} days*.", ""]
    L += feedback_lines(win, code, c)
    L += mistakes_lines(win, code)
    L += ["## Shops",
         f"- {len(shops)} shops: {status.get('active', 0)} active, {status.get('invited', 0)} invited, "
         f"{status.get('opted_out', 0)} stopped",
         f"- Used the bot: {len(active_today)} today, {len(active_week)} in {days} days",
         cohort_line(shops, active_week),
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
         photo_line([e["props"] or {} for e in win if e["event"] == "photo_timed"]),
         f"- Replies over 5 s: {len(slow)}" + (f" (slowest {max(slow) / 1000:.1f} s)" if slow else ""),
         f"- AI calls: {sum(ai.values())} ({', '.join(f'{k} {v}' for k, v in ai.most_common()) or 'none'}); "
         f"bill reading about Rs {(ai.get('bill', 0) * BILL_COST_RS):.0f} at the measured Rs {BILL_COST_RS} a bill",
         whatsapp_line(win, timed),
         *([month_line(month_replies)] if month_replies is not None else []),
         f"- Daily AI limit hit: {c(win, 'limit_hit')}", "",
         "## Broke",
         f"- Sends failed {c(win, 'send_failed')}; bill reading failed {c(win, 'extract_failed')}; voice failed "
         f"{c(win, 'voice_failed')}; PDFs refused {c(win, 'pdf_rejected')}",
         failures_line(win), ""]

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
        L += [""] + adoption_lines(win, code)
        quiet = sorted(code[str(s["id"])] for s in shops if s["status"] == "active" and str(s["id"]) not in active_week)
        if quiet:
            L += ["", f"Active but silent for {days} days: {', '.join(quiet)} (a call or a nudge?)"]
    return "\n".join(L) + "\n"


FIX_EVENTS = ("entry_edited", "entry_deleted", "entry_adjusted", "fix_open_card")
# Feature adoption (24 Nov): which ways in and which features each shop has used at least once in the window
FEATURES = (("Photo", lambda e: e["event"] == "bill_received" and (e["props"] or {}).get("source", "photo") == "photo"),
            ("PDF", lambda e: e["event"] == "bill_received"     # 24 Nov: sources are pdf_text / pdf_scan, not "pdf"
             and str((e["props"] or {}).get("source") or "").startswith("pdf")),
            ("Voice", lambda e: e["event"] == "voice_received"),
            ("Typed", lambda e: e["event"] == "entry_parsed" and (e["props"] or {}).get("source") == "text"),
            ("Question", lambda e: e["event"] in ("question_answered", "dues_asked")),
            ("Fix", lambda e: e["event"] in FIX_EVENTS),
            ("Reminder", lambda e: e["event"] == "reminder_set"),
            ("Old dues", lambda e: e["event"] == "old_dues_added" or (e["event"] == "bill_confirmed"
                                                                     and (e["props"] or {}).get("kind") == "udhaar")),
            ("Return", lambda e: e["event"] == "refund_logged" or (e["event"] == "bill_confirmed"
                                                                  and (e["props"] or {}).get("kind") == "return")),
            ("Feedback", lambda e: e["event"] == "feedback_given"),
            ("Tour done", lambda e: e["event"] == "tour_done" and (e["props"] or {}).get("how") != "skipped"))


def cohort_line(shops: list[dict], active_week) -> str:
    """24 Nov: real pilot shops (S01...) and friendly users (F01... family and friends, T01... test phones) are counted
    apart: only S shops go into the case study's numbers and the experiment."""
    real = [s for s in shops if re.fullmatch(r"S\d+", s.get("shop_code") or "")]
    friendly = [s for s in shops if s not in real]
    used = lambda group: sum(1 for s in group if str(s["id"]) in active_week)   # noqa: E731
    return (f"- **Real pilot shops (S codes): {len(real)}, {used(real)} used it in the window**; "
            f"friendly users (F family / T test): {len(friendly)}, {used(friendly)} used it. Case study counts S only.")


def adoption_lines(win: list[dict], code: dict) -> list[str]:
    """A table: one row per shop, a tick for each feature it used; the last row counts shops per feature."""
    used = defaultdict(set)
    for e in win:
        if e["shop_id"]:
            for name, test in FEATURES:
                if test(e):
                    used[str(e["shop_id"])].add(name)
    if not used:
        return []
    names = [n for n, _ in FEATURES]
    out = ["## Feature adoption (used at least once in the window)", "",
           "| Shop | " + " | ".join(names) + " |", "| --- " * (len(names) + 1) + "|"]
    for sid in sorted(used, key=lambda x: code.get(x, "?")):
        out.append(f"| {code.get(sid, '?')} | " + " | ".join("✓" if n in used[sid] else "" for n in names) + " |")
    out.append("| **Shops** | " + " | ".join(str(sum(1 for u in used.values() if n in u)) for n in names) + " |")
    return out + tour_lines(win, code)


def tour_lines(win: list[dict], code: dict) -> list[str]:
    """24 Nov: how the first-day tour went: finished, skipped (at which step), or still open (nudge those shops
    yourself on WhatsApp: the bot never chases them)."""
    started = {str(e["shop_id"]) for e in win if e["event"] == "tour_started"}
    if not started:
        return []
    ended = {str(e["shop_id"]): e["props"] or {} for e in win if e["event"] == "tour_done"}
    done = [s for s in started if s in ended and ended[s].get("how") != "skipped"]
    skipped = [s for s in started if s in ended and ended[s].get("how") == "skipped"]
    open_ = sorted((s for s in started if s not in ended), key=lambda x: code.get(x, "?"))
    reminded = sum(1 for e in win if e["event"] == "tour_reminded")
    guide = sum(1 for e in win if e["event"] == "guide_viewed")
    line = f"- First-day tour: {len(started)} started, {len(done)} finished, {len(skipped)} skipped"
    if skipped:
        line += " (at step " + ", ".join(str(ended[s].get("step", "?")) for s in sorted(skipped)) + ")"
    out = ["", line + f"; shown the step again {reminded} times; 'sikhao' guide opened {guide} times"]
    if open_:
        out.append("- Tour not finished, nudge them yourself: " + ", ".join(code.get(s, "?") for s in open_))
    return out


def mistakes_lines(win: list[dict], code: dict, limit: int = 15) -> list[str]:
    """Second section (24 Nov): what owners sent that the bot did not understand, in their words (numbers masked),
    newest first, and the other signs of a stuck owner. Each repeated miss becomes a rule (a bug fix)."""
    missed = sorted((e for e in win if e["event"] == "off_topic" and (e["props"] or {}).get("text")),
                    key=lambda e: e["created_at"], reverse=True)
    other = Counter((e["props"] or {}).get("type") for e in win
                    if e["event"] == "off_topic" and not (e["props"] or {}).get("text"))
    cancels = sum(1 for e in win if e["event"] in ("bill_cancelled", "payment_cancelled"))
    fixes = sum(1 for e in win if e["event"] in FIX_EVENTS)
    hints = sum(1 for e in win if e["event"] == "typed_yes" and (e["props"] or {}).get("result") != "confirm")
    out = ["## What owners sent that the bot did not get (fix these)"]
    out += [f"- {e['created_at'].astimezone(IST):%d %b %H:%M} · {code.get(str(e['shop_id']), '?')} · "
            f"\"{mask_numbers(e['props']['text'])}\"" for e in missed[:limit]]
    if len(missed) > limit:
        out.append(f"- ... and {len(missed) - limit} more: RUN_mistakes.bat lists them all")
    if not missed:
        out.append("- Nothing missed in typed messages")
    if other:
        out.append("- Other things not understood: " + ", ".join(f"{k} {v}" for k, v in other.most_common()))
    out += [f"- Cards cancelled: {cancels}; fixes after saving: {fixes}; 'ok' that needed a hint: {hints}", ""]
    return out


def feedback_lines(win: list[dict], code: dict, c) -> list[str]:
    """First section: what owners said (bot/feedback_flow.py), newest first, then ratings and offers."""
    said = sorted((e for e in win if e["event"] == "feedback_given"), key=lambda e: e["created_at"], reverse=True)
    rated = Counter((e["props"] or {}).get("rating") for e in win if e["event"] == "rating_given")
    started = Counter((e["props"] or {}).get("source") for e in win if e["event"] == "feedback_started")
    out = ["## Feedback from owners (read this first)"]
    for e in said[:20]:
        p = e["props"] or {}
        words = mask_numbers(re.sub(r"\s+", " ", str(p.get("text") or ""))).strip()[:300]
        out.append(f"- {e['created_at'].astimezone(IST):%d %b %H:%M} · {code.get(str(e['shop_id']), '?')} · "
                   f"{p.get('via', '?')} · {p.get('source', '?')}: \"{words}\"")
    if len(said) > 20:
        out.append(f"- ... and {len(said) - 20} more in this window")
    if not said:
        out.append("- No feedback in this window")
    out += [f"- Ratings: {rated.get('good', 0)} achha, {rated.get('ok', 0)} theek-thaak, {rated.get('bad', 0)} dikkat "
            f"(asked {c(win, 'rating_asked')})",
            f"- The bot offered feedback {c(win, 'feedback_offered')} times (after cancels "
            f"{c(win, 'feedback_offered', why='cancels')}, frustrated words {c(win, 'feedback_offered', why='words')}); "
            f"taken {started.get('button', 0)}, declined {c(win, 'feedback_declined')}", ""]
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


TEMPLATE_RS = 0.115   # Meta's India utility template price, Oct 2026 list (docs/10_templates.md)


def whatsapp_line(win: list[dict], timed: list[dict]) -> str:
    """What Meta may bill: templates (always paid) and the bot's own messages (free up to about 1,000 a month)."""
    sent = sum(1 for e in win if e["event"] in ("reminder_sent", "summary_sent", "owner_reminder_sent"))
    capped = sum(1 for e in win if e["event"] == "template_cap_hit")
    msgs = sum(int(t.get("wa_n") or 0) for t in timed)
    return (f"- WhatsApp: {sent} templates sent (about Rs {sent * TEMPLATE_RS:.0f}; monthly cap in bot/jobs.py"
            f"{', cap reached ' + str(capped) + ' times' if capped else ''}); {msgs} bot messages "
            f"(free up to about 1,000 a month, then about Rs {TEMPLATE_RS} each)")


def photo_line(photos: list[dict]) -> str:
    """'- Photos: 5, median download 1.2 s, AI reading 6.8 s; 2 re-read by Sonnet (median 11.0 s) ...'"""
    if not photos:
        return "- Photos: none timed yet (photo_timed events start 20 Nov)"
    esc = [p for p in photos if p.get("escalated")]
    plain = [p for p in photos if not p.get("escalated")]
    down = [p["download_ms"] for p in photos if p.get("download_ms") is not None]
    line = (f"- Photos: {len(photos)}, median download {(_median(down) or 0) / 1000:.1f} s, AI reading "
            f"{_median([p.get('read_ms', 0) for p in photos]) / 1000:.1f} s; Haiku only "
            f"{len(plain)} (median {(_median([p.get('read_ms', 0) for p in plain]) or 0) / 1000:.1f} s), "
            f"re-read by Sonnet {len(esc)} (median {(_median([p.get('read_ms', 0) for p in esc]) or 0) / 1000:.1f} s); "
            f"median size {_median([p.get('kb', 0) for p in photos]):.0f} KB")
    # 21 Nov: split the AI reading into the AI service's own time and ours (client, image prep, checks)
    split = [p for p in photos if p.get("api_ms") is not None and p.get("read_ms") is not None]
    if split:
        api = _median([p["api_ms"] for p in split])
        ours = _median([max(0, p["read_ms"] - p["api_ms"]) for p in split])
        think = [p["think_tok"] for p in split if p.get("think_tok") is not None]
        efforts = Counter(p.get("effort") or "default" for p in split)
        line += (f"\n- AI reading split ({len(split)} photos): AI service {api / 1000:.1f} s, our side {ours / 1000:.1f} s"
                 + (f"; thinking {_median(think):.0f} tokens a photo" if think else "")
                 + f"; effort {', '.join(f'{k} {v}' for k, v in efforts.most_common())}")
    return line


# Meta error codes seen on WhatsApp sends, in plain words (https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes)
WA_ERRORS = {
    131047: "24-hour window closed (only a template can be sent)",
    131026: "could not be delivered (not on WhatsApp, old app, or blocked)",
    131042: "payment problem on the WhatsApp account",
    131049: "held back by Meta (too many business messages to this person)",
    131056: "too many messages to one person too fast",
    131030: "number not allowed (test mode)",
    130429: "rate limit",
    132000: "template variables do not match",
    132001: "template missing or not approved in this language",
    132015: "template paused (low quality)",
    100: "invalid request (e.g. a text too long)",
    190: "token expired or revoked",
}


def failures_line(win: list[dict]) -> str:
    """'- WhatsApp refused 1 (131047: 24-hour window closed x1); accepted but not delivered 0'."""
    def label(code):
        if code is None:
            return "no code (logged before 21 Nov, or Meta did not answer)"
        return f"{code}: {WA_ERRORS.get(code, 'see Render logs')}"

    def part(name):
        codes = Counter((e["props"] or {}).get("code") for e in win if e["event"] == name)
        n = sum(codes.values())
        return f"{n} ({'; '.join(f'{label(c)} x{k}' for c, k in codes.most_common())})" if n else "0"
    return f"- WhatsApp refused {part('send_failed')}; accepted but not delivered {part('delivery_failed')}"


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
    first = [t.get("ms", 0) for t in timed if t.get("first")]
    cold = (f"\n- First reply after a restart or a sleep: median {_median(first) / 1000:.1f} s over {len(first)} "
            f"(slow = the host slept: check the 5-minute pinger)") if first else ""
    return (f"- Reply time: median {_median([t.get('ms', 0) for t in timed]) / 1000:.1f} s over {len(timed)} replies "
            f"({kinds}); median parts: {parts}" + cold)


def window_start(today: date, days: int) -> datetime:
    return datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=IST)
