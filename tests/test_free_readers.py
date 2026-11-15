"""Tests for the free readers: field rules, checks, PDF reader and the step-6 eval.
No network, no API. All shop names and numbers below are invented."""

import csv
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from bot import checks, field_rules, read_pdf
from bot.extract import FIELDS

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 25)


def read(text):
    fields, results = field_rules.read_text(text, today=TODAY)
    return fields, results


# ---------------------------------------------------------------- sample bills (layouts copied, names invented)
MARG = """Scan to Pay
SHIV MEDICAL DISTRIBUTORS
SHOP NO. 12, NEW GRAIN MARKET
Phone : 9000000000
GST INVOICE
Original for Buyer
M/s RAM MEDICOS CREDIT
A/C Balance With This Bill : 23897.00
Invoice No. : D5721
Date : 05-10-2026 Time :17:27
Due Date : 05-10-2026
Challan No.: Transport : G.R. No.: G.R. Date :01-10-2026 Case :
STOCKIST FOR:- SUB TOTAL 4731.45
All disputes subject to local Jurisdication only. ROUND OFF -0.30
Rs. Four Thousand Seven Hundred and Sixty Nine only GRAND TOTAL 4769.00
"""

KARP = """TAX INVOICE
GANGA ENTERPRISES, AMBALA
Plot No. 1, Industrial Area
Invoice No.: I26-001234
Date : 09-Oct-2026
Page No. : 1
Due Date :
Drug Lic. No. : ABC ExpDt: 11-Aug-31
Total: 375 48 33505.27 40.45 3723.29 29781.98 -------------- Total Taxable Value 29781.98
CGST @ 2.50% on 29781.98 744.55
Rounding Off -0.08 -------------- Total Invoice Value 31271.00 -------------- Freight Amount : 0.00
E-Way Bill No : 330000123456
"""

PHARMA_STYLE = """Tax Invoice - Goods Narmada Pharma Pvt. Ltd
From
Narmada Pharma Pvt. Ltd
Date: 16.10.2026
INVOICE NO: 47K5520193
ORDER DATE: 15.10.2026
TAXABLE AMOUNT
414,347.02
INVOICE AMOUNT
414,347.02
DN/CN AMOUNT NET AMOUNT PAYABLE
435,064.00
AMOUNT IN WORDS: Rs. FOUR HUNDRED THIRTY-FIVE THOUSAND SIXTY-FOUR ONLY
DUE DATE 06.11.2026
"""

NET_PAYMENT = """Kaveri Healthcare Pvt. Ltd.
Invoice No : K-5521 Date : 30.09.2026
Due Date : 21.10.2026 Payt. Terms : 21 Days
Balance C/F 37,670.75 4,447.86 33,222.89
Basic Amount 33,222.89
Bill Amount 34,884.00
Rupees Thirty Four Thousand Eight Hundred Eighty Four Only Net Payment 34,884.00
"""

BALANCE_TRAP = """SAI TRADERS
Bill No 77  Date 02/10/26
Previous Balance 12,500.00
Total 3,250.00
Balance Amount Due 15,750.00
"""


@pytest.mark.parametrize("text, want", [
    (MARG, {"supplier_name": "shiv medical distributors", "bill_date": "2026-10-05",
            "invoice_number": "D5721", "total_amount": 4769, "due_date": "2026-10-05"}),
    (KARP, {"supplier_name": "ganga enterprises", "bill_date": "2026-10-09",
            "invoice_number": "I26-001234", "total_amount": 31271, "due_date": None}),
    (PHARMA_STYLE, {"supplier_name": "narmada pharma", "bill_date": "2026-10-16",
                   "invoice_number": "47K5520193", "total_amount": 435064, "due_date": "2026-11-06"}),
    (NET_PAYMENT, {"supplier_name": "kaveri healthcare", "bill_date": "2026-09-30",
                   "invoice_number": "K-5521", "total_amount": 34884, "due_date": "2026-10-21"}),
    (BALANCE_TRAP, {"supplier_name": "sai traders", "bill_date": "2026-10-02",
                    "invoice_number": "77", "total_amount": 3250, "due_date": None}),
], ids=["marg_grand_total", "karp_total_invoice_value", "pharma_net_amount_payable_next_line",
        "net_payment", "balance_line_trap"])
