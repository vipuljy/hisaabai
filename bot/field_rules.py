"""Find bill fields in plain text with rules: no AI, no cost.

Input: text from a PDF's text layer (bot/read_pdf.py) or, later, from OCR (bot/read_ocr.py).
Output: raw values for the 5 fields; bot.extract.clean_fields() then normalises them
exactly like a Claude reply, so the eval scores both the same way.

Written for messy Indian bills: OCR slips in labels ("GRAND T0TAL"), extra spaces,
"Rs."/"₹" before amounts, lakh commas (1,01,757.00), day-first dates, and amounts
printed on the line AFTER their label.
"""

from __future__ import annotations

import re

from bot.extract import FIELDS, clean_fields, norm_date

# ---------------------------------------------------------------- building blocks
_LOOKALIKE = {"o": "[o0]", "i": "[il1|]", "l": "[l1i|]", "s": "[s5]"}


def fuzzy(label: str) -> str:
    """'grand total' -> a regex that also matches 'GRAND T0TAL', 'GRAND  TOTAL', 'GRANDTOTAL'."""
    words = [r"\s?".join(_LOOKALIKE.get(c, re.escape(c)) for c in w) for w in label.lower().split()]
    return r"\s*".join(words)


# An amount: 4769 | 4769.00 | 21,390.35 | 1,01,757 | OCR-spaced "21 ,390. 35". Not part of a code like I26-007351.
_NUM = (r"(?<![\w,])(?<!\d\.)(?<!\d[-/])"
        r"(\d{1,3}(?:\s?,\s?\d{2,3})+(?:\s?\.\s?\d{1,2})?|\d+(?:\s?\.\s?\d{1,2})?)(?![\w,]|\.\d|[-/]\d)")
AMOUNT_RX = re.compile(_NUM)
_CURRENCY = r"(?:rs\.?|inr|₹|rupees)?"
_ONLY_AMOUNT = re.compile(r"\s*[:\-=]?\s*" + _CURRENCY + r"\s*[:\-]?\s*" + _NUM + r"\s*(?:/-)?\s*", re.I)
BALANCE_RX = re.compile(r"balance|amount\s*due|\bb\s*/\s*f\b|\bc\s*/\s*f\b", re.I)


def parse_amount(s: str) -> float | None:
    try:
        return float(re.sub(r"[\s,]", "", s))
    except ValueError:
        return None


def amounts_in(line: str) -> list[float]:
    """Every amount on one line, in order."""
    out = [parse_amount(m.group(1)) for m in AMOUNT_RX.finditer(line)]
    return [a for a in out if a is not None]


def _lines(text: str) -> list[str]:
    return [l.strip() for l in (text or "").splitlines()]


def _pure_amount(line: str) -> str | None:
    """'435,064.00' or 'Rs. 4,769/-' alone on a line -> the number."""
    m = _ONLY_AMOUNT.fullmatch(line)
    return m.group(1) if m else None


# ---------------------------------------------------------------- total
# Priority order: the first label that is found wins (one pharma format prints INVOICE AMOUNT before NET AMOUNT PAYABLE).
TOTAL_LABELS = [
    ("net amount payable", fuzzy("net amount payable")),
    ("grand total", fuzzy("grand total")),
    ("total invoice value", fuzzy("total invoice value")),
    ("net payment", fuzzy("net payment")),
    ("net amount", fuzzy("net amount")),
    ("bill amount", fuzzy("bill amount")),
    ("total", fuzzy("total") + r"(?!\s*(?:tax|taxable|wt|weight|qty|quantity|items?|disc|cgst|sgst|igst|gst|b\s*/\s*f|c\s*/\s*f))"),
]
_SUB_BEFORE = re.compile(fuzzy("sub") + r"\s*[-.]?\s*$", re.I)


def _amount_after_label(lines: list[str], i: int, end: int) -> tuple[str, int] | None:
    """(number, line index): same line after the label; else a line holding only a number in the next 2 lines."""
    m = AMOUNT_RX.search(lines[i][end:])
    if m:
        return m.group(1), i
    for j in (i + 1, i + 2):
        if j < len(lines):
            found = _pure_amount(lines[j])
            if found and parse_amount(found):  # a lone 0.00 nearby is another field (freight, discount)
                return found, j
    return None


def locate_total(text: str) -> dict | None:
    """Where the total is: {"amount", "label", "label_line", "number_line"} (line indices of text.splitlines()).
    Best label by priority; last match of that label wins. Skips balance / B/F / C/F lines."""
    lines = _lines(text)
    for name, pattern in TOTAL_LABELS:
        rx = re.compile(r"(?<![a-z])" + pattern, re.I)
        best = None
        for i, line in enumerate(lines):
            if BALANCE_RX.search(line):
                continue
            for m in rx.finditer(line):
                if name == "total" and _SUB_BEFORE.search(line[: m.start()]):
                    continue
                found = _amount_after_label(lines, i, m.end())
                if found:
                    best = {"amount": found[0], "label": name, "label_line": i, "number_line": found[1]}
        if best:
            return best
    return None


