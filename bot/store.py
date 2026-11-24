"""Where the bot keeps its data.

Two interchangeable stores with the same methods:
  MemoryStore  everything in RAM (lost on restart). For tests and for trying the bot
               on the free test number before Supabase exists.
  PgStore      Postgres / Supabase, using db/schema.sql. Used whenever DATABASE_URL is set.

Bills and payments both wait as 'pending_confirm' until the owner taps Confirm; only confirmed rows count anywhere
(dues, answers, summaries, the experiment). A removed entry is never hard-deleted: its status becomes 'deleted' and
the change is written to entry_edits, like every edit made after confirming.
"""

from __future__ import annotations

import contextvars
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from bot.extract import FIELDS, norm_supplier

IST = timezone(timedelta(hours=5, minutes=30))
BILL_COLUMN = {f: f for f in FIELDS} | {"supplier_name": "supplier_name_raw"}
SHOP_UPDATABLE = {"status", "consent_at", "onboarded_at", "state", "segment", "experiment_group",
                  "shop_name", "eval_consent"}


_last_now = [datetime.min.replace(tzinfo=timezone.utc)]


def _now() -> datetime:
    """Strictly increasing: on Windows the clock ticks about every 16 ms, so two entries saved in a row could get the
    same time and 'the newest entry' (fixes, proof photos) would pick the wrong one. Postgres stamps its own times."""
    t = datetime.now(timezone.utc)
    if t <= _last_now[0]:
        t = _last_now[0] + timedelta(microseconds=1)
    _last_now[0] = t
    return t


def _as_text(value) -> str | None:
    return None if value is None else str(value)


def _cast(field: str, value):
    """Text from bill_fields -> the bills column type."""
    if value is None:
        return None
    if field == "total_amount":
        return Decimal(str(value))
    if field in ("bill_date", "due_date"):
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    return str(value)


CONFIRMED = ("confirmed", "corrected")
PROOF_PENDING = "pending"  # proof_image_path while a proof photo is being read (claimed by one tap)
BILL_META = ("image_path", "model", "prompt_version", "input_tokens", "output_tokens", "latency_ms", "source", "raw_text",
             "credit_days", "new_supplier", "supplier_id", "kind", "reason")
BILL_KINDS = ("bill", "udhaar", "return")     # a return / credit note LOWERS the dues
PAYMENT_KINDS = ("payment", "refund")         # a refund (money back from the supplier) RAISES the dues again


def _bill_sign(b) -> int:
    return -1 if b.get("kind") == "return" else 1


def _pay_sign(p) -> int:
    return -1 if p.get("kind") == "refund" else 1
EDITABLE = {"bill": {"amount", "supplier_id", "date", "credit_days", "invoice_number"},
            "payment": {"amount", "supplier_id", "date"}}
PENDING_PAYMENT_FIELDS = {"supplier_id", "supplier_name_raw", "new_supplier", "amount", "paid_on", "reason"}


def _edit_text(field, value) -> str | None:
    """How a value is written to entry_edits: amounts always with paise ('2800.00'), the same on both stores."""
    if value is None:
        return None
    if field == "amount":
        return f"{Decimal(str(value)):.2f}"
    return str(value)


def due_from(bill_date, created_at, credit_days):
    """Due date = bill date (or the day the bill reached the bot) + credit days."""
    if credit_days is None:
        return None
    start = bill_date or created_at.astimezone(IST).date()
    return start + timedelta(days=int(credit_days))


def today_ist() -> date:
    return datetime.now(IST).date()


def _week_summary(n_bills, billed, paid, dues) -> dict:
    top = max(dues, key=lambda r: r["due"], default=None)
    return {"bills": n_bills, "billed": billed, "paid": paid,
            "total_due": sum((r["due"] for r in dues), Decimal(0)),
            "top_supplier": top["supplier"] if top and top["due"] > 0 else None,
            "top_due": top["due"] if top and top["due"] > 0 else Decimal(0)}


