"""Free checks that catch a wrong bill read without asking a model.

Each check returns "pass", "fail" or "na" (not enough on the bill to judge):
  words_match_total  "Rupees ... Only" in words equals the total in digits
  items_sum          line amounts + tax + round-off add up to the total (within Rs 2)
  sane_fields        dates plausible, total between 0 and 50 lakh, invoice no. not a GSTIN/phone
  not_balance        the total is not just an old balance / B/F / C/F figure
run_checks() runs them all and returns the fields a failed check makes doubtful,
so the bot asks the owner instead of showing a confident wrong value.
"""

from __future__ import annotations

import re
from datetime import date

from bot.extract import FIELDS, norm_amount, norm_date
from bot.field_rules import BALANCE_RX, amounts_in

PASS, FAIL, NA = "pass", "fail", "na"

# ---------------------------------------------------------------- Indian number words
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_TENS["fourty"] = 40  # common misspelling on bills
_BIG = {"thousand": 1_000, "lakh": 1_00_000, "lakhs": 1_00_000, "lac": 1_00_000, "lacs": 1_00_000,
        "crore": 1_00_00_000, "crores": 1_00_00_000}
_FILLER = {"and", "rupees", "rupee", "rs", "inr", "only", "in", "words", "amount", "indian"}


def _words_to_int(tokens: list[str]) -> int | None:
    total, current, seen = 0, 0, False
    for t in tokens:
        if t in _FILLER:
            continue
        if t in _UNITS:
            current += _UNITS[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t in _BIG:
            total += (current or 1) * _BIG[t]
            current = 0
        else:
            return None  # an unknown word: do not guess
        seen = True
    return total + current if seen else None


def words_to_number(phrase: str) -> float | None:
    """'Rupees One Lakh One Thousand Seven Hundred Fifty Seven Only' -> 101757.
    'Twenty One Thousand ... and Paisa Thirty Five only' -> 21xxx.35. Returns None if any word is unknown."""
    tokens = re.sub(r"[^a-z]+", " ", (phrase or "").lower()).split()
    for cut in ("paisa", "paise"):
        if cut in tokens:
            k = tokens.index(cut)
            rupees, paisa = _words_to_int(tokens[:k]), _words_to_int(tokens[k + 1:])
            if rupees is None:
                return None
            return rupees + (paisa or 0) / 100
    value = _words_to_int(tokens)
    return None if value is None else float(value)


_WORDS_RX = re.compile(r"(?<![a-z])(?:rupees|rs\.?|inr)\s*:?\s*((?:[a-z]+[\s\-,]+){1,25}?)only\b", re.I)


def amount_in_words(text: str) -> float | None:
    """First 'Rs./Rupees <words> only' on the bill that parses to a number.
    Skips a phrase labelled as TAX ('Tax Amount (in words): ... Only'): that is the GST, not the bill total."""
    text = text or ""
    for m in _WORDS_RX.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if re.search(r"\btax\b", text[line_start:m.start()], re.I):
            continue
        value = words_to_number(m.group(1))
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------- the checks
def words_match_total(text: str, total) -> str:
    words = amount_in_words(text)
    if words is None or total is None:
        return NA
    return PASS if abs(words - float(total)) < 1 else FAIL


def items_sum(line_amounts, total, tax: float = 0.0, round_off: float = 0.0) -> str:
    """Only judged when line amounts were read."""
    if not line_amounts or total is None:
        return NA
    return PASS if abs(sum(line_amounts) + tax + round_off - float(total)) <= 2 else FAIL


_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
_PHONE = re.compile(r"^(?:\+?91|0)?[6-9]\d{9}$")


def _as_date(value) -> date | None:
    iso = norm_date(value)
    return date.fromisoformat(iso) if iso else None


def bad_fields(fields: dict, today: date | None = None) -> list[str]:
    """Fields that fail the sanity rules."""
    today = today or date.today()
    bad = []
    for f in ("bill_date", "due_date"):
        if fields.get(f) is not None:
            d = _as_date(fields[f])
            if d is None or abs((d - today).days) > 730:
                bad.append(f)
    bill, due = _as_date(fields.get("bill_date")), _as_date(fields.get("due_date"))
    if bill and due and due < bill and "due_date" not in bad:
        bad.append("due_date")
    if fields.get("total_amount") is not None:
        amt = norm_amount(fields["total_amount"])
        if amt is None or not 0 < amt < 50_00_000:
            bad.append("total_amount")
    if fields.get("invoice_number") is not None:
        inv = re.sub(r"[\s\-]", "", str(fields["invoice_number"]).upper())
        if _GSTIN.match(inv) or _PHONE.match(inv):
            bad.append("invoice_number")
    return bad


def sane_fields(fields: dict, today: date | None = None) -> str:
    if all(fields.get(f) is None for f in ("bill_date", "due_date", "total_amount", "invoice_number")):
        return NA
    return FAIL if bad_fields(fields, today) else PASS


def not_balance(text: str, total) -> str:
    """fail if the total appears on the bill ONLY on balance / amount due / B/F / C/F lines."""
    if total is None:
        return NA
    target = norm_amount(total)
    lines = [l.strip() for l in (text or "").splitlines()]
    on_balance = on_other = False
    for i, line in enumerate(lines):
        if not any(norm_amount(a) == target for a in amounts_in(line)):
            continue
        context = line if re.search(r"[a-z]", line, re.I) else (lines[i - 1] if i else "") + " " + line
        if BALANCE_RX.search(context):
            on_balance = True
        else:
            on_other = True
    if on_other:
        return PASS
    return FAIL if on_balance else NA


def run_checks(text: str, fields: dict, today: date | None = None, line_amounts=None) -> tuple[dict, list[str]]:
    """All checks -> ({check: pass/fail/na}, fields to mark unsure)."""
    total = fields.get("total_amount")
    results = {
        "words_match_total": words_match_total(text, total),
        "items_sum": items_sum(line_amounts, total),
        "sane_fields": sane_fields(fields, today),
        "not_balance": not_balance(text, total),
    }
    unsure = set(bad_fields(fields, today))
    if FAIL in (results["words_match_total"], results["items_sum"], results["not_balance"]):
        unsure.add("total_amount")
    return results, [f for f in FIELDS if f in unsure]


# ---------------------------------------------------------------- maths proofs (trust gates, designed on DEV)
# A total is trusted only with a PROOF: the amount in words equals it, or the bill's own GST maths adds up to it.
# Tolerance is the nearest rupee (|total - expected| <= 0.50), not Rs 1: with Rs 1, a last-digit OCR misread
# (31272 for 31271) would pass the proof.
NEAREST_RUPEE = 0.51
_N = r"(\d[\d,]*(?:\.\d{1,2})?)"
_MARG_GST = re.compile(_N + r"\s*\*\s*(\d+(?:\.\d+)?)\s*(?:\+\s*(\d+(?:\.\d+)?))?\s*%\s*=\s*(\d+(?:\.\d+)?)\s*"
                       r"(?:SGST|IGST|CGST)?(?:\s*\+\s*(\d+(?:\.\d+)?)\s*(?:SGST|CGST))?", re.I)
_RATE_ON = re.compile(r"\b(CGST|SGST|IGST|UTGST)\s*@?\s*(\d+(?:\.\d+)?)\s*%\s*on\s*" + _N, re.I)
_BASE_LABEL = re.compile(r"(?<![a-z])(total\s*taxable\s*(value|amount|amt)|taxable\s*(value|amount|amt)|basic\s*amount|"
                         r"sub\s*total|subtotal)(?![a-z])", re.I)
_TAX_LABEL = re.compile(r"(?<![a-z+])(cgst|sgst|igst|utgst|cess)(?![a-z+])(?!\s*@)", re.I)
_DISC_LABEL = re.compile(r"(?<![a-z])(discount|disc\.?|less)(?![a-z])", re.I)
_ROUND_LABEL = re.compile(r"(?<![a-z])(round\s*off|rounding\s*off|round\s*-?\s*off|r\s*/\s*o)(?![a-z])", re.I)


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _first_amount_after(lines: list[str], i: int, end: int, allow_next: bool = True) -> float | None:
    """First amount after a label that is not a percentage; else a number-only line just below."""
    rest = lines[i][end:]
    for m in re.finditer(r"-?\s*" + _N + r"(\s*-)?", rest):
        tail = rest[m.end():m.end() + 2]
        if "%" in tail or re.match(r"\s*%", rest[m.end():]):
            continue
        value = _f(m.group(1))
        return -value if m.group(0).strip().startswith("-") or (m.group(2) or "").strip() == "-" else value
    if allow_next:
        for j in (i + 1, i + 2):
            if j < len(lines):
                m = re.fullmatch(r"\s*[:\-]?\s*(-?)\s*" + _N + r"\s*(-?)\s*", lines[j])
                if m:
                    return -_f(m.group(2)) if (m.group(1) or m.group(3)) else _f(m.group(2))
    return None


def rate_formula_totals(text: str) -> list[float]:
    """Totals implied by the bill's own tax formulas:
       Marg style  '508.27*2.5+2.5%=12.7SGST+12.7CGST, 443.88*9+9%=39.95SGST+39.95CGST' -> sum(base + tax)
       KARP style  'CGST @ 2.50% on 29781.98' + 'SGST @ 2.50% on 29781.98'               -> base x (1 + rates)"""
    out = []
    parts = []
    for m in _MARG_GST.finditer(text or ""):
        base = _f(m.group(1))
        rate = float(m.group(2)) + float(m.group(3) or 0)
        tax = float(m.group(4)) + float(m.group(5) or 0)
        if m.group(3) and not m.group(5):
            tax *= 2  # '=12.7SGST' printed once for a split rate: the other half is the same
        if base > 0 and abs(tax - base * rate / 100) <= max(1.0, 0.01 * base * rate / 100):
            parts.append(base + tax)
    if parts:
        out.append(sum(parts))
    rates: dict[float, float] = {}
    for m in _RATE_ON.finditer(text or ""):
        base = _f(m.group(3))
        rates[base] = rates.get(base, 0) + float(m.group(2))
    if rates:
        out.append(sum(b * (1 + r / 100) for b, r in rates.items()))
    return out


def summary_parts(text: str) -> dict:
    """The printed GST summary: base (taxable / sub total / basic amount), tax amounts, discounts."""
    lines = [l.strip() for l in (text or "").splitlines()]
    base = None
    taxes, discounts = [], []
    for i, line in enumerate(lines):
        if _RATE_ON.search(line) or re.search(r"cgst\s*\+\s*sgst", line, re.I):
            continue  # 'CGST @ x% on base' is handled by rate_formula_totals; 'CGST+SGST' is an item column
        if base is None:
            m = _BASE_LABEL.search(line)
            if m:
                base = _first_amount_after(lines, i, m.end())
                continue
        m = _TAX_LABEL.search(line)
        if m:
            v = _first_amount_after(lines, i, m.end())
            if v and v > 0:
                taxes.append(v)
            continue
        m = _DISC_LABEL.search(line)
        if m:
            v = _first_amount_after(lines, i, m.end(), allow_next=False)
            if v and v > 0:
                discounts.append(abs(v))
    return {"base": base, "taxes": taxes, "discounts": discounts}


def summary_totals(text: str) -> list[float]:
    """Totals implied by a printed GST summary: taxable / sub total (- discounts) + CGST + SGST (+ IGST).
    Needs a base and at least one tax amount; every tax amount must be smaller than the base."""
    p = summary_parts(text)
    base, taxes = p["base"], p["taxes"]
    if not base or base <= 0 or not taxes or any(t >= base for t in taxes):
        return []
    out = [base + sum(taxes)]
    if p["discounts"]:
        out.append(base - sum(p["discounts"]) + sum(taxes))
    return out


def round_off(text: str) -> float | None:
    lines = [l.strip() for l in (text or "").splitlines()]
    for i, line in enumerate(lines):
        m = _ROUND_LABEL.search(line)
        if m:
            v = _first_amount_after(lines, i, m.end())
            if v is not None and abs(v) < 1:
                return v
    return None


def maths_proof(text: str, total) -> str:
    """pass if the bill's own maths (rate formulas or GST summary) adds up to the total, to the nearest rupee."""
    if total is None:
        return NA
    expected = rate_formula_totals(text) + summary_totals(text)
    if not expected:
        return NA
    ro = round_off(text)
    t = float(total)
    for e in expected:
        if abs(t - e) <= NEAREST_RUPEE or (ro is not None and abs(t - (e + ro)) <= 0.05):
            return PASS
    return FAIL


def items_proof(text: str, total) -> str:
    """Line items: amounts at the end of item rows (above the first total / tax / round-off line)
    + the summary's tax amounts (+ round-off) = total, to the nearest rupee. Needs 2+ item rows."""
    if total is None:
        return NA
    lines = [l.strip() for l in (text or "").splitlines()]
    items = []
    for line in lines:
        if _BASE_LABEL.search(line) or _TAX_LABEL.search(line) or re.search(r"total|round", line, re.I):
            break
        m = re.search(r"(\d[\d,]*\.\d{2})\s*$", line)
        if m and len(re.findall(r"\d+(?:\.\d+)?", line)) >= 3:
            items.append(_f(m.group(1)))
    if len(items) < 2:
        return NA
    e = sum(items) + sum(summary_parts(text)["taxes"])
    ro = round_off(text)
    t = float(total)
    ok = abs(t - e) <= NEAREST_RUPEE or (ro is not None and abs(t - (e + ro)) <= 0.05)
    return PASS if ok else FAIL


def total_proof(text: str, total) -> str | None:
    """'words' or 'maths' if a proof passes for this total, else None."""
    if words_match_total(text, total) == PASS:
        return "words"
    if maths_proof(text, total) == PASS or items_proof(text, total) == PASS:
        return "maths"
    return None


def total_is_safe(text: str, total) -> bool:
    """Sanity + balance: 0 < total < 50 lakh and not only on a balance / B/F / C/F line."""
    if total is None:
        return False
    amt = norm_amount(total)
    return amt is not None and 0 < amt < 50_00_000 and not_balance(text, total) != FAIL
