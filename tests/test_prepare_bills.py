"""src/00_prepare_bills.py: renames, strips metadata, continues numbering, starts labels.csv."""

import csv
import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prep", ROOT / "src" / "00_prepare_bills.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


_shade = iter(range(10, 250, 7))


def _photo(path, gps=True):
    im = Image.new("RGB", (3000, 1500), (next(_shade), 200, 200))  # each photo different
    exif = Image.Exif()
    exif[0x0110] = "PhoneModel"
    if gps:
        exif[0x8825] = {1: "N", 2: (28.0, 26.0, 0.0)}
    im.save(path, "JPEG", exif=exif)


def test_prepare(tmp_path):
    inbox = tmp_path / "data" / "raw" / "bills_inbox"
    inbox.mkdir(parents=True)
    _photo(inbox / "IMG_2024 sharma.jpg")
    _photo(inbox / "b.jpeg")
    (inbox / "x.heic").write_bytes(b"nope")
    lines = []
    assert prep.main(tmp_path, out=lines.append) == 0
    imgs = sorted((tmp_path / "data" / "eval" / "images").glob("*.jpg"))
    assert [p.name for p in imgs] == ["B001.jpg", "B002.jpg"]
    with Image.open(imgs[0]) as im:
        assert max(im.size) == 2000 and len(im.getexif()) == 0  # shrunk, metadata gone
    rows = list(csv.DictReader(open(tmp_path / "data" / "eval" / "labels.csv")))
    assert [r["bill_id"] for r in rows] == ["B001", "B002"] and rows[0]["supplier_name"] == ""
    assert (inbox / "done" / "b.jpeg").exists() and not (inbox / "b.jpeg").exists()
    assert any("heic" in line for line in lines)
    _photo(inbox / "later.png")
    prep.main(tmp_path, out=lines.append)  # second batch continues numbering
    assert (tmp_path / "data" / "eval" / "images" / "B003.jpg").exists()
    assert len(list(csv.DictReader(open(tmp_path / "data" / "eval" / "labels.csv")))) == 3


def test_empty_inbox(tmp_path):
    assert prep.main(tmp_path, out=lambda *_: None) == 1


def test_pdf_pages_and_duplicates(tmp_path):
    inbox = tmp_path / "data" / "raw" / "bills_inbox"
    inbox.mkdir(parents=True)
    pages = [Image.new("RGB", (600, 800), c) for c in ((255, 255, 255), (230, 230, 230))]
    pages[0].save(inbox / "invoice.pdf", save_all=True, append_images=pages[1:])
    _photo(inbox / "a.jpg")
    (inbox / "a (1).jpg").write_bytes((inbox / "a.jpg").read_bytes())  # WhatsApp duplicate
    (inbox / "list.xls").write_bytes(b"x")
    lines = []
    assert prep.main(tmp_path, out=lines.append) == 0
    rows = list(csv.DictReader(open(tmp_path / "data" / "eval" / "labels.csv")))
    assert len(rows) == 3  # 2 PDF pages + 1 photo, duplicate skipped
    assert sum(r["notes"] == "from PDF" for r in rows) == 2
    assert any("duplicate" in line for line in lines) and any("list.xls" in line for line in lines)
    assert (inbox / "list.xls").exists()  # Excel left in the inbox
