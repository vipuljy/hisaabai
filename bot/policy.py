"""Read policies: Haiku first, free checks on its answer, Sonnet only when a check fails.

The same functions run in the eval simulation (src/09_policies_eval.py, cached readings) and in the live bot
(bot/conversation.py), so what we measure is what ships.
Checks on Haiku's answer (all free):
  a) ocr_agree  Haiku's total vs the free OCR total (when OCR read one): different -> escalate
  b) proof      words = digits, GST / Marg maths, line items on the OCR text: a proof that can be computed and
                none passes -> escalate
  c) unsure     Haiku listed any field in unsure_fields -> escalate
  d) sanity     total missing or <= 0, bill date invalid or in the future, invoice no. that is a GSTIN / phone
Escalate = Sonnet re-reads the photo. Policy P3 also asks the owner when Sonnet and Haiku still disagree on the
total or the date (those fields are shown with ⚠️: flagged, never silent).
P5 "money first": Sonnet ONLY when a check on the TOTAL fails (a, b, Haiku unsure about the total, total sanity).
A doubt about the date / invoice no. / due date never calls Sonnet: the owner is asked (date: one tap, suggesting
the day the photo arrived). Also: a bill date more than 6 months before the photo arrived is suspicious -> asked.
P5_no_ocr = P5 without the free OCR (checks a and b off; the OCR is not even run): for a host without RapidOCR.
P5_trusted_ocr = P5, but check a only fires when the OCR total is TRUSTED (proved by words = digits or the bill's
maths); an unproved OCR total is ignored. Check b stays.
P5e = P5 + check e) the invoice no. shown to the owner vs the free OCR's invoice no. (when OCR read a sane one):
different -> invoice no. gets a warning (the owner checks it; never a Sonnet call).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from bot import checks
from bot.extract import FIELDS, norm_amount, norm_date, norm_invoice

POLICIES = {
    "P1": {"a", "c", "d"},
    "P2": {"a", "b", "c", "d"},
    "P3": {"a", "b", "c", "d", "ask"},
    "P5": {"a", "b", "c", "d", "ask", "money_first", "date_age"},
    "P5e": {"a", "b", "c", "d", "ask", "money_first", "date_age", "invoice_ocr"},
    "P5_no_ocr": {"c", "d", "ask", "money_first", "date_age"},
    "P5_trusted_ocr": {"a_trusted", "b", "c", "d", "ask", "money_first", "date_age"},
}
OCR_RULES = {"a", "a_trusted", "b", "invoice_ocr"}  # a policy with none of these never runs the OCR
TRUSTED_PROOFS = ("words", "maths")  # read_ocr's total_proof values that make an OCR total trusted
DEFAULT_POLICY = "P5_no_ocr"  # 29 Oct 2026: live on Render free, no OCR (docs/04_decisions_log.md); Oracle option: P5_trusted_ocr
# One OCR at a time: RapidOCR peaks at ~0.7-0.9 GB per photo, so photos arriving together queue instead of
# doubling the memory (docs/11_setup_and_hosting.md).
_OCR_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
DATE_MAX_AGE_DAYS = 183  # a bill dated more than ~6 months before the photo arrived is suspicious
KEY_FIELDS = ("total_amount", "bill_date")


def haiku_checks(fields: dict, ocr: dict | None, today: date) -> dict:
    """Free checks on one Haiku answer. ocr = a RapidOCR record (fields + raw_text) or None."""
    total = fields.get("total_amount")
    ocr_total = ((ocr or {}).get("fields") or {}).get("total_amount")
    ocr_text = (ocr or {}).get("raw_text") or ""
    out = {"ocr_total": ocr_total, "ocr_agree": None, "proof": "na", "unsure": list(fields.get("unsure_fields") or []),
           "sanity": "pass"}
    if ocr_total is not None and total is not None:
        out["ocr_agree"] = norm_amount(ocr_total) == norm_amount(total)
    elif ocr_total is not None and total is None:
        out["ocr_agree"] = False
    if total is not None and ocr_text:
        results = [checks.words_match_total(ocr_text, total), checks.maths_proof(ocr_text, total),
                   checks.items_proof(ocr_text, total)]
        out["proof"] = "pass" if checks.PASS in results else "fail" if checks.FAIL in results else "na"
    bad = []
    if total is None or (norm_amount(total) or 0) <= 0:
        bad.append("total")
    d = fields.get("bill_date")
    if d is not None and (norm_date(d) is None or date.fromisoformat(norm_date(d)) > today):
        bad.append("bill_date")
    if "invoice_number" in checks.bad_fields({"invoice_number": fields.get("invoice_number")}, today):
        bad.append("invoice_number")
    out["sanity"] = "fail" if bad else "pass"
    out["sanity_bad"] = bad
    out["date_old"] = date_is_old(d, today)
    out["ocr_trusted"] = ocr_total is not None and (ocr or {}).get("total_proof") in TRUSTED_PROOFS \
        and "total_amount" not in (((ocr or {}).get("fields") or {}).get("unsure_fields") or [])
    out["ocr_agree_trusted"] = out["ocr_agree"] if out["ocr_trusted"] else None
    out["ocr_invoice"] = ocr_invoice(ocr, today)
    out["invoice_agree"] = invoice_agrees(fields.get("invoice_number"), out["ocr_invoice"])
    return out


def ocr_invoice(ocr: dict | None, today: date) -> str | None:
    """The free OCR's invoice no., if it read a sane one (a phone number or GSTIN is not an invoice no.)."""
    inv = ((ocr or {}).get("fields") or {}).get("invoice_number")
    if norm_invoice(inv) is None or checks.bad_fields({"invoice_number": inv}, today):
        return None
    return norm_invoice(inv)


