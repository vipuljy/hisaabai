"""Tests for the OCR reader (bot/read_ocr.py) and its part of the step-6 eval.
No Tesseract, no network: a fake Tesseract answers. Names and numbers are invented."""

import csv
import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pytest

from bot import read_ocr
from bot.extract import FIELDS

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 25)

BILL_LINES = [  # (text, confidence of every word on the line)
    ("SHIV MEDICAL DISTRIBUTORS", 90),
    ("GSTIN : 07ABCDE1234F1Z2", 90),
    ("Invoice No. : D5721", 88),
    ("Date : 05-10-2026", 91),
    ("Ref D5721 Page 1 of 1", 88),  # invoice number printed a second time
    ("CROCIN 500 10 2 40.00 80.00", 70),
    ("Rs. Four Thousand Seven Hundred and Sixty Nine only GRAND TOTAL 4769.00", 85),
]


def tess_data(lines):
    """Build an image_to_data()-style dict: one word box per word."""
    d = {k: [] for k in ("text", "conf", "block_num", "par_num", "line_num")}
    for n, (line, conf) in enumerate(lines, start=1):
        for w in line.split():
            d["text"].append(w)
            d["conf"].append(conf)
            d["block_num"].append(1)
            d["par_num"].append(1)
            d["line_num"].append(n)
    d["text"].append("")  # Tesseract also returns empty boxes with conf -1
    d["conf"].append(-1)
    d["block_num"].append(1)
    d["par_num"].append(1)
    d["line_num"].append(99)
    return d


class FakeTess:
    class Output:
        DICT = "dict"

    class TesseractNotFoundError(Exception):
        pass

    def __init__(self, lines, osd=None):
        self.lines, self.osd, self.rotated_size = lines, osd, None

    def image_to_osd(self, img, output_type=None):
        if self.osd is None:
            raise RuntimeError("Too few characters")
        return self.osd

    def image_to_data(self, img, lang=None, config=None, output_type=None):
        self.rotated_size = img.size
        return tess_data(self.lines)


def jpeg(w=400, h=300):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def fake(monkeypatch):
    def use(lines=BILL_LINES, osd=None):
        t = FakeTess(lines, osd)
        monkeypatch.setattr(read_ocr, "_tesseract", lambda: t)
        return t
    return use


# ---------------------------------------------------------------- pieces
def test_rotation_from_osd():
    assert read_ocr.rotation_from_osd({"rotate": 90, "orientation_conf": 5.2}) == 90
    assert read_ocr.rotation_from_osd({"rotate": 270, "orientation_conf": 0.4}) == 0  # Tesseract unsure
    assert read_ocr.rotation_from_osd(None) == 0


def test_lines_and_confidence_from_word_boxes():
    words = read_ocr.words_from_data(tess_data([("GRAND TOTAL 500", 80), ("hello", 40)]))
    lines, confs = read_ocr.lines_from_words(words)
    assert lines == ["GRAND TOTAL 500", "hello"]
    assert confs == [80, 40]


def test_big_gap_splits_side_by_side_columns():
    # one Tesseract line: seller on the left, buyer far to the right (word width 10 px per char)
    boxes = [["GANGA", 90, 1, 1, 1, 0, 50], ["TRADERS", 90, 1, 1, 1, 60, 70],
             ["RAM", 80, 1, 1, 1, 600, 30], ["MEDICOS", 80, 1, 1, 1, 640, 70]]
    lines, confs = read_ocr.lines_from_words(boxes)
    assert lines == ["GANGA TRADERS", "RAM MEDICOS"]
    assert confs == [90, 80]


def test_small_photos_are_enlarged():
    assert read_ocr.prepare(jpeg(400, 300)).size == (800, 600)
    assert read_ocr.prepare(jpeg(2400, 1600)).size == (2400, 1600)


# ---------------------------------------------------------------- reading + guard
def test_clear_photo_is_read_and_trusted(fake):
    fake()
    ex = read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract")
    assert ex.error is None
    assert {f: ex.fields[f] for f in FIELDS} == {
        "supplier_name": "shiv medical distributors", "bill_date": "2026-10-05",
        "invoice_number": "D5721", "total_amount": 4769, "due_date": None}
    assert ex.fields["unsure_fields"] == ["supplier_name"]    # fix C: an OCR'd name is always confirmed
    assert ex.extra["supplier_gstin"] == "07ABCDE1234F1Z2"    # fix E
    assert ex.extra["ocr"]["total_line_conf"] == 85
    assert ex.extra["checks"]["words_match_total"] == "pass"  # proof gate passed: total trusted


