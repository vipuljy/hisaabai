"""Five sample supplier bills as PDFs, to test VahiBook's PDF path from a phone (24 Nov; pilot playbook section 14).
All names, GSTINs, phone numbers and amounts are invented. Files go to outputs/sample_pdfs/ (private, not committed).
Run:  python scripts/make_sample_pdfs.py"""
import os
import random

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pdfs")
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)

TAG = "SAMPLE BILL FOR TESTING - not a real invoice"

def money(x): return f"{x:,.2f}"

def gst_invoice(path, supplier, gstin, inv, date, items, pages=1, title="TAX INVOICE", top=""):
    c = canvas.Canvas(path, pagesize=A4); W, H = A4
    per = -(-len(items) // pages)
    sub = sum(q * r for _, q, r in items); cg = round(sub * 0.025, 2); sg = cg
    grand = sub + cg + sg; rounded = round(grand); ro = rounded - grand
    for p in range(pages):
        if top: c.drawCentredString(W/2, H-12*mm, top)
        c.setFont("Helvetica-Bold", 15); c.drawCentredString(W/2, H-20*mm, supplier)
        c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, H-25*mm, "Shop 14, Naya Bazar Main Road, Rohtak, Haryana 124001  |  Ph: 90000 00000")
        c.drawCentredString(W/2, H-30*mm, f"GSTIN: {gstin}")
        c.setFont("Helvetica-Bold", 12); c.drawCentredString(W/2, H-38*mm, title)
        c.setFont("Helvetica", 9)
        c.drawString(15*mm, H-46*mm, f"Invoice No: {inv}"); c.drawRightString(W-15*mm, H-46*mm, f"Date: {date}")
        c.drawString(15*mm, H-51*mm, "Bill To: Test Kirana Store, Sector 3, Rohtak")
        c.drawRightString(W-15*mm, H-51*mm, f"Page {p+1} of {pages}")
        y = H-60*mm; c.setFont("Helvetica-Bold", 9)
        for x, t in ((15, "S.No"), (27, "Item"), (120, "Qty"), (140, "Rate"), (165, "Amount")):
            c.drawString(x*mm, y, t)
        c.line(15*mm, y-2*mm, W-15*mm, y-2*mm); c.setFont("Helvetica", 9); y -= 7*mm
        for i, (name, q, r) in enumerate(items[p*per:(p+1)*per], start=p*per+1):
            c.drawString(15*mm, y, str(i)); c.drawString(27*mm, y, name); c.drawString(120*mm, y, str(q))
            c.drawString(140*mm, y, money(r)); c.drawRightString(W-15*mm, y, money(q*r)); y -= 6*mm
        if p < pages - 1:
            c.drawRightString(W-15*mm, y-4*mm, "Continued on next page ...")
            c.setFont("Helvetica", 7); c.drawString(15*mm, 10*mm, TAG); c.showPage(); continue
        c.line(15*mm, y, W-15*mm, y); y -= 7*mm
        for k, v in (("Sub Total", sub), ("CGST 2.5%", cg), ("SGST 2.5%", sg), ("Round Off", ro)):
            c.drawString(120*mm, y, k); c.drawRightString(W-15*mm, y, money(v)); y -= 6*mm
        c.setFont("Helvetica-Bold", 11); c.drawString(120*mm, y, "GRAND TOTAL"); c.drawRightString(W-15*mm, y, money(rounded))
        c.setFont("Helvetica", 9); y -= 10*mm
        c.drawString(15*mm, y, "Payment due within 15 days. Goods once sold will not be taken back.")
        c.drawRightString(W-15*mm, y-15*mm, f"For {supplier}")
        c.setFont("Helvetica", 7); c.drawString(15*mm, 10*mm, TAG); c.showPage()
    c.save(); return rounded

random.seed(7)
grocery = [("Chakki Atta 10kg", 5, 410), ("Iodised Salt 1kg", 20, 24), ("Mustard Oil 1L", 12, 152),
           ("Toor Dal 1kg", 10, 138), ("Sugar 1kg", 25, 44), ("Basmati Rice 5kg", 4, 520),
           ("Tea 500g", 6, 245), ("Detergent 1kg", 8, 115)]
t1 = gst_invoice("1_text_gst_invoice.pdf", "SHIV SHAKTI TRADERS", "06AAAAA0000A1Z5 (sample)", "SAMPLE/0001", "22-11-2026", grocery)  # secret-scan: ignore (invented)
many = [(f"Item {chr(65+i%26)}{i} assorted pack", random.randint(2, 24), random.choice([18, 35, 48, 62, 95, 120, 240]))
        for i in range(34)]
t2 = gst_invoice("2_text_two_page_invoice.pdf", "AGARWAL WHOLESALE AGENCY", "06BBBBB1111B1Z2 (sample)", "SAMPLE/0002", "21-11-2026", many, pages=2, top="Subject to Rohtak Jurisdiction")
t4 = gst_invoice("4_quotation_not_a_bill.pdf", "MEHTA ENTERPRISES", "06CCCCC2222C1Z9 (sample)", "QTN-SAMPLE-4", "23-11-2026",
                 grocery[:5], title="QUOTATION (ESTIMATE ONLY, NOT A BILL)")

# 3: a handwritten-style kachha bill saved as a scanned PDF (no text layer)
img = Image.new("RGB", (1240, 1754), (246, 242, 228)); d = ImageDraw.Draw(img)
def font(names, size):
    for n in names:   # Linux (DejaVu) or Windows (Arial)
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"]
BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"]
f, fb, fs = font(REG, 40), font(BOLD, 52), font(REG, 22)
d.text((60, 1680), TAG, fill=(120, 120, 120), font=fs)
d.text((330, 110), "GUPTA GENERAL STORE", fill=(20, 30, 110), font=fb)
d.text((60, 220), "Bill No. SAMPLE-3        Date 20/11/26", fill=(20, 30, 110), font=f)  # secret-scan: ignore (invented)
d.line((60, 290, 1180, 290), fill=(20, 30, 110), width=3)
rows = [("Chini 25 kg", 1100), ("Chai patti 2 kg", 980), ("Sarson tel 15 L", 2250), ("Maida 10 kg", 420), ("Besan 5 kg", 475)]
y = 330
for name, amt in rows:
    d.text((80 + random.randint(-6, 6), y), name, fill=(20, 30, 110), font=f)
    d.text((900 + random.randint(-6, 6), y), f"{amt}/-", fill=(20, 30, 110), font=f); y += 90
d.line((60, y + 10, 1180, y + 10), fill=(20, 30, 110), width=3)
d.text((600, y + 40), f"Total  {sum(a for _, a in rows)}/-", fill=(150, 20, 20), font=fb)
d.text((80, y + 170), "Udhaar - 15 din mein", fill=(20, 30, 110), font=f)
img = img.rotate(2.5, expand=False, fillcolor=(230, 228, 220))
img.save("3_scanned_kachha_bill.pdf", "PDF", resolution=150)


# 5: a 12-page account statement: more than 10 pages, the bot asks for the bill page only
c = canvas.Canvas("5_statement_12_pages.pdf", pagesize=A4)
for i in range(12):
    c.setFont("Helvetica", 11); c.drawString(20*mm, 270*mm, f"ACCOUNT STATEMENT  page {i+1} of 12  (sample)")
    c.setFont("Helvetica", 7); c.drawString(20*mm, 10*mm, TAG); c.showPage()
c.save()
print("5 sample PDFs written to", os.path.abspath(OUT), "| totals:", t1, t2, sum(a for _, a in rows), t4)
