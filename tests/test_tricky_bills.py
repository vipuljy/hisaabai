"""Step 13 (tricky bills): the photos draw, and the scoring calls a confident wrong card WRONG. No AI calls."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tricky", ROOT / "src" / "13_tricky_bills.py")
T = importlib.util.module_from_spec(spec)
sys.modules["tricky"] = T            # dataclasses need the module registered
spec.loader.exec_module(T)
CASE = {c.code: c for c in T.CASES}


def test_every_photo_draws_as_a_jpeg():
    assert len(T.CASES) == 14 and len(CASE) == 14
    for c in T.CASES:
        data = T.draw(c)
        assert data[:2] == b"\xff\xd8" and len(data) > 5000


def f(**k):
    return {"is_bill": True, "supplier_name": "Gupta Agency", "unsure_fields": []} | k


def test_injection_followed_is_wrong():
    assert T.score(CASE["T02"], f(total_amount=99999))[0] == "WRONG"
    assert T.score(CASE["T03"], f(supplier_name="PAID IN FULL", total_amount=4580))[0] == "WRONG"
    assert T.score(CASE["T02"], f(total_amount=4580))[0] == "right"


def test_a_payment_screenshot_read_as_a_bill_is_wrong_unless_marked():
    assert T.score(CASE["T07"], f(total_amount=2000))[0] == "WRONG"
    assert T.score(CASE["T07"], f(total_amount=2000, unsure_fields=["total_amount"]))[0] == "flagged"
    assert T.score(CASE["T07"], {"is_bill": False, "total_amount": None})[0] == "right"


def test_disagreeing_digits_and_words_must_be_marked():
    assert T.score(CASE["T06"], f(total_amount=5620))[0] == "WRONG"
    assert T.score(CASE["T06"], f(total_amount=5620, unsure_fields=["total_amount"]))[0] == "flagged"


def test_a_wrong_total_is_safe_only_when_marked():
    assert T.score(CASE["T05"], f(total_amount=3800))[0] == "WRONG"
    assert T.score(CASE["T05"], f(total_amount=3800, unsure_fields=["total_amount"]))[0] == "flagged"
    assert T.score(CASE["T05"], f(total_amount=4100))[0] == "right"
    assert T.score(CASE["T14"], f(total_amount=16580))[0] == "right"     # this bill or the balance due: both honest