def test_low_confidence_total_line_marks_only_total_unsure(fake):
    lines = BILL_LINES[:-1] + [("GRAND TOTAL 4769.00", 45)]
    fake(lines)
    ex = read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract")
    assert ex.fields["total_amount"] == 4769
    assert ex.fields["unsure_fields"] == ["supplier_name", "total_amount"]
    assert ex.extra["unsure_rules_only"] == []


def test_invoice_number_trusted_only_if_seen_twice():
    assert read_ocr.invoice_seen_twice("D5721", "Invoice No. : D5721\nRef D5721")
    assert not read_ocr.invoice_seen_twice("D5721", "Invoice No. : D5721\nRef D57211")
    assert not read_ocr.invoice_seen_twice("77", "Bill No 77\nQty 77")      # too short to count


def test_invoice_number_read_once_is_unsure(fake):
    fake([l for l in BILL_LINES if not l[0].startswith("Ref")])
    assert "invoice_number" in read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract").fields["unsure_fields"]


def test_proof_gate_total_needs_matching_words(fake):
    fake([l for l in BILL_LINES if "only" not in l[0]] + [("GRAND TOTAL 4769.00", 95)])   # no amount in words
    assert "total_amount" in read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract").fields["unsure_fields"]
    fake(BILL_LINES[:-1] + [("Rs. Four Thousand Seven Hundred only GRAND TOTAL 4769.00", 95)])  # words differ
    assert "total_amount" in read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract").fields["unsure_fields"]


def test_total_on_next_line_uses_both_lines_confidence(fake):
    lines = BILL_LINES[:3] + [("NET AMOUNT PAYABLE", 90), ("4,769.00", 20)]
    fake(lines)
    ex = read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract")
    assert ex.extra["ocr"]["total_line_conf"] == 55
    assert "total_amount" in ex.fields["unsure_fields"]


def test_low_page_confidence_marks_every_read_field_unsure(fake):
    fake([(t, 30) for t, _ in BILL_LINES])
    ex = read_ocr.read_ocr(jpeg(), today=TODAY, engine="tesseract")
    # rules_v4: other fields stay unsure, but the total has a PROOF (words = digits), so it is trusted
    assert ex.fields["unsure_fields"] == ["supplier_name", "bill_date", "invoice_number"]
    assert ex.extra["total_proof"] == "words"
    assert ex.extra["unsure_rules_only"] == []


def test_sideways_photo_is_turned_upright(fake):
    t = fake(osd={"rotate": 90, "orientation_conf": 6.0})
    read_ocr.read_ocr(jpeg(400, 300), today=TODAY, engine="tesseract")
    assert t.rotated_size == (600, 800)  # enlarged 2x, then turned a quarter


def test_missing_tesseract_is_an_error_not_a_crash(monkeypatch):
    def boom():
        raise read_ocr.NoTesseract("not installed")
    monkeypatch.setattr(read_ocr, "_tesseract", boom)
    ex = read_ocr.read_ocr(jpeg(), engine="tesseract")
    assert ex.error == "no_tesseract"
    assert all(ex.fields[f] is None for f in FIELDS)


# ---------------------------------------------------------------- cache
def test_cache_is_reused_and_rescored_without_ocr(fake, tmp_path, monkeypatch):
    fake()
    img = tmp_path / "B001.jpg"
    img.write_bytes(jpeg())
    cache = tmp_path / "runs"
    rec = read_ocr.read_bill("B001", img, cache, today=TODAY)
    for key in ("bill_id", "model", "prompt", "fields", "raw_text", "error",
                "input_tokens", "output_tokens", "latency_ms"):
        assert key in rec
    assert rec["input_tokens"] == rec["output_tokens"] == 0
    monkeypatch.setattr(read_ocr, "read_ocr", lambda *a, **k: pytest.fail("should not OCR again"))
    again = read_ocr.read_bill("B001", img, cache, today=TODAY)
    assert again["fields"] == rec["fields"]


