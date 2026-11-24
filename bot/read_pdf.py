"""Read a bill from a PDF's text layer: free, instant, no AI.

Suppliers often send bills as PDFs made by billing software (Marg, KARP, SAP). Those
PDFs carry real text, so bot/field_rules.py can read the fields without a photo or a model.
Scanned PDFs (photos saved as PDF) have no text: they come back all-null with error
"no_text", so the cascade passes them on to OCR or Claude.
Eval results are cached in the same format as the Claude runs (tokens 0).
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from bot import field_rules
from bot.extract import FIELDS, Extraction

READER = "pdf_text"
RULES_VERSION = "rules_v1"
MIN_CHARS = 80  # fewer letters/digits than this (after dropping watermark lines) = scanned page
_WATERMARK = re.compile(r"created with|https?://|www\.", re.I)


def page_text(pdf_path: Path, page: int) -> str:
    """Text layer of one page (page numbers start at 1)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pg = pdf[page - 1]
        tp = pg.get_textpage()
        text = tp.get_text_range()
        tp.close()
        pg.close()
        return text
    finally:
        pdf.close()


def has_text(text: str) -> bool:
    kept = "".join(l for l in (text or "").splitlines() if not _WATERMARK.search(l))
    return len(re.sub(r"\W", "", kept)) >= MIN_CHARS


def _blank(error: str, raw_text: str = "") -> Extraction:
    return Extraction(fields=field_rules.blank_fields(), raw_text=raw_text, model=READER,
                      prompt_version=RULES_VERSION, error=error, extra={"checks": {}})


def read_pdf_text(pdf_path: Path, page: int, today=None) -> Extraction:
    """One PDF page -> fields (same shape as a Claude reply), with check results in .extra."""
    started = time.perf_counter()
    try:
        text = page_text(pdf_path, page)
    except Exception as err:  # missing file, broken PDF, page out of range
        ex = _blank(f"pdf_error: {err}")
    else:
        if not has_text(text):
            ex = _blank("no_text", text)
        else:
            fields, results = field_rules.read_text(text, today=today)
            ex = Extraction(fields=fields, raw_text=text, model=READER, prompt_version=RULES_VERSION,
                            extra={"checks": results})
    ex.latency_ms = int((time.perf_counter() - started) * 1000)
    return ex


# ---------------------------------------------------------------- a PDF sent on WhatsApp (bytes, not a file)
MAX_PAGES = 10          # supplier bills are 1-3 pages; more is a statement or catalogue -> ask for the bill page
RENDER_LONG_SIDE = 1600  # about a WhatsApp photo, so a scanned page reads like a photo
MIN_CHARS_SHORT_PAGE = 15  # in a PDF that has text, a page this short is still read (a last page with only the total)
# A title near the top of page 1. Not "estimate": many kachha (non-GST) bills are printed as ESTIMATE.
NOT_A_BILL = re.compile(r"(?<![a-z])(?:quotation|quote\s*no|proforma|pro[\s-]*forma|price\s*list|rate\s*list)(?![a-z])",
                        re.I)
HEADER_FIELDS = ("supplier_name", "bill_date", "invoice_number", "due_date")  # printed on page 1 of a multi-page bill


class PdfTooBig(ValueError):
    pass


def read_whatsapp_pdf(data: bytes, today=None) -> dict:
    """What to do with a PDF bill. Raises PdfTooBig (> MAX_PAGES) or pdfium errors (broken / password).

    route "text":  a text PDF whose rules found a trusted total (words = digits, maths...): fields, no AI call.
    route "image": a scanned PDF, or a text PDF with no total / a doubtful total: the page image goes to the normal
                   photo flow (policy P5). Scanned multi-page PDF: the LAST page (where totals are printed).
    Both routes carry "image" (JPEG of the page used) so the bot keeps the same copy as for a photo."""
    import pypdfium2 as pdfium

    from bot.extract import IMAGE_DECODE_SLOTS

    with IMAGE_DECODE_SLOTS:  # rendering a page is the same memory spike as decoding a photo
        return _read_whatsapp_pdf(pdfium.PdfDocument(data), today)