# =============================================================== in memory
class MemoryStore:
    def __init__(self):
        self.shops: dict[str, dict] = {}
        self.suppliers: dict[str, dict] = {}
        self.messages: list[dict] = []
        self._seen: set[str] = set()
        self.bills: dict[str, dict] = {}
        self.bill_fields: dict[tuple[str, str], dict] = {}
        self.payments: list[dict] = []
        self.events: list[dict] = []
        self.reminders: dict[str, dict] = {}
        self.edits: list[dict] = []

    # shops
    def get_shop_by_wa(self, wa_id):
        return next((dict(s) for s in self.shops.values() if s["wa_id"] == wa_id), None)

    def get_shop(self, shop_id):
        return dict(self.shops[shop_id]) if shop_id in self.shops else None

    def create_shop(self, wa_id, shop_code=None, segment=None, status="invited", shop_name=None, prefix="T"):
        sid = str(uuid.uuid4())
        # the next free number for the prefix (T test phone, W self-signup). Not the count: codes must not repeat
        prefix = prefix if re.fullmatch(r"[A-Z]", prefix or "") else "T"
        used = [int(x["shop_code"][1:]) for x in self.shops.values()
                if re.fullmatch(prefix + r"\d+", x["shop_code"] or "")]
        code = shop_code or f"{prefix}{max(used, default=0) + 1:02d}"
        self.shops[sid] = {"id": sid, "shop_code": code, "wa_id": wa_id, "segment": segment, "status": status,
                           "experiment_group": None, "consent_at": None, "onboarded_at": None,
                           "state": {}, "shop_name": shop_name, "eval_consent": False,
                           "created_at": _now()}
        return dict(self.shops[sid])

    def update_shop(self, shop_id, **fields):
        bad = set(fields) - SHOP_UPDATABLE
        if bad:
            raise ValueError(f"cannot update {bad}")
        self.shops[shop_id].update(fields)

    def ping(self) -> bool:
        return True

    # messages
    def record_message(self, shop_id, direction, wa_message_id, mtype, body=None, media_id=None) -> bool:
        if wa_message_id:
            if wa_message_id in self._seen:
                return False
            self._seen.add(wa_message_id)
        self.messages.append({"shop_id": shop_id, "direction": direction, "wa_message_id": wa_message_id,
                              "type": mtype, "body": body, "media_id": media_id, "created_at": _now()})
        return True

    # bills
    def create_bill(self, shop_id, fields: dict, meta: dict) -> dict:
        bid = str(uuid.uuid4())
        bill = {"id": bid, "shop_id": shop_id, "supplier_id": None, "status": "pending_confirm",
                "created_at": _now(), "confirmed_at": None, "photo_deleted_at": None, "image_path": None,
                "source": None, "raw_text": None, "credit_days": None, "new_supplier": False, "proof_image_path": None,
                "proof_total": None, "proof_model": None, "proof_deleted_at": None, "deleted_at": None, "kind": "bill",
                "reason": None,
                **{k: v for k, v in meta.items() if k in BILL_META and v is not None}}
        unsure = set(fields.get("unsure_fields") or [])
        for f in FIELDS:
            bill[BILL_COLUMN[f]] = _cast(f, fields.get(f))
            self.bill_fields[(bid, f)] = {"ai_value": _as_text(fields.get(f)), "final_value": None,
                                          "ai_unsure": f in unsure}
        self.bills[bid] = bill
        return self.get_bill(bid)

    def get_bill(self, bill_id):
        if bill_id not in self.bills:
            return None
        bill = dict(self.bills[bill_id])
        bill["unsure"] = [f for f in FIELDS if self.bill_fields[(bill_id, f)]["ai_unsure"]]
        bill["checked"] = [f for f in FIELDS if self.bill_fields[(bill_id, f)]["final_value"] is not None]  # owner set it
        return bill

    def set_bill_field(self, bill_id, field, value):
        self.bills[bill_id][BILL_COLUMN[field]] = _cast(field, value)
        self.bill_fields[(bill_id, field)]["final_value"] = _as_text(value)

    def confirm_bill(self, bill_id, supplier_id) -> str:
        corrected = False
        for f in FIELDS:
            row = self.bill_fields[(bill_id, f)]
            if row["final_value"] is None:
                row["final_value"] = row["ai_value"]
            corrected |= row["final_value"] != row["ai_value"]
        status = "corrected" if corrected else "confirmed"
        self.bills[bill_id].update(status=status, supplier_id=supplier_id, confirmed_at=_now())
        return status

    def corrected_fields(self, bill_id) -> list[str]:
        return [f for f in FIELDS if self.bill_fields[(bill_id, f)]["final_value"] != self.bill_fields[(bill_id, f)]["ai_value"]]

    def reject_bill(self, bill_id):
        self.bills[bill_id]["status"] = "rejected"

    def set_bill_supplier(self, bill_id, supplier_id=None, new_supplier=False):
        """A pending bill: the supplier the owner picked (or 'new supplier' tapped). Saved only on Confirm."""
        self.bills[bill_id].update(supplier_id=supplier_id, new_supplier=bool(new_supplier))

    def set_bill_credit_days(self, bill_id, days):
        b = self.bills[bill_id]
        b["credit_days"] = days
        due = due_from(b.get("bill_date"), b["created_at"], days)
        self.set_bill_field(bill_id, "due_date", due.isoformat() if due else None)

    def old_photos(self, before) -> list[dict]:
        """Photos older than `before`, from shops that did NOT agree to keep them for the reading test.
        kind 'bill' = the bill photo; 'proof' = a photo sent later as proof of a voice / typed bill."""
        out = []
        for b in self.bills.values():
            shop = self.shops.get(b["shop_id"])
            if not shop or shop.get("eval_consent") or b["created_at"] >= before:
                continue
            if b.get("image_path"):
                out.append({"bill_id": b["id"], "image_path": b["image_path"], "shop_code": shop["shop_code"],
                            "kind": "bill"})
            if b.get("proof_image_path") and b["proof_image_path"] != PROOF_PENDING:
                out.append({"bill_id": b["id"], "image_path": b["proof_image_path"], "shop_code": shop["shop_code"],
                            "kind": "proof"})
        return out

    def clear_photo(self, bill_id, kind="bill"):
        if kind == "proof":
            self.bills[bill_id].update(proof_image_path=None, proof_deleted_at=_now())
        else:
            self.bills[bill_id].update(image_path=None, photo_deleted_at=_now())

    def claim_proof(self, bill_id) -> bool:
        """Reserve the bill's proof slot before the slow download / reading: two quick taps cannot both attach."""
        b = self.bills.get(bill_id)
        if b is None or b.get("proof_image_path"):
            return False
        b["proof_image_path"] = PROOF_PENDING
        return True

    def release_proof(self, bill_id):
        if self.bills[bill_id].get("proof_image_path") == PROOF_PENDING:
            self.bills[bill_id]["proof_image_path"] = None

    def attach_proof(self, bill_id, path, total, model):
        self.bills[bill_id].update(proof_image_path=path, proof_total=None if total is None else Decimal(str(total)),
                                   proof_model=model)

    def latest_proofless_bill(self, shop_id, since):
        """The newest voice / typed bill confirmed since `since` that has no proof photo yet."""
        rows = [b for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED
                and b.get("kind", "bill") == "bill"
                and b.get("source") in ("voice", "typed") and not b.get("proof_image_path")
                and b.get("confirmed_at") and b["confirmed_at"] >= since]
        rows.sort(key=lambda b: b["confirmed_at"], reverse=True)
        return self._bill_view(rows[0]) if rows else None

    def _bill_view(self, b):
        return self.get_bill(b["id"]) | {"supplier": self._name(b["supplier_id"]) if b.get("supplier_id") else None}

    def proofless_bills(self, shop_id, since) -> list[dict]:
        """Voice / typed bills confirmed since `since` with no proof photo yet, newest first."""
        rows = [b for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED
                and b.get("kind", "bill") == "bill" and b.get("source") in ("voice", "typed")
                and not b.get("proof_image_path") and b.get("confirmed_at") and b["confirmed_at"] >= since]
        rows.sort(key=lambda b: b["confirmed_at"], reverse=True)
        return [self._bill_view(b) for b in rows]

    def detach_photo(self, bill_id):
        """The photo of a cancelled bill became another bill's proof: this row no longer holds it."""
        self.bills[bill_id]["image_path"] = None

    def latest_photo_bill(self, shop_id, since):
        """The newest bill made from a photo / PDF since `since` (pending, confirmed or cancelled), with its photo."""
        rows = [b for b in self.bills.values() if b["shop_id"] == shop_id and b.get("image_path")
                and b.get("source") not in ("voice", "typed") and b["created_at"] >= since and b["status"] != "deleted"]
        rows.sort(key=lambda b: b["created_at"], reverse=True)
        return self._bill_view(rows[0]) if rows else None

    def bill_by_invoice(self, shop_id, supplier_id, invoice, exclude_id=None):
        """A confirmed bill of this supplier with the same invoice number (any date): the same paper bill twice."""
        key = str(invoice or "").strip().upper()
        if not key or not supplier_id:
            return None
        hit = next((b for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED
                    and b["supplier_id"] == supplier_id and str(b.get("invoice_number") or "").strip().upper() == key
                    and b["id"] != exclude_id), None)
        return None if hit is None else self.get_entry(shop_id, "bill", hit["id"])

    def paired(self, shop_id, entry_type, entry_id):
        """'Maal lautaya, paisa wapas mila' saves a refund AND a return; each one finds the other by bills.reason."""
        if entry_type == "payment":
            b = next((b for b in self.bills.values() if b["shop_id"] == shop_id and b.get("reason") == f"pair:{entry_id}"
                      and b["status"] in CONFIRMED), None)
            return ("bill", b["id"]) if b else None
        b = self.bills.get(entry_id)
        reason = (b or {}).get("reason") or ""
        if reason.startswith("pair:"):
            p = self._pay(reason[5:])
            return ("payment", p["id"]) if p and p.get("status") == "confirmed" else None
        return None

    def shop_counts(self, shop_id) -> dict:
        return {"suppliers": len(self.list_suppliers(shop_id)),
                "bills": sum(1 for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED),
                "payments": len(self._paid(shop_id))}

    # suppliers and payments
    def list_suppliers(self, shop_id):
        return [dict(s) for s in self.suppliers.values() if s["shop_id"] == shop_id]

    def find_or_create_supplier(self, shop_id, name):
        norm = norm_supplier(name)
        for s in self.suppliers.values():
            if s["shop_id"] == shop_id and s["name_norm"] == norm:
                return dict(s)
        sid = str(uuid.uuid4())
        self.suppliers[sid] = {"id": sid, "shop_id": shop_id, "name": name.strip(), "name_norm": norm}
        return dict(self.suppliers[sid])

    def add_payment(self, shop_id, supplier_id, amount, source, raw_text=None):
        """A payment saved straight away (seed data, tests). The bot itself uses create_payment + confirm_payment."""
        p = self.create_payment(shop_id, supplier_id, amount, source, raw_text)
        self.confirm_payment(p["id"], supplier_id)

    def create_payment(self, shop_id, supplier_id, amount, source, raw_text=None, paid_on=None,
                       supplier_name_raw=None, new_supplier=False, kind="payment", reason=None) -> dict:
        if kind not in PAYMENT_KINDS:
            raise ValueError(kind)
        pid = str(uuid.uuid4())
        self.payments.append({"id": pid, "shop_id": shop_id, "supplier_id": supplier_id,
                              "amount": Decimal(str(amount)), "source": source, "raw_text": raw_text,
                              "paid_on": paid_on or today_ist(), "created_at": _now(), "status": "pending_confirm",
                              "supplier_name_raw": supplier_name_raw, "new_supplier": bool(new_supplier),
                              "confirmed_at": None, "deleted_at": None, "kind": kind, "reason": reason})
        return self.get_payment(pid)

    def _pay(self, pid):
        return next((p for p in self.payments if p.get("id") == pid), None)

    def get_payment(self, payment_id):
        p = self._pay(payment_id)
        if p is None:
            return None
        return dict(p) | {"supplier": self._name(p["supplier_id"]) if p["supplier_id"] else None}

    def set_payment_fields(self, payment_id, **fields):
        bad = set(fields) - PENDING_PAYMENT_FIELDS
        if bad:
            raise ValueError(f"cannot set {bad}")
        p = self._pay(payment_id)
        if "amount" in fields:
            fields["amount"] = Decimal(str(fields["amount"]))
        p.update(fields)

    def confirm_payment(self, payment_id, supplier_id):
        self._pay(payment_id).update(status="confirmed", supplier_id=supplier_id, confirmed_at=_now())

    def cancel_payment(self, payment_id):
        self._pay(payment_id)["status"] = "cancelled"

    def _paid(self, shop_id=None, supplier_id=None, kinds=PAYMENT_KINDS):
        """Confirmed payments (and refunds, unless kinds says otherwise)."""
        return [p for p in self.payments if p.get("status", "confirmed") == "confirmed" and p.get("kind", "payment") in kinds
                and (shop_id is None or p["shop_id"] == shop_id) and (supplier_id is None or p["supplier_id"] == supplier_id)]

    def pending_count(self, shop_id, since) -> int:
        """Bills and payments still waiting for the owner's tap, made since `since`."""
        return (sum(1 for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] == "pending_confirm"
                    and b["created_at"] >= since)
                + sum(1 for p in self.payments if p["shop_id"] == shop_id and p.get("status") == "pending_confirm"
                      and p["created_at"] >= since))

    def pending_entries(self, shop_id, since) -> list[dict]:
        """Open cards (not yet confirmed), newest first: {'t': 'b' | 'p', 'id', 'amount', 'created_at'}."""
        rows = [{"t": "b", "id": b["id"], "amount": b["total_amount"], "created_at": b["created_at"]}
                for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] == "pending_confirm"
                and b["created_at"] >= since]
        rows += [{"t": "p", "id": p["id"], "amount": p["amount"], "created_at": p["created_at"]}
                 for p in self.payments if p["shop_id"] == shop_id and p.get("status") == "pending_confirm"
                 and p["created_at"] >= since]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    # fixes after confirming: recent entries, edits, removal (every change goes to the edit history)
    def recent_entries(self, shop_id, since) -> list[dict]:
        rows = [{"type": "bill", "id": b["id"], "supplier_id": b["supplier_id"], "supplier": self._name(b["supplier_id"]),
                 "amount": b["total_amount"] or Decimal(0),
                 "day": b.get("bill_date") or b["created_at"].astimezone(IST).date(),
                 "created_at": b.get("confirmed_at") or b["created_at"], "source": b.get("source"),
                 "credit_days": b.get("credit_days"), "kind": b.get("kind", "bill"), "reason": b.get("reason"),
                 "invoice": b.get("invoice_number"), "status": b["status"]}
                for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED
                and b["supplier_id"] and (b.get("confirmed_at") or b["created_at"]) >= since]
        rows += [{"type": "payment", "id": p["id"], "supplier_id": p["supplier_id"], "supplier": self._name(p["supplier_id"]),
                  "amount": p["amount"], "day": p["paid_on"], "created_at": p.get("confirmed_at") or p["created_at"],
                  "source": p["source"], "credit_days": None, "kind": p.get("kind", "payment"),
                  "reason": p.get("reason"), "invoice": None, "status": p.get("status", "confirmed")}
                 for p in self._paid(shop_id) if p["supplier_id"] and (p.get("confirmed_at") or p["created_at"]) >= since]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def get_entry(self, shop_id, entry_type, entry_id) -> dict | None:
        since = datetime(2000, 1, 1, tzinfo=timezone.utc)
        return next((e for e in self.recent_entries(shop_id, since) if e["type"] == entry_type
                     and str(e["id"]) == str(entry_id)), None)

    def edit_entry(self, shop_id, entry_type, entry_id, field, value, via) -> dict:
        """Change one field of a CONFIRMED bill or payment and write it to the edit history. Returns the old value."""
        if field not in EDITABLE[entry_type]:
            raise ValueError(f"cannot edit {field} of a {entry_type}")
        if entry_type == "bill":
            b = self.bills[entry_id]
            old = {"amount": b["total_amount"], "supplier_id": b["supplier_id"], "date": b.get("bill_date"),
                   "credit_days": b.get("credit_days"), "invoice_number": b.get("invoice_number")}[field]
            if field == "amount":
                self.set_bill_field(entry_id, "total_amount", value)
            elif field == "date":
                self.set_bill_field(entry_id, "bill_date", value)
                if b.get("credit_days") is not None:
                    self.set_bill_credit_days(entry_id, b["credit_days"])
            elif field == "credit_days":
                self.set_bill_credit_days(entry_id, value)
            elif field == "invoice_number":
                self.set_bill_field(entry_id, "invoice_number", value)
            else:
                b["supplier_id"] = value
            if field != "supplier_id" and b["status"] == "confirmed" and self.corrected_fields(entry_id):
                b["status"] = "corrected"
        else:
            p = self._pay(entry_id)
            col = {"amount": "amount", "supplier_id": "supplier_id", "date": "paid_on"}[field]
            old = p[col]
            p[col] = Decimal(str(value)) if field == "amount" else _cast("bill_date", value) if field == "date" else value
        self.edits.append({"shop_id": shop_id, "entry_type": entry_type, "entry_id": entry_id, "action": "edit",
                           "field": field, "old_value": _edit_text(field, old), "new_value": _edit_text(field, value),
                           "via": via,
                           "created_at": _now()})
        return {"old": old}

    def delete_entry(self, shop_id, entry_type, entry_id, via):
        if entry_type == "bill":
            self.bills[entry_id].update(status="deleted", deleted_at=_now())
        else:
            self._pay(entry_id).update(status="deleted", deleted_at=_now())
        self.edits.append({"shop_id": shop_id, "entry_type": entry_type, "entry_id": entry_id, "action": "delete",
                           "field": None, "old_value": None, "new_value": None, "via": via, "created_at": _now()})

    def entry_edits(self, shop_id) -> list[dict]:
        return [dict(e) for e in self.edits if e["shop_id"] == shop_id]

    def dues(self, shop_id) -> list[dict]:
        rows = []
        today = date.today()
        for s in self.list_suppliers(shop_id):
            bills = [b for b in self.bills.values()
                     if b["supplier_id"] == s["id"] and b["status"] in ("confirmed", "corrected")]
            billed = sum(((b["total_amount"] or 0) * _bill_sign(b) for b in bills), Decimal(0))
            paid = sum((p["amount"] * _pay_sign(p) for p in self._paid(supplier_id=s["id"])), Decimal(0))
            upcoming = [b["due_date"] for b in bills if b.get("due_date") and b["due_date"] >= today
                        and b.get("kind") != "return"]
            if billed or paid:
                rows.append({"supplier_id": s["id"], "supplier": s["name"], "billed": billed, "paid": paid,
                             "due": billed - paid, "next_due": min(upcoming) if upcoming else None})
        return sorted(rows, key=lambda r: -r["due"])

    # events
    def log_event(self, shop_id, event, props=None):
        self.events.append({"shop_id": shop_id, "event": event, "props": props or {}, "created_at": _now()})

    def count_events(self, event, since, shop_id=None) -> int:
        return sum(1 for e in self.events if e["event"] == event and e["created_at"] >= since
                   and (shop_id is None or e["shop_id"] == shop_id))

    def events_since(self, since) -> list[dict]:
        """Every event since `since`, oldest first (the pilot health report)."""
        return [dict(e) for e in self.events if e["created_at"] >= since]

    def replies_sent_since(self, since) -> int:
        """Bot messages Meta accepted since `since`, templates not counted (Meta bills those apart)."""
        return sum(1 for m in self.messages if m["direction"] == "out" and m["wa_message_id"]
                   and m["type"] != "template" and m["created_at"] >= since)

    # pilot admin and scheduled jobs
    def list_shops(self, status=None):
        rows = [dict(s) for s in self.shops.values() if status is None or s["status"] == status]
        return sorted(rows, key=lambda s: s["shop_code"])

    def delete_shop(self, shop_code) -> bool:
        sid = next((k for k, s in self.shops.items() if s["shop_code"] == shop_code), None)
        if sid is None:
            return False
        del self.shops[sid]
        self.suppliers = {k: v for k, v in self.suppliers.items() if v["shop_id"] != sid}
        for bid in [k for k, b in self.bills.items() if b["shop_id"] == sid]:
            del self.bills[bid]
            for f in FIELDS:
                self.bill_fields.pop((bid, f), None)
        self.payments = [p for p in self.payments if p["shop_id"] != sid]
        self.edits = [e for e in self.edits if e["shop_id"] != sid]
        self.reminders = {k: r for k, r in self.reminders.items() if r["shop_id"] != sid}
        self.events = [e for e in self.events if e["shop_id"] != sid]
        self.messages = [m for m in self.messages if m["shop_id"] != sid]
        return True

    def bills_due_on(self, shop_id, day) -> list[dict]:
        return [self.get_bill(b["id"]) | {"supplier": self.suppliers[b["supplier_id"]]["name"]}
                for b in self.bills.values()
                if b["shop_id"] == shop_id and b["status"] in ("confirmed", "corrected") and b.get("kind") != "return"
                and b.get("due_date") == day and b["supplier_id"]]

    def event_exists(self, shop_id, event, key, value) -> bool:
        return any(e["shop_id"] == shop_id and e["event"] == event and str(e["props"].get(key)) == str(value)
                   for e in self.events)

    def week_stats(self, shop_id, end_day) -> dict:
        start = end_day - timedelta(days=6)
        bills = [b for b in self.bills.values() if b["shop_id"] == shop_id and b.get("kind", "bill") == "bill"
                 and b["status"] in ("confirmed", "corrected") and start <= b["created_at"].astimezone(IST).date() <= end_day]
        paid = sum((p["amount"] for p in self._paid(shop_id, kinds=("payment",)) if start <= p["paid_on"] <= end_day
                    and not str(p.get("reason") or "").startswith("sudhaar")),
                   Decimal(0))
        return _week_summary(len(bills), sum((b["total_amount"] or 0 for b in bills), Decimal(0)), paid,
                             self.dues(shop_id))

    # questions (bot/answers.py): every answer is one of these queries
    def _name(self, supplier_id):
        return self.suppliers[supplier_id]["name"] if supplier_id in self.suppliers else None

    def _ledger_bills(self, shop_id, supplier_id=None, kinds=BILL_KINDS):
        return [b for b in self.bills.values() if b["shop_id"] == shop_id and b["status"] in CONFIRMED
                and b.get("kind", "bill") in kinds
                and b["supplier_id"] and (supplier_id is None or b["supplier_id"] == supplier_id)]

    def supplier_summary(self, shop_id, supplier_id) -> dict | None:
        if self._name(supplier_id) is None or self.suppliers[supplier_id]["shop_id"] != shop_id:
            return None
        bills = self._ledger_bills(shop_id, supplier_id)
        pays = self._paid(shop_id, supplier_id)
        days = [b["bill_date"] or b["created_at"].astimezone(IST).date() for b in bills] + [p["paid_on"] for p in pays]
        billed = sum(((b["total_amount"] or 0) * _bill_sign(b) for b in bills), Decimal(0))  # net of returns
        paid = sum((p["amount"] * _pay_sign(p) for p in pays), Decimal(0))                  # net of refunds
        def adj(r):
            return str(r.get("reason") or "").startswith("sudhaar")

        def total(rows, kind, key):   # corrections ('sudhaar') are shown on their own line, not as goods / payments
            return sum((r[key] or 0 for r in rows if r.get("kind", "bill" if key == "total_amount" else "payment") == kind
                        and not adj(r)), Decimal(0))
        return {"supplier": self._name(supplier_id), "billed": billed, "paid": paid, "due": billed - paid,
                "bills": sum(b.get("kind") != "return" for b in bills),
                "payments": sum(p.get("kind") != "refund" for p in pays), "first_day": min(days) if days else None,
                "n_bill": sum(b.get("kind", "bill") == "bill" for b in bills), "bill_sum": total(bills, "bill", "total_amount"),
                "udhaar_sum": total(bills, "udhaar", "total_amount"), "return_sum": total(bills, "return", "total_amount"),
                "n_payment": sum(p.get("kind", "payment") == "payment" and not adj(p) for p in pays),
                "payment_sum": total(pays, "payment", "amount"), "refund_sum": total(pays, "refund", "amount"),
                "adjust_sum": sum(((b["total_amount"] or 0) * _bill_sign(b) for b in bills if adj(b)), Decimal(0))
                - sum((p["amount"] * _pay_sign(p) for p in pays if adj(p)), Decimal(0))}

    def payments_between(self, shop_id, start, end, supplier_id=None) -> list[dict]:
        """Money actually paid in the period: payments only (not refunds), not corrections ('sudhaar')."""
        rows = [p for p in self._paid(shop_id, supplier_id, kinds=("payment",)) if start <= p["paid_on"] <= end
                and not str(p.get("reason") or "").startswith("sudhaar")]
        rows.sort(key=lambda p: (p["paid_on"], p["created_at"]))
        return [{"supplier_id": p["supplier_id"], "supplier": self._name(p["supplier_id"]), "amount": p["amount"],
                 "paid_on": p["paid_on"]} for p in rows]

    def bills_between(self, shop_id, start, end, supplier_id=None) -> list[dict]:
        """Confirmed bills by the day they reached the bot (IST)."""
        rows = [b | {"day": b["created_at"].astimezone(IST).date()}
                for b in self._ledger_bills(shop_id, supplier_id, kinds=("bill",))]  # only real bills
        rows = sorted((b for b in rows if start <= b["day"] <= end), key=lambda b: b["day"])
        return [{"supplier_id": b["supplier_id"], "supplier": self._name(b["supplier_id"]),
                 "total_amount": b["total_amount"] or Decimal(0), "day": b["day"]} for b in rows]

    def last_payment(self, shop_id, supplier_id=None) -> dict | None:
        rows = self.payments_between(shop_id, date.min, date.max, supplier_id)
        return rows[-1] if rows else None

    def add_reminder(self, shop_id, supplier_id, text, remind_on) -> dict:
        rid = str(uuid.uuid4())
        self.reminders[rid] = {"id": rid, "shop_id": shop_id, "supplier_id": supplier_id, "text": text,
                               "remind_on": remind_on, "sent_at": None, "created_at": _now()}
        return self._reminder(rid)

    def _reminder(self, rid):
        r = dict(self.reminders[rid])
        return r | {"supplier": self._name(r["supplier_id"]) if r["supplier_id"] else None}

    def list_reminders(self, shop_id, from_day) -> list[dict]:
        rows = [self._reminder(k) for k, r in self.reminders.items()
                if r["shop_id"] == shop_id and r["sent_at"] is None and r["remind_on"] >= from_day]
        return sorted(rows, key=lambda r: (r["remind_on"], r["created_at"]))

    def cancel_reminder(self, shop_id, reminder_id) -> dict | None:
        r = self.reminders.get(reminder_id)
        if r is None or r["shop_id"] != shop_id or r["sent_at"] is not None:
            return None
        out = self._reminder(reminder_id)
        del self.reminders[reminder_id]
        return out

    def reminders_due(self, shop_id, day) -> list[dict]:
        rows = [self._reminder(k) for k, r in self.reminders.items()
                if r["shop_id"] == shop_id and r["sent_at"] is None and r["remind_on"] <= day]
        return sorted(rows, key=lambda r: (r["remind_on"], r["created_at"]))

    def mark_reminder_sent(self, reminder_id):
        self.reminders[reminder_id]["sent_at"] = _now()