def find_total(text: str) -> tuple[str | None, str | None]:
    """(amount as printed, label used)."""
    loc = locate_total(text)
    return (loc["amount"], loc["label"]) if loc else (None, None)


# ---------------------------------------------------------------- dates
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE = (r"(\d{1,2}\s?[/\-.]\s?\d{1,2}\s?[/\-.]\s?(?:\d{4}|\d{2})(?!\d)"
         r"|\d{1,2}[\s\-/.]*" + _MONTH + r"[\s\-/.,]*(?:\d{4}|\d{2})(?!\d))")
_DATE_START = re.compile(r"[\s:.\-]*" + _DATE, re.I)
_NOT_BILL_DATE = re.compile(
    r"(due|order|lr|rr|po|ack\.?|g\.?\s*r\.?|way\s*bill|exp\.?|expiry|mfg\.?|challan|delivery|dispatch|"
    r"supply|cheque|chq|dt)\s*[.:]?\s*$", re.I)


def _to_iso(raw: str) -> str | None:
    if re.search(r"[a-z]", raw, re.I):
        return norm_date(re.sub(r"[\s.\-/,]+", " ", raw).strip())
    return norm_date(re.sub(r"\s+", "", raw))


def _date_after(lines: list[str], i: int, end: int) -> str | None:
    m = _DATE_START.match(lines[i][end:])
    if m and _to_iso(m.group(1)):
        return _to_iso(m.group(1))
    if not lines[i][end:].strip(" :.-") and i + 1 < len(lines):  # label alone: date may be on the next line
        m = re.fullmatch(r"[\s:.\-]*" + _DATE + r"\s*", lines[i + 1], re.I)
        if m:
            return _to_iso(m.group(1))
    return None


_BILL_DATE_LABEL = re.compile(
    r"(?<![a-z])(?:(?:" + fuzzy("invoice") + "|" + fuzzy("inv") + r"\.?|" + fuzzy("bill") + r")\s*)?"
    + fuzzy("date") + r"d?(?![a-z])", re.I)
_DT_LABEL = re.compile(r"(?<![a-z])d\s?t\.?(?![a-z])", re.I)  # "Dt. 3-9-26" on kachcha bills; tried after "Date"
_DUE_DATE_LABEL = re.compile(r"(?<![a-z])" + fuzzy("due date") + r"(?![a-z])", re.I)


def find_bill_date(text: str) -> str | None:
    """First 'Date' / 'Invoice Date' / 'Bill Date' that is not a due, order, LR, expiry... date."""
    lines = _lines(text)
    for rx in (_BILL_DATE_LABEL, _DT_LABEL):
        for i, line in enumerate(lines):
            for m in rx.finditer(line):
                if _NOT_BILL_DATE.search(line[: m.start()]):
                    continue
                found = _date_after(lines, i, m.end())
                if found:
                    return found
    return None


def find_due_date(text: str) -> str | None:
    lines = _lines(text)
    for i, line in enumerate(lines):
        for m in _DUE_DATE_LABEL.finditer(line):
            found = _date_after(lines, i, m.end())
            if found:
                return found
    return None


# ---------------------------------------------------------------- invoice number
_NO = r"(?:" + fuzzy("no") + r"\.?|" + fuzzy("number") + r"|#)(?![a-z])"
_INVOICE_LABEL = re.compile(r"(?<![a-z])(?:" + fuzzy("invoice") + "|" + fuzzy("inv") + r"\.?)\s*" + _NO + r"[\s:.\-#]*", re.I)
_BILL_LABEL = re.compile(r"(?<![a-z])" + fuzzy("bill") + r"\s*" + _NO + r"[\s:.\-#]*", re.I)
_WAY_BILL_BEFORE = re.compile(r"way\s*[-.]?\s*$", re.I)
_TOKEN = re.compile(r"([A-Z0-9][A-Z0-9\-/]{0,24})(?![A-Z0-9])", re.I)


def _token_after(lines: list[str], i: int, end: int) -> str | None:
    m = _TOKEN.match(lines[i][end:])
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip("-/")
    if not lines[i][end:].strip() and i + 1 < len(lines):
        m = re.fullmatch(r"[\s:#\-]*([A-Z0-9][A-Z0-9\-/]{0,24})\s*", lines[i + 1], re.I)
        if m and re.search(r"\d", m.group(1)):
            return m.group(1)
    return None


def find_invoice_number(text: str) -> str | None:
    """'Invoice No.: I26-007351', 'INVOICE NO: 47K5520193', 'Bill No 1458' (not 'E-Way Bill No')."""
    lines = _lines(text)
    for rx in (_INVOICE_LABEL, _BILL_LABEL):
        for i, line in enumerate(lines):
            for m in rx.finditer(line):
                if rx is _BILL_LABEL and _WAY_BILL_BEFORE.search(line[: m.start()]):
                    continue
                found = _token_after(lines, i, m.end())
                if found:
                    return found
    return None