def test_rules_on_sample_bills(text, want):
    fields, results = read(text)
    assert {f: fields[f] for f in FIELDS} == want
    # the balance trap's total sits under the plain "Total" label: read right, but marked unsure (fix A)
    assert fields["unsure_fields"] == (["total_amount"] if text is BALANCE_TRAP else [])
    assert results["sane_fields"] == "pass" and results["not_balance"] == "pass"


def test_total_priority_beats_order_on_page():
    text = "NET AMOUNT PAYABLE 1,050.00\nTotal 900.00\nGRAND TOTAL 1,000.00\nINVOICE AMOUNT 990.00"
    assert field_rules.find_total(text) == ("1,050.00", "net amount payable")


def test_total_on_next_line_but_not_from_an_unrelated_line():
    assert field_rules.find_total("GRAND TOTAL\n\n12,345.00")[0] == "12,345.00"
    assert field_rules.find_total("Total Invoice Value\n: 0.00\n31,271.00")[0] == "31,271.00"  # skip a lone 0
    assert field_rules.find_total("Total Invoice Value\nFreight Amount : 0.00\nTransporter X") == (None, None)


def test_plain_total_label_is_never_trusted():
    fields, _ = read("SAI TRADERS\nTotal: 48 33505.27 40.45")
    assert fields["total_amount"] == 48 and fields["unsure_fields"] == ["total_amount"]
    fields, _ = read("SAI TRADERS\nGRAND TOTAL 3,250.00")
    assert fields["unsure_fields"] == []


def test_gstin_check_character():
    assert field_rules.gstin_ok("07ABCDE1234F1Z2")
    assert not field_rules.gstin_ok("07ABCDE1234F1Z3")   # one character off
    assert not field_rules.gstin_ok("07ABCDE1234F1Y2")


def test_seller_gstin_is_first_valid_one_in_top_third_with_ocr_repairs():
    text = "\n".join(["GANGA ENTERPRISES", "GSTIN : O7ABCDE1234F1Z2", "Bill to RAM MEDICOS",
                      "GSTIN : 09PQRST6789K1ZU", "a", "b", "c", "d", "e"])
    assert field_rules.find_seller_gstin(text) == "07ABCDE1234F1Z2"   # O read as 0 repaired
    low = "\n".join(["GANGA ENTERPRISES", "a", "b", "c", "d", "e", "GSTIN 07ABCDE1234F1Z2"])
    assert field_rules.find_seller_gstin(low) is None                # below the top third: likely the buyer
    assert field_rules.find_seller_gstin("GSTIN 07ABCDE1234F1Z9") is None  # bad check character


def test_lakh_commas_and_currency_signs():
    assert field_rules.amounts_in("Rs.1,01,757.00 and ₹ 4,769/- and INR 250") == [101757.0, 4769.0, 250.0]
    assert field_rules.amounts_in("Invoice I26-007351") == []


# ---------------------------------------------------------------- noisy OCR-style text (step 2 reuses these rules)
def test_noisy_ocr_text_one():
    text = ("SHRI KRISHNA  TRADERS\nlnvoice  N0 : 1458\nDate: 05/10/26\n"
            "GRAND  T0TAL :  Rs. 21,390.35\n"
            "Rupees Twenty One Thousand Three Hundred Ninety and Paise Thirty Five Only")
    fields, results = read(text)
    assert fields["supplier_name"] == "shri krishna traders"
    assert fields["invoice_number"] == "1458"
    assert fields["bill_date"] == "2026-10-05"
    assert fields["total_amount"] == 21390
    assert results["words_match_total"] == "pass"


def test_noisy_ocr_text_two():
    text = ("|| Shree Ganeshay Namah ||\nM/s. GUPTA  HARDWARE STORE\nBill No. 0042   Dt. 3-9-2026\n"
            "NET  AMOUNT  PAYABLE\n₹ 1,01,757.00\n"
            "Rs. One Lakh One Thousand Seven Hundred Fifty Seven Only")
    fields, results = read(text)
    assert fields["supplier_name"] == "gupta hardware store"
    assert fields["invoice_number"] == "0042"
    assert fields["bill_date"] == "2026-09-03"
    assert fields["total_amount"] == 101757
    assert results["words_match_total"] == "pass"