# =============================================================== Postgres / Supabase
# Per-message database timing for the reply log (bot/conversation.py): [queries, seconds]. A context variable, so two
# messages handled at the same time keep separate counts. None outside a message: nothing is counted.
DB_TIMER: contextvars.ContextVar = contextvars.ContextVar("db_timer", default=None)


def _count_db(started: float, n: int = 1):
    timer = DB_TIMER.get()
    if timer is not None:
        timer[0] += n
        timer[1] += time.perf_counter() - started


class PgStore:
    def __init__(self, database_url: str):
        self._url = database_url
        self.conn = self._connect()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        # prepare_threshold=None: Supabase's connection pooler does not allow prepared statements
        return psycopg.connect(self._url, autocommit=True, row_factory=dict_row, prepare_threshold=None)

    def _run(self, sql, params, fetch):
        """Runs one statement. If the connection was dropped (idle timeout, network blip), reconnects once."""
        import psycopg

        started = time.perf_counter()
        try:
            for attempt in (1, 2):
                if self.conn.closed or self.conn.broken:
                    self.conn = self._connect()
                try:
                    with self.conn.cursor() as cur:
                        cur.execute(sql, params)
                        return fetch(cur)
                except psycopg.OperationalError:
                    if attempt == 2:
                        raise
                    self.conn = self._connect()
        finally:
            _count_db(started)

    def _one(self, sql, params=()):
        return self._run(sql, params, lambda cur: cur.fetchone() if cur.description else None)

    def _all(self, sql, params=()):
        return self._run(sql, params, lambda cur: cur.fetchall())

    # shops
    def get_shop_by_wa(self, wa_id):
        return self._one("select * from shops where wa_id = %s", (wa_id,))

    def get_shop(self, shop_id):
        return self._one("select * from shops where id = %s", (shop_id,))

    def create_shop(self, wa_id, shop_code=None, segment=None, status="invited", shop_name=None, prefix="T"):
        if shop_code is None:
            prefix = prefix if re.fullmatch(r"[A-Z]", prefix or "") else "T"   # one capital letter: safe in the SQL
            n = self._one(f"select coalesce(max(substring(shop_code from '^{prefix}([0-9]+)$')::int), 0) as n "
                          "from shops")["n"]
            shop_code = f"{prefix}{n + 1:02d}"
        return self._one("insert into shops (wa_id, shop_code, segment, status, shop_name) "
                         "values (%s, %s, %s, %s, %s) returning *", (wa_id, shop_code, segment, status, shop_name))

    def update_shop(self, shop_id, **fields):
        from psycopg.types.json import Jsonb

        bad = set(fields) - SHOP_UPDATABLE
        if bad:
            raise ValueError(f"cannot update {bad}")
        cols = ", ".join(f"{k} = %s" for k in fields)
        vals = [Jsonb(v) if k == "state" else v for k, v in fields.items()]
        self._one(f"update shops set {cols} where id = %s", (*vals, shop_id))

    # messages
    def record_message(self, shop_id, direction, wa_message_id, mtype, body=None, media_id=None) -> bool:
        row = self._one(
            "insert into messages (shop_id, direction, wa_message_id, type, body, media_id) "
            "values (%s, %s, %s, %s, %s, %s) on conflict (wa_message_id) do nothing returning id",
            (shop_id, direction, wa_message_id, mtype, body, media_id))
        return row is not None

    # bills
    def create_bill(self, shop_id, fields: dict, meta: dict) -> dict:
        cols = {BILL_COLUMN[f]: _cast(f, fields.get(f)) for f in FIELDS}
        cols.update({k: meta.get(k) for k in ("image_path", "model", "prompt_version",
                                               "input_tokens", "output_tokens", "latency_ms")})
        cols.update({k: meta[k] for k in ("source", "raw_text", "credit_days", "supplier_id", "kind", "reason")
                     if meta.get(k) is not None})
        if meta.get("new_supplier"):
            cols["new_supplier"] = True
        names = ", ".join(cols)
        marks = ", ".join(["%s"] * len(cols))
        bill = self._one(f"insert into bills (shop_id, {names}) values (%s, {marks}) returning id",
                         (shop_id, *cols.values()))
        unsure = set(fields.get("unsure_fields") or [])
        started = time.perf_counter()
        with self.conn.cursor() as cur:
            cur.executemany(
                "insert into bill_fields (bill_id, field_name, ai_value, ai_unsure) values (%s, %s, %s, %s)",
                [(bill["id"], f, _as_text(fields.get(f)), f in unsure) for f in FIELDS])
        _count_db(started)
        return self.get_bill(bill["id"])

    def get_bill(self, bill_id):
        bill = self._one("select * from bills where id = %s", (bill_id,))
        if bill:
            bill["id"] = str(bill["id"])
            bill["shop_id"] = str(bill["shop_id"])
            rows = self._all("select field_name, ai_unsure, final_value is not null as checked from bill_fields "
                             "where bill_id = %s", (bill_id,))
            bill["unsure"] = [f for f in FIELDS if f in {r["field_name"] for r in rows if r["ai_unsure"]}]
            bill["checked"] = [f for f in FIELDS if f in {r["field_name"] for r in rows if r["checked"]}]
        return bill

    def set_bill_field(self, bill_id, field, value):
        col = BILL_COLUMN[field]  # from a fixed map, never user input
        self._one(f"update bills set {col} = %s where id = %s", (_cast(field, value), bill_id))
        self._one("update bill_fields set final_value = %s where bill_id = %s and field_name = %s",
                  (_as_text(value), bill_id, field))

    def confirm_bill(self, bill_id, supplier_id) -> str:
        self._one("update bill_fields set final_value = ai_value where bill_id = %s and final_value is null", (bill_id,))
        row = self._one(
            "update bills set supplier_id = %s, confirmed_at = now(), status = case when exists "
            "(select 1 from bill_fields where bill_id = %s and corrected) then 'corrected' else 'confirmed' end "
            "where id = %s returning status", (supplier_id, bill_id, bill_id))
        return row["status"]

    def corrected_fields(self, bill_id) -> list[str]:
        rows = self._all("select field_name from bill_fields where bill_id = %s and corrected", (bill_id,))
        return [r["field_name"] for r in rows]

    def reject_bill(self, bill_id):
        self._one("update bills set status = 'rejected' where id = %s", (bill_id,))

    def set_bill_supplier(self, bill_id, supplier_id=None, new_supplier=False):
        self._one("update bills set supplier_id = %s, new_supplier = %s where id = %s and status = 'pending_confirm'",
                  (supplier_id, bool(new_supplier), bill_id))

    def set_bill_credit_days(self, bill_id, days):
        b = self._one("select bill_date, created_at from bills where id = %s", (bill_id,))
        self._one("update bills set credit_days = %s where id = %s", (days, bill_id))
        due = due_from(b["bill_date"], b["created_at"], days)
        self.set_bill_field(bill_id, "due_date", due.isoformat() if due else None)

    def old_photos(self, before) -> list[dict]:
        rows = self._all(
            "select b.id as bill_id, b.image_path, s.shop_code, 'bill' as kind from bills b "
            "join shops s on s.id = b.shop_id where b.image_path is not null and b.created_at < %s and not s.eval_consent "
            "union all select b.id, b.proof_image_path, s.shop_code, 'proof' from bills b join shops s on s.id = b.shop_id "
            "where b.proof_image_path is not null and b.proof_image_path <> %s and b.created_at < %s "
            "and not s.eval_consent", (before, PROOF_PENDING, before))
        return [r | {"bill_id": str(r["bill_id"])} for r in rows]

    def clear_photo(self, bill_id, kind="bill"):
        if kind == "proof":
            self._one("update bills set proof_image_path = null, proof_deleted_at = now() where id = %s", (bill_id,))
        else:
            self._one("update bills set image_path = null, photo_deleted_at = now() where id = %s", (bill_id,))

    def claim_proof(self, bill_id) -> bool:
        """One statement: only one tap can take the empty proof slot."""
        return self._one("update bills set proof_image_path = %s where id = %s and proof_image_path is null "
                         "returning id", (PROOF_PENDING, bill_id)) is not None

    def release_proof(self, bill_id):
        self._one("update bills set proof_image_path = null where id = %s and proof_image_path = %s",
                  (bill_id, PROOF_PENDING))

    def attach_proof(self, bill_id, path, total, model):
        self._one("update bills set proof_image_path = %s, proof_total = %s, proof_model = %s where id = %s",
                  (path, None if total is None else Decimal(str(total)), model, bill_id))

    def latest_proofless_bill(self, shop_id, since):
        row = self._one("select b.id from bills b where b.shop_id = %s and b.status in ('confirmed', 'corrected') "
                        "and b.kind = 'bill' and b.source in ('voice', 'typed') and b.proof_image_path is null and b.confirmed_at >= %s "
                        "order by b.confirmed_at desc limit 1", (shop_id, since))
        if row is None:
            return None
        bill = self.get_bill(row["id"])
        name = self._one("select name from suppliers where id = %s", (bill["supplier_id"],)) if bill["supplier_id"] else None
        return bill | {"supplier": name["name"] if name else None}

    def _bill_with_name(self, bill_id):
        bill = self.get_bill(bill_id)
        if bill is None:
            return None
        name = self._one("select name from suppliers where id = %s", (bill["supplier_id"],)) if bill["supplier_id"] else None
        return bill | {"supplier": name["name"] if name else None}

    def proofless_bills(self, shop_id, since) -> list[dict]:
        rows = self._all("select b.id from bills b where b.shop_id = %s and b.status in ('confirmed', 'corrected') "
                         "and b.kind = 'bill' and b.source in ('voice', 'typed') and b.proof_image_path is null "
                         "and b.confirmed_at >= %s order by b.confirmed_at desc limit 10", (shop_id, since))
        return [self._bill_with_name(r["id"]) for r in rows]

    def detach_photo(self, bill_id):
        self._one("update bills set image_path = null where id = %s", (bill_id,))

    def latest_photo_bill(self, shop_id, since):
        row = self._one("select id from bills where shop_id = %s and image_path is not null "
                        "and coalesce(source, '') not in ('voice', 'typed') and created_at >= %s and status <> 'deleted' "
                        "order by created_at desc limit 1", (shop_id, since))
        return self._bill_with_name(row["id"]) if row else None

    def bill_by_invoice(self, shop_id, supplier_id, invoice, exclude_id=None):
        key = str(invoice or "").strip().upper()
        if not key or not supplier_id:
            return None
        row = self._one("select id from bills where shop_id = %s and supplier_id = %s and status in ('confirmed', "
                        "'corrected') and upper(trim(invoice_number)) = %s and id::text <> %s limit 1",
                        (shop_id, supplier_id, key, str(exclude_id or "")))
        return self.get_entry(shop_id, "bill", row["id"]) if row else None

    def paired(self, shop_id, entry_type, entry_id):
        if entry_type == "payment":
            row = self._one("select id from bills where shop_id = %s and reason = %s and status in ('confirmed', "
                            "'corrected')", (shop_id, f"pair:{entry_id}"))
            return ("bill", str(row["id"])) if row else None
        row = self._one("select reason from bills where id = %s and shop_id = %s", (entry_id, shop_id))
        reason = (row or {}).get("reason") or ""
        if reason.startswith("pair:"):
            p = self._one("select id from payments where id::text = %s and status = 'confirmed'", (reason[5:],))
            return ("payment", str(p["id"])) if p else None
        return None

    def shop_counts(self, shop_id) -> dict:
        return self._one("select (select count(*) from suppliers where shop_id = %s) as suppliers, "
                         "(select count(*) from bills where shop_id = %s and status in ('confirmed', 'corrected')) as bills, "
                         "(select count(*) from payments where shop_id = %s and status = 'confirmed') as payments",
                         (shop_id, shop_id, shop_id))

    # suppliers and payments
    def list_suppliers(self, shop_id):
        return [r | {"id": str(r["id"])} for r in
                self._all("select id, shop_id, name, name_norm from suppliers where shop_id = %s", (shop_id,))]

    def find_or_create_supplier(self, shop_id, name):
        row = self._one(
            "insert into suppliers (shop_id, name, name_norm) values (%s, %s, %s) "
            "on conflict (shop_id, name_norm) do update set name = suppliers.name returning id, name, name_norm",
            (shop_id, name.strip(), norm_supplier(name)))
        return row | {"id": str(row["id"])}

    def add_payment(self, shop_id, supplier_id, amount, source, raw_text=None):
        """A payment saved straight away (seed data, tests). The bot itself uses create_payment + confirm_payment."""
        p = self.create_payment(shop_id, supplier_id, amount, source, raw_text)
        self.confirm_payment(p["id"], supplier_id)

    _PAY_SQL = ("select p.*, s.name as supplier from payments p left join suppliers s on s.id = p.supplier_id ")

    def _payrow(self, row):
        return None if row is None else row | {"id": str(row["id"]), "shop_id": str(row["shop_id"]),
                                               "supplier_id": _as_text(row["supplier_id"])}

    def create_payment(self, shop_id, supplier_id, amount, source, raw_text=None, paid_on=None,
                       supplier_name_raw=None, new_supplier=False, kind="payment", reason=None) -> dict:
        if kind not in PAYMENT_KINDS:
            raise ValueError(kind)
        # paid_on is the shop's (IST) date, not the database server's UTC date
        row = self._one("insert into payments (shop_id, supplier_id, amount, source, raw_text, paid_on, status, "
                        "supplier_name_raw, new_supplier, kind, reason) values (%s, %s, %s, %s, %s, %s, 'pending_confirm', "
                        "%s, %s, %s, %s) returning id", (shop_id, supplier_id, Decimal(str(amount)), source, raw_text,
                                                         paid_on or today_ist(), supplier_name_raw, bool(new_supplier),
                                                         kind, reason))
        return self.get_payment(row["id"])

    def get_payment(self, payment_id):
        return self._payrow(self._one(self._PAY_SQL + "where p.id = %s", (payment_id,)))

    def set_payment_fields(self, payment_id, **fields):
        bad = set(fields) - PENDING_PAYMENT_FIELDS
        if bad:
            raise ValueError(f"cannot set {bad}")
        cols = ", ".join(f"{k} = %s" for k in fields)  # names from a fixed set, never user input
        vals = [Decimal(str(v)) if k == "amount" else v for k, v in fields.items()]
        self._one(f"update payments set {cols} where id = %s", (*vals, payment_id))

    def confirm_payment(self, payment_id, supplier_id):
        self._one("update payments set status = 'confirmed', supplier_id = %s, confirmed_at = now() where id = %s",
                  (supplier_id, payment_id))

    def cancel_payment(self, payment_id):
        self._one("update payments set status = 'cancelled' where id = %s", (payment_id,))

    def pending_count(self, shop_id, since) -> int:
        r = self._one("select (select count(*) from bills where shop_id = %s and status = 'pending_confirm' "
                      "and created_at >= %s) + (select count(*) from payments where shop_id = %s "
                      "and status = 'pending_confirm' and created_at >= %s) as n", (shop_id, since, shop_id, since))
        return int(r["n"])

    def pending_entries(self, shop_id, since) -> list[dict]:
        return self._all("select 'b' as t, id, total_amount as amount, created_at from bills where shop_id = %s "
                         "and status = 'pending_confirm' and created_at >= %s union all "
                         "select 'p' as t, id, amount, created_at from payments where shop_id = %s "
                         "and status = 'pending_confirm' and created_at >= %s order by created_at desc",
                         (shop_id, since, shop_id, since))

    # fixes after confirming: recent entries, edits, removal (every change goes to the edit history)
    def recent_entries(self, shop_id, since) -> list[dict]:
        rows = self._all(
            "select 'bill' as type, b.id, b.supplier_id, s.name as supplier, coalesce(b.total_amount, 0) as amount, "
            "coalesce(b.bill_date, (b.created_at at time zone 'Asia/Kolkata')::date) as day, "
            "coalesce(b.confirmed_at, b.created_at) as created_at, b.source, b.credit_days, b.kind, b.reason, "
            "b.invoice_number as invoice, b.status "
            "from bills b join suppliers s on s.id = b.supplier_id where b.shop_id = %s "
            "and b.status in ('confirmed', 'corrected') and coalesce(b.confirmed_at, b.created_at) >= %s "
            "union all select 'payment', p.id, p.supplier_id, s.name, p.amount, p.paid_on, "
            "coalesce(p.confirmed_at, p.created_at), p.source, null, p.kind, p.reason, null, p.status from payments p "
            "join suppliers s on s.id = p.supplier_id "
            "where p.shop_id = %s and p.status = 'confirmed' and coalesce(p.confirmed_at, p.created_at) >= %s "
            "order by created_at desc", (shop_id, since, shop_id, since))
        return [r | {"id": str(r["id"]), "supplier_id": str(r["supplier_id"])} for r in rows]

    def get_entry(self, shop_id, entry_type, entry_id) -> dict | None:
        """One confirmed entry (one query, not the shop's whole history)."""
        if entry_type == "bill":
            row = self._one(
                "select 'bill' as type, b.id, b.supplier_id, s.name as supplier, coalesce(b.total_amount, 0) as amount, "
                "coalesce(b.bill_date, (b.created_at at time zone 'Asia/Kolkata')::date) as day, "
                "coalesce(b.confirmed_at, b.created_at) as created_at, b.source, b.credit_days, b.kind, b.reason, "
                "b.invoice_number as invoice, b.status from bills b join suppliers s on s.id = b.supplier_id "
                "where b.shop_id = %s and b.id::text = %s and b.status in ('confirmed', 'corrected')",
                (shop_id, str(entry_id)))
        else:
            row = self._one(
                "select 'payment' as type, p.id, p.supplier_id, s.name as supplier, p.amount, p.paid_on as day, "
                "coalesce(p.confirmed_at, p.created_at) as created_at, p.source, null as credit_days, p.kind, p.reason, "
                "null as invoice, p.status from payments p join suppliers s on s.id = p.supplier_id "
                "where p.shop_id = %s and p.id::text = %s and p.status = 'confirmed'", (shop_id, str(entry_id)))
        return None if row is None else row | {"id": str(row["id"]), "supplier_id": str(row["supplier_id"])}

    def edit_entry(self, shop_id, entry_type, entry_id, field, value, via) -> dict:
        if field not in EDITABLE[entry_type]:
            raise ValueError(f"cannot edit {field} of a {entry_type}")
        if entry_type == "bill":
            b = self._one("select total_amount, supplier_id, bill_date, credit_days, invoice_number from bills "
                          "where id = %s", (entry_id,))
            old = {"amount": b["total_amount"], "supplier_id": b["supplier_id"], "date": b["bill_date"],
                   "credit_days": b["credit_days"], "invoice_number": b["invoice_number"]}[field]
            if field == "amount":
                self.set_bill_field(entry_id, "total_amount", value)
            elif field == "date":
                self.set_bill_field(entry_id, "bill_date", value)
                if b["credit_days"] is not None:
                    self.set_bill_credit_days(entry_id, b["credit_days"])
            elif field == "credit_days":
                self.set_bill_credit_days(entry_id, value)
            elif field == "invoice_number":
                self.set_bill_field(entry_id, "invoice_number", value)
            else:
                self._one("update bills set supplier_id = %s where id = %s", (value, entry_id))
            if field != "supplier_id":
                self._one("update bills set status = 'corrected' where id = %s and status = 'confirmed' and exists "
                          "(select 1 from bill_fields where bill_id = %s and corrected)", (entry_id, entry_id))
        else:
            col = {"amount": "amount", "supplier_id": "supplier_id", "date": "paid_on"}[field]  # fixed map
            old = self._one(f"select {col} as v from payments where id = %s", (entry_id,))["v"]
            new = Decimal(str(value)) if field == "amount" else _cast("bill_date", value) if field == "date" else value
            self._one(f"update payments set {col} = %s where id = %s", (new, entry_id))
        self._one("insert into entry_edits (shop_id, entry_type, entry_id, action, field, old_value, new_value, via) "
                  "values (%s, %s, %s, 'edit', %s, %s, %s, %s)",
                  (shop_id, entry_type, entry_id, field, _edit_text(field, old), _edit_text(field, value), via))
        return {"old": old}

    def delete_entry(self, shop_id, entry_type, entry_id, via):
        table = "bills" if entry_type == "bill" else "payments"  # fixed map
        self._one(f"update {table} set status = 'deleted', deleted_at = now() where id = %s", (entry_id,))
        self._one("insert into entry_edits (shop_id, entry_type, entry_id, action, via) values (%s, %s, %s, 'delete', %s)",
                  (shop_id, entry_type, entry_id, via))

    def entry_edits(self, shop_id) -> list[dict]:
        rows = self._all("select * from entry_edits where shop_id = %s order by id", (shop_id,))
        return [r | {"shop_id": str(r["shop_id"]), "entry_id": str(r["entry_id"])} for r in rows]

    def dues(self, shop_id) -> list[dict]:
        rows = self._all("select supplier_id, supplier, billed, paid, due, next_due from v_supplier_dues "
                         "where shop_id = %s and (billed > 0 or paid > 0) order by due desc", (shop_id,))
        return [r | {"supplier_id": str(r["supplier_id"])} for r in rows]

    # events
    def log_event(self, shop_id, event, props=None):
        self._one("insert into events (shop_id, event, props) values (%s, %s, %s)",
                  (shop_id, event, json.dumps(props or {}, default=str)))

    def events_since(self, since) -> list[dict]:
        rows = self._all("select shop_id, event, props, created_at from events where created_at >= %s "
                         "order by created_at, id", (since,))
        return [r | {"shop_id": _as_text(r["shop_id"])} for r in rows]

    def replies_sent_since(self, since) -> int:
        """Bot messages Meta accepted since `since`, templates not counted (Meta bills those apart)."""
        return self._one("select count(*) as n from messages where direction = 'out' and wa_message_id is not null "
                         "and type <> 'template' and created_at >= %s", (since,))["n"]

    def count_events(self, event, since, shop_id=None) -> int:
        if shop_id is None:
            return self._one("select count(*) as n from events where event = %s and created_at >= %s", (event, since))["n"]
        return self._one("select count(*) as n from events where event = %s and created_at >= %s and shop_id = %s",
                         (event, since, shop_id))["n"]

    def ping(self) -> bool:
        return self._one("select 1 as ok")["ok"] == 1

    # pilot admin and scheduled jobs
    def list_shops(self, status=None):
        if status is None:
            return self._all("select * from shops order by shop_code")
        return self._all("select * from shops where status = %s order by shop_code", (status,))

    def delete_shop(self, shop_code) -> bool:
        return self._one("delete from shops where shop_code = %s returning id", (shop_code,)) is not None

    def bills_due_on(self, shop_id, day) -> list[dict]:
        rows = self._all("select b.id, b.total_amount, b.due_date, s.name as supplier from bills b "
                         "join suppliers s on s.id = b.supplier_id where b.shop_id = %s and b.due_date = %s "
                         "and b.status in ('confirmed', 'corrected') and b.kind <> 'return'", (shop_id, day))
        return [r | {"id": str(r["id"])} for r in rows]

    def event_exists(self, shop_id, event, key, value) -> bool:
        row = self._one("select exists (select 1 from events where shop_id = %s and event = %s "
                        "and props ->> %s = %s) as e", (shop_id, event, key, str(value)))
        return row["e"]

    def week_stats(self, shop_id, end_day) -> dict:
        start = end_day - timedelta(days=6)
        b = self._one("select count(*) as n, coalesce(sum(total_amount), 0) as billed from bills where shop_id = %s "
                      "and kind = 'bill' and status in ('confirmed', 'corrected') "
                      "and (created_at at time zone 'Asia/Kolkata')::date "
                      "between %s and %s", (shop_id, start, end_day))
        p = self._one("select coalesce(sum(amount), 0) as paid from payments where shop_id = %s "
                      "and status = 'confirmed' and kind = 'payment' and coalesce(reason, '') not like 'sudhaar%%' "
                      "and paid_on between %s and %s", (shop_id, start, end_day))
        return _week_summary(b["n"], b["billed"], p["paid"], self.dues(shop_id))

    # questions (bot/answers.py): every answer is one of these queries
    def supplier_summary(self, shop_id, supplier_id) -> dict | None:
        row = self._one(
            "select s.name as supplier, "
            "(select count(*) from bills b where b.supplier_id = s.id and b.status in ('confirmed', 'corrected') "
            "  and b.kind <> 'return') as bills, "
            "(select coalesce(sum(case when b.kind = 'return' then -b.total_amount else b.total_amount end), 0) "
            "  from bills b where b.supplier_id = s.id and b.status in ('confirmed', 'corrected')) as billed, "
            "(select count(*) from payments p where p.supplier_id = s.id and p.status = 'confirmed' "
            "  and p.kind = 'payment') as payments, "
            "(select count(*) from bills b where b.supplier_id = s.id and b.status in ('confirmed', 'corrected') "
            "  and b.kind = 'bill') as n_bill, "
            "(select count(*) from payments p where p.supplier_id = s.id and p.status = 'confirmed' "
            "  and p.kind = 'payment' and coalesce(p.reason, '') not like 'sudhaar%%') as n_payment, "
            "(select coalesce(sum(case when b.kind = 'bill' and coalesce(b.reason, '') not like 'sudhaar%%' then b.total_amount end), 0) from bills b "
            "  where b.supplier_id = s.id and b.status in ('confirmed', 'corrected')) as bill_sum, "
            "(select coalesce(sum(case when b.kind = 'udhaar' and coalesce(b.reason, '') not like 'sudhaar%%' then b.total_amount end), 0) from bills b "
            "  where b.supplier_id = s.id and b.status in ('confirmed', 'corrected')) as udhaar_sum, "
            "(select coalesce(sum(case when b.kind = 'return' and coalesce(b.reason, '') not like 'sudhaar%%' then b.total_amount end), 0) from bills b "
            "  where b.supplier_id = s.id and b.status in ('confirmed', 'corrected')) as return_sum, "
            "(select coalesce(sum(case when p.kind = 'payment' and coalesce(p.reason, '') not like 'sudhaar%%' then p.amount end), 0) from payments p "
            "  where p.supplier_id = s.id and p.status = 'confirmed') as payment_sum, "
            "(select coalesce(sum(case when p.kind = 'refund' and coalesce(p.reason, '') not like 'sudhaar%%' then p.amount end), 0) from payments p "
            "  where p.supplier_id = s.id and p.status = 'confirmed') as refund_sum, "
            "(select coalesce(sum(case when b.kind = 'return' then -b.total_amount else b.total_amount end), 0) from bills b "
            "  where b.supplier_id = s.id and b.status in ('confirmed', 'corrected') and b.reason like 'sudhaar%%') "
            "- (select coalesce(sum(case when p.kind = 'refund' then -p.amount else p.amount end), 0) from payments p "
            "  where p.supplier_id = s.id and p.status = 'confirmed' and p.reason like 'sudhaar%%') as adjust_sum, "
            "(select coalesce(sum(case when p.kind = 'refund' then -p.amount else p.amount end), 0) from payments p "
            "  where p.supplier_id = s.id and p.status = 'confirmed') as paid, "
            "least((select min(coalesce(b.bill_date, (b.created_at at time zone 'Asia/Kolkata')::date)) from bills b "
            "       where b.supplier_id = s.id and b.status in ('confirmed', 'corrected')), "
            "      (select min(p.paid_on) from payments p where p.supplier_id = s.id and p.status = 'confirmed')) as first_day "
            "from suppliers s where s.id = %s and s.shop_id = %s", (supplier_id, shop_id))
        return None if row is None else row | {"due": row["billed"] - row["paid"]}

    def payments_between(self, shop_id, start, end, supplier_id=None) -> list[dict]:
        rows = self._all(
            "select p.supplier_id, s.name as supplier, p.amount, p.paid_on from payments p "
            "left join suppliers s on s.id = p.supplier_id where p.shop_id = %s and p.status = 'confirmed' "
            "and p.kind = 'payment' and coalesce(p.reason, '') not like 'sudhaar%%' and p.paid_on between %s and %s "
            "and (%s::uuid is null or p.supplier_id = %s::uuid) order by p.paid_on, p.created_at",
            (shop_id, start, end, supplier_id, supplier_id))
        return [r | {"supplier_id": _as_text(r["supplier_id"])} for r in rows]

    def bills_between(self, shop_id, start, end, supplier_id=None) -> list[dict]:
        """Confirmed bills by the day they reached the bot (IST)."""
        rows = self._all(
            "select b.supplier_id, s.name as supplier, coalesce(b.total_amount, 0) as total_amount, "
            "(b.created_at at time zone 'Asia/Kolkata')::date as day from bills b "
            "join suppliers s on s.id = b.supplier_id where b.shop_id = %s and b.status in ('confirmed', 'corrected') "
            "and b.kind = 'bill' and (b.created_at at time zone 'Asia/Kolkata')::date between %s and %s "
            "and (%s::uuid is null or b.supplier_id = %s::uuid) order by day, b.created_at",
            (shop_id, start, end, supplier_id, supplier_id))
        return [r | {"supplier_id": _as_text(r["supplier_id"])} for r in rows]

    def last_payment(self, shop_id, supplier_id=None) -> dict | None:
        row = self._one(
            "select p.supplier_id, s.name as supplier, p.amount, p.paid_on from payments p "
            "left join suppliers s on s.id = p.supplier_id where p.shop_id = %s and p.status = 'confirmed' "
            "and p.kind = 'payment' and coalesce(p.reason, '') not like 'sudhaar%%' "
            "and (%s::uuid is null or p.supplier_id = %s::uuid) order by p.paid_on desc, p.created_at desc limit 1",
            (shop_id, supplier_id, supplier_id))
        return None if row is None else row | {"supplier_id": _as_text(row["supplier_id"])}

    _REMINDER_SQL = ("select r.id, r.shop_id, r.supplier_id, r.text, r.remind_on, r.sent_at, r.created_at, "
                     "s.name as supplier from reminders r left join suppliers s on s.id = r.supplier_id ")

    def _rem(self, row):
        return None if row is None else row | {"id": str(row["id"]), "shop_id": str(row["shop_id"]),
                                               "supplier_id": _as_text(row["supplier_id"])}

    def add_reminder(self, shop_id, supplier_id, text, remind_on) -> dict:
        row = self._one("insert into reminders (shop_id, supplier_id, text, remind_on) values (%s, %s, %s, %s) "
                        "returning id", (shop_id, supplier_id, text, remind_on))
        return self._rem(self._one(self._REMINDER_SQL + "where r.id = %s", (row["id"],)))

    def list_reminders(self, shop_id, from_day) -> list[dict]:
        rows = self._all(self._REMINDER_SQL + "where r.shop_id = %s and r.sent_at is null and r.remind_on >= %s "
                         "order by r.remind_on, r.created_at", (shop_id, from_day))
        return [self._rem(r) for r in rows]

    def cancel_reminder(self, shop_id, reminder_id) -> dict | None:
        row = self._rem(self._one(self._REMINDER_SQL + "where r.id = %s and r.shop_id = %s and r.sent_at is null",
                                  (reminder_id, shop_id)))
        if row:
            self._one("delete from reminders where id = %s", (reminder_id,))
        return row

    def reminders_due(self, shop_id, day) -> list[dict]:
        rows = self._all(self._REMINDER_SQL + "where r.shop_id = %s and r.sent_at is null and r.remind_on <= %s "
                         "order by r.remind_on, r.created_at", (shop_id, day))
        return [self._rem(r) for r in rows]

    def mark_reminder_sent(self, reminder_id):
        self._one("update reminders set sent_at = now() where id = %s", (reminder_id,))