def invoice_agrees(value, ocr_inv: str | None) -> bool | None:
    """Check e: None = nothing to compare (no OCR invoice no., or the reader left it blank)."""
    if ocr_inv is None or norm_invoice(value) is None:
        return None
    return norm_invoice(value) == ocr_inv


def date_is_old(value, today: date) -> bool:
    iso = norm_date(value) if value is not None else None
    return bool(iso) and (today - date.fromisoformat(iso)).days > DATE_MAX_AGE_DAYS


def reasons_to_escalate(policy: str, c: dict) -> list[str]:
    rules = POLICIES[policy]
    why = []
    if "money_first" in rules:  # only doubts about the TOTAL are worth a Sonnet call
        if "a" in rules and c["ocr_agree"] is False:
            why.append("ocr_disagrees")
        if "a_trusted" in rules and c.get("ocr_agree_trusted") is False:
            why.append("trusted_ocr_disagrees")
        if "b" in rules and c["proof"] == "fail":
            why.append("proof_fails")
        if "total_amount" in c["unsure"]:
            why.append("haiku_unsure_total")
        if "total" in c.get("sanity_bad", []):
            why.append("total_sanity_fails")
        return why
    if "a" in rules and c["ocr_agree"] is False:
        why.append("ocr_disagrees")
    if "b" in rules and c["proof"] == "fail":
        why.append("proof_fails")
    if "c" in rules and c["unsure"]:
        why.append("haiku_unsure")
    if "d" in rules and c["sanity"] == "fail":
        why.append("sanity_fails")
    return why


def disagree(h: dict, s: dict) -> list[str]:
    """Key fields where Haiku and Sonnet read different values (after normalisation)."""
    out = []
    for f in KEY_FIELDS:
        a, b = h.get(f), s.get(f)
        same = norm_amount(a) == norm_amount(b) if f == "total_amount" else norm_date(a) == norm_date(b)
        if not same:
            out.append(f)
    return out


def owner_flags(policy: str, c: dict) -> list[str]:
    """P5: fields to ASK the owner about instead of calling Sonnet (date / invoice no. / due date / supplier doubts)."""
    rules = POLICIES.get(policy, set())
    if "money_first" not in rules:
        return []
    flags = {f for f in c["unsure"] if f in FIELDS and f != "total_amount"}
    flags |= {f for f in c.get("sanity_bad", []) if f in ("bill_date", "invoice_number")}
    if "date_age" in rules and c.get("date_old"):
        flags.add("bill_date")
    if "invoice_ocr" in rules and c.get("invoice_agree") is False:
        flags.add("invoice_number")
    return [f for f in FIELDS if f in flags]


def _flag(fields: dict, extra: list) -> dict:
    out = dict(fields)
    out["unsure_fields"] = [f for f in FIELDS if f in set(out.get("unsure_fields") or []) | set(extra)]
    return out


def apply_policy(policy: str, haiku: dict, sonnet: dict | None, c: dict, today: date) -> tuple[dict, list]:
    """(fields shown to the owner, fields the policy asks the owner to check). sonnet=None: not escalated."""
    if sonnet is None:
        flags = owner_flags(policy, c)
        return (_flag(haiku, flags), flags) if flags else (haiku, [])
    fields, asked = final_fields(policy, haiku, sonnet)
    rules = POLICIES.get(policy, set())
    extra = []
    if "date_age" in rules and date_is_old(fields.get("bill_date"), today):
        extra.append("bill_date")
    if "invoice_ocr" in rules and invoice_agrees(fields.get("invoice_number"), c.get("ocr_invoice")) is False:
        extra.append("invoice_number")  # check e on the invoice no. actually shown (Sonnet's here)
    if extra:
        fields, asked = _flag(fields, extra), [f for f in FIELDS if f in set(asked) | set(extra)]
    return fields, asked


