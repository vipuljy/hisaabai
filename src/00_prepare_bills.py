"""Step 0: get bill photos ready for the eval.

Put photos (any names, .jpg/.jpeg/.png/.webp) and PDF invoices in data/raw/bills_inbox/, then run this
(RUN_STEP1a_prepare_bills.bat). Each PDF page becomes one image. Exact duplicate files are skipped.
Excel files cannot be read as photos and are left in the inbox. For each photo it:
  - turns it upright, shrinks it to max 2000 px, saves as JPEG
  - strips hidden metadata (GPS location, phone model, time) by re-saving the pixels only
  - names it B001.jpg, B002.jpg ... in data/eval/images/ (continuing after existing ones)
  - adds a row to data/eval/labels.csv with bill_id and image_file filled, the rest blank
  - moves the original to data/raw/bills_inbox/done/
The link between new names and original file names is kept privately in data/eval/source_map.csv.
Everything here is inside data/, which never goes to GitHub.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
EXT = {".jpg", ".jpeg", ".png", ".webp"}
PDF = {".pdf"}
FIELDS = ["bill_id", "image_file", "bill_type", "segment", "supplier_name", "bill_date",
          "invoice_number", "total_amount", "due_date", "notes"]
MAX_SIDE = 2000


def save_clean(im: Image.Image, dst: Path) -> None:
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    fresh = Image.new("RGB", im.size)
    fresh.paste(im)  # pixels only: no EXIF, no GPS
    fresh.save(dst, "JPEG", quality=90)


def pages(src: Path):
    """Yields (label, PIL image) for a photo, or one per page for a PDF."""
    if src.suffix.lower() in PDF:
        import pypdfium2 as pdfium  # pip install pypdfium2 (in requirements.txt)
        doc = pdfium.PdfDocument(str(src))
        for i in range(len(doc)):
            yield f"page {i + 1}", doc[i].render(scale=2).to_pil()
        doc.close()
    else:
        with Image.open(src) as im:
            im.load()
            yield "", im.copy()


def digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def next_number(images: Path) -> int:
    nums = [int(m.group(1)) for p in images.glob("B*.jpg") if (m := re.fullmatch(r"B(\d{3,})", p.stem))]
    return max(nums, default=0) + 1


def main(root: Path = ROOT, out=print) -> int:
    inbox = root / "data" / "raw" / "bills_inbox"
    images = root / "data" / "eval" / "images"
    labels = root / "data" / "eval" / "labels.csv"
    source_map = root / "data" / "eval" / "source_map.csv"
    inbox.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    photos = sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in EXT | PDF)
    skipped = sorted(p.name for p in inbox.iterdir() if p.is_file() and p.suffix.lower() not in EXT | PDF)
    if not photos:
        out(f"No photos in {inbox}. Put .jpg/.png photos there and run again.")
        return 1
    new_labels = not labels.exists()
    new_map = not source_map.exists()
    done = inbox / "done"
    done.mkdir(exist_ok=True)
    n = next_number(images)
    added = dupes = 0
    seen = set()
    if source_map.exists():
        seen = {r["sha1"] for r in csv.DictReader(open(source_map, encoding="utf-8")) if r.get("sha1")}
    with open(labels, "a", newline="", encoding="utf-8") as lf, \
         open(source_map, "a", newline="", encoding="utf-8") as mf:
        lw, mw = csv.writer(lf), csv.writer(mf)
        if new_labels:
            lw.writerow(FIELDS)
        if new_map:
            mw.writerow(["bill_id", "original_file", "page", "sha1"])
        for p in photos:
            h = digest(p)
            if h in seen:
                dupes += 1
                shutil.move(str(p), str(done / p.name))
                continue
            seen.add(h)
            try:
                for page, im in pages(p):
                    bill_id = f"B{n:03d}"
                    save_clean(im, images / f"{bill_id}.jpg")
                    note = "from PDF" if page else ""
                    lw.writerow([bill_id, f"{bill_id}.jpg"] + [""] * (len(FIELDS) - 3) + [note])
                    mw.writerow([bill_id, p.name, page, h])
                    n += 1
                    added += 1
            except Exception as err:
                out(f"SKIP  {p.name}: cannot open ({type(err).__name__})")
                continue
            shutil.move(str(p), str(done / p.name))
    out(f"OK    {added} image(s) ready in data/eval/images/ (metadata removed), rows added to data/eval/labels.csv")
    if dupes:
        out(f"OK    {dupes} exact duplicate file(s) skipped")
    if skipped:
        out(f"SKIP  {len(skipped)} file(s) not photo/PDF (Excel, .heic...): {', '.join(skipped[:5])}")
    out("Next: tell Claude 'bills ready'. Claude pre-fills the labels, then you check each one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
