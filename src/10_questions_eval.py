"""Step 10: how well do the RULES understand owner questions? (no AI, no cost)

Sends every message in data/eval/questions.csv through the real bot (bot/conversation.py with a memory store and a
fake WhatsApp, the AI question classifier OFF = QA_LLM=0) for a fixed fake shop with 6 suppliers, bills, payments
and one reminder. 'Today' is fixed (Wed 28 Oct 2026) so periods and reminder days never drift.

Each message is scored:
  correct  right intent, and every supplier / period / day the message gave was read right (a follow-up for
           something the message did NOT say is fine). Payments: the payment is proposed for the right supplier
           and amount. Off-topic: not answered.
  safe     not understood, but nothing wrong happened: the menu, or a follow-up list for something it did say.
  WRONG    the dangerous case: answered (or started) a DIFFERENT question, used the wrong supplier / period / day,
           or treated a payment as a question (or a question as a payment).

The first run is saved as questions_detail_before.csv and never overwritten: the headline score. Later runs print
before vs after.

Usage: python src/10_questions_eval.py      Outputs: outputs/eval/questions_table.csv, questions_detail.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import itertools
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import questions as Q  # noqa: E402
from bot.conversation import Bot  # noqa: E402
from bot.store import MemoryStore  # noqa: E402

TODAY = date(2026, 10, 28)  # a Wednesday
FROZEN_SHA256 = "8370bd65e9aad15484a1293e0c38e53f35aee5d51a06a5a4553d69f23cb5ccef"  # docs/05_data_log.md
OWNER = "910000000000"  # a fake number  # secret-scan: ignore
SUPPLIERS = ["Sharma Traders", "Sharma Medical", "Verma Traders", "Shiv Medical Distributors", "Balaji Enterprises",
             "Agarwal Kirana"]
BILLS = [  # (supplier, bill date, total)
    ("Sharma Traders", "2026-09-12", 18400), ("Sharma Traders", "2026-10-20", 9650),
    ("Sharma Medical", "2026-10-05", 4210), ("Verma Traders", "2026-09-28", 12000),
    ("Verma Traders", "2026-10-27", 7340), ("Shiv Medical Distributors", "2026-10-14", 4769),
    ("Shiv Medical Distributors", "2026-10-26", 2380), ("Balaji Enterprises", "2026-10-21", 15150),
    ("Balaji Enterprises", "2026-10-28", 3900), ("Agarwal Kirana", "2026-09-30", 6120),
    ("Agarwal Kirana", "2026-10-16", 2875), ("Verma Traders", "2026-10-02", 5500),
]
PAYMENTS = [  # (supplier, paid on, amount)
    ("Sharma Traders", "2026-09-25", 10000), ("Verma Traders", "2026-10-10", 8000),
    ("Balaji Enterprises", "2026-10-22", 5000), ("Agarwal Kirana", "2026-10-27", 3000),
    ("Shiv Medical Distributors", "2026-10-28", 2000), ("Sharma Traders", "2026-10-21", 4000),
]
OUTCOMES = ("correct", "safe", "WRONG")
_ids = itertools.count(1)


class FakeWA:
    def __init__(self):
        self.sent = []

    async def _out(self, kind, to, body, **more):
        self.sent.append({"kind": kind, "to": to, "body": body} | more)
        return f"wamid.eval{next(_ids)}"

    async def send_text(self, to, body):
        return await self._out("text", to, body)

    async def send_buttons(self, to, body, buttons, header=None, footer=None):
        return await self._out("buttons", to, body, buttons=buttons)

    async def send_list(self, to, body, button, rows):
        return await self._out("list", to, body, rows=rows)

    async def send_template(self, to, name, params, language="en"):
        return await self._out("template", to, name)

    async def download_media(self, media_id):
        raise RuntimeError("no media in the question eval")


def fake_shop(photo_dir: Path):
    """A fresh bot + store holding the fixed fake shop (consent given, name set: no onboarding questions)."""
    store = MemoryStore()
    shop = store.create_shop(OWNER, shop_code="EVAL01", status="active", shop_name="Gupta General Store")
    now = datetime.now(timezone.utc)
    store.update_shop(shop["id"], consent_at=now, onboarded_at=now)
    sup = {n: store.find_or_create_supplier(shop["id"], n) for n in SUPPLIERS}
    for name, day, total in BILLS:
        bill = store.create_bill(shop["id"], {"supplier_name": name, "bill_date": day, "total_amount": total,
                                              "invoice_number": None, "due_date": None, "unsure_fields": []},
                                 {"model": "typed"})
        store.confirm_bill(bill["id"], sup[name]["id"])
        # bills are counted by the day they reached the bot: pretend each came in on its bill date (noon IST)
        store.bills[bill["id"]]["created_at"] = datetime.fromisoformat(f"{day}T06:30:00+00:00")
    for name, day, amount in PAYMENTS:
        store.add_payment(shop["id"], sup[name]["id"], amount, "text")
        store.payments[-1]["paid_on"] = date.fromisoformat(day)
    store.add_reminder(shop["id"], sup["Verma Traders"]["id"], "Verma Traders ko payment", date(2026, 10, 30))
    bot = Bot(store, FakeWA(), extractor=None, pilot_open=False, image_dir=photo_dir, question_llm=None,
              today_fn=lambda: TODAY)
    return bot, store, shop


def load_questions(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_one(row: dict, photo_dir: Path) -> dict:
    """Send one message to a fresh copy of the fake shop; return what the bot did."""
    bot, store, shop = fake_shop(photo_dir)
    suppliers = store.list_suppliers(shop["id"])
    parsed = Q.parse_question(row["message"], suppliers, TODAY)  # the same call the bot makes first
    before = len(store.events)
    msg = {"from": OWNER, "id": f"wamid.in{next(_ids)}", "type": "text", "text": {"body": row["message"]}}
    asyncio.run(bot.handle(msg))
    events = store.events[before:]
    names = [e["event"] for e in events]
    state = store.get_shop(shop["id"])["state"] or {}
    asked = next((e["props"] for e in events if e["event"] == "question_asked"), None)
    # Payments wait as 'pending_confirm' rows with a card (31 Oct 2026; was a 'payment' state). A pending payment
    # with a matched supplier = proposed; with no supplier yet (a list to pick from) or a new supplier = not matched.
    pending = [p for p in store.payments if p["status"] == "pending_confirm"]
    if pending and pending[-1]["supplier_id"] and not pending[-1]["new_supplier"]:
        action, got = "payment", "payment"
        state = {"supplier": store.suppliers[pending[-1]["supplier_id"]]["name"], "amount": pending[-1]["amount"]}
    elif pending or "payment_unmatched" in names:
        action, got = "payment_no_supplier", "payment"
    elif {"kind_asked", "received_asked", "future_asked", "fix_asked"} & set(names):
        action, got = "typed_bill", "entry"
    elif "dues_asked" in names:
        action, got = "answered", "dues_all"
    elif "question_answered" in names:
        action, got = "answered", next(e["props"]["intent"] for e in events if e["event"] == "question_answered")
    elif asked and asked.get("intent"):
        action, got = "follow_up", asked["intent"]
    elif "bill_received" in names:
        action, got = "typed_bill", "typed_bill"
    else:
        action, got = "menu", None
    return {"parsed": parsed, "action": action, "got_intent": got, "state": state,
            "reply": (bot.wa.sent[-1]["body"] if bot.wa.sent else "")[:120]}


def _sup_name(parsed) -> str:
    if parsed.supplier:
        return parsed.supplier["name"]
    if parsed.candidates:
        return "ASK:" + "/".join(c["name"] for c in parsed.candidates)
    if parsed.supplier_text and not parsed.note:
        return "NOT_FOUND:" + parsed.supplier_text
    return ""


def judge(row: dict, r: dict) -> tuple[str, str]:
    """(outcome, why)."""
    exp, action, p = row["intent"], r["action"], r["parsed"]
    if exp == "off_topic":
        return ("correct", "not answered") if action == "menu" else ("WRONG", f"off-topic message -> {action}")
    if exp == "payment":
        if action == "payment":
            st = r["state"]
            if row["supplier"] in ("ASK", "UNKNOWN"):
                return "WRONG", f"payment proposed for {st.get('supplier')} (name was ambiguous / unknown)"
            if st.get("supplier") != row["supplier"]:
                return "WRONG", f"payment proposed for {st.get('supplier')}"
            if row["amount"] and int(st.get("amount") or 0) != int(row["amount"]):
                return "WRONG", f"payment amount {st.get('amount')}"
            return "correct", "payment proposed"
        if action == "payment_no_supplier":
            return ("correct", "asked which supplier") if row["supplier"] in ("ASK", "UNKNOWN") \
                else ("safe", "payment seen, supplier not matched")
        if action == "menu":
            return "safe", "menu"
        return "WRONG", f"payment treated as a question ({r['got_intent']}, {action})"
    # a question
    if action in ("menu", "payment_no_supplier"):
        return "safe", action
    if action in ("payment", "typed_bill"):
        return "WRONG", f"question treated as a {action}"
    if r["got_intent"] != exp:
        return "WRONG", f"{action} as {r['got_intent']}"
    problems, missing = [], []
    want = row["supplier"]
    got = _sup_name(p)
    if want == "ASK":
        if p.supplier:
            problems.append(f"picked {p.supplier['name']} of two look-alikes")
        elif not p.candidates:
            missing.append("supplier")
    elif want == "UNKNOWN":
        if p.supplier or p.candidates:
            problems.append(f"matched an unknown name to {got}")
        elif not got.startswith("NOT_FOUND"):
            missing.append("supplier")
    elif want:
        if p.supplier and p.supplier["name"] != want:
            problems.append(f"supplier {p.supplier['name']} instead of {want}")
        elif not p.supplier:
            missing.append("supplier")
    elif p.supplier and exp not in ("dues_all", "top_dues", "list_reminders"):
        problems.append(f"narrowed to {p.supplier['name']} (asked about all suppliers)")
    if row["period"]:
        key = row["period"]
        want_day = date.fromisoformat(key[4:]) if key.startswith("day:") else None
        if p.period is None:
            missing.append("period")
        elif want_day and (p.period.start, p.period.end) != (want_day, want_day):
            problems.append(f"period {p.period.start}..{p.period.end} instead of {want_day}")
        elif not want_day and (p.period.key != key and (p.period.start, p.period.end) !=
                               (Q.period(key, TODAY).start, Q.period(key, TODAY).end)):
            problems.append(f"period {p.period.key} instead of {key}")
    if row["remind_on"]:
        if p.remind_on is None:
            missing.append("day")
        elif p.remind_on.isoformat() != row["remind_on"]:
            problems.append(f"reminder on {p.remind_on} instead of {row['remind_on']}")
    if problems:
        return "WRONG", "; ".join(problems)
    if missing and action == "answered":
        return "WRONG", f"answered without the {', '.join(missing)} the owner gave"
    if missing:
        return "safe", f"asked for the {', '.join(missing)} the owner gave"
    return "correct", action


def table(detail: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for d in detail:
        groups[d["intent"]].append(d)
        groups["tricky" if d["tricky"] else "ordinary"].append(d)
        groups["ALL"].append(d)
    order = Q.INTENTS + ["payment", "off_topic", "ordinary", "tricky", "ALL"]
    rows = []
    for g in order:
        ds = groups.get(g, [])
        if ds:
            n = len(ds)
            rows.append({"group": g, "n": n} | {f"{o.lower()}_pct": round(sum(d["outcome"] == o for d in ds) / n, 4)
                                                 for o in OUTCOMES} | {"wrong_n": sum(d["outcome"] == "WRONG" for d in ds)})
    return rows


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    qpath = root / "data" / "eval" / "questions.csv"
    if not qpath.exists():
        print(f"Missing {qpath}")
        return 1
    sha = hashlib.sha256(qpath.read_bytes()).hexdigest()
    if sha != FROZEN_SHA256:
        print(f"WARNING: questions.csv changed since it was frozen (sha256 {sha[:12]}...): scores are not comparable")
    questions = load_questions(qpath)
    detail = []
    with tempfile.TemporaryDirectory() as tmp:
        for row in questions:
            r = run_one(row, Path(tmp))
            outcome, why = judge(row, r)
            p = r["parsed"]
            detail.append({"q_id": row["q_id"], "message": row["message"], "intent": row["intent"],
                           "tricky": row["tricky"], "outcome": outcome, "why": why, "action": r["action"],
                           "got_intent": r["got_intent"] or "", "got_supplier": _sup_name(p),
                           "got_period": f"{p.period.key}:{p.period.start}..{p.period.end}" if p.period else "",
                           "got_remind_on": p.remind_on.isoformat() if p.remind_on else "",
                           "want_supplier": row["supplier"], "want_period": row["period"],
                           "want_remind_on": row["remind_on"], "reply": r["reply"].replace("\n", " | ")})
    out = root / "outputs" / "eval"
    rows = table(detail)
    write_csv(out / "questions_detail.csv", detail)
    write_csv(out / "questions_table.csv", rows)
    before_path = out / "questions_detail_before.csv"
    first = not before_path.exists()
    if first:
        write_csv(before_path, detail)  # the headline: rules as they were before any fix from this eval
    with open(before_path, newline="", encoding="utf-8") as f:
        before = {r["q_id"]: r for r in csv.DictReader(f)}
    before_rows = {r["group"]: r for r in table(list(before.values()))}

    print(f"QUESTION EVAL (rules only, no AI; {len(detail)} messages; today = {TODAY}; questions.csv sha {sha[:12]})")
    head = "BEFORE fixes (headline)" if first else "before -> after"
    print(f"{'group':<17}{'n':>4}   {head}")
    for r in rows:
        b = before_rows.get(r["group"], r)
        cells = "  ".join(f"{o} {float(b[o.lower() + '_pct']):>4.0%}" + ("" if first else f" -> {r[o.lower() + '_pct']:>4.0%}")
                          for o in OUTCOMES)
        print(f"{r['group']:<17}{r['n']:>4}   {cells}")
    wrong = [d for d in detail if d["outcome"] == "WRONG"]
    print(f"\nWRONG ({len(wrong)}): answered a different question / wrong supplier, period or day")
    for d in wrong:
        print(f"  {d['q_id']} [{d['intent']}] \"{d['message']}\" -> {d['why']}")
    if not first:
        changed = [(q, before[q]["outcome"], d["outcome"]) for q, d in ((d["q_id"], d) for d in detail)
                   if q in before and before[q]["outcome"] != d["outcome"]]
        print(f"\nChanged since the first run ({len(changed)}): " + ", ".join(f"{q} {a}->{b}" for q, a, b in changed))
    safe = [d for d in detail if d["outcome"] == "safe"]
    print(f"\nSAFE but not understood ({len(safe)}): " + ", ".join(f"{d['q_id']} ({d['why']})" for d in safe))
    print(f"\nSaved: {out / 'questions_table.csv'}, questions_detail.csv" + (", questions_detail_before.csv" if first else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