def _read_whatsapp_pdf(pdf, today) -> dict:
    try:
        n = len(pdf)
        if n > MAX_PAGES:
            raise PdfTooBig(n)
        texts = []
        for i in range(n):
            pg = pdf[i]
            tp = pg.get_textpage()
            texts.append(tp.get_text_range())
            tp.close()
            pg.close()
        out = {"pages": n, "route": "image", "reason": "scanned", "page": n, "fields": None}
        if texts and NOT_A_BILL.search("\n".join((texts[0] or "").splitlines()[:15])):
            out.update(route="none", reason="not_a_bill")   # 24 Nov: a quotation is a price offer, not goods bought
            return out
        text_pages = []
        if any(has_text(t) for t in texts):  # a text PDF: its short last page ('GRAND TOTAL ...') counts too
            text_pages = [i for i, t in enumerate(texts) if len(re.sub(r"\W", "", t or "")) >= MIN_CHARS_SHORT_PAGE]
        if text_pages:
            read = {i: field_rules.read_text(texts[i], today=today)[0] for i in text_pages}
            with_total = [i for i in text_pages if read[i].get("total_amount") is not None]
            if not with_total:
                out.update(reason="no_total")
            else:
                k = with_total[-1]  # the last page with a total: the grand total of a multi-page bill
                fields = dict(read[k])
                first = read[text_pages[0]]
                unsure = set(fields.get("unsure_fields") or [])
                for f in HEADER_FIELDS:  # page 1 wins: on a later page the rules may take any line as the name
                    if k != text_pages[0] and first.get(f) is not None:
                        fields[f] = first[f]
                        unsure.discard(f)
                        unsure |= {f} & set(first.get("unsure_fields") or [])
                fields["unsure_fields"] = [f for f in FIELDS if f in unsure]
                trusted = "total_amount" not in unsure
                out.update(page=k + 1, fields=fields, route="text" if trusted else "image",
                           reason="trusted_total" if trusted else "total_unsure")
        out["image"] = render_page(pdf, out["page"])
        return out
    finally:
        pdf.close()


def render_page(pdf, page: int, long_side: int = RENDER_LONG_SIDE) -> bytes:
    """One page (from 1) as a JPEG about the size of a WhatsApp photo."""
    import io

    pg = pdf[page - 1]
    try:
        w, h = pg.get_size()
        img = pg.render(scale=long_side / max(w, h, 1)).to_pil().convert("RGB")
    finally:
        pg.close()
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()

# ---------------------------------------------------------------- eval helpers
def load_source_map(path: Path) -> dict[str, dict]:
    """data/eval/source_map.csv -> {bill_id: {"file": name, "page": int or None}}."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            m = re.search(r"\d+", r.get("page") or "")
            out[r["bill_id"]] = {"file": r.get("original_file") or "", "page": int(m.group()) if m else None}
    return out


def read_bill(bill_id: str, source_map: dict, inbox_dir: Path, cache_dir: Path, today=None) -> dict:
    """Read one labelled bill from its original PDF and write the cache record (always re-run: it is free)."""
    src = source_map.get(bill_id) or {}
    if not src.get("file", "").lower().endswith(".pdf") or not src.get("page"):
        ex = _blank("not_pdf")
    else:
        ex = read_pdf_text(inbox_dir / src["file"], src["page"], today=today)
    record = {
        "bill_id": bill_id, "model": READER, "prompt": RULES_VERSION,
        "fields": ex.fields, "raw_text": ex.raw_text, "error": ex.error,
        "input_tokens": 0, "output_tokens": 0, "latency_ms": ex.latency_ms,
        "checks": ex.extra.get("checks", {}),
        "supplier_gstin": field_rules.find_seller_gstin(ex.raw_text) if ex.error is None else None,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{bill_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record