def test_bill_date_skips_lr_order_and_expiry_dates():
    text = "LR Date : 01.10.2026\nOrder Date: 02.10.2026\nExp. Dt. 05/2028\nInvoice Date : 03.10.2026"
    assert field_rules.find_bill_date(text) == "2026-10-03"


def test_eway_bill_no_is_not_the_invoice_number():
    assert field_rules.find_invoice_number("E-Way Bill No : 330000123456\nBill No. A-17") == "A-17"


# ---------------------------------------------------------------- number words
@pytest.mark.parametrize("phrase, want", [
    ("Rupees One Lakh One Thousand Seven Hundred Fifty Seven Only", 101757),
    ("FOUR HUNDRED THIRTY-FIVE THOUSAND SIXTY-FOUR", 435064),
    ("Twenty One Thousand Three Hundred and Ninety and Paisa Thirty Five only", 21390.35),
    ("Two Crore Five Lakh", 20500000),
    ("Eleven Hundred Rupees Only", 1100),
    ("Rupees Banana Only", None),
    ("", None),
])
def test_words_to_number(phrase, want):
    assert checks.words_to_number(phrase) == want


def test_words_match_total():
    assert checks.words_match_total(MARG, 4769) == "pass"
    assert checks.words_match_total(MARG, 4796) == "fail"
    assert checks.words_match_total("GRAND TOTAL 500", 500) == "na"
    assert checks.words_match_total(MARG, None) == "na"


# ---------------------------------------------------------------- other checks
def test_items_sum():
    assert checks.items_sum([100, 200], 315, tax=15.4, round_off=-0.4) == "pass"
    assert checks.items_sum([100, 200], 400, tax=15) == "fail"
    assert checks.items_sum([], 400) == "na"
    assert checks.items_sum(None, 400) == "na"


GOOD = {"bill_date": "2026-10-05", "due_date": "2026-10-20", "total_amount": 4769, "invoice_number": "D5721"}


@pytest.mark.parametrize("change, bad", [
    ({}, []),
    ({"invoice_number": "06ABCDE1234F1Z5"}, ["invoice_number"]),       # a GSTIN
    ({"invoice_number": "9812345678"}, ["invoice_number"]),  # fake phone number  # secret-scan: ignore
    ({"total_amount": 0}, ["total_amount"]),
    ({"total_amount": 60_00_000}, ["total_amount"]),                   # over 50 lakh
    ({"bill_date": "2023-01-01"}, ["bill_date"]),                      # more than 2 years old
    ({"due_date": "2026-10-01"}, ["due_date"]),                        # due before bill date
])
def test_sane_fields(change, bad):
    fields = GOOD | change
    got = checks.bad_fields(fields, today=TODAY)
    assert sorted(got) == sorted(bad)
    assert checks.sane_fields(fields, today=TODAY) == ("fail" if bad else "pass")


def test_sane_fields_na_when_nothing_read():
    assert checks.sane_fields({f: None for f in FIELDS}, today=TODAY) == "na"


def test_not_balance():
    assert checks.not_balance(BALANCE_TRAP, 3250) == "pass"
    assert checks.not_balance(BALANCE_TRAP, 15750) == "fail"          # only on a balance line
    assert checks.not_balance("Balance B/F\n8,200.00", 8200) == "fail"  # label on the line above
    assert checks.not_balance(BALANCE_TRAP, 999) == "na"
    assert checks.not_balance(BALANCE_TRAP, None) == "na"


def test_failed_check_marks_field_unsure_so_it_is_not_silent():
    fields = {f: None for f in FIELDS} | {"total_amount": 4796, "invoice_number": "06ABCDE1234F1Z5"}
    results, unsure = checks.run_checks(MARG, fields, today=TODAY)
    assert results["words_match_total"] == "fail"
    assert unsure == ["invoice_number", "total_amount"]