# ---------------------------------------------------------------- supplier
_HEADER_PREFIX = re.compile(r"^(?:(?:tax|gst|retail|sales)\s*)?invoice\s*(?:-\s*goods)?\s*[-:]?\s*", re.I)
_SKIP_START = re.compile(
    r"^(?:original|duplicate|triplicate|scan\s*to\s*pay|from|page|bill\s*to|ship\s*to|bill\s*of\s*supply|"
    r"cash\s*memo|estimate|quotation|credit|cash|debit|to)\b", re.I)
_SKIP_ANY = re.compile(
    r"@|www\.|https?:|gstin|gst\s*no|phone|\bph\.|\bmob|e-?mail|\btel\b|d\.?\s*l\.?\s*no|\bpan\b|\bcin\b|fssai|"
    r"\|\||!!|namah|shree\s*ganesh|jai\s*mata|\bom\b", re.I)


def find_supplier(text: str) -> str | None:
    """First line near the top that looks like a business name (the seller is printed first)."""
    for line in _lines(text)[:12]:
        line = _HEADER_PREFIX.sub("", line).strip(" -:")
        if not line or _SKIP_START.match(line) or _SKIP_ANY.search(line):
            continue
        letters = sum(c.isalpha() for c in line)
        if letters < 3 or letters < 0.6 * len(line.replace(" ", "")) or len(line) > 60:
            continue
        line = re.sub(r"^m\s*/\s*s\.?\s*", "", line, flags=re.I)
        return line.split(",")[0].strip()  # "NAVRANG ENTERPRISES, HISAR" -> drop the city
    return None


# ---------------------------------------------------------------- all together
def extract_fields(text: str) -> dict:
    """Raw values (as printed) for the 5 fields, None where nothing was found."""
    total, _ = find_total(text)
    return {
        "supplier_name": find_supplier(text),
        "bill_date": find_bill_date(text),
        "invoice_number": find_invoice_number(text),
        "total_amount": total,
        "due_date": find_due_date(text),
    }


def read_text(text: str, today=None) -> tuple[dict, dict]:
    """Rules + free checks on plain text -> (fields like a Claude reply, check results).
    A failed check lists the field in unsure_fields, so the bot asks instead of trusting it.
    A total found only under the plain "Total" label is always unsure (often an item-table row)."""
    from bot import checks  # local import: checks uses this module's amount helpers

    fields = clean_fields(extract_fields(text) | {"is_bill": True})
    results, unsure = checks.run_checks(text, fields, today=today)
    loc = locate_total(text)
    if fields.get("total_amount") is not None and loc and loc["label"] == WEAK_TOTAL_LABEL:
        unsure = sorted(set(unsure) | {"total_amount"}, key=FIELDS.index)
    fields["unsure_fields"] = unsure
    return fields, results


# ---------------------------------------------------------------- seller GSTIN
WEAK_TOTAL_LABEL = "total"
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"(?<![A-Z0-9])([0-9OIlSB]{2}[A-Z0-9]{5}[0-9OIlSB]{4}[A-Z0-9][A-Z0-9][Z2][A-Z0-9])(?![A-Z0-9])", re.I)
_AS_DIGIT = str.maketrans("OoIlSB", "001158")
_AS_LETTER = str.maketrans("0158", "OISB")


def gstin_ok(g: str) -> bool:
    """True if g has the GSTIN shape AND its last character is the correct check character."""
    g = g.upper()
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", g):
        return False
    total = 0
    for i, c in enumerate(g[:14]):
        product = _GST_CHARS.index(c) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36] == g[14]


def _repair_gstin(raw: str) -> str:
    """Undo common OCR swaps by position: digits where digits belong, letters where letters belong."""
    g = raw.upper()
    digits = lambda t: t.translate(_AS_DIGIT)  # noqa: E731
    letters = lambda t: t.translate(_AS_LETTER)  # noqa: E731
    return digits(g[:2]) + letters(g[2:7]) + digits(g[7:11]) + letters(g[11]) + g[12] + "Z" + g[14]


def find_seller_gstin(text: str) -> str | None:
    """First valid GSTIN in the top third of the page (the seller's; the buyer's comes lower or to the right).
    Only returned if the check character is right, so a misread GSTIN is never recorded."""
    lines = _lines(text)
    top = lines[: max(1, -(-len(lines) // 3))]
    for line in top:
        for m in _GSTIN_SHAPE.finditer(line):
            g = _repair_gstin(m.group(1))
            if gstin_ok(g):
                return g
    return None


def blank_fields() -> dict:
    return {f: None for f in FIELDS} | {"is_bill": True, "unsure_fields": []}
