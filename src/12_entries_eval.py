"""Step 12: how well does the TEXT ENGINE understand what owners want to record? (rules only, no AI, no cost)

Sends every message in data/eval/entries.csv through the real bot (bot/conversation.py + bot/entry_flow.py, memory
store, fake WhatsApp, no AI) for a fixed fake shop: 7 suppliers, bills and payments, some of them saved in the last
7 days (so fixes like "8000 nahi 8500" have something to fix). 'Today' is fixed (Wed 28 Oct 2026).

The file was written and frozen BEFORE the engine was built (docs/05_data_log.md has its sha256). Every message has
the expected result, one or more parts separated by ';':
  bill|<supplier>|<amount>|<credit days>|<bill date>     payment|<supplier>|<amount>||<date>
  received|<supplier>|<amount>    future|<supplier>|<amount>    correction|<supplier>|<new amount>
  undo|<supplier>|<amount>        undo_none (nothing to fix)    question    other (nothing to record)
  <supplier>: a supplier of the fake shop; new:<name> = a supplier the shop does not have; ?<name> = 2+ suppliers
  fit, so the bot must ask.

Each message is scored:
  correct  everything the message said was read right: a card (or the right question) with the right type, supplier,
           amount, credit days and date; nothing extra.
  partial  nothing wrong is shown, but the owner has one more tap or a gap to fill: the supplier list for a name the
           bot could have matched, credit days missed, an undo card / list instead of the fix card.
  safe     nothing recorded and nothing wrong shown: the menu, "not found", "Bill aaya / Payment diya?" for something
           that is not an entry.
  WRONG    the dangerous case: a card with the wrong type, supplier, amount or date; a card for a message that records
           nothing; money received / still to pay offered as a payment card; one of several entries silently lost;
           a fix pointed at the wrong entry.
Cards still need the owner's tap, so even WRONG saves nothing by itself, but a wrong card can be tapped by mistake.

The first run is saved as entries_detail_before.csv and never overwritten: the headline score. Later runs print
before vs after.

Usage: python src/12_entries_eval.py      Outputs: outputs/eval/entries_table.csv, entries_detail.csv
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.conversation import Bot  # noqa: E402
from bot.extract import norm_supplier, same_supplier  # noqa: E402
from bot.store import MemoryStore  # noqa: E402

TODAY = date(2026, 10, 28)  # a Wednesday
IST = timezone(timedelta(hours=5, minutes=30))
SETS = {  # name -> (file, sha256 when frozen; docs/05_data_log.md)
    "main": ("entries.csv", "528c757f8751e2ddee82db7cf5b5e34ac128713709c0b193731664513ab29d25"),
    # written by a separate Claude agent that never saw the bot's code, after the engine was built (31 Oct 2026)
    # a second blind set, by a new agent, written AFTER the engine was tuned on 'blind': the fair after-score
    "blind2": ("entries_blind2.csv", "7dc4948f5dff035a0085f76fcad55e9d85deaa43fae146960ed0974ff59a1374"),
    "blind": ("entries_blind.csv", "9d0b91be5b89c1d548dca8e0fbb80ff3ee22e8d8359356eee6aca2f866dd2cc3"),
}
OWNER = "910000000001"  # a fake number  # secret-scan: ignore
SUPPLIERS = ["Sharma Traders", "Sharma Medical", "Verma Traders", "Shiv Medical Distributors", "Balaji Enterprises",
             "Agarwal Kirana", "Gupta Agency"]
BILLS = [  # (supplier, day it was saved, total) — older than 7 days unless said
    ("Sharma Traders", "2026-09-12", 18400), ("Sharma Traders", "2026-10-20", 9650), ("Sharma Medical", "2026-10-05", 4210),
    ("Verma Traders", "2026-09-28", 12000), ("Gupta Agency", "2026-10-10", 6000), ("Agarwal Kirana", "2026-10-16", 2875),
    ("Balaji Enterprises", "2026-10-22", 15150),          # in the last 7 days
    ("Shiv Medical Distributors", "2026-10-26", 2380),    # in the last 7 days
]
PAYMENTS = [  # (supplier, day, amount, hour IST)
    ("Sharma Traders", "2026-09-25", 10000, 12), ("Sharma Traders", "2026-10-21", 4000, 12),
    ("Agarwal Kirana", "2026-10-27", 3000, 10), ("Verma Traders", "2026-10-27", 8000, 18),  # the latest entry
]
OUTCOMES = ("correct", "partial", "safe", "WRONG")
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
        raise RuntimeError("no media in the entries eval")


def _at(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=IST).astimezone(timezone.utc)


def fake_shop(photo_dir: Path):
    store = MemoryStore()
    shop = store.create_shop(OWNER, shop_code="EVAL02", status="active", shop_name="Gupta General Store")
    now = datetime.now(timezone.utc)
    store.update_shop(shop["id"], consent_at=now, onboarded_at=now)
    sup = {n: store.find_or_create_supplier(shop["id"], n) for n in SUPPLIERS}
    for name, day, total in BILLS:
        bill = store.create_bill(shop["id"], {"supplier_name": name, "bill_date": day, "total_amount": total,
                                              "invoice_number": None, "due_date": None, "unsure_fields": []},
                                 {"model": "typed", "source": "typed"})
        store.confirm_bill(bill["id"], sup[name]["id"])
        store.bills[bill["id"]]["created_at"] = store.bills[bill["id"]]["confirmed_at"] = _at(day)
    for name, day, amount, hour in PAYMENTS:
        store.add_payment(shop["id"], sup[name]["id"], amount, "text")
        store.payments[-1].update(paid_on=date.fromisoformat(day), created_at=_at(day, hour),
                                  confirmed_at=_at(day, hour))
    bot = Bot(store, FakeWA(), extractor=None, pilot_open=False, image_dir=photo_dir, question_llm=None,
              today_fn=lambda: TODAY)
    return bot, store, shop


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- what the bot did
def _name(store, sid):
    return store.suppliers[sid]["name"] if sid in store.suppliers else None


def _choice_payload(button_id: str) -> tuple[int, str]:
    """'kind_bill:tok|5000|<sid>|Sharma|text' -> (5000, sid or name)."""
    bits = button_id.partition(":")[2].split("|")
    return int(bits[1]), bits[2] or bits[3]


def run_one(row: dict, photo_dir: Path) -> dict:
    bot, store, shop = fake_shop(photo_dir)
    seeded_bills, seeded_pays = set(store.bills), {p["id"] for p in store.payments}
    before_ev, before_sent = len(store.events), len(bot.wa.sent)
    msg = {"from": OWNER, "id": f"wamid.in{next(_ids)}", "type": "text", "text": {"body": row["message"]}}
    asyncio.run(bot.handle(msg))
    return collect(bot, store, shop, seeded_bills, seeded_pays, before_ev, before_sent)


def collect(bot, store, shop, seeded_bills, seeded_pays, before_ev, before_sent) -> dict:
    """What the owner sees after one message, in the order it was sent: cards (bills / payments), supplier lists,
    the kind / received / future questions, fix cards, 'which entry' lists, 'not found', and whether a question was
    answered. Also used by step 11."""
    events = store.events[before_ev:]
    names = [e["event"] for e in events]
    sent = bot.wa.sent[before_sent:]
    got, seen = [], set()
    lists = [m for m in sent if m["kind"] == "list"]
    bills = {b["id"]: b for b in store.bills.values() if b["id"] not in seeded_bills}
    pays = {p["id"]: p for p in store.payments if p["id"] not in seeded_pays}

    def entry(eid):
        if eid in seen:
            return
        seen.add(eid)
        if eid in bills:
            got.append(_entry_seen("bill", bills[eid], store, lists, bills[eid]["total_amount"]))
        elif eid in pays:
            got.append(_entry_seen("payment", pays[eid], store, lists, pays[eid]["amount"]))

    for m in sent:
        ids = [b for b, _ in m.get("buttons") or []] + [r[0] for r in m.get("rows") or []]
        for bid in ids:
            kind, _, rest = bid.partition(":")
            if kind in ("ok", "pok", "old_yes"):   # a card (old_yes: the 'dues from before?' ask before a card)
                entry(rest.split(":")[0])
            elif kind in ("rfw_g", "rcash_got"):      # money back: 'why?' / 'cash or cut from the baaki?' before the card
                amount, who = _choice_payload(bid)
                got.append({"intent": "refund", "card": True, "amount": amount, "supplier": _name(store, who) or who})
            elif kind == "rmaan" and bid == ids[0]:   # a partial return: 'how much did the supplier accept?' (16 Nov)
                amount, who = _choice_payload(bid)
                intent = bid.split("|")[5] if len(bid.split("|")) > 5 else "return"
                got.append({"intent": intent, "card": True, "amount": amount, "supplier": _name(store, who) or who})
            elif kind in ("sp", "spn", "spt"):
                entry(rest.split(":")[1])
            elif kind in ("kind_bill", "rcv_yes", "rcv_refund", "fut_rem"):
                amount, who = _choice_payload(bid)
                label = {"kind_bill": "kind", "rcv_yes": "received", "rcv_refund": "received", "fut_rem": "future"}[kind]
                ids = [b for b, _ in m.get("buttons") or []]
                ask = "dir" if any(b.startswith(("rcv_pay:", "rcv_yes:")) for b in ids) else "what"
                got.append({"intent": label, "amount": amount, "supplier": _name(store, who) or who, "ask": ask})
            elif kind in ("fix_ok", "ued"):
                bits = rest.split(":")
                e = store.get_entry(shop["id"], {"b": "bill", "p": "payment"}[bits[0]], bits[1])
                got.append({"intent": "correction" if kind == "fix_ok" else "undo", "supplier": e["supplier"],
                            "amount": int(bits[3]) if kind == "fix_ok" else int(e["amount"]),
                            "entry_amount": int(e["amount"])})
        if any(b.startswith(("adj:", "adjc:")) for b in ids):   # an entry older than 7 days: a dated correction
            got.append({"intent": "adjustment"})
        if m["kind"] == "list" and any(r[0].startswith("fsel:") for r in m["rows"]):
            got.append({"intent": "fix_list", "rows": [r[1] + " " + r[2] for r in m["rows"]]})
    for eid in list(bills) + list(pays):  # a pending entry the owner was not shown (should not happen)
        entry(eid)
    if "fix_not_found" in names:
        got.append({"intent": "fix_not_found"})
    question = "dues_asked" in names or any(e["event"] in ("question_asked", "question_answered") and
                                            (e["props"].get("intent")) for e in events)
    return {"got": got, "question": question, "events": names,
            "reply": " || ".join(m["body"].replace("\n", " ")[:90] for m in sent)[:400]}


def _entry_seen(kind, row, store, lists, amount) -> dict:
    entry_kind = row.get("kind") or kind   # bill / udhaar / return, payment / refund
    out = {"intent": entry_kind if entry_kind in ("udhaar", "return", "refund") else kind, "card": True,
           "amount": int(amount), "credit_days": row.get("credit_days"),
           "date": row.get("bill_date") if kind == "bill" else row.get("paid_on")}
    if row.get("supplier_id") and not row.get("new_supplier"):
        out["supplier"] = _name(store, row["supplier_id"])
    elif row.get("new_supplier"):
        out["supplier"] = "new:" + (row.get("supplier_name_raw") or "")
    else:
        rows = next((m["rows"] for m in lists if any(r[0].endswith(row["id"]) or f":{row['id']}:" in r[0] + ":"
                                                     for r in m["rows"])), [])
        out["supplier"] = "?" + "/".join(r[1] for r in rows if r[0].startswith("sp:"))
        out["picker_new"] = any(r[0].startswith("spn:") for r in rows)
    return out


# ---------------------------------------------------------------- scoring
def parse_expected(text: str) -> list[dict]:
    out = []
    for part in text.split(";"):
        bits = part.split("|") + [""] * 5
        out.append({"intent": bits[0], "supplier": bits[1], "amount": int(bits[2]) if bits[2] else None,
                    "credit_days": "any" if bits[3] == "?" else int(bits[3]) if bits[3] else None,
                    "date": date.fromisoformat(bits[4]) if bits[4] else None})
    return out


def _supplier_verdict(want: str, got: str) -> str:
    """correct / partial / WRONG for one supplier."""
    if want.startswith("?"):  # 2+ suppliers fit: must ask
        stem = norm_supplier(want[1:])
        if got.startswith("?") and sum(1 for n in got[1:].split("/") if n and stem in norm_supplier(n)) >= 2:
            return "correct"
        return "partial" if got.startswith("?") else "WRONG"
    if want.startswith("new:"):
        if got.startswith("new:") and same_supplier(got[4:], want[4:]):
            return "correct"
        if got.startswith("?"):
            return "partial"
        return "WRONG"
    if got == want:
        return "correct"
    if got.startswith("?"):
        return "partial"
    return "WRONG"


def judge(row: dict, r: dict, today: date = TODAY) -> tuple[str, str]:
    """(outcome, why). Also used by the voice eval (src/11_voice_eval.py) with its own today."""
    exp = parse_expected(row["expected"])
    got = r["got"]
    cards = [g for g in got if g.get("card")]  # bill / udhaar / return / payment / refund cards
    kind0 = exp[0]["intent"]
    if kind0 in ("question", "other"):
        if cards:
            return "WRONG", f"a {cards[0]['intent']} card for a message that records nothing"
        if any(g["intent"] in ("correction", "undo") for g in got):
            return "WRONG", "a fix card for a message that records nothing"
        if kind0 == "question":
            return ("correct", "question") if r["question"] else ("safe", "not answered (menu)")
        if r["question"] and kind0 == "other":
            return "safe", "treated as a question (read-only)"
        if got:
            return "safe", f"asked ({got[0]['intent']})"
        return "correct", "nothing recorded"
    if kind0 == "undo_none":
        if any(g["intent"] == "fix_not_found" for g in got):
            return "correct", "not found"
        if any(g["intent"] == "adjustment" for g in got) and not cards:
            # (since 3 Nov) the entry is older than 7 days: never rewritten; a dated correcting entry is OFFERED
            return "correct", "old entry: correcting entry offered"
        if cards or any(g["intent"] in ("correction", "undo") for g in got):
            return "WRONG", "offered to change an entry the owner did not mean"
        return "safe", "nothing done"
    if kind0 in ("correction", "undo") and len(exp) == 1:
        want = exp[0]
        if cards:
            return "WRONG", "a new entry card instead of a fix"
        fixes = [g for g in got if g["intent"] in ("correction", "undo")]
        if fixes:
            g = fixes[0]
            if g["supplier"] != want["supplier"]:
                return "WRONG", f"fix offered on {g['supplier']}"
            if kind0 == "correction":
                if g["intent"] == "correction":
                    return ("correct", "fix card") if g["amount"] == want["amount"] else \
                        ("WRONG", f"fix to {g['amount']}")
                return "partial", "undo card (Badlo) instead of the fix"
            if g["intent"] == "undo":
                return ("correct", "undo card") if g["entry_amount"] == want["amount"] else \
                    ("WRONG", f"undo on the {g['entry_amount']} entry")
            return "WRONG", "a fix card when the owner asked to remove"
        if any(g["intent"] == "fix_list" for g in got):
            return "partial", "list of entries to pick from"
        if any(g["intent"] == "fix_not_found" for g in got):
            return "safe", "not found"
        return "safe", "nothing done"
    # entries: bill / payment / received / future (one or more), maybe with one fix among them
    items = [g for g in got if g["intent"] in ("bill", "payment", "received", "future", "kind", "correction", "undo",
                                              "refund", "return", "udhaar")]
    if not items:
        if any(g["intent"] == "fix_list" for g in got):
            return "partial", "list of entries to pick from"
        return "safe", "question / menu" if r["question"] else "nothing recorded"
    if len(items) > len(exp):
        return "WRONG", f"{len(items)} entries for {len(exp)}"
    worst, notes = "correct", []
    order = {"correct": 0, "partial": 1, "safe": 2, "WRONG": 3}
    for k, want in enumerate(exp):
        if k >= len(items):
            return "WRONG", f"entry {k + 1} of {len(exp)} lost"
        g = items[k]
        v, why = _fix_verdict(want, g) if want["intent"] in ("correction", "undo") else _part_verdict(want, g, today)
        notes.append(why)
        if order[v] > order[worst]:
            worst = v
    return worst, "; ".join(notes)


def _fix_verdict(want: dict, g: dict) -> tuple[str, str]:
    if g["intent"] not in ("correction", "undo"):
        return "WRONG", f"a {g['intent']} card instead of a fix"
    if g["supplier"] != want["supplier"]:
        return "WRONG", f"fix offered on {g['supplier']}"
    if want["intent"] == "correction":
        if g["intent"] == "correction":
            return ("correct", "fix card") if g["amount"] == want["amount"] else ("WRONG", f"fix to {g['amount']}")
        return "partial", "undo card (Badlo) instead of the fix"
    if g["intent"] == "undo":
        return ("correct", "undo card") if g["entry_amount"] == want["amount"] else \
            ("WRONG", f"undo on the {g['entry_amount']} entry")
    return "WRONG", "a fix card when the owner asked to remove"


def _part_verdict(want: dict, g: dict, today: date = TODAY) -> tuple[str, str]:
    wi, gi = want["intent"], g["intent"]
    if gi == "kind":
        if wi in ("bill", "payment"):
            return ("partial", "asked bill or payment") if g["amount"] == want["amount"] else ("WRONG", f"amount {g['amount']}")
        return "WRONG", f"{wi} offered as bill / payment"
    if gi == "received" and wi in ("payment", "bill"):
        # 'Sharma ne 2000 diya' -> "aapne diya ya aapko mila?" (has "Maine diye"); 'Gupta se 500 aaye' -> "maal ya
        # paisa wapas?" (has "Maal aaya"). One tap to the right card = partial; the right choice missing = safe.
        if g["amount"] != want["amount"]:
            return "WRONG", f"amount {g['amount']}"
        sv = _supplier_verdict(want["supplier"], g["supplier"] or "")
        if sv == "WRONG":
            return "WRONG", f"supplier {g['supplier']}"
        fits = (wi == "payment" and g.get("ask") == "dir") or (wi == "bill" and g.get("ask") == "what")
        return ("partial", "asked, one tap to the card") if fits else ("safe", "asked, the right choice not offered")
    if wi == "received" and gi == "refund":
        gi = "received"   # money back: the refund card is what 'received' means
    if wi in ("received", "future"):
        if gi != wi:
            return "WRONG", f"{wi} shown as {gi}"
        if g["amount"] != want["amount"]:
            return "WRONG", f"amount {g['amount']}"
        sv = _supplier_verdict(want["supplier"], g["supplier"] or "")
        return (sv, f"{wi} asked") if sv != "WRONG" else ("WRONG", f"supplier {g['supplier']}")
    if gi != wi:
        return "WRONG", f"{wi} shown as {gi}"
    if g["amount"] != want["amount"]:
        return "WRONG", f"amount {g['amount']} (want {want['amount']})"
    sv = _supplier_verdict(want["supplier"], g.get("supplier") or "")
    if sv == "WRONG":
        return "WRONG", f"supplier {g.get('supplier')} (want {want['supplier']})"
    verdict, why = sv, f"{wi} {g['amount']} {g.get('supplier')}"
    got_date = g.get("date")
    if wi == "bill" and want["credit_days"] != "any":  # 'any': the label does not say (the voice eval)
        if want["credit_days"] and g.get("credit_days") != want["credit_days"]:
            if g.get("credit_days"):
                return "WRONG", f"credit {g.get('credit_days')} days (want {want['credit_days']})"
            verdict, why = ("partial" if verdict == "correct" else verdict), why + "; credit days missed"
        elif not want["credit_days"] and g.get("credit_days"):
            return "WRONG", f"invented credit {g['credit_days']} days"
    wanted_date = want["date"] or today
    if got_date is not None and got_date != wanted_date:
        return "WRONG", f"date {got_date} (want {wanted_date})"
    return verdict, why


# ---------------------------------------------------------------- report
def _table(results: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in results:
        kind = r["expected"].split("|")[0] if ";" not in r["expected"] else "multi"
        groups[kind].append(r["outcome"])
        groups["ALL"].append(r["outcome"])
    rows = []
    for g in ["bill", "payment", "received", "future", "question", "other", "correction", "undo", "undo_none",
              "multi", "ALL"]:
        out = groups.get(g)
        if not out:
            continue
        rows.append({"group": g, "n": len(out), **{o: sum(x == o for x in out) for o in OUTCOMES},
                     "correct_pct": round(100 * sum(x == "correct" for x in out) / len(out)),
                     "wrong_pct": round(100 * sum(x == "WRONG" for x in out) / len(out))})
    return rows


def _write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=list(SETS), default="main",
                    help="main = written before the engine; blind = written by an agent that never saw the code")
    args = ap.parse_args(argv)
    fname, frozen = SETS[args.set]
    path = root / "data" / "eval" / fname
    if not path.exists():
        print(f"Missing {path}")
        return 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != frozen:
        print(f"WARNING: {fname} changed since it was frozen (sha256 {digest[:12]}...). Scores are not comparable.")
    tag = "" if args.set == "main" else f"_{args.set}"
    rows = load_rows(path)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for row in rows:
            r = run_one(row, Path(tmp))
            outcome, why = judge(row, r)
            results.append({"e_id": row["e_id"], "message": row["message"], "expected": row["expected"],
                            "outcome": outcome, "why": why, "reply": r["reply"]})
    out = root / "outputs" / "eval"
    _write(out / f"entries{tag}_detail.csv", results)
    before_path = out / f"entries{tag}_detail_before.csv"
    first = not before_path.exists()
    if first:
        _write(before_path, results)
    table = _table(results)
    _write(out / f"entries{tag}_table.csv", table)
    print(f"{'group':<12}{'n':>4}{'correct':>9}{'partial':>9}{'safe':>6}{'WRONG':>7}")
    for t in table:
        print(f"{t['group']:<12}{t['n']:>4}{t['correct']:>9}{t['partial']:>9}{t['safe']:>6}{t['WRONG']:>7}")
    wrong = [r for r in results if r["outcome"] == "WRONG"]
    for r in wrong:
        print(f"  WRONG {r['e_id']}: {r['message'][:60]} -> {r['why']}")
    if not first:
        with open(before_path, newline="", encoding="utf-8") as f:
            before = {r["e_id"]: r["outcome"] for r in csv.DictReader(f)}
        b = _table([r | {"outcome": before.get(r["e_id"], "safe")} for r in results])[-1]
        a = table[-1]
        print(f"\nbefore -> after (ALL {a['n']}): correct {b['correct']} -> {a['correct']}, "
              f"WRONG {b['WRONG']} -> {a['WRONG']}")
    else:
        print(f"\nFirst run: saved as the 'before' score ({before_path.name}), never overwritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