# ---------------------------------------------------------------- PDF reader
def test_scanned_pdf_gives_no_text(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    pdf.save(str(tmp_path / "scan.pdf"))
    pdf.close()
    ex = read_pdf.read_pdf_text(tmp_path / "scan.pdf", 1)
    assert ex.error == "no_text"
    assert all(ex.fields[f] is None for f in FIELDS)


def test_watermark_only_counts_as_no_text():
    assert not read_pdf.has_text("Created with Photo-to-PDF Converter. Download: https://example.com/x\r\nhttps://example.com/")
    assert read_pdf.has_text(MARG)


def test_missing_pdf_is_an_error_not_a_crash(tmp_path):
    ex = read_pdf.read_pdf_text(tmp_path / "nope.pdf", 1)
    assert ex.error.startswith("pdf_error")


def _fake_bills(tmp_path, monkeypatch):
    eval_dir = tmp_path / "data" / "eval"
    inbox = tmp_path / "data" / "raw" / "bills_inbox" / "done"
    eval_dir.mkdir(parents=True)
    inbox.mkdir(parents=True)
    (inbox / "a.pdf").write_bytes(b"%PDF-fake")
    with open(eval_dir / "source_map.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bill_id", "original_file", "page", "sha1"])
        w.writerows([["B001", "a.pdf", "page 2", "x"], ["B002", "photo.jpeg", "", "y"]])
    with open(eval_dir / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bill_id", "image_file", "bill_type", "segment", *FIELDS, "notes"])
        w.writerow(["B001", "B001.jpg", "printed", "medical", "Shiv Medical Distributors", "05-10-2026",
                    "D5721", "4769", "05-10-2026", ""])
        w.writerow(["B002", "B002.jpg", "handwritten", "hardware", "Sai Traders", "02-10-2026", "77", "3250", "", ""])
    seen = []
    monkeypatch.setattr(read_pdf, "page_text", lambda path, page: seen.append(page) or MARG)
    return eval_dir, inbox, seen


def test_source_map_and_cache_format(tmp_path, monkeypatch):
    eval_dir, inbox, seen = _fake_bills(tmp_path, monkeypatch)
    smap = read_pdf.load_source_map(eval_dir / "source_map.csv")
    assert smap["B001"] == {"file": "a.pdf", "page": 2} and smap["B002"]["page"] is None
    cache = eval_dir / "runs" / "pdf_text"
    rec = read_pdf.read_bill("B001", smap, inbox, cache, today=TODAY)
    assert seen == [2]
    saved = json.loads((cache / "B001.json").read_text(encoding="utf-8"))
    for key in ("bill_id", "model", "prompt", "fields", "raw_text", "error",
                "input_tokens", "output_tokens", "latency_ms"):
        assert key in saved
    assert saved["input_tokens"] == saved["output_tokens"] == 0
    assert rec["fields"]["total_amount"] == 4769
    assert "supplier_gstin" in rec
    photo = read_pdf.read_bill("B002", smap, inbox, cache, today=TODAY)
    assert photo["error"] == "not_pdf" and photo["fields"]["total_amount"] is None


def test_step6_script_end_to_end(tmp_path, monkeypatch, capsys):
    _fake_bills(tmp_path, monkeypatch)
    spec = importlib.util.spec_from_file_location("eval06", ROOT / "src" / "06_free_readers_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--readers", "pdf_text"], root=tmp_path, today=TODAY) == 0
    with open(tmp_path / "outputs" / "eval" / "free_readers_summary.csv", newline="", encoding="utf-8") as f:
        rows = {r["scope"]: r for r in csv.DictReader(f) if r["reader"] == "pdf_text"}
    assert rows["all"]["bills"] == "2"
    assert float(rows["text_pdf_only"]["all_fields_right"]) == 1.0
    assert float(rows["all"]["bills_with_silent_error"]) == 0.0   # the photo is "missed", not silent
    assert "FREE READERS" in capsys.readouterr().out


def test_tax_amount_in_words_is_not_the_total():
    """DEV bill B120 printed the GST in words too; it must not prove a wrong total."""
    from bot import checks
    text = "Total Rs.8,012.00\nTax Amount (in words) : Indian Rupees One Thousand Sixty Two Only"
    assert checks.amount_in_words(text) is None
    assert checks.words_match_total(text, 1062) == "na"