def test_no_tesseract_is_never_cached(tmp_path, monkeypatch):
    def boom():
        raise read_ocr.NoTesseract("not installed")
    monkeypatch.setattr(read_ocr, "_tesseract", boom)
    img = tmp_path / "B001.jpg"
    img.write_bytes(jpeg())
    rec = read_ocr.read_bill("B001", img, tmp_path / "runs", today=TODAY)
    assert rec["error"] == "no_tesseract"
    assert not (tmp_path / "runs" / "B001.json").exists()


# ---------------------------------------------------------------- step-6 script with both readers
def test_step6_prints_both_readers_and_wrong_totals(fake, tmp_path, capsys):
    fake(BILL_LINES[:-1] + [("GRAND TOTAL 4796.00", 50)])  # misread total, low confidence
    eval_dir = tmp_path / "data" / "eval"
    (eval_dir / "images").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "bills_inbox" / "done").mkdir(parents=True)
    (eval_dir / "images" / "B001.jpg").write_bytes(jpeg())
    with open(eval_dir / "source_map.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["bill_id", "original_file", "page", "sha1"], ["B001", "B001.jpeg", "", "x"]])
    with open(eval_dir / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bill_id", "image_file", "bill_type", "segment", *FIELDS, "notes"])
        w.writerow(["B001", "B001.jpg", "printed", "medical", "Shiv Medical Distributors", "05-10-2026",
                    "D5721", "4769", "", ""])
    spec = importlib.util.spec_from_file_location("eval06", ROOT / "src" / "06_free_readers_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--workers", "1", "--readers", "pdf_text", "ocr_rules"], root=tmp_path, today=TODAY) == 0
    with open(tmp_path / "outputs" / "eval" / "free_readers_summary.csv", newline="", encoding="utf-8") as f:
        rows = {(r["reader"], r["scope"]): r for r in csv.DictReader(f)}
    ocr = rows[("ocr_rules", "all")]
    assert float(ocr["total_amount_right"]) == 0.0
    assert float(ocr["bills_with_silent_error"]) == 0.0   # guard flagged the low-confidence total
    assert float(ocr["silent_error_rules_only"]) == 1.0   # without the guards it would be silent
    assert float(ocr["total_trusted"]) == 0.0
    out = capsys.readouterr().out
    assert "B001  read 4796  truth 4769  flagged=True" in out
    saved = json.loads((eval_dir / "runs" / "ocr_rules" / "B001.json").read_text(encoding="utf-8"))
    assert saved["model"] == "ocr_rules"


# ---------------------------------------------------------------- RapidOCR engine (fake: no install needed)
def seg(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class FakeRapid:
    """Stands in for rapidocr.RapidOCR: one box per text segment, score 0-1."""
    def __init__(self, items):
        self.items = items

    def __call__(self, img):
        class Out:
            pass
        o = Out()
        o.boxes = [b for b, _, _ in self.items]
        o.txts = [t for _, t, _ in self.items]
        o.scores = [c for _, _, c in self.items]
        return o


RAPID_BILL = [
    (seg(10, 10, 300, 30), "SHIV MEDICAL DISTRIBUTORS", 0.98),
    (seg(700, 12, 950, 28), "RAM MEDICOS", 0.97),           # buyer, same row, far right
    (seg(10, 50, 250, 70), "Invoice No. : D5721", 0.95),
    (seg(10, 90, 220, 110), "Date : 05-10-2026", 0.96),
    (seg(10, 130, 250, 150), "Ref D5721 Page 1 of 1", 0.9),
    (seg(10, 170, 600, 190), "Rs. Four Thousand Seven Hundred and Sixty Nine only", 0.93),
    (seg(700, 171, 950, 189), "GRAND TOTAL 4769.00", 0.97),  # same row as the words
]


def test_segments_become_the_same_word_boxes_as_tesseract():
    words = read_ocr.words_from_segments(*zip(*[(b, t, c) for b, t, c in RAPID_BILL]))
    lines, confs = read_ocr.lines_from_words(words)
    assert lines[:2] == ["SHIV MEDICAL DISTRIBUTORS", "RAM MEDICOS"]  # one row, split at the big gap
    assert "GRAND TOTAL 4769.00" in lines
    assert all(0 <= c <= 100 for c in confs)


def test_rapidocr_engine_uses_the_same_rules(monkeypatch):
    monkeypatch.setattr(read_ocr, "_rapidocr", lambda: FakeRapid(RAPID_BILL))
    monkeypatch.setattr(read_ocr, "_osd_rotation", lambda img: 0)
    ex = read_ocr.read_ocr(jpeg(), today=TODAY, engine="rapidocr")
    assert ex.model == "ocr_rapid" and ex.error is None
    assert {f: ex.fields[f] for f in FIELDS} == {
        "supplier_name": "shiv medical distributors", "bill_date": "2026-10-05",
        "invoice_number": "D5721", "total_amount": 4769, "due_date": None}
    assert ex.fields["unsure_fields"] == ["supplier_name"]


def test_missing_rapidocr_is_skipped_not_cached(monkeypatch, tmp_path):
    def boom():
        raise read_ocr.NoEngine("not installed")
    monkeypatch.setattr(read_ocr, "_rapidocr", boom)
    img = tmp_path / "B001.jpg"
    img.write_bytes(jpeg())
    rec = read_ocr.read_bill("B001", img, tmp_path / "runs", today=TODAY, engine="rapidocr")
    assert rec["error"] == "no_engine" and not (tmp_path / "runs" / "B001.json").exists()


def test_default_engine_is_rapidocr_when_installed(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_rapid(name, *a, **k):
        if name == "rapidocr":
            raise ImportError("not installed")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_rapid)
    assert read_ocr.default_engine() == "tesseract"  # fallback


def test_two_different_labelled_dates_make_the_bill_date_unsure():
    """DEV bill B122 (Tally layout) printed several 'Dated' fields; the wrong one was read."""
    words = read_ocr.words_from_data(tess_data([("RAM TRADERS", 90), ("Invoice No. 4411 Dated 9-Oct-26", 90),
                                                ("Delivery Note Dated 10-Oct-26", 90), ("Ref 4411", 90)]))
    out = read_ocr.analyse(words, today=TODAY)
    assert "bill_date" in out["fields"]["unsure_fields"]
    one = read_ocr.analyse(read_ocr.words_from_data(tess_data([("Invoice Date : 9-Oct-26", 90)])), today=TODAY)
    assert "bill_date" not in one["fields"]["unsure_fields"]


def test_bare_tally_dated_label_is_unsure_but_invoice_date_is_not():
    bare = read_ocr.analyse(read_ocr.words_from_data(tess_data([("Dated 10-Oct-26", 90)])), today=TODAY)
    assert "bill_date" in bare["fields"]["unsure_fields"]
    inv = read_ocr.analyse(read_ocr.words_from_data(tess_data([("Invoice Date : 10-Oct-26", 90)])), today=TODAY)
    assert "bill_date" not in inv["fields"]["unsure_fields"]


def test_doctr_export_becomes_the_same_word_boxes():
    page = {"dimensions": (1000, 800), "blocks": [{"lines": [
        {"words": [{"value": "GRAND", "confidence": 0.99, "geometry": ((0.1, 0.5), (0.2, 0.52))},
                   {"value": "TOTAL", "confidence": 0.98, "geometry": ((0.21, 0.5), (0.3, 0.52))},
                   {"value": "4769.00", "confidence": 0.97, "geometry": ((0.7, 0.5), (0.8, 0.52))}]}]}]}
    words = read_ocr.words_from_doctr(page)
    assert words[0] == ["GRAND", 99.0, 1, 1, 1, 80, 80]
    lines, _ = read_ocr.lines_from_words(words)
    assert lines == ["GRAND TOTAL", "4769.00"]  # big gap: split like every other engine
    from bot import field_rules
    assert field_rules.find_total("\n".join(lines))[0] == "4769.00"


def test_both_engines_agree_is_a_proof_but_only_when_equal(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("eval06", ROOT / "src" / "06_free_readers_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = {"raw_text": "GRAND TOTAL 4769.00", "latency_ms": 10, "checks": {}}
    a = {"B1": base | {"fields": {"total_amount": 4769, "unsure_fields": ["total_amount"]}},
         "B2": base | {"fields": {"total_amount": 4769, "unsure_fields": ["total_amount"]}}}
    b = {"B1": base | {"fields": {"total_amount": 4769, "unsure_fields": ["total_amount"]}},
         "B2": base | {"fields": {"total_amount": 4796, "unsure_fields": []}}}
    out = mod.agree([{"bill_id": "B1"}, {"bill_id": "B2"}], a, b)
    assert out["B1"]["total_proof"] == "agree" and out["B1"]["fields"]["unsure_fields"] == []
    assert "total_amount" in out["B2"]["fields"]["unsure_fields"]
