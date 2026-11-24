"""Supplier PDFs sent as WhatsApp documents (bot/read_pdf.read_whatsapp_pdf + Bot._on_document).
Every PDF here is built inside the test (invented supplier, no real bill)."""

import io
import itertools

import pytest
from conftest import OWNER, _events, onboard, tap

pytest.importorskip("pypdfium2")

from bot import messages as M  # noqa: E402
from bot import read_pdf  # noqa: E402
from bot.conversation import Limits  # noqa: E402

_ids = itertools.count(1)
HEADER = ["SHIV MEDICAL DISTRIBUTORS", "12 Station Road, Jaipur", "TAX INVOICE",
          "Invoice No: D5721   Date: 05/10/2026"]
ITEMS = ["Paracetamol 500 10 x 45.00 450.00", "Amoxicillin 250 5 x 120.00 600.00",
         "Cough syrup 100ml 12 x 85.00 1020.00"]
TOTAL = ["GRAND TOTAL 4769.00", "Rupees Four Thousand Seven Hundred Sixty Nine Only"]


def text_pdf(pages: list[list[str]]) -> bytes:
    """A minimal text PDF (Helvetica), one list of lines per page."""
    objs = {1: "<< /Type /Catalog /Pages 2 0 R >>", 3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for k, lines in enumerate(pages):
        page_id, content_id = 4 + 2 * k, 5 + 2 * k
        kids.append(f"{page_id} 0 R")
        esc = [l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for l in lines]
        stream = "BT /F1 11 Tf 14 TL 50 800 Td " + " ".join(f"({l}) Tj T*" for l in esc) + " ET"
        objs[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_id} 0 R "
                         f"/Resources << /Font << /F1 3 0 R >> >> >>")
        objs[content_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out, offsets = io.BytesIO(), {}
    out.write(b"%PDF-1.4\n")
    for i in sorted(objs):
        offsets[i] = out.tell()
        out.write(f"{i} 0 obj\n{objs[i]}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    n = max(objs) + 1
    out.write(f"xref\n0 {n}\n0000000000 65535 f \n".encode())
    for i in range(1, n):
        out.write(f"{offsets[i]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def scanned_pdf(pages: int = 1) -> bytes:
    """Image-only pages (a phone photo saved as PDF): no text layer."""
    from PIL import Image, ImageDraw
    imgs = []
    for k in range(pages):
        img = Image.new("RGB", (850, 1100), "white")
        ImageDraw.Draw(img).text((60, 60 + 40 * k), f"page {k + 1} GRAND TOTAL 4769", fill="black")
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def document(mime="application/pdf", filename="bill.pdf", sender=OWNER):
    return {"id": f"wamid.doc{next(_ids)}", "from": sender, "type": "document",
            "document": {"id": f"media{next(_ids)}", "mime_type": mime, "filename": filename}}


def serve(world, data: bytes):
    seen = []

    async def download(media_id):
        seen.append(media_id)
        return data
    world.wa.download_media = download
    return seen


def received(world) -> dict:
    return next(e["props"] for e in reversed(_events(world.store)) if e["event"] == "bill_received")


# ---------------------------------------------------------------- the reader
def test_text_pdf_with_a_proved_total_is_read_free():
    r = read_pdf.read_whatsapp_pdf(text_pdf([HEADER + ITEMS + TOTAL]))
    assert r["route"] == "text" and r["pages"] == 1 and r["page"] == 1
    assert r["fields"]["total_amount"] == 4769 and r["fields"]["invoice_number"] == "D5721"
    assert r["image"][:2] == b"\xff\xd8"  # the page is kept as a JPEG, like a photo


def test_multi_page_text_pdf_uses_the_page_with_the_total_and_page_one_for_the_rest():
    r = read_pdf.read_whatsapp_pdf(text_pdf([HEADER + ["(continued)"] * 3, ITEMS * 3, ["Page 3 of 3"] + TOTAL]))
    assert r["route"] == "text" and r["pages"] == 3 and r["page"] == 3
    assert r["fields"]["total_amount"] == 4769 and r["fields"]["supplier_name"] == "shiv medical distributors"
    assert r["fields"]["bill_date"] == "2026-10-05"


def test_text_pdf_without_a_total_goes_to_the_photo_flow():
    r = read_pdf.read_whatsapp_pdf(text_pdf([HEADER + ITEMS + ["Thank you for your business, visit again soon"]]))
    assert r["route"] == "image" and r["reason"] == "no_total"


def test_scanned_pdf_uses_the_last_page_image():
    r = read_pdf.read_whatsapp_pdf(scanned_pdf(3))
    assert r["route"] == "image" and r["reason"] == "scanned" and r["pages"] == 3 and r["page"] == 3
    from PIL import Image
    assert max(Image.open(io.BytesIO(r["image"])).size) == read_pdf.RENDER_LONG_SIDE


def test_too_many_pages_and_broken_files_raise():
    with pytest.raises(read_pdf.PdfTooBig):
        read_pdf.read_whatsapp_pdf(text_pdf([["page"]] * (read_pdf.MAX_PAGES + 1)))
    with pytest.raises(Exception):
        read_pdf.read_whatsapp_pdf(b"%PDF-1.4 this is not really a pdf")


# ---------------------------------------------------------------- the bot
def test_bot_text_pdf_is_free_and_ends_with_the_confirm_buttons(world):
    onboard(world)
    serve(world, text_pdf([HEADER + ITEMS + TOTAL]))
    world.run(document())
    last = world.wa.last
    assert last["kind"] == "buttons" and M.BTN_OK in [t for _, t in last["buttons"]] and "₹4,769" in last["body"]
    assert "ai_call" not in world.events()
    props = received(world)
    assert props["source"] == "pdf_text" and props["pages"] == 1 and props["page_used"] == 1
    world.run(tap(world.wa.button_ids()[0]))
    assert "bill_confirmed" in world.events()


def test_bot_scanned_pdf_goes_through_the_reader_with_the_page_image(world):
    onboard(world)
    serve(world, scanned_pdf(2))
    got = []
    real = world.reader

    def reader(image_bytes):
        got.append(image_bytes)
        return real(image_bytes)
    world.bot.extractor = reader
    world.reader.next = {"supplier_name": "Shiv Medical Distributors", "total_amount": 4769}
    world.run(document())
    assert got and got[0][:2] == b"\xff\xd8"
    assert world.events().count("ai_call") == 1
    props = received(world)
    assert props["source"] == "pdf_scan" and props["page_used"] == 2 and props["pdf_route"] == "scanned"
    assert M.BTN_OK in [t for _, t in world.wa.last["buttons"]]


def test_bot_image_sent_as_a_document_uses_the_photo_flow(world):
    onboard(world)
    seen = serve(world, b"\xff\xd8fake-jpeg")
    world.reader.next = {"supplier_name": "Shiv Medical Distributors", "total_amount": 4769}
    world.run(document(mime="image/jpeg", filename="IMG-2026.jpg"))
    assert seen and received(world)["source"] == "photo"


@pytest.mark.parametrize("mime, filename, data, reason, reply", [
    ("application/msword", "bill.doc", b"", "not_pdf", M.DOC_NOT_SUPPORTED),
    ("application/pdf", "bill.pdf", b"%PDF-1.4 broken", "unreadable", M.PDF_FAILED),
    ("application/pdf", "bill.pdf", b"PK\x03\x04 a zip renamed to pdf", "unreadable", M.PDF_FAILED),
])
def test_bot_refuses_other_files_politely(world, mime, filename, data, reason, reply):
    onboard(world)
    seen = serve(world, data)
    world.run(document(mime=mime, filename=filename))
    assert world.wa.last["body"] == reply
    assert next(e["props"]["reason"] for e in _events(world.store) if e["event"] == "pdf_rejected") == reason
    assert (not seen) if reason == "not_pdf" else seen


def test_bot_pdf_too_big_or_too_many_pages(world):
    onboard(world)
    serve(world, text_pdf([["page"]] * (read_pdf.MAX_PAGES + 1)))
    world.run(document())
    assert world.wa.last["body"] == M.PDF_TOO_BIG
    world.bot.limits = Limits(max_pdf_mb=0.0001)
    serve(world, text_pdf([HEADER + ITEMS + TOTAL]))
    world.run(document())
    assert world.wa.last["body"] == M.PDF_TOO_BIG and "bill_received" not in world.events()


def test_bot_at_the_ai_cap_text_pdf_still_works_scanned_pdf_waits(world):
    onboard(world)
    world.bot.limits = Limits(per_shop_per_day=0)
    serve(world, text_pdf([HEADER + ITEMS + TOTAL]))
    world.run(document())
    assert received(world)["source"] == "pdf_text"
    serve(world, scanned_pdf(1))
    world.run(document())
    assert "limit_hit" in world.events() and world.events().count("bill_received") == 1


# ---------------------------------------------------------------- 24 Nov: quotations, jurisdiction lines, the report
def test_a_quotation_is_not_saved_as_a_bill(world):
    onboard(world)
    serve(world, text_pdf([["MEHTA ENTERPRISES", "QUOTATION", "Quote No: Q-42  Date: 05/10/2026"] + ITEMS + TOTAL]))
    world.run(document())
    assert world.wa.last["body"] == M.PDF_NOT_A_BILL
    assert "bill_received" not in world.events() and "ai_call" not in world.events()
    assert any(e["event"] == "pdf_rejected" and e["props"]["reason"] == "not_a_bill" for e in _events(world.store))


def test_an_estimate_is_still_a_bill():   # kachha (non-GST) bills are often printed as ESTIMATE
    r = read_pdf.read_whatsapp_pdf(text_pdf([["GUPTA GENERAL STORE", "ESTIMATE", "No: 342  Date: 05/10/2026"]
                                             + ITEMS + TOTAL]))
    assert r["route"] == "text" and r["fields"]["total_amount"] == 4769


def test_a_jurisdiction_line_on_top_is_not_the_supplier():
    r = read_pdf.read_whatsapp_pdf(text_pdf([["Subject to Jaipur Jurisdiction"] + HEADER + ITEMS + TOTAL]))
    assert r["fields"]["supplier_name"] == "shiv medical distributors"


def test_pdf_bills_tick_the_pdf_column():
    from datetime import datetime, timezone

    from bot import health
    now = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
    win = [{"shop_id": "1", "event": "bill_received", "props": {"source": s}, "created_at": now}
           for s in ("pdf_text", "pdf_scan")]
    out = "\n".join(health.adoption_lines(win, {"1": "S01"}))
    assert "| S01 |  | ✓ |" in out
