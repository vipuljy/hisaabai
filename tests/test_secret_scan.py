"""The pre-commit secret scan (scripts/secret_scan.py): catches real-looking secrets, lets placeholders through."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("secret_scan", ROOT / "scripts" / "secret_scan.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

FAKE_KEY = "sk-ant-" + "api03-" + "a" * 30  # built at runtime so this file itself stays clean


def kinds(text):
    return [k for _, k in scan.findings_in("x", text)]


def test_catches_secrets():
    assert "Anthropic API key" in kinds(f"key = '{FAKE_KEY}'")
    assert "Meta access token" in kinds("t = EAA" + "B" * 40)
    assert kinds("DATABASE_URL=postgresql://postgres.ab:" + "Real" + "Pass99@host:5432/postgres")
    assert kinds("JOBS_TOKEN=" + "q" * 30) == [".env-style secret"]
    assert kinds("owner: +91 " + "9988" + "7 66554") == ["Indian mobile number"]


def test_allows_placeholders_and_fakes():
    assert kinds("WHATSAPP_TOKEN=paste-your-access-token-here") == []
    assert kinds("postgresql://postgres:postgres@localhost:5432/postgres") == []
    assert kinds("OWNER = '919876543210'") == []  # the test fixture number family
    assert kinds("wa = '911111111111'") == []
    assert kinds("amount 4580 on 05/10/2026, invoice 1234567890") == []
    assert kinds(f"{FAKE_KEY}  # secret-scan: ignore") == []


def test_blocks_private_files():
    assert scan.check_file(".env")
    assert scan.check_file(".env.production")
    assert not scan.check_file(".env.example")
    assert scan.check_file("data/raw/bills/S01_1.jpg")
    assert scan.check_file("discovery/private/shops_tracker.csv")
    assert scan.check_file("docs/bill.jpg")


def test_project_is_clean_now():
    assert scan.main(["--all"]) == 0


def test_outputs_are_private_now():
    """29 Oct: outputs/ holds eval results that quote real bills (and voice transcripts): never committed,
    not even the router chart."""
    assert scan.check_file("outputs/eval/router_tree.png")
    assert scan.check_file("outputs/eval/voice_results.csv")
    assert scan.check_file("deploy/brand/vahi_icon_1024.png") == []  # our own brand image is fine


def test_privacy_guard_finds_real_names_and_invoice_numbers_without_printing_them(tmp_path, monkeypatch, capsys):
    labels = tmp_path / "labels.csv"
    labels.write_text("bill_id,supplier_name,invoice_number\n"
                      "B1,Zebra Pharma Distributors,QX-77123\nB2,Om Sai,4471\nB3,B K,\n", encoding="utf-8")
    v = scan.private_values(labels)
    text = ("supplier = 'ZEBRA  PHARMA, distributors'\n"   # 1: name, any case / punctuation
            "amount = 4471\n"                                # 2: a bare number: not an invoice context
            "invoice_number = '4471'\n"                      # 3: plain-number invoice in context
            "ref qx-77123 copied\n"                          # 4: letter code anywhere
            "zebra pharmacy is a different word\n"           # 5: no
            "b k sharma\n")                                  # 6: 'B K' too short to test
    got = scan.private_findings(text, v)
    assert [n for n, _ in got] == [1, 3, 4]
    assert all("labels.csv" in kind for _, kind in got)
    monkeypatch.setattr(scan, "_private", {"v": v})
    monkeypatch.setattr(scan, "ROOT", tmp_path)  # a pretend project, so nothing is written into the real one
    (tmp_path / "probe.py").write_text("x = 'Zebra Pharma Distributors'\n", encoding="utf-8")
    msgs = scan.check_file("probe.py")
    assert msgs == ["probe.py:1: looks like a real supplier name from data/eval/labels.csv"]
    assert "Zebra" not in msgs[0]  # never the value
    assert scan.private_values(tmp_path / "missing.csv") is None
