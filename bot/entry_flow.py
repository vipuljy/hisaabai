"""Voice / typed entries: cards, the supplier picker, fixes and proof photos. Mixed into bot.conversation.Bot.

The text engine (bot/entries.py) says WHAT a message wants to record; this module walks the owner through it:
  bill / payment   -> a pending row in the database -> supplier step -> a card [Sahi hai] [Badlo] [Cancel].
                      Nothing counts anywhere (dues, answers, summaries, the experiment) until "Sahi hai".
  supplier step    -> one supplier clearly matches: used (and shown on the card)
                      names that only look alike, or 2+ matches: a list (closest 3 with their baaki, "➕ Naya: <name>",
                      "✍️ Koi aur (likhunga)"); no supplier looks like it: the card says "➕ naya supplier".
                      A supplier is created only when the owner taps Sahi hai on a card that says so.
  kind             -> "Sharma ko 5000" (no verb): [Bill aaya] [Payment diya] [Kuch nahi]
  received         -> "Verma se 2000 aaye": asks "Kya aapne Verma ko 2000 diye?" before anything else
  future           -> "Gupta ko 5000 dena hai": [Reminder lagao] [Payment ho gaya] [Kuch nahi]
  fix              -> an entry saved in the last 7 days: "8000 nahi 8500" -> [Haan, badlo] [Nahi];
                      "last wala galat hai" -> [Badlo] [Hata do] [Rehne do]. Every change goes to entry_edits;
                      a removed entry gets status 'deleted' (never hard-deleted).
  proof            -> every photo is READ first. Not a bill -> "ye bill nahi lagta". A bill that matches a saved voice /
                      typed bill (same supplier + amount, or the same supplier said in the last 30 minutes) -> "Ye photo
                      us bill ka proof hai?" [Haan, proof hai] [Naya bill hai]; anything else -> a new bill card.
                      "ye Sharma ke bill ka proof hai" (typed) attaches the last photo. A different total on the proof
                      is shown next to the owner's amount and the owner picks (saved as an edit, via 'proof').
  guards           -> every card shows the baaki before -> after; a likely duplicate (same supplier, kind and amount
                      within 7 days, or the same bill number) says so and changes the buttons to [Naya hai, save]
                      [Duplicate hai]; an amount 10x the supplier's usual, a payment above the baaki, a date ahead
                      get a warning line. A first payment to a supplier with no dues asks for dues from before the bot.
  old entries      -> older than 7 days: never rewritten; a dated correcting entry ("sudhaar") is offered instead.
Button ids carry what they need (entry id, amount, supplier id), so they keep working whatever happens in between.
Owner-facing wording: bot/messages.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from bot import entries as E
from bot import messages as M
from bot import numbers as N
from bot import questions as Q
from bot.extract import norm_amount, norm_date, norm_supplier, same_supplier

log = logging.getLogger("hisaabai")
IST = timezone(timedelta(hours=5, minutes=30))
VIA = {"text": "typed", "typed": "typed", "voice": "voice", "llm": "voice", "button": "button", "proof": "proof"}


def _token() -> str:
    return uuid.uuid4().hex[:8]


def _clean_name(name: str | None) -> str:
    return re.sub(r"[|:]", " ", name or "").strip()[:40]


VALUE_WORDS = N.CURRENCY | {"ka", "ki", "ke", "hai", "h", "tha", "the", "total", "amount", "sahi", "karo", "kardo",
                            "kar", "do", "sirf", "only", "bas", "ji", "hona", "chahiye", "likho", "rakho", "din", "days",
                            "day", "udhaar", "udhar", "credit", "mahine", "mahina", "month", "months", "hafte", "hafta",
                            "week", "weeks"}


def parse_amount_text(text: str) -> int | None:
    """A typed value for an amount: '8500', '8,500', 'saade aath hazaar', 'Rs 8500 hai'. Exactly one amount and
    nothing else ('Sharma ko 2000 diye' is a new message, not a value), at most Rs 1 crore."""
    toks = N.tokenize(text)
    found = N.find_amounts(toks)
    if len(found) != 1:
        return None
    a = found[0]
    rest = [t for i, t in enumerate(toks) if not a.start <= i < a.end]
    if any(t not in VALUE_WORDS for t in rest):
        return None
    return a.value if 0 < a.value <= E.MAX_AMOUNT else None


def parse_credit_text(text: str) -> int | None:
    """'15', '15 din', '2 mahine', 'do hafte', '0', 'nahi' -> days (0-365). Anything else -> None."""
    t = (text or "").strip().lower()
    if t in ("0", "nahi", "nahin", "no", "koi nahi", "udhaar nahi", "cash"):
        return 0
    toks = N.tokenize(t)
    unit = 30 if set(toks) & {"mahine", "mahina", "month", "months"} else \
        7 if set(toks) & {"hafte", "hafta", "week", "weeks"} else 1
    found = N.find_amounts(toks)
    if len(found) == 1:
        a = found[0]
        if any(x not in VALUE_WORDS for k, x in enumerate(toks) if not a.start <= k < a.end):
            return None
        days = a.value * unit
    else:
        words = [x for x in toks if x in N.WORD_VALUES]
        if len(words) != 1 or any(x not in VALUE_WORDS and x not in N.WORD_VALUES for x in toks):
            return None
        days = int(N.WORD_VALUES[words[0]] * unit)
    return days if 0 <= days <= 365 else None


def parse_date_text(text: str, today: date) -> date | None:
    d = norm_date(text)
    if d:
        out = date.fromisoformat(d)
        return out if out <= today + timedelta(days=1) else None
    day, _ = Q.find_day(Q.tokens(text), today, future=False)
    return day if day and day <= today else None


class EntryFlow:
    """Methods used by bot.conversation.Bot (self.store, self.wa, self._text, self._buttons, self._list ...)."""

    # ------------------------------------------------------------ helpers
    def _fix_since(self):
        """Entries saved on or after this moment can be fixed by message: 00:00 IST, 7 days before today."""
        return datetime.combine(self._today() - timedelta(days=M.FIX_WINDOW_DAYS), time.min, tzinfo=IST)

    def _recent(self, shop):
        since = self._fix_since()
        return [e for e in self._history(shop) if e["created_at"] >= since]

    def _history(self, shop):
        """Confirmed entries of the last 120 days (fixes, duplicates, 'unusual amount'): read once per message."""
        return self._memo(shop, "history", lambda: self.store.recent_entries(
            shop["id"], datetime.combine(self._today() - timedelta(days=120), time.min, tzinfo=IST)))

    def _dues_map(self, shop) -> dict:
        return self._memo(shop, "dues", lambda: {str(r["supplier_id"]): r["due"] for r in self.store.dues(shop["id"])})

    def _due_of(self, shop, supplier_id) -> object:
        return self._dues_map(shop).get(str(supplier_id), 0)

    def _memo(self, shop, key, fn):
        """A per-message cache (bot.conversation sets shop['_memo'] = {} for every message): the same query is not run
        twice while one message is handled. Anything that changes the data calls _forget()."""
        memo = shop.setdefault("_memo", {})
        if key not in memo:
            memo[key] = fn()
        return memo[key]

    def _forget(self, shop):
        shop["_memo"] = {}

    def _lazy_recent(self, shop):
        """The recent entries, read only if the text engine actually looks at them (a fix like '8000 nahi 8500')."""
        return _Lazy(lambda: self._recent(shop))

    async def _gone(self, shop, t: str, eid: str):
        """A tap on a card that no longer applies: say what happened to that entry (saved, cancelled, too old)."""
        e = None
        if _uuid(eid):
            if t == "b":
                b = self.store.get_bill(eid)
                if b and str(b["shop_id"]) == str(shop["id"]):
                    sup = self._own_supplier(shop, b["supplier_id"]) if b.get("supplier_id") else None
                    e = {"type": "bill", "kind": b.get("kind"), "amount": b.get("total_amount") or 0,
                         "supplier": sup["name"] if sup else b.get("supplier_name_raw"), "status": b["status"],
                         "day": b.get("bill_date"), "created_at": b.get("confirmed_at") or b["created_at"]}
            else:
                pay = self.store.get_payment(eid)
                if pay and str(pay["shop_id"]) == str(shop["id"]):
                    e = {"type": "payment", "kind": pay.get("kind"), "amount": pay["amount"],
                         "supplier": pay.get("supplier") or pay.get("supplier_name_raw"), "status": pay.get("status"),
                         "day": pay.get("paid_on"), "created_at": pay.get("confirmed_at") or pay["created_at"]}
        old = bool(e and e["status"] in ("confirmed", "corrected") and e["created_at"] < self._fix_since())
        await self._text(shop, M.gone(e, self._today(), old))

    def _suppliers(self, shop):
        return self._memo(shop, "suppliers", lambda: self.store.list_suppliers(shop["id"]))

    def _own_supplier(self, shop, supplier_id):
        return next((s for s in self._suppliers(shop) if str(s["id"]) == str(supplier_id)), None)

    def _own_pending_payment(self, shop, pid):
        if not _uuid(pid):
            return None
        p = self.store.get_payment(pid)
        if p and str(p["shop_id"]) == str(shop["id"]) and p["status"] == "pending_confirm":
            return p
        return None

    def _own_entry(self, shop, etype, eid):
        """A confirmed entry of this shop that may still be changed by message (last 7 days)."""
        if etype not in ("bill", "payment") or not _uuid(eid):
            return None
        e = self.store.get_entry(shop["id"], etype, eid)
        if e is None or e["created_at"] < self._fix_since():
            return None
        return e

    def _once(self, shop, token: str) -> bool:
        """A kind / received / future / proof button works once (a second tap must not make a second entry).
        Uses the messages table's unique id, so two taps arriving at the same moment cannot both pass."""
        if not token:
            return False
        return self.store.record_message(shop["id"], "in", f"once:{shop['id']}:{token}"[:200], "choice")

    async def _say_heard(self, shop, heard):
        if heard:
            await self._text(shop, M.heard(heard))

    # ------------------------------------------------------------ entry point for parsed messages
    async def _start_entries(self, shop, parts, source: str, heard: str | None = None, understood_by: str = "rules",
                             fix=None, order=None):
        self.store.log_event(shop["id"], "entry_parsed", {"source": source, "understood_by": understood_by,
                                                          "intents": [p.intent for p in parts], "parts": len(parts),
                                                          "with_fix": fix is not None})
        await self._say_heard(shop, heard)
        n = len(parts) + (1 if fix else 0)
        if n > 1:
            await self._text(shop, M.entries_intro(n))
        for kind, item in (order or [("part", p) for p in parts]):
            if kind == "fix":
                await self._start_fix(shop, item, source)
            else:
                await self._start_part(shop, item, source)

    async def _say_skipped(self, shop, skipped):
        """A piece of the message had an amount but recorded nothing: say so, never drop it silently."""
        if skipped:
            self.store.log_event(shop["id"], "entry_part_skipped", {"n": len(skipped)})
            await self._text(shop, M.parts_skipped(skipped))

    async def _start_part(self, shop, p: "E.Part", source: str):
        name = _clean_name(p.supplier["name"] if p.supplier else p.name)
        sid = p.supplier["id"] if p.supplier else ""
        if len(p.amounts) == 2 and p.intent in ("return", "refund"):
            # '500 ka maal wapas kiya, 300 hi kata': the baaki changes by what the supplier ACCEPTED: the owner picks
            tok = _token()
            goods, lo = max(p.amounts), min(p.amounts)
            base = f"{tok}|{{amt}}|{sid}|{name}|{source}|{p.intent}|{goods}"
            self.store.log_event(shop["id"], "partial_asked", {"goods": goods, "accepted_guess": lo, "source": source})
            await self._buttons(shop, M.partial_ask(name or "Supplier", goods),
                                [(f"rmaan:{base.format(amt=lo)}", M.btn_amount(lo)),
                                 (f"rmaan:{base.format(amt=goods)}", M.btn_amount(goods)),
                                 (f"kind_no:{tok}", M.BTN_NOTHING)])
            return
        if p.intent == "refund" and p.why == "goods" and not p.cash_checked and sid:
            due = self._due_of(shop, sid)
            if due is not None and Decimal(str(due)) >= Decimal(str(p.amount)):
                # goods went back while the shop still owes at least that much: usually the dues are just cut and no
                # cash moves (16 Nov phone test). Cash in hand is the other answer.
                tok = _token()
                goods = max(p.amounts) if p.amounts else ""
                payload = f"{tok}|{p.amount}|{sid}|{name}|{source}|{goods}"
                self.store.log_event(shop["id"], "goods_back_asked", {"amount": p.amount, "source": source})
                await self._buttons(shop, M.goods_back_how(name or "Supplier", p.amount, due, goods or None),
                                    [(f"rcash_cut:{payload}", M.BTN_CASH_CUT), (f"rcash_got:{payload}", M.BTN_CASH_GOT),
                                     (f"kind_no:{tok}", M.BTN_NOTHING)])
                return
        if p.intent in ("bill", "udhaar", "return"):
            await self._new_text_bill(shop, p, source, kind=p.intent)
        elif p.intent == "refund" and p.why not in M.REFUND_WHY:
            tok = _token()
            payload = f"{tok}|{p.amount}|{sid}|{name}|{source}"
            self.store.log_event(shop["id"], "refund_why_asked", {"amount": p.amount, "source": source})
            await self._buttons(shop, M.refund_why(name or "Supplier", p.amount),
                                [(f"rfw_g:{payload}", M.BTN_WHY_GOODS), (f"rfw_o:{payload}", M.BTN_WHY_OVER),
                                 (f"kind_no:{tok}", M.BTN_NOTHING)])
        elif p.intent in ("payment", "refund"):
            await self._new_payment(shop, p, source, kind=p.intent)
        elif p.intent == "kind":
            tok = _token()
            payload = f"{tok}|{p.amount}|{sid}|{name}|{source}"
            self.store.log_event(shop["id"], "kind_asked", {"amount": p.amount, "source": source})
            await self._buttons(shop, M.kind_ask(name or "?", p.amount),
                                [(f"kind_bill:{payload}", M.BTN_KIND_BILL), (f"kind_pay:{payload}", M.BTN_KIND_PAY),
                                 (f"kind_no:{tok}", M.BTN_NOTHING)])
        elif p.intent == "received":
            tok = _token()
            payload = f"{tok}|{p.amount}|{sid}|{name}|{source}"
            self.store.log_event(shop["id"], "received_asked", {"amount": p.amount, "source": source, "ask": p.ask})
            if p.ask == "dir":   # 'Sharma ne 2000 diya': to Sharma, or from Sharma?
                await self._buttons(shop, M.received_ask(name or "Supplier", p.amount),
                                    [(f"rcv_pay:{payload}", M.BTN_RCV_PAY), (f"rcv_refund:{payload}", M.BTN_RCV_GOT),
                                     (f"rcv_no:{tok}", M.BTN_NOTHING)])
            else:                # 'Gupta se 500 aaye': goods, or money back?
                await self._buttons(shop, M.received_what(name or "Supplier", p.amount),
                                    [(f"rcv_bill:{payload}", M.BTN_RCV_GOODS), (f"rcv_refund:{payload}", M.BTN_RCV_MONEY),
                                     (f"rcv_no:{tok}", M.BTN_NOTHING)])
        elif p.intent == "future":
            tok = _token()
            day = p.day.isoformat() if p.day else ""
            payload = f"{tok}|{p.amount}|{sid}|{name}|{source}|{day}"
            due = self._due_of(shop, sid) if sid else None
            inside = due is not None and Decimal(str(due)) >= Decimal(str(p.amount))
            self.store.log_event(shop["id"], "future_asked", {"amount": p.amount, "source": source, "has_day": bool(day),
                                                              "within_due": inside})
            add = (f"fut_add:{payload}", M.BTN_FUT_NEW if inside else M.BTN_FUT_ADD)
            rem = (f"fut_rem:{payload}", M.BTN_FUT_REMIND)
            # the amount is already inside the known baaki: most likely a reminder, so that comes first
            await self._buttons(shop, M.future_ask(name or "Supplier", p.amount, p.day, due),
                                [rem, add, (f"fut_no:{tok}", M.BTN_NOTHING)] if inside else
                                [add, rem, (f"fut_no:{tok}", M.BTN_NOTHING)])

    # ------------------------------------------------------------ bills by voice / typing
    async def _new_text_bill(self, shop, p, source, kind="bill"):
        today = self._today()
        bill_date = p.bill_date or today
        due = bill_date + timedelta(days=p.credit_days) if p.credit_days else None
        name = p.supplier["name"] if p.supplier else p.name
        fields = {"is_bill": True, "supplier_name": p.name or name, "total_amount": p.amount,
                  "bill_date": bill_date.isoformat(),
                  "invoice_number": p.invoice, "due_date": due.isoformat() if due else None,
                  "unsure_fields": ["total_amount"] if p.llm_amount else []}
        src = "voice" if source in ("voice", "llm") else "typed"
        bill = self.store.create_bill(shop["id"], fields, {
            "model": src, "source": src, "raw_text": p.raw[:300], "credit_days": p.credit_days,
            "supplier_id": p.supplier["id"] if p.supplier else None, "kind": kind})
        self.store.log_event(shop["id"], "bill_received", {"bill_id": bill["id"], "source": src, "unsure": bill["unsure"],
                                                           "missing": [], "credit_days": p.credit_days, "kind": kind})
        if p.note:
            self._card_notes()[str(bill["id"])] = p.note
        await self._supplier_step(shop, "b", bill["id"], name, p.supplier, p.candidates, p.amount)

    # ------------------------------------------------------------ payments
    async def _new_payment(self, shop, p, source, kind="payment"):
        src = "voice" if source in ("voice", "llm") else "text"
        name = p.supplier["name"] if p.supplier else p.name
        pay = self.store.create_payment(shop["id"], p.supplier["id"] if p.supplier else None, p.amount, src,
                                        p.raw[:200], paid_on=p.bill_date or self._today(), supplier_name_raw=p.name or name,
                                        kind=kind, reason=p.why if kind == "refund" else None)
        self.store.log_event(shop["id"], "payment_proposed", {"payment_id": pay["id"], "amount": p.amount,
                                                              "source": src, "llm_amount": p.llm_amount, "kind": kind})
        if p.note:
            self._card_notes()[str(pay["id"])] = p.note
        if p.llm_amount:  # the AI gave an amount our number reader did not find: flag it on the card
            self.store.log_event(shop["id"], "amount_unsure", {"payment_id": pay["id"]})
        await self._supplier_step(shop, "p", pay["id"], name, p.supplier, p.candidates, p.amount)

    # ------------------------------------------------------------ the supplier step
    async def _supplier_step(self, shop, t, eid, name, match=None, cands=(), amount=None):
        """match -> card; 2+ matches or look-alikes -> list; nothing alike -> card with '➕ naya supplier'."""
        suppliers = self._suppliers(shop)
        if match is None and not cands and name:
            match, cands = E.strict_match(Q.tokens(name), suppliers)
        if match:
            self._set_entry_supplier(t, eid, match["id"], False)
            await self._show_card(shop, t, eid)
            return
        close = list(cands)
        if name and len(close) < 3:  # add look-alikes after the tied / partial matches
            close += [s for s in E.close_suppliers(name, suppliers) if s["id"] not in {c["id"] for c in close}]
            close = close[:3]
        if name and not close:
            self._set_entry_supplier(t, eid, None, True)
            await self._show_card(shop, t, eid)
            return
        await self._supplier_list(shop, t, eid, name, close, amount, ambiguous=len(cands) > 1)

    async def _supplier_list(self, shop, t, eid, name, close, amount, ambiguous=False):
        dues = {str(r["supplier_id"]): r["due"] for r in self.store.dues(shop["id"])}
        if not close:  # no name at all: the suppliers with the biggest baaki
            close = sorted(self._suppliers(shop),
                           key=lambda s: (-dues.get(str(s["id"]), 0), s["name"]))[:7]
        rows = [(f"sp:{t}:{eid}:{s['id']}", s["name"][:24],
                 f"Baaki {M.inr(dues[str(s['id'])])}" if str(s["id"]) in dues else "") for s in close[:7]]
        if name and not any(norm_supplier(s["name"]) == norm_supplier(name) for s in close):
            rows.append((f"spn:{t}:{eid}", (M.ADD_SUPPLIER_ROW + name)[:24], M.ADD_SUPPLIER_DESC))
        rows.append((f"spt:{t}:{eid}", M.TYPE_SUPPLIER_ROW, M.TYPE_SUPPLIER_DESC))
        self.store.log_event(shop["id"], "supplier_pick_asked", {"entry": t, "options": len(rows),
                                                                 "ambiguous": ambiguous, "has_name": bool(name)})
        await self._list(shop, M.pick_supplier(name, amount, t, close=not ambiguous), M.LIST_BTN_SUPPLIER, rows)

    def _set_entry_supplier(self, t, eid, supplier_id, new):
        if t == "b":
            self.store.set_bill_supplier(eid, supplier_id, new)
        else:
            self.store.set_payment_fields(eid, supplier_id=supplier_id, new_supplier=bool(new))

    def _card_notes(self) -> dict:
        """Extra card lines by entry id (a partial return's '₹500 ka maal, supplier ne ₹300 maana'). In memory: only
        a nicety for the card shown right away, never data."""
        if not hasattr(self, "_card_note_map"):
            self._card_note_map = {}
        return self._card_note_map

    async def _show_card(self, shop, t, eid):
        today = self._today()
        if t == "b":
            bill = self.store.get_bill(eid)
            if bill.get("source") in ("voice", "typed"):
                sup = self._own_supplier(shop, bill["supplier_id"]) if bill.get("supplier_id") else None
                label = sup["name"] if sup else (bill.get("supplier_name_raw") or "?")
                unsure = tuple(f for f in (bill.get("unsure") or []) if f not in (bill.get("checked") or []))
                before, after, notes, dup = self._card_checks(shop, "b", bill, log=True)
                notes = (self._card_notes().get(str(eid)),) + notes
                self._set_state(shop, {})
                await self._buttons(shop, M.bill_card(bill, label, today, bill.get("new_supplier"), unsure, before, after,
                                                      said=bill.get("supplier_name_raw"), notes=notes),
                                    self._card_buttons("b", eid, dup))
            else:
                await self._show_photo_summary(shop, bill)
            return
        pay = self.store.get_payment(eid)
        label = pay.get("supplier") or pay.get("supplier_name_raw") or "?"
        unsure = ("amount",) if (self.store.event_exists(shop["id"], "amount_unsure", "payment_id", eid)
                                 and not self.store.event_exists(shop["id"], "amount_checked", "payment_id", eid)) else ()
        before, after, notes, dup = self._card_checks(shop, "p", pay, log=True)
        notes = (self._card_notes().get(str(eid)),) + notes
        if (pay.get("kind") or "payment") == "payment" and Decimal(str(after)) < 0 and Decimal(str(before)) <= 0 \
                and not self._has_history(shop, pay.get("supplier_id")) and self._once(shop, f"olddue:{eid}"):
            # a first payment that would show as advance: dues from before the bot are probably missing
            self.store.log_event(shop["id"], "old_dues_asked", {"payment_id": eid})
            self._set_state(shop, {})
            await self._buttons(shop, M.olddue_ask(label, pay["amount"]),
                                [(f"old_yes:{eid}", M.BTN_OLD_YES), (f"old_no:{eid}", M.BTN_OLD_NO),
                                 (f"pcancel:{eid}", M.BTN_CANCEL)])
            return
        self._set_state(shop, {})
        await self._buttons(shop, M.payment_card(pay, label, today, pay.get("new_supplier"), unsure, before, after,
                                                 said=pay.get("supplier_name_raw"), notes=notes),
                            self._card_buttons("p", eid, dup))

    def _card_buttons(self, t, eid, dup):
        ok, edit, cancel = ("ok", "edit", "bcancel") if t == "b" else ("pok", "pedit", "pcancel")
        if dup:   # the owner decides: a second, real entry, or the same one again
            return [(f"{ok}:{eid}:dup", M.BTN_DUP_SAVE), (f"{edit}:{eid}", M.BTN_EDIT),
                    (f"{cancel}:{eid}:dup", M.BTN_DUP_CANCEL)]
        return [(f"{ok}:{eid}", M.BTN_OK), (f"{edit}:{eid}", M.BTN_EDIT), (f"{cancel}:{eid}", M.BTN_CANCEL)]

    def _has_history(self, shop, supplier_id) -> bool:
        if not supplier_id:
            return False
        return str(supplier_id) in self._dues_map(shop) or any(
            str(e["supplier_id"]) == str(supplier_id) for e in self._history(shop))

    @staticmethod
    def _effect(t, kind, amount) -> Decimal:
        """What confirming this entry does to the supplier's baaki."""
        amount = Decimal(str(amount or 0))
        if t == "b":
            return -amount if kind == "return" else amount
        return amount if kind == "refund" else -amount

    def _card_checks(self, shop, t, entry, log=False):
        """(baaki before, baaki after, warning lines, the earlier entry this one may repeat). log=True (a card being
        shown, not the re-check at the tap) records which warnings were shown, for the pilot health report."""
        today = self._today()
        sid = entry.get("supplier_id")
        kind = entry.get("kind") or ("bill" if t == "b" else "payment")
        amount = entry.get("total_amount") if t == "b" else entry.get("amount")
        day = (_as_day(entry.get("bill_date")) if t == "b" else _as_day(entry.get("paid_on"))) or today
        before = Decimal(str(self._due_of(shop, sid))) if sid else Decimal(0)
        effect = self._effect(t, kind, amount)
        if t == "p" and kind == "refund" and entry.get("reason") == "goods":
            effect = Decimal(0)        # goods back and money back: the return and the refund cancel out
        after = before + effect
        notes = []
        dup = self._find_dup(shop, t, entry, kind, amount, day) if sid and amount is not None else None
        if dup:
            notes.append(M.dup_note(dup, today))
        usual = self._usual(shop, t, kind, sid) if sid else None
        if usual and amount is not None and Decimal(str(amount)) >= 10 * usual:
            notes.append(M.warn_big(amount, usual))
        if t == "p" and kind == "payment" and before >= 0 and after < 0 and self._has_history(shop, sid):
            notes.append(M.warn_advance(-after))
        if day > today:
            notes.append(M.WARN_FUTURE)
        if log and notes:
            kinds = [k for k, on in (("duplicate", dup), ("unusual", any(n.startswith("⚠️ Is supplier") for n in notes)),
                                     ("advance", any("advance ho jayega" in n for n in notes)), ("date_ahead", day > today))
                     if on]
            self.store.log_event(shop["id"], "card_warned", {"entry": t, "kinds": kinds})
        return before, after, tuple(notes), dup

    def _find_dup(self, shop, t, entry, kind, amount, day):
        """The same supplier, kind and amount within 7 days, or (bills) the same invoice number at any date."""
        etype = "bill" if t == "b" else "payment"
        if t == "b" and entry.get("invoice_number"):
            hit = self.store.bill_by_invoice(shop["id"], entry["supplier_id"], entry["invoice_number"], entry["id"])
            if hit:
                return hit
        for e in self._recent(shop):
            if e["type"] != etype or (e.get("kind") or etype) != kind or str(e["supplier_id"]) != str(entry["supplier_id"]):
                continue
            if str(e.get("reason") or "").startswith(("pair:", "sudhaar")) or str(e["id"]) == str(entry["id"]):
                continue
            if abs(Decimal(str(e["amount"])) - Decimal(str(amount))) <= 1 and abs((_as_day(e["day"]) - day).days) <= 7:
                return e
        return None

    def _usual(self, shop, t, kind, sid):
        """The supplier's typical amount for this kind of entry (median of up to 10), if there are 3 or more."""
        etype = "bill" if t == "b" else "payment"
        amounts = sorted([Decimal(str(e["amount"])) for e in self._history(shop)   # newest first: the last 10
                          if e["type"] == etype and (e.get("kind") or etype) == kind and str(e["supplier_id"]) == str(sid)
                          and not str(e.get("reason") or "").startswith(("pair:", "sudhaar"))][:10])
        if len(amounts) < 3:
            return None
        return amounts[len(amounts) // 2]

    async def _on_supplier_pick(self, shop, kind, rest):
        t, _, rest2 = rest.partition(":")
        eid, _, sid = rest2.partition(":")
        entry = self._own_pending_bill(shop, eid) if t == "b" else self._own_pending_payment(shop, eid)
        if entry is None:
            await self._text(shop, M.ENTRY_GONE)
            return
        if kind == "sp":
            sup = self._own_supplier(shop, sid) if _uuid(sid) else None
            if sup is None:
                await self._text(shop, M.ENTRY_GONE)
                return
            self._set_entry_supplier(t, eid, sup["id"], False)
            self.store.log_event(shop["id"], "supplier_picked", {"entry": t, "choice": "existing"})
            await self._show_card(shop, t, eid)
        elif kind == "spn":
            name = entry.get("supplier_name_raw") or ""
            words = set((norm_supplier(name) or "").split())
            twin = next((s for s in self._suppliers(shop)
                         if words and words <= set(s["name_norm"].split())), None)   # 'Sharma' inside 'Sharma Traders' 
            self.store.log_event(shop["id"], "supplier_picked", {"entry": t, "choice": "new", "same_name": bool(twin)})
            if twin:   # a second 'Sharma' would make every later 'Sharma' ambiguous: ask for the place / full name
                self._set_state(shop, {"await": "sname", "t": t, "id": eid, "new_only": True})
                await self._text(shop, M.same_name_ask(twin["name"]))
                return
            self._set_entry_supplier(t, eid, None, True)
            await self._show_card(shop, t, eid)
        else:  # spt: the owner will type the name
            self._set_state(shop, {"await": "sname", "t": t, "id": eid})
            await self._text(shop, M.ASK_SUPPLIER_NAME)

    async def _on_supplier_name(self, shop, st, text):
        t, eid = st["t"], st["id"]
        entry = self._own_pending_bill(shop, eid) if t == "b" else self._own_pending_payment(shop, eid)
        if entry is None:
            self._set_state(shop, {})
            await self._gone(shop, t, eid)
            return
        name = re.sub(r"\s+", " ", text).strip()[:60]
        if not norm_supplier(name) or sum(c.isalpha() for c in name) < 2 or not E._runs(E.tokenize(name)[0], set()):
            await self._text(shop, M.BAD_VALUE_ANY["supplier_name"])  # 'ok', 'haan', '5000': not a name
            return
        if not st.get("new_only"):
            self._set_state(shop, {})
        if t == "b":
            self.store.set_bill_field(eid, "supplier_name", name)
            self.store.set_bill_supplier(eid, None, False)
        else:
            self.store.set_payment_fields(eid, supplier_name_raw=name, supplier_id=None, new_supplier=False)
        amount = entry["total_amount"] if t == "b" else entry["amount"]
        if st.get("new_only"):   # 'naya supplier' with a name the shop already has: the owner adds a place / more name
            words = set((norm_supplier(name) or "").split())
            if any(words <= set(s["name_norm"].split()) for s in self._suppliers(shop)):
                self._set_state(shop, st)
                await self._text(shop, M.same_name_ask(name))
                return
            self._set_entry_supplier(t, eid, None, True)
            await self._show_card(shop, t, eid)
            return
        await self._typed_supplier_step(shop, t, eid, name, amount)

    async def _typed_supplier_step(self, shop, t, eid, name, amount):
        """A name the owner TYPED for this entry: only the exact name is used without asking. 'Sharma Traders Sadar'
        must not silently become 'Sharma Traders' (two shops can share a name; the city tells them apart)."""
        suppliers = self._suppliers(shop)
        exact = [s for s in suppliers if s["name_norm"] == norm_supplier(name)]
        if len(exact) == 1:
            self._set_entry_supplier(t, eid, exact[0]["id"], False)
            await self._show_card(shop, t, eid)
            return
        from bot.conversation import match_supplier
        close = [m for m in [match_supplier(name, suppliers)] if m]
        close += [s for s in E.strict_match(Q.tokens(name), suppliers)[1] + E.close_suppliers(name, suppliers)
                  if s["id"] not in {c["id"] for c in close}]
        if not close:
            self._set_entry_supplier(t, eid, None, True)
            await self._show_card(shop, t, eid)
            return
        await self._supplier_list(shop, t, eid, name, close[:3], amount, ambiguous=False)

    # ------------------------------------------------------------ confirming / cancelling a payment card
    async def _resolve_for_confirm(self, shop, t, entry, name):
        """The supplier to save under: the picked one, a new one the owner tapped, or the one clear match.
        Returns None after showing the picker (nothing is created silently)."""
        if entry.get("supplier_id"):
            sup = self._own_supplier(shop, entry["supplier_id"])
            if sup:
                return sup
        if entry.get("new_supplier") and name:
            sup = self.store.find_or_create_supplier(shop["id"], name)
            self._forget(shop)
            self.store.log_event(shop["id"], "supplier_created", {"entry": t})
            return sup
        from bot.conversation import match_supplier
        match = match_supplier(name or "", self._suppliers(shop)) if name else None
        if match:
            return match
        amount = entry.get("total_amount") if t == "b" else entry.get("amount")
        await self._supplier_step(shop, t, entry["id"], name, amount=amount)
        return None

    async def _confirm_payment(self, shop, pid, dup_ok=False):
        pay = self._own_pending_payment(shop, pid)
        if pay is None:
            await self._gone(shop, "p", pid)
            return
        sup = await self._resolve_for_confirm(shop, "p", pay, pay.get("supplier_name_raw"))
        if sup is None:
            return
        if not dup_ok and pay.get("supplier_id") and self._card_checks(shop, "p", pay)[3]:
            await self._show_card(shop, "p", pid)   # an identical entry was saved after this card was shown
            return
        self.store.confirm_payment(pid, sup["id"])
        refund = pay.get("kind") == "refund"
        why = pay.get("reason") if refund else None
        if why == "goods":   # goods went back AND money came back: the return is saved with the refund
            ret = self.store.create_bill(shop["id"], {"is_bill": True, "supplier_name": sup["name"],
                                                      "total_amount": pay["amount"],
                                                      "bill_date": _as_day(pay.get("paid_on")).isoformat(),
                                                      "unsure_fields": []},
                                         {"model": "typed", "source": "voice" if pay["source"] == "voice" else "typed",
                                          "raw_text": (pay.get("raw_text") or "")[:300], "kind": "return",
                                          "supplier_id": sup["id"], "reason": f"pair:{pid}"})
            self.store.confirm_bill(ret["id"], sup["id"])
        # a refund is logged as its own event: 'payment_logged' feeds the experiment's secondary metric
        self.store.log_event(shop["id"], "refund_logged" if refund else "payment_logged",
                             {"amount": pay["amount"], "source": pay["source"], "payment_id": pid, "dup_ok": dup_ok}
                             | ({"why": why} if refund else {}))
        self._set_state(shop, {})
        self._forget(shop)
        due = self._due_of(shop, sup["id"])
        await self._text(shop, M.refund_saved(sup["name"], due, why) if refund else M.pay_saved(sup["name"], due))
        if not await self._maybe_ask_shop_name(shop):
            await self._maybe_ask_rating(shop)

    async def _cancel_payment(self, shop, pid, dup=False):
        if self._own_pending_payment(shop, pid) is None:
            await self._gone(shop, "p", pid)
            return
        self.store.cancel_payment(pid)
        self.store.log_event(shop["id"], "payment_cancelled", {"payment_id": pid, "duplicate": dup})
        self._set_state(shop, {})
        await self._text(shop, M.DUP_CANCELLED if dup else M.PAY_CANCELLED)
        if not dup:
            await self._after_cancel(shop)

    async def _cancel_bill(self, shop, bill_id, dup=False):
        if self._own_pending_bill(shop, bill_id) is None:
            await self._gone(shop, "b", bill_id)
            return
        self.store.reject_bill(bill_id)
        self.store.log_event(shop["id"], "bill_cancelled", {"bill_id": bill_id, "duplicate": dup})
        self._set_state(shop, {})
        await self._text(shop, M.DUP_CANCELLED if dup else M.BILL_CANCELLED)
        if not dup:
            await self._after_cancel(shop)

    # ------------------------------------------------------------ old dues before a first payment
    async def _on_old_dues_button(self, shop, kind, pid):
        pay = self._own_pending_payment(shop, pid)
        if pay is None:
            await self._gone(shop, "p", pid)
            return
        if kind == "old_no":
            self.store.log_event(shop["id"], "old_dues_none", {"payment_id": pid})
            await self._show_card(shop, "p", pid)
            return
        self._set_state(shop, {"await": "oldamt", "id": pid})
        await self._text(shop, M.ASK_OLD_AMOUNT)

    async def _on_old_dues_value(self, shop, st, text):
        pay = self._own_pending_payment(shop, st["id"])
        if pay is None:
            self._set_state(shop, {})
            await self._gone(shop, "p", st["id"])
            return
        amount = parse_amount_text(text)
        if amount is None:
            await self._text(shop, M.BAD_VALUE_ANY["amount"])
            return
        self._set_state(shop, {})
        sup = self._own_supplier(shop, pay["supplier_id"]) if pay.get("supplier_id") else None
        if sup is None:   # the owner said this supplier had dues before the bot: it is real, so it is added now
            sup = self.store.find_or_create_supplier(shop["id"], pay.get("supplier_name_raw") or "?")
            self._forget(shop)
            self.store.log_event(shop["id"], "supplier_created", {"entry": "p", "via": "old_dues"})
            self.store.set_payment_fields(pay["id"], supplier_id=sup["id"], new_supplier=False)
        bill = self.store.create_bill(shop["id"], {"is_bill": True, "supplier_name": sup["name"], "total_amount": amount,
                                                   "bill_date": self._today().isoformat(), "unsure_fields": []},
                                      {"model": "typed", "source": "typed", "raw_text": "purana baaki", "kind": "udhaar",
                                       "supplier_id": sup["id"], "reason": "purana baaki"})
        self.store.confirm_bill(bill["id"], sup["id"])
        self.store.log_event(shop["id"], "old_dues_added", {"payment_id": pay["id"], "bill_id": bill["id"]})
        self._forget(shop)
        await self._text(shop, M.olddue_saved(sup["name"], amount))
        await self._show_card(shop, "p", pay["id"])

    # ------------------------------------------------------------ editing a card before it is saved
    async def _edit_pending(self, shop, t, eid):
        entry = self._own_pending_bill(shop, eid) if t == "b" else self._own_pending_payment(shop, eid)
        if entry is None:
            await self._gone(shop, t, eid)
            return
        rows = M.EDIT_ROWS_BILL if t == "b" else M.EDIT_ROWS_PAYMENT
        await self._list(shop, M.EDIT_WHAT, M.LIST_BTN_EDIT, [(f"pf:{t}:{eid}:{f}", title, d) for f, title, d in rows])

    async def _on_pending_field_pick(self, shop, rest):
        t, _, rest2 = rest.partition(":")
        eid, _, field = rest2.partition(":")
        entry = self._own_pending_bill(shop, eid) if t == "b" else self._own_pending_payment(shop, eid)
        valid = {f for f, _, _ in (M.EDIT_ROWS_BILL if t == "b" else M.EDIT_ROWS_PAYMENT)}
        if entry is None or field not in valid:
            await self._text(shop, M.ENTRY_GONE)
            return
        self.store.log_event(shop["id"], "edit_started", {"entry": t, "field": field})
        self._set_state(shop, {"await": "pfield", "t": t, "id": eid, "field": field})
        await self._text(shop, M.ASK_VALUE[field])

    async def _on_pending_field_value(self, shop, st, text):
        t, eid, field = st["t"], st["id"], st["field"]
        entry = self._own_pending_bill(shop, eid) if t == "b" else self._own_pending_payment(shop, eid)
        if entry is None:
            self._set_state(shop, {})
            await self._text(shop, M.ENTRY_GONE)
            return
        if field == "supplier_name":
            await self._on_supplier_name(shop, {"t": t, "id": eid}, text)
            return
        value = self._parse_value(field, text)
        if value is None:
            await self._text(shop, M.BAD_VALUE_ANY[field])
            return
        self._set_state(shop, {})
        if t == "b":
            if field == "credit_days":
                self.store.set_bill_credit_days(eid, value)
            elif field == "bill_date":
                self.store.set_bill_field(eid, "bill_date", value.isoformat())
                if entry.get("credit_days") is not None:
                    self.store.set_bill_credit_days(eid, entry["credit_days"])
            else:
                self.store.set_bill_field(eid, field, value)
        else:
            self.store.set_payment_fields(eid, **{field: value})
            if field == "amount":
                self.store.log_event(shop["id"], "amount_checked", {"payment_id": eid})
        await self._show_card(shop, t, eid)

    def _parse_value(self, field, text):
        today = self._today()
        if field in ("total_amount", "amount"):
            return parse_amount_text(text)
        if field in ("bill_date", "paid_on", "date"):
            return parse_date_text(text, today)
        if field == "credit_days":
            return parse_credit_text(text)
        if field == "invoice_number":  # 'Bill no 4471' -> 4471; 'INV-22/A' stays
            words = text.strip().split()
            with_digits = [w for w in words if re.search(r"\d", w)]
            v = (with_digits[-1] if with_digits else "".join(words)).strip(".:#")[:40]
            return v.upper() if v else None
        return None

    # ------------------------------------------------------------ kind / received / future buttons
    async def _on_choice(self, shop, kind, rest):
        parts = rest.split("|")
        tok = parts[0]
        if not self._once(shop, tok):
            await self._text(shop, M.ENTRY_GONE)
            return
        if kind in ("kind_no", "fut_no"):
            self.store.log_event(shop["id"], "choice_none", {"kind": kind})
            await self._text(shop, M.NOTHING_SAVED)
            return
        if kind == "rcv_no":   # the money came in, and it was not a refund either: nothing to record
            self.store.log_event(shop["id"], "received_denied", {})
            await self._text(shop, M.RECEIVED_NO)
            return
        try:
            amount = int(parts[1])
            sid, name, source = parts[2], parts[3] or None, parts[4] or "text"
        except (IndexError, ValueError):
            await self._text(shop, M.ENTRY_GONE)
            return
        sup = self._own_supplier(shop, sid) if _uuid(sid) else None
        if kind == "fut_rem":
            day = None
            if len(parts) > 5 and parts[5]:
                try:
                    day = date.fromisoformat(parts[5])
                except ValueError:
                    day = None
            today = self._today()
            st = {"await": "q", "intent": "set_reminder", "by": "rules", "source": "menu", "supplier_id":
                  sup["id"] if sup else None, "supplier_set": True, "period": None,
                  "remind_on": day.isoformat() if day and day > today else None,
                  "note": M.future_note(amount, None if sup else name)}
            self.store.log_event(shop["id"], "question_asked", {"intent": "set_reminder", "source": "future",
                                                                "understood_by": "rules", "unclear": []})
            await self._continue_q(shop, st)
            return
        if kind == "rmaan":   # the amount the supplier accepted for goods sent back
            intent = parts[5] if len(parts) > 5 and parts[5] in ("return", "refund") else "return"
            goods = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else amount
            p = E.Part(intent=intent, amount=amount, supplier=sup, name=name, raw=f"{name or ''} {amount}",
                       why="goods" if intent == "refund" else None, amounts=(goods,) if goods != amount else (),
                       note=M.partial_note(goods, amount) if goods > amount else None)
            self.store.log_event(shop["id"], "choice_made", {"kind": kind, "less_than_goods": goods > amount})
            await self._start_part(shop, p, source)
            return
        if kind in ("rcash_cut", "rcash_got"):   # goods back: cut from the baaki (a return) or cash in hand
            goods = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else amount
            note = M.partial_note(goods, amount) if goods > amount else None
            if kind == "rcash_cut":
                p = E.Part(intent="return", amount=amount, supplier=sup, name=name, raw=f"{name or ''} {amount}",
                           note=note)
            else:
                p = E.Part(intent="refund", amount=amount, supplier=sup, name=name, raw=f"{name or ''} {amount}",
                           why="goods", cash_checked=True, note=note)
            self.store.log_event(shop["id"], "choice_made", {"kind": kind})
            await self._start_part(shop, p, source)
            return
        intent = {"kind_bill": "bill", "rcv_bill": "bill", "rcv_refund": "refund", "fut_add": "udhaar",
                  "rfw_g": "refund", "rfw_o": "refund"}.get(kind, "payment")
        p = E.Part(intent=intent, amount=amount, supplier=sup, name=name, raw=f"{name or ''} {amount}",
                   why={"rfw_g": "goods", "rfw_o": "over"}.get(kind))
        self.store.log_event(shop["id"], "choice_made", {"kind": kind})
        await self._start_part(shop, p, source)

    # ------------------------------------------------------------ fixing saved entries
    async def _start_fix(self, shop, fix: "E.Fix", source: str, heard: str | None = None):
        await self._say_heard(shop, heard)
        if await self._fix_open_card(shop, fix, source):
            return
        recent = self._recent(shop)
        cands = recent
        ids = {str(fix.supplier["id"])} if fix.supplier else {str(c["id"]) for c in fix.candidates}
        if ids:
            cands = [e for e in cands if str(e["supplier_id"]) in ids]
        elif fix.name:
            cands = [e for e in cands if same_supplier(fix.name, e["supplier"] or "")]
        if fix.hint:
            cands = [e for e in cands if e["type"] == fix.hint]
        if fix.old_amount is not None:
            cands = [e for e in cands if int(e["amount"]) == int(fix.old_amount)]
        # 'maal lautaya, paisa wapas' is two rows but one event for the owner: offer it once (the pair follows)
        cands = [e for e in cands if not str(e.get("reason") or "").startswith("pair:")] or cands
        self.store.log_event(shop["id"], "fix_asked", {"action": fix.action, "source": source, "found": len(cands),
                                                       "has_supplier": bool(ids or fix.name),
                                                       "has_amount": fix.old_amount is not None})
        if not cands:
            older = self._older_matches(shop, fix, ids)
            if older:
                await self._offer_adjustment(shop, fix, older)
                return
            self.store.log_event(shop["id"], "fix_not_found", {"action": fix.action})
            await self._text(shop, M.fix_not_found(fix.old_amount))
            return
        precise = bool(ids or fix.name or fix.old_amount is not None)
        if len(cands) == 1 or not precise:
            await self._fix_card_for(shop, cands[0], fix.action, fix.new_amount, source)
            return
        if fix.action == "edit" and fix.new_amount is not None:
            rows = [(f"fsel:e:{e['type'][0]}:{e['id']}:{fix.new_amount}:{VIA.get(source, 'typed')}", *M.entry_row(e))
                    for e in cands[:10]]
        else:
            rows = [(f"fsel:u:{e['type'][0]}:{e['id']}", *M.entry_row(e)) for e in cands[:10]]
        await self._list(shop, M.FIX_PICK, M.LIST_BTN_ENTRY, rows)

    async def _fix_open_card(self, shop, fix: "E.Fix", source: str) -> bool:
        """'30 nahi 300' while the card with 30 is still open (not yet Sahi hai): change THAT card and show it again.
        20 Nov phone test: the bot answered 'pichhle 7 din mein 30 ki entry nahi', because only saved entries
        were searched. Open cards of the last 30 minutes come first; a saved entry is never touched here."""
        if fix.action != "edit" or fix.old_amount is None or fix.new_amount is None:
            return False
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        hit = next((e for e in self.store.pending_entries(shop["id"], since)
                    if e["amount"] is not None and int(e["amount"]) == int(fix.old_amount)), None)
        if hit is None:
            return False
        if hit["t"] == "b":
            self.store.set_bill_field(hit["id"], "total_amount", int(fix.new_amount))
        else:
            self.store.set_payment_fields(hit["id"], amount=int(fix.new_amount))
        self.store.log_event(shop["id"], "fix_open_card", {"t": hit["t"], "source": source})
        await self._show_card(shop, hit["t"], hit["id"])
        return True

    def _older_matches(self, shop, fix, ids):
        """Saved entries older than the 7-day edit window that fit the fix (supplier, kind, old amount)."""
        if not (ids or fix.name or fix.old_amount is not None):
            return []   # 'last wala galat hai' is always about a recent entry
        since = datetime(2000, 1, 1, tzinfo=timezone.utc)
        cut = self._fix_since()
        rows = [e for e in self.store.recent_entries(shop["id"], since) if e["created_at"] < cut]
        if ids:
            rows = [e for e in rows if str(e["supplier_id"]) in ids]
        elif fix.name:
            rows = [e for e in rows if same_supplier(fix.name, e["supplier"] or "")]
        if fix.hint:
            rows = [e for e in rows if e["type"] == fix.hint]
        if fix.old_amount is not None:
            rows = [e for e in rows if int(e["amount"]) == int(fix.old_amount)]
        return [e for e in rows if not str(e.get("reason") or "").startswith(("pair:", "sudhaar"))][:10]

    async def _offer_adjustment(self, shop, fix, older):
        new = "x" if fix.action == "undo" else (str(int(fix.new_amount)) if fix.new_amount is not None else "?")
        self.store.log_event(shop["id"], "adjustment_offered", {"action": fix.action, "found": len(older)})
        if len(older) == 1:
            await self._adjust_step(shop, older[0], new)
            return
        rows = [(f"adjc:{e['type'][0]}:{e['id']}:{new}", *M.entry_row(e)) for e in older[:10]]
        await self._list(shop, M.old_found(len(older)), M.LIST_BTN_ENTRY, rows)

    async def _adjust_step(self, shop, e, new: str):
        """new: an amount, 'x' (remove the entry), or '?' (ask the right amount first)."""
        if new == "?":
            self._set_state(shop, {"await": "adjamt", "t": e["type"][0], "id": e["id"]})
            await self._text(shop, M.ASK_ADJ_AMOUNT)
            return
        new_amount = None if new == "x" else int(new)
        if new_amount is not None and new_amount == int(e["amount"]):
            await self._text(shop, M.KEPT)
            return
        delta = self._adjust_delta(e, new_amount)
        before = Decimal(str(self._due_of(shop, e["supplier_id"])))
        await self._buttons(shop, M.adjust_card(e, new_amount, before, before + delta),
                            [(f"adj:{e['type'][0]}:{e['id']}:{new}", M.BTN_ADJ_YES), ("fix_no", M.BTN_ADJ_NO)])

    def _adjust_delta(self, e, new_amount) -> Decimal:
        """How much the supplier's baaki changes when an old entry is corrected to new_amount (None: removed)."""
        old = Decimal(str(e["amount"]))
        diff = (Decimal(0) if new_amount is None else Decimal(str(new_amount))) - old
        return self._effect("b" if e["type"] == "bill" else "p", e.get("kind"), diff)

    async def _on_adjust_value(self, shop, st, text):
        etype = {"b": "bill", "p": "payment"}.get(st["t"])
        e = self.store.get_entry(shop["id"], etype, st["id"])
        if e is None:
            self._set_state(shop, {})
            await self._gone(shop, st["t"], st["id"])
            return
        value = parse_amount_text(text)
        if value is None:
            await self._text(shop, M.BAD_VALUE_ANY["amount"])
            return
        self._set_state(shop, {})
        await self._adjust_step(shop, e, str(value))

    async def _on_adjust_button(self, shop, kind, rest):
        bits = rest.split(":")
        etype = {"b": "bill", "p": "payment"}.get(bits[0])
        e = self.store.get_entry(shop["id"], etype, bits[1]) if etype and len(bits) > 2 and _uuid(bits[1]) else None
        new = bits[2] if len(bits) > 2 else ""
        if e is None or not (new in ("x", "?") or new.isdigit()):
            await self._text(shop, M.ENTRY_GONE)
            return
        if kind == "adjc":
            await self._adjust_step(shop, e, new)
            return
        if new == "?" or not self._once(shop, f"adj:{e['id']}:{new}"):
            await self._text(shop, M.ENTRY_GONE)
            return
        delta = self._adjust_delta(e, None if new == "x" else int(new))
        if delta == 0:
            await self._text(shop, M.KEPT)
            return
        reason = f"sudhaar:{etype}:{e['id']}" + (":radd" if new == "x" else f":{new}")
        today = self._today()
        if e["type"] == "bill":   # a bill-side correction: more dues -> udhaar, fewer -> a return (credit note)
            kind2 = "udhaar" if delta > 0 else "return"
            b = self.store.create_bill(shop["id"], {"is_bill": True, "supplier_name": e["supplier"],
                                                    "total_amount": abs(delta), "bill_date": today.isoformat(),
                                                    "unsure_fields": []},
                                       {"model": "typed", "source": "typed", "raw_text": "sudhaar", "kind": kind2,
                                        "supplier_id": e["supplier_id"], "reason": reason})
            self.store.confirm_bill(b["id"], e["supplier_id"])
        else:                     # a payment-side correction: paid more -> a payment, paid less -> money back
            kind2 = "refund" if delta > 0 else "payment"
            pay = self.store.create_payment(shop["id"], e["supplier_id"], abs(delta), "text", "sudhaar", paid_on=today,
                                            supplier_name_raw=e["supplier"], kind=kind2, reason=reason)
            self.store.confirm_payment(pay["id"], e["supplier_id"])
        self.store.log_event(shop["id"], "entry_adjusted", {"entry": etype, "kind": kind2, "remove": new == "x",
                                                            "age_days": (today - _as_day(e["day"])).days})
        self._set_state(shop, {})
        self._forget(shop)
        await self._text(shop, M.adjusted(e["supplier"], self._due_of(shop, e["supplier_id"])))

    async def _fix_card_for(self, shop, e, action, new_amount, source):
        t = e["type"][0]
        if action == "edit" and new_amount is not None and int(new_amount) != int(e["amount"]):
            via = VIA.get(source, "typed")
            await self._buttons(shop, M.fix_card(e, "amount", M.inr(e["amount"]), M.inr(new_amount)),
                                [(f"fix_ok:{t}:{e['id']}:amount:{int(new_amount)}:{via}", M.BTN_FIX_YES),
                                 ("fix_no", M.BTN_FIX_NO)])
            return
        await self._buttons(shop, M.undo_card(e), [(f"ued:{t}:{e['id']}", M.BTN_UNDO_EDIT),
                                                    (f"udel:{t}:{e['id']}", M.BTN_UNDO_DELETE),
                                                    ("ukeep", M.BTN_UNDO_KEEP)])

    async def _on_fix_button(self, shop, kind, rest):
        if kind in ("fix_no", "ukeep"):
            self.store.log_event(shop["id"], "fix_declined", {})
            self._set_state(shop, {})
            await self._text(shop, M.KEPT)
            return
        if kind == "fsel":
            action, _, rest2 = rest.partition(":")
            bits = rest2.split(":")
            etype = {"b": "bill", "p": "payment"}.get(bits[0])
            e = self._own_entry(shop, etype, bits[1] if len(bits) > 1 else "")
            if e is None:
                await self._gone(shop, bits[0], bits[1] if len(bits) > 1 else "")
                return
            new = int(bits[2]) if action == "e" and len(bits) > 2 and bits[2].isdigit() else None
            await self._fix_card_for(shop, e, "edit" if new is not None else "undo", new,
                                     bits[3] if len(bits) > 3 else "typed")
            return
        bits = rest.split(":")
        etype = {"b": "bill", "p": "payment"}.get(bits[0])
        e = self._own_entry(shop, etype, bits[1] if len(bits) > 1 else "")
        if e is None:
            await self._gone(shop, bits[0], bits[1] if len(bits) > 1 else "")
            return
        if kind == "udel":
            self.store.delete_entry(shop["id"], etype, e["id"], "button")
            pair = self.store.paired(shop["id"], etype, e["id"])
            if pair:   # 'maal lautaya, paisa wapas' was saved as two entries: they go together
                self.store.delete_entry(shop["id"], pair[0], pair[1], "button")
            self.store.log_event(shop["id"], "entry_deleted", {"entry": etype, "source": e.get("source"),
                                                               "with_pair": bool(pair)})
            self._set_state(shop, {})
            self._forget(shop)
            await self._text(shop, M.deleted(e["supplier"], self._due_of(shop, e["supplier_id"])))
            return
        if kind == "ued":
            rows = M.EDIT_ROWS_FIX_BILL if etype == "bill" else M.EDIT_ROWS_FIX_PAYMENT
            await self._list(shop, M.EDIT_WHAT, M.LIST_BTN_EDIT,
                             [(f"ef:{bits[0]}:{e['id']}:{f}", title, d) for f, title, d in rows])
            return
        if kind == "ef":
            field = bits[2] if len(bits) > 2 else ""
            if field not in {f for f, _, _ in (M.EDIT_ROWS_FIX_BILL if etype == "bill" else M.EDIT_ROWS_FIX_PAYMENT)}:
                await self._text(shop, M.ENTRY_GONE)
                return
            self._set_state(shop, {"await": "efield", "t": bits[0], "id": e["id"], "field": field, "via": "button"})
            await self._text(shop, M.ASK_VALUE["supplier_name" if field == "supplier_id" else field])
            return
        if kind == "fix_ok":
            field, value, via = bits[2], bits[3], bits[4] if len(bits) > 4 else "button"
            await self._apply_fix(shop, etype, e, field, value, via if via in VIA.values() else "button")

    async def _on_entry_field_value(self, shop, st, text):
        etype = {"b": "bill", "p": "payment"}.get(st["t"])
        e = self._own_entry(shop, etype, st["id"])
        field = st["field"]
        if e is None:
            self._set_state(shop, {})
            await self._gone(shop, st["t"], st["id"])
            return
        t = st["t"]
        if field == "supplier_id":
            name = re.sub(r"\s+", " ", text).strip()[:60]
            match, cands = E.strict_match(Q.tokens(name), self._suppliers(shop))
            if not norm_supplier(name):
                await self._text(shop, M.BAD_VALUE_ANY["supplier_name"])
                return
            self._set_state(shop, {})
            if match:
                value, shown = match["id"], match["name"]
            elif cands:
                rows = [(f"fix_ok:{t}:{e['id']}:supplier_id:{c['id']}:button", c["name"][:24], "") for c in cands[:9]]
                await self._list(shop, M.FIX_PICK, M.LIST_BTN_SUPPLIER, rows)
                return
            else:
                value, shown = f"new~{_clean_name(name)}", name + M.NEW_SUPPLIER_TAG
            await self._buttons(shop, M.fix_card(e, field, e["supplier"], shown),
                                [(f"fix_ok:{t}:{e['id']}:supplier_id:{value}:{st.get('via', 'button')}", M.BTN_FIX_YES),
                                 ("fix_no", M.BTN_FIX_NO)])
            return
        value = self._parse_value(field, text)
        if value is None:
            await self._text(shop, M.BAD_VALUE_ANY[field])
            return
        self._set_state(shop, {})
        current = {"amount": int(e["amount"]), "date": e["day"], "credit_days": e.get("credit_days") or 0}.get(field)
        if current is not None and (value or 0 if field == "credit_days" else value) == current:
            await self._text(shop, M.KEPT)  # the same value: nothing to change
            return
        if field == "amount":
            old, new, raw = M.inr(e["amount"]), M.inr(value), str(int(value))
        elif field == "date":
            old, new, raw = M.nice_date(e["day"]), M.nice_date(value), value.isoformat()
        else:
            old, new, raw = M.credit_label(e.get("credit_days")), M.credit_label(value), str(value)
        await self._buttons(shop, M.fix_card(e, field, old, new),
                            [(f"fix_ok:{t}:{e['id']}:{field}:{raw}:{st.get('via', 'button')}", M.BTN_FIX_YES),
                             ("fix_no", M.BTN_FIX_NO)])

    async def _apply_fix(self, shop, etype, e, field, value, via):
        if field == "amount":
            if not value.isdigit() or int(value) <= 0:
                await self._text(shop, M.ENTRY_GONE)
                return
            new = int(value)
        elif field == "date":
            try:
                new = date.fromisoformat(value).isoformat()
            except ValueError:
                await self._text(shop, M.ENTRY_GONE)
                return
        elif field == "credit_days":
            if not value.isdigit():
                await self._text(shop, M.ENTRY_GONE)
                return
            new = int(value)
        elif field == "supplier_id":
            if value.startswith("new~"):
                sup = self.store.find_or_create_supplier(shop["id"], value[4:])
                self._forget(shop)
                self.store.log_event(shop["id"], "supplier_created", {"entry": etype, "via": "fix"})
            else:
                sup = self._own_supplier(shop, value) if _uuid(value) else None
            if sup is None:
                await self._text(shop, M.ENTRY_GONE)
                return
            new = sup["id"]
        else:
            await self._text(shop, M.ENTRY_GONE)
            return
        self.store.edit_entry(shop["id"], etype, e["id"], field, new, via)
        pair = self.store.paired(shop["id"], etype, e["id"]) if field in ("amount", "supplier_id", "date") else None
        if pair:   # the return and the refund of one 'maal lautaya, paisa wapas' stay equal
            self.store.edit_entry(shop["id"], pair[0], pair[1], field, new, via)
        self.store.log_event(shop["id"], "entry_edited", {"entry": etype, "field": field, "via": via,
                                                          "with_pair": bool(pair)})
        self._set_state(shop, {})
        self._forget(shop)
        after = self.store.get_entry(shop["id"], etype, e["id"]) or e
        msgs = [M.fixed(after["supplier"], self._due_of(shop, after["supplier_id"]))]
        if field == "supplier_id" and str(after["supplier_id"]) != str(e["supplier_id"]):
            msgs.append(M.due_now(e["supplier"], self._due_of(shop, e["supplier_id"])))
        await self._text(shop, "\n".join(msgs))

    # ------------------------------------------------------------ proof photos
    def _proof_match(self, shop, photo_bill):
        """A saved voice / typed bill (last 7 days, no proof yet) that this freshly READ photo looks like:
        same supplier and amount; or same supplier said in the last 30 minutes with a total within 20% (the owner may
        have said 5000 for a 5,180 bill); or same amount in the last 30 minutes when the photo's supplier name cannot be
        matched. Anything else is a new bill: a photo of another bill never gets the proof question."""
        cands = self.store.proofless_bills(shop["id"], self._fix_since())
        if not cands:
            return None
        cands = [c for c in cands if not self.store.event_exists(shop["id"], "proof_declined", "bill_id", c["id"])]
        raw = photo_bill.get("supplier_name_raw") or ""
        total = photo_bill.get("total_amount")
        fresh = datetime.now(timezone.utc) - timedelta(minutes=M.PROOF_WINDOW_MINUTES)
        suppliers = self._suppliers(shop)
        from bot.conversation import match_supplier
        m = match_supplier(raw, suppliers) if raw else None
        for c in cands:   # newest first
            csup = next((x for x in suppliers if str(x["id"]) == str(c["supplier_id"])), None)
            if m is not None:
                same_sup = str(m["id"]) == str(c["supplier_id"])
            else:
                same_sup = bool(raw and csup and E.close_suppliers(raw, [csup]))
            said = Decimal(str(c["total_amount"]))
            same_amt = total is not None and abs(Decimal(str(total)) - said) <= 1
            near_amt = total is None or abs(Decimal(str(total)) - said) <= max(Decimal(1), said / 5)   # within 20%
            is_fresh = c.get("confirmed_at") is not None and c["confirmed_at"] >= fresh
            if (same_sup and same_amt) or (same_sup and is_fresh and near_amt) or (same_amt and is_fresh and m is None):
                return c
        return None

    async def _ask_proof_read(self, shop, cand, photo_bill):
        self.store.log_event(shop["id"], "proof_asked", {"bill_id": cand["id"], "read_first": True})
        await self._buttons(shop, M.proof_match_ask(cand, photo_bill.get("total_amount"), self._today()),
                            [(f"pfy:{cand['id']}:{photo_bill['id']}", M.BTN_PROOF_ATTACH),
                             (f"pfn:{cand['id']}:{photo_bill['id']}", M.BTN_PROOF_NEWBILL)])

    async def _on_proof_read_button(self, shop, kind, rest):
        cid, _, pid = rest.partition(":")
        photo = self._own_pending_bill(shop, pid) if _uuid(pid) else None
        cand = self.store.get_bill(cid) if _uuid(cid) else None
        own = cand is not None and str(cand["shop_id"]) == str(shop["id"])
        if photo is None:
            await self._gone(shop, "b", pid)
            return
        if kind == "pfn":
            if not self._once(shop, f"pfn:{pid}"):
                await self._text(shop, M.ENTRY_GONE)
                return
            if own:
                self.store.log_event(shop["id"], "proof_declined", {"bill_id": cid, "read_first": True})
            await self._next_step(shop, photo, missing_from_ai=True)
            return
        if not own or cand["status"] not in ("confirmed", "corrected") or not self.store.claim_proof(cid):
            await self._text(shop, M.ENTRY_GONE)
            return
        self.store.attach_proof(cid, photo.get("image_path"), photo.get("total_amount"), photo.get("model"))
        self.store.reject_bill(pid)       # the photo became proof: no second bill
        self.store.detach_photo(pid)      # ... and it now belongs to the proof only
        self.store.log_event(shop["id"], "photo_used_as_proof", {"bill_id": cid, "photo_bill_id": pid, "via": "button"})
        await self._proof_result(shop, cand, photo.get("total_amount"))

    async def _proof_result(self, shop, bill, total):
        """After a photo was attached as proof: same total -> done; another total -> the owner picks; unread -> say so."""
        said = int(norm_amount(bill["total_amount"]))
        if total is None:
            self.store.log_event(shop["id"], "proof_attached", {"bill_id": bill["id"], "match": None})
            await self._text(shop, M.PROOF_UNREAD)
            return
        read = int(norm_amount(total))
        if abs(read - said) <= 1:
            self.store.log_event(shop["id"], "proof_attached", {"bill_id": bill["id"], "match": True})
            await self._text(shop, M.proof_ok(said))
            return
        self.store.log_event(shop["id"], "proof_mismatch", {"bill_id": bill["id"]})
        await self._buttons(shop, M.proof_mismatch(said, read),
                            [(f"pkeep:{bill['id']}", M.inr(said)[:20]), (f"pfix:{bill['id']}:{read}", M.inr(read)[:20]),
                             (f"pown:{bill['id']}", M.BTN_PROOF_OWN)])

    async def _proof_from_text(self, shop, text, suppliers) -> bool:
        """'ye Sharma ke bill ka proof hai' (after sending a photo): the last photo becomes that bill's proof."""
        photo = self.store.latest_photo_bill(shop["id"], datetime.now(timezone.utc) - timedelta(hours=2))
        if photo is None:
            await self._text(shop, M.PROOF_NO_PHOTO)
            return True
        toks, orig = E.tokenize(text)
        match, cands, name = E.find_supplier(toks, orig, set(), suppliers)
        amounts = [a.value for a in N.find_amounts(toks)]
        targets = [c for c in self.store.proofless_bills(shop["id"], self._fix_since())]
        if match:
            targets = [c for c in targets if str(c["supplier_id"]) == str(match["id"])]
        elif cands:
            targets = [c for c in targets if str(c["supplier_id"]) in {str(x["id"]) for x in cands}]
        elif name:
            targets = [c for c in targets if same_supplier(name, c.get("supplier") or "")]
        if amounts:
            targets = [c for c in targets if any(abs(Decimal(str(c["total_amount"])) - Decimal(str(a))) <= 1
                                                 for a in amounts)] or targets
        if not targets:
            self.store.log_event(shop["id"], "proof_text_no_target", {})
            await self._text(shop, M.proof_no_target(match["name"] if match else name))
            return True
        bill = targets[0]
        if not self.store.claim_proof(bill["id"]):
            await self._text(shop, M.ENTRY_GONE)
            return True
        self.store.attach_proof(bill["id"], photo.get("image_path"), photo.get("total_amount"), photo.get("model"))
        if photo["status"] == "pending_confirm":
            self.store.reject_bill(photo["id"])
        if photo["status"] != "confirmed" and photo["status"] != "corrected":
            self.store.detach_photo(photo["id"])
        self.store.log_event(shop["id"], "photo_used_as_proof", {"bill_id": bill["id"], "photo_bill_id": photo["id"],
                                                                 "via": "text"})
        self._set_state(shop, {})
        await self._proof_result(shop, bill, photo.get("total_amount"))
        if photo["status"] in ("confirmed", "corrected"):   # it had been saved as a bill of its own: a duplicate now
            e = self.store.get_entry(shop["id"], "bill", photo["id"])
            if e:
                await self._buttons(shop, M.proof_dup(e), [(f"udel:b:{e['id']}", M.BTN_UNDO_DELETE),
                                                          ("ukeep", M.BTN_UNDO_KEEP)])
        return True

    async def _on_proof_button(self, shop, kind, rest):
        bill_id, _, media_id = rest.partition(":")
        bill = self.store.get_bill(bill_id) if _uuid(bill_id) else None
        own = bill is not None and str(bill["shop_id"]) == str(shop["id"])
        if kind == "proof_no":
            # once per photo question, and never after this photo (or another) became the bill's proof
            if not own or bill.get("proof_image_path") or not self._once(shop, f"proof:{bill_id}:{media_id}"):
                await self._text(shop, M.ENTRY_GONE)
                return
            self.store.log_event(shop["id"], "proof_declined", {"bill_id": bill_id})
            await self._process_photo(shop, media_id)
            return
        if kind == "proof_yes":
            if not own or bill["status"] not in ("confirmed", "corrected"):
                await self._text(shop, M.ENTRY_GONE)
                return
            if not await self._ai_allowed(shop):
                return  # nothing used up: the owner can tap again tomorrow
            if not self.store.claim_proof(bill_id):  # one statement: two quick taps cannot both pass
                await self._text(shop, M.ENTRY_GONE)
                return
            self._once(shop, f"proof:{bill_id}:{media_id}")  # 'Nahi, naya bill' on this question is now closed
            await self._read_proof(shop, bill, media_id)
            return
        if not own:
            await self._text(shop, M.ENTRY_GONE)
            return
        e = self._own_entry(shop, "bill", bill_id)
        if e is None:
            await self._text(shop, M.ENTRY_GONE)
            return
        if kind == "pkeep":
            self.store.log_event(shop["id"], "proof_kept_owner_amount", {"bill_id": bill_id})
            await self._text(shop, M.proof_kept(e["amount"]))
        elif kind == "pfix" and media_id.isdigit():
            await self._apply_fix(shop, "bill", e, "amount", media_id, "proof")
        elif kind == "pown":
            self._set_state(shop, {"await": "efield", "t": "b", "id": bill_id, "field": "amount", "via": "proof"})
            await self._text(shop, M.ASK_VALUE["amount"])

    async def _read_proof(self, shop, bill, media_id):
        """The proof slot is already claimed (store.claim_proof); it is released if nothing gets attached."""
        await self._text(shop, M.PROOF_READING)
        try:
            image = await self.wa.download_media(media_id)
            if len(image) > self.limits.max_image_mb * 1024 * 1024:
                self.store.release_proof(bill["id"])
                await self._text(shop, M.PHOTO_TOO_BIG)
                return
        except Exception as err:
            log.exception("proof download failed")
            self.store.release_proof(bill["id"])
            self.store.log_event(shop["id"], "extract_failed", {"error": type(err).__name__, "proof": True})
            await self._text(shop, M.READ_FAILED)
            return
        try:
            path = await asyncio.to_thread(self.photos.save, shop["shop_code"], image)
        except Exception:
            log.exception("photo storage failed")
            path = None
        total, model = None, None
        try:
            self.store.log_event(shop["id"], "ai_call", {"kind": "proof"})
            if hasattr(self.extractor, "read"):
                ex = await asyncio.to_thread(self.extractor.read, image, await self._ai_quietly_allowed(shop))
            else:
                ex = await asyncio.to_thread(self.extractor, image)
            for _ in (ex.extra or {}).get("calls", [])[1:]:
                self.store.log_event(shop["id"], "ai_call", {"kind": "proof_escalation"})
            total = ex.fields.get("total_amount") if ex.fields.get("is_bill", True) else None
            model = ex.model
        except Exception as err:
            log.exception("proof reading failed")
            self.store.log_event(shop["id"], "extract_failed", {"error": type(err).__name__, "proof": True})
        self.store.attach_proof(bill["id"], path, total, model)
        await self._proof_result(shop, bill, total)

class _Lazy:
    """A list read on first use (truthy without reading, so 'recent or []' costs nothing until it is iterated)."""

    def __init__(self, load):
        self._load, self._rows = load, None

    def _get(self):
        if self._rows is None:
            self._rows = self._load()
        return self._rows

    def __bool__(self):
        return True

    def __iter__(self):
        return iter(self._get())

    def __len__(self):
        return len(self._get())

    def __getitem__(self, i):
        return self._get()[i]


def _as_day(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(IST).date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
