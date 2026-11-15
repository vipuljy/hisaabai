"""The review tagger (src/04_review_pains.py): right themes on realistic reviews, no tags on look-alikes."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pains", ROOT / "src" / "04_review_pains.py")
pains = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pains)

CASES = [
    ("Entering every purchase bill from my supplier takes so much time, wish I could just scan it",
     {"W_supplier_bills", "manual_entry_effort", "wants_scan_or_voice"}),
    ("Customers never pay back, the udhar reminder sms is not going to them", {"W_customer_udhaar"}),
    ("udhaar wale customer ko payment reminder bhejne ka option chahiye", {"W_customer_udhaar"}),
    ("Daily cash book closing balance does not match with UPI entries", {"W_cash_upi"}),
    ("After update I lost all my data, entries missing, very bad", {"data_loss_trust"}),
    ("GST invoice format is wrong and e-way bill not supported", {"gst_compliance"}),
    ("Premium subscription is too expensive for a small shop", {"pricing"}),
    ("App crashes on login, OTP not coming", {"bugs_login"}),
    ("Hindi language option nahi hai, bahut confusing app", {"language_ease"}),
    ("Distributor ka hisaab rakhna mushkil hai, har bill manually daalna padta hai", {"W_supplier_bills", "manual_entry_effort"}),
    # look-alikes that must NOT be tagged as the wedge
    ("Made an in-app purchase for premium and it did not activate", {"pricing"}),
    ("Could not recover my account after changing phone", set()),
    ("They collect too much personal data, privacy issue", set()),
]


@pytest.mark.parametrize("review, expected", CASES)
def test_tagger(review, expected):
    got = {t for t, rx in pains.THEMES.items() if rx.search(review)}
    assert got == expected


def test_end_to_end(tmp_path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    rows = [{"content": r, "score": 1, "at": "2026-05-01", "thumbsUpCount": i} for i, (r, _) in enumerate(CASES)]
    rows += [{"content": "Very easy to keep udhaar of my customers, love it", "score": 5, "at": "2026-05-02", "thumbsUpCount": 3}]
    pd.DataFrame(rows).to_csv(raw / "khatabook_reviews.csv", index=False)
    pd.DataFrame(rows[:4]).to_csv(raw / "vyapar_reviews.csv", index=False)
    assert pains.main(raw, tmp_path / "out") == 0
    overall = pd.read_csv(tmp_path / "out" / "pain_overall.csv").set_index("theme")
    assert overall.loc["W_customer_udhaar", "unhappy_mentions"] == 4  # 2 in khatabook + the same 2 in the vyapar copy
    assert overall.loc["W_customer_udhaar", "share_of_happy"] == 1.0
    sample = pd.read_csv(tmp_path / "out" / "hand_label_sample.csv")
    assert {"tags", "correct_tags_yes_no"} <= set(sample.columns)
    assert "WEDGES" in capsys.readouterr().out
