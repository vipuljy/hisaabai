"""Bill reading: one module shared by the live bot and the eval.

The eval (src/01_eval_bill_reading.py) tests exactly this code, so what we
measure is what ships.

Flow: image bytes -> vision model -> JSON -> normalised fields.
"""

from __future__ import annotations

import base64
import difflib
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

FIELDS = ["supplier_name", "bill_date", "invoice_number", "total_amount", "due_date"]

# ---------------------------------------------------------------------------
# Prompts. Change wording only by adding a new version, so old eval runs stay comparable.
# ---------------------------------------------------------------------------

_JSON_SPEC = """Reply with JSON only, no other text, in exactly this shape:
{"is_bill": true or false,
 "supplier_name": string or null,
 "bill_date": "YYYY-MM-DD" or null,
 "invoice_number": string or null,
 "total_amount": number or null,
 "due_date": "YYYY-MM-DD" or null,
 "unsure_fields": [names of any fields above you are not sure about]}"""

PROMPTS = {
    "v1": f"""This is a photo of a supplier bill from a small shop in India.
Read the supplier's name, the bill date, the invoice number, the final total amount in rupees, and the payment due date if one is printed.
Use null for anything not on the bill or not readable.
{_JSON_SPEC}""",
    "v2": f"""You read supplier bills for small Indian shops (kirana, medical, hardware). The photo may be printed or handwritten (kachcha bill), in English or Hindi, tilted or partly blurry.

Rules:
- supplier_name: the business that ISSUED the bill (usually at the top), not the buyer, not a GSTIN, not a phone number.
- bill_date: dates on Indian bills are day/month/year. 05/10/26 means 5 October 2026.
- invoice_number: as printed, e.g. "INV-2231" or "1458". Not the GSTIN.
- total_amount: the FINAL amount payable after tax and round-off ("Grand Total", "Net Amount", "Total Rs"). Not a subtotal, not a tax line. Number only, no commas or Rs sign.
- due_date: only if a due date or credit days are printed. If credit days are printed (e.g. "15 days"), add them to bill_date. Otherwise null.
- Never guess. If a value is not readable, use null. If you read a value but are not confident, keep it and list the field in unsure_fields.
- If the image is not a bill (a selfie, a product photo), set is_bill to false and all fields to null.
- If there are several bills in one photo, read only the largest, clearest one and add "multiple_bills" to unsure_fields.
{_JSON_SPEC}""",
}

# ---------------------------------------------------------------------------
# Normalisation: used to clean model output AND to score it against the truth.
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_LEGAL_SUFFIX = re.compile(r"\b(pvt\.?\s*ltd\.?|private\s+limited|limited|ltd\.?|llp)\s*$")


def norm_amount(value) -> int | None:
    """'Rs 1,234.50/-' -> 1235. Compared to the rupee."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        num = Decimal(str(value))
    else:
        cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
        cleaned = cleaned.strip(".")
        if not cleaned:
            return None
        try:
            num = Decimal(cleaned)
        except InvalidOperation:
            return None
    return int(num.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def norm_date(value) -> str | None:
    """Accepts ISO, dd/mm/yy(yy), dd-mm-yyyy, dd.mm.yyyy, '5 Oct 2026'. Returns 'YYYY-MM-DD'."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})", s)
        if m:
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
        else:
            m = re.fullmatch(r"(\d{1,2})\s*[-/ ]?\s*([a-z]{3})[a-z]*\.?\s*[-/ ,]?\s*(\d{2}|\d{4})", s)
            if not m or m[2] not in _MONTHS:
                return None
            d, mo, y = int(m[1]), _MONTHS[m[2]], int(m[3])
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def norm_supplier(value) -> str | None:
    """'M/s. Sharma & Sons Pvt Ltd' -> 'sharma and sons'."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).lower().replace("&", " and ")
    s = re.sub(r"^m\s*/\s*s\.?\s*", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _LEGAL_SUFFIX.sub("", s).strip()
    return s or None


def same_supplier(a, b) -> bool:
    """Same supplier after norm_supplier()? Exact; or one name's words are the START of the other's
    ('navrang enterprises' vs 'navrang enterprises hisar': the city appended); or a near-identical spelling
    (difflib ratio >= 0.9). 'sharma traders' vs 'verma traders' (0.82) is NOT the same."""
    a, b = norm_supplier(a), norm_supplier(b)
    if not a or not b:
        return False
    if a == b:
        return True
    wa, wb = a.split(), b.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if long_[: len(short)] == short:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def norm_invoice(value) -> str | None:
    """' inv - 2231 ' -> 'INV-2231'. Leading zeros are kept."""
    if value is None or str(value).strip() == "":
        return None
    s = re.sub(r"\s+", "", str(value).upper()).strip(".:#")
    return s or None


NORMALISERS = {
    "supplier_name": norm_supplier,
    "bill_date": norm_date,
    "invoice_number": norm_invoice,
    "total_amount": norm_amount,
    "due_date": norm_date,
}


def parse_model_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, even if wrapped in ``` fences or prose."""
    m = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


