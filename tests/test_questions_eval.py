"""Step 10 question eval (src/10_questions_eval.py): scoring rules, on a tiny invented question file."""

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("q10", ROOT / "src" / "10_questions_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = [  # q_id, message, intent, supplier, period, remind_on, amount, tricky, notes
    ("Q001", "verma traders ka kitna baaki hai", "dues_supplier", "Verma Traders", "", "", "", "", ""),
    ("Q002", "verma ko 2000 de diya", "payment", "Verma Traders", "", "", "2000", "payment", ""),
    ("Q003", "sharma ko 5000 diye", "payment", "ASK", "", "", "5000", "payment", ""),
    ("Q004", "aaj match kaun jeetega", "off_topic", "", "", "", "", "off_topic", ""),
    ("Q005", "kal yaad dilana balaji ko", "set_reminder", "Balaji Enterprises", "", "2026-10-29", "", "kal", ""),
    ("Q006", "kal kitna payment kiya", "paid_period", "", "yesterday", "", "", "kal", ""),
    ("Q007", "agarwal kirana ka baaki", "dues_supplier", "Verma Traders", "", "", "", "", "deliberately wrong label"),
]


def test_question_eval_end_to_end(tmp_path, capsys):
    q10 = load()
    (tmp_path / "data" / "eval").mkdir(parents=True)
    with open(tmp_path / "data" / "eval" / "questions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["q_id", "message", "intent", "supplier", "period", "remind_on", "amount", "tricky", "notes"])
        w.writerows(ROWS)
    assert q10.main([], root=tmp_path) == 0
    out = tmp_path / "outputs" / "eval"
    with open(out / "questions_detail.csv", newline="", encoding="utf-8") as f:
        got = {r["q_id"]: r["outcome"] for r in csv.DictReader(f)}
    assert got == {"Q001": "correct", "Q002": "correct", "Q003": "correct", "Q004": "correct",
                   "Q005": "correct", "Q006": "correct", "Q007": "WRONG"}
    assert (out / "questions_detail_before.csv").exists() and (out / "questions_table.csv").exists()
    printed = capsys.readouterr().out
    assert "WARNING: questions.csv changed" in printed and "Q007" in printed
    assert q10.main([], root=tmp_path) == 0  # second run: before -> after
    assert "before -> after" in capsys.readouterr().out