def final_fields(policy: str, haiku: dict, sonnet: dict | None, ask_on_disagree: bool | None = None) -> tuple[dict, list]:
    """(fields shown to the owner, fields the owner is explicitly asked to check). sonnet=None: not escalated."""
    if sonnet is None:
        return haiku, []
    ask = "ask" in POLICIES.get(policy, set()) if ask_on_disagree is None else ask_on_disagree
    fields = dict(sonnet)
    asked = disagree(haiku, sonnet) if ask else []
    if asked:
        fields["unsure_fields"] = [f for f in FIELDS if f in set(fields.get("unsure_fields") or []) | set(asked)]
    return fields, asked


# ---------------------------------------------------------------- the live reader (bot/app.py builds it)
class PolicyReader:
    """Reads one bill photo with a policy: Haiku, free checks, Sonnet when a check fails.

    haiku / sonnet: callables(image_bytes) -> bot.extract.Extraction. ocr: callable(image_bytes) -> a RapidOCR
    record ({"fields", "raw_text"}) or None; without OCR the checks a and b simply have nothing to compare.
    read(image, allow_escalation=False) never calls Sonnet (daily AI cap reached): the fields a check
    doubted are then flagged instead. policy "haiku" = one Haiku read, no checks (the old behaviour)."""

    def __init__(self, policy: str, haiku, sonnet=None, ocr=None, today_fn=None):
        if policy != "haiku" and policy not in POLICIES:
            raise ValueError(f"READ_POLICY must be haiku or one of {sorted(POLICIES)}, got {policy!r}")
        self.policy, self.haiku, self.sonnet, self.ocr = policy, haiku, sonnet, ocr
        self.today_fn = today_fn

    def _today(self) -> date:
        if self.today_fn:
            return self.today_fn()
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()

    def __call__(self, image_bytes: bytes):
        return self.read(image_bytes)

    def _safe_ocr(self, image_bytes: bytes):
        try:
            return self.ocr(image_bytes)
        except Exception:
            return None  # OCR is only a helper: never block a reading

    def read(self, image_bytes: bytes, allow_escalation: bool = True):
        # the free OCR runs WHILE Haiku reads (Haiku mostly waits on the network), so it adds little to the reply time
        uses_ocr = self.policy != "haiku" and bool(POLICIES[self.policy] & OCR_RULES)
        ocr_job = _OCR_POOL.submit(self._safe_ocr, image_bytes) if uses_ocr and self.ocr else None
        h = self.haiku(image_bytes)
        h.extra = dict(h.extra or {}) | {"policy": self.policy, "calls": [h.model], "reasons": [], "asked": []}
        if self.policy == "haiku" or not h.fields.get("is_bill", True):
            return h
        ocr = ocr_job.result() if ocr_job else None
        c = haiku_checks(h.fields, ocr, self._today())
        why = reasons_to_escalate(self.policy, c) + (["haiku_error"] if h.error else [])
        h.extra["reasons"] = why
        if not why:
            h.fields, asked = apply_policy(self.policy, h.fields, None, c, self._today())
            h.extra["asked"] = asked
            h.extra["ask_date"] = "bill_date" in asked and "money_first" in POLICIES[self.policy]
            return h
        if not allow_escalation or self.sonnet is None:
            doubted = {"total_amount", "bill_date"}
            h.fields = dict(h.fields)
            h.fields["unsure_fields"] = [f for f in FIELDS if f in set(h.fields.get("unsure_fields") or []) | doubted]
            h.extra["not_escalated"] = "ai_cap" if self.sonnet is not None else "no_sonnet"
            return h
        try:
            s = self.sonnet(image_bytes)
        except Exception as err:
            h.extra["sonnet_error"] = type(err).__name__
            h.fields = dict(h.fields)
            h.fields["unsure_fields"] = [f for f in FIELDS if f in set(h.fields.get("unsure_fields") or []) | set(KEY_FIELDS)]
            return h
        fields, asked = apply_policy(self.policy, h.fields, s.fields, c, self._today())
        s.fields = fields
        s.input_tokens += h.input_tokens
        s.output_tokens += h.output_tokens
        s.latency_ms += h.latency_ms
        if s.thinking_tokens is not None or h.thinking_tokens is not None:
            s.thinking_tokens = (s.thinking_tokens or 0) + (h.thinking_tokens or 0)
        s.extra = dict(s.extra or {}) | {"policy": self.policy, "calls": [h.model, s.model], "reasons": why,
                                         "asked": asked, "haiku_fields": h.fields,
                                         "ask_date": asked == ["bill_date"] and "money_first" in POLICIES[self.policy]}
        return s