def clean_fields(raw: dict) -> dict:
    """Normalise every field; unknown keys are dropped."""
    out = {f: NORMALISERS[f](raw.get(f)) for f in FIELDS}
    out["is_bill"] = bool(raw.get("is_bill", True))
    unsure = raw.get("unsure_fields") or []
    out["unsure_fields"] = [u for u in unsure if isinstance(u, str)]
    return out


# ---------------------------------------------------------------------------
# Calling the model
# ---------------------------------------------------------------------------

MAX_SIDE = 1568  # longer images cost more tokens and are not read better
# Haiku 5.5 / Sonnet 5.5 think adaptively by default, and max_tokens caps thinking + answer together.
# 600 was too small (B013: the whole budget went on thinking, the JSON was cut off). Thinking is kept on.
MAX_OUTPUT_TOKENS = 2000
# Short text calls (voice payment, question classifier): the answer is ~30 tokens, but thinking shares the
# budget, so 120-150 could leave no room for the JSON. 1000 keeps a margin and costs only what is used.
SMALL_CALL_MAX_TOKENS = 1000


def _usage_thinking_tokens(usage) -> int | None:
    """usage.output_tokens_details.thinking_tokens (part of output_tokens, billed as output), if reported."""
    details = getattr(usage, "output_tokens_details", None)
    if details is None:
        return None
    value = details.get("thinking_tokens") if isinstance(details, dict) else getattr(details, "thinking_tokens", None)
    return int(value) if value is not None else None


# A 12 MP photo needs ~80 MB while it is decoded and turned; on a 512 MB host (Render free) a burst of photos
# decoded at once ran out of memory in a load test (542 MB peak, docs/13 Build log). At most 2 decodes run at a
# time; the others wait a fraction of a second. The pixels are exactly the same as before.
IMAGE_DECODE_SLOTS = threading.BoundedSemaphore(2)


def prepare_image(image_bytes: bytes) -> tuple[bytes, str]:
    """Shrink to MAX_SIDE on the long edge and re-encode as JPEG."""
    from PIL import Image, ImageOps

    with IMAGE_DECODE_SLOTS:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos carry a rotation flag
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        del img
    return buf.getvalue(), "image/jpeg"


@dataclass
class Extraction:
    fields: dict
    raw_text: str
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    extra: dict = field(default_factory=dict)
    stop_reason: str | None = None          # "end_turn", "max_tokens" (= truncated) ...
    block_types: list = field(default_factory=list)  # e.g. ["thinking", "text"]
    thinking_tokens: int | None = None      # included in output_tokens (billed); None if not reported


def extract_bill(image_bytes: bytes, model: str, prompt_version: str = "v2", client=None) -> Extraction:
    """Read one bill with a Claude vision model. `client` is an anthropic.Anthropic()."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    data, media_type = prepare_image(image_bytes)
    started = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": PROMPTS[prompt_version]},
            ],
        }],
    )
    latency = int((time.perf_counter() - started) * 1000)
    text = "".join(getattr(b, "text", None) or "" for b in resp.content)  # thinking blocks have no text
    ex = Extraction(fields={}, raw_text=text, model=model, prompt_version=prompt_version,
                    input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                    latency_ms=latency, stop_reason=getattr(resp, "stop_reason", None),
                    block_types=[getattr(b, "type", None) or "text" for b in resp.content],
                    thinking_tokens=_usage_thinking_tokens(resp.usage))
    blank = {f: None for f in FIELDS} | {"is_bill": True, "unsure_fields": list(FIELDS)}
    if ex.stop_reason == "max_tokens":  # cut off: never trust a half reply, even if some JSON parsed
        ex.error, ex.fields = "truncated", blank
        return ex
    try:
        ex.fields = clean_fields(parse_model_json(text))
    except (ValueError, json.JSONDecodeError) as err:
        ex.error = f"bad_json: {err}"
        ex.fields = blank
    return ex
