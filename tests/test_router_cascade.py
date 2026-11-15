"""Steps 07 (router) and 08 (cascade) end to end on FAKE cached runs in a temp folder (never in data/).
No network, no API, no OCR. Supplier names are invented."""

import csv
import importlib.util
import io
import json
import random
from pathlib import Path

import pytest

from bot import router
from bot.extract import FIELDS

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jpeg(noisy: bool, seed: int) -> bytes:
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    img = Image.new("L", (300, 420), 70 if noisy else 245)
    d = ImageDraw.Draw(img)
    for y in range(20, 400, 18):
        d.line((20, y, 280, y), fill=200 if noisy else 20, width=2)
    if noisy:
        for _ in range(3000):
            img.putpixel((rng.randrange(300), rng.randrange(420)), rng.randrange(256))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


def rec(bill_id, model, fields, tokens=(1500, 80), **extra):
    f = {k: None for k in FIELDS} | {"is_bill": True, "unsure_fields": []} | fields
    return {"bill_id": bill_id, "model": model, "prompt": "v2", "fields": f, "raw_text": "", "error": None,
            "input_tokens": tokens[0], "output_tokens": tokens[1], "latency_ms": 900} | extra


@pytest.fixture()
def world(tmp_path):
    """24 fake bills: 2 PDFs with text, 3 photos with a trusted OCR total, the rest go to the router.
    'Hard' photos (noisy, many low-confidence words) are the ones Haiku gets wrong; Sonnet gets them right."""
    ev = tmp_path / "data" / "eval"
    (ev / "images").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "eval_config.json").write_text(json.dumps({
        "runs": [], "usd_to_inr": 90,
        "prices": {"claude-haiku-5-5": {"input_usd_per_mtok": 1, "output_usd_per_mtok": 5},
                   "claude-sonnet-5-5": {"input_usd_per_mtok": 3, "output_usd_per_mtok": 15}}}), encoding="utf-8")
    labels, smap = [], []
    runs = {r: {} for r in ("pdf_text", "ocr_rapid", "haiku_v2", "sonnet_v2")}
    for i in range(24):
        b = f"B{i + 1:03d}"
        hard = i % 3 == 0
        sup = f"Supplier {i % 8}"
        # cached readings store NORMALISED values (bot.extract.clean_fields), like the real runs
        truth = {"supplier_name": sup.lower(), "bill_date": "2026-10-05", "invoice_number": f"INV{i}", "total_amount": 1000 + i}
        labels.append({"bill_id": b, "image_file": f"{b}.jpg", "bill_type": "handwritten" if hard else "printed",
                       "segment": "kirana", "supplier_name": sup, "bill_date": "05-10-2026", "invoice_number": f"INV{i}",
                       "total_amount": str(1000 + i), "due_date": "", "notes": ""})
        (ev / "images" / f"{b}.jpg").write_bytes(jpeg(hard, i))
        is_pdf = i in (1, 2)
        smap.append([b, f"bill{i}.pdf" if is_pdf else f"photo{i}.jpeg", "page 1" if is_pdf else "", "x"])
        runs["pdf_text"][b] = rec(b, "pdf_text", truth if is_pdf else {}, (0, 0), error=None if is_pdf else "not_pdf")
        words = [[f"w{j}", 50.0 if hard else 97.0, 1, 1, j // 4, 10 * j, 30] for j in range(40)]
        trusted = i in (4, 5, 7)
        ocr_total = (1000 + i) if i != 7 else 9999  # B008: OCR 'trusted' but Haiku disagrees -> ask the owner
        runs["ocr_rapid"][b] = rec(b, "ocr_rapid", {"total_amount": ocr_total if trusted else None}, (0, 0),
                                   ocr={"words": words, "page_conf": 50.0 if hard else 97.0},
                                   checks={"words_match_total": "pass" if trusted else "na"},
                                   total_proof="words" if trusted else None, raw_text="GRAND TOTAL 1000\nDate 05/10/26")
        wrong = truth | {"total_amount": 7}
        runs["haiku_v2"][b] = rec(b, "claude-haiku-5-5", wrong if hard else truth)
        runs["sonnet_v2"][b] = rec(b, "claude-sonnet-5-5", truth, (1500, 90))
    with open(ev / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(labels[0]))
        w.writeheader()
        w.writerows(labels)
    with open(ev / "source_map.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["bill_id", "original_file", "page", "sha1"], *smap])
    for run, recs in runs.items():
        (ev / "runs" / run).mkdir(parents=True)
        for b, r in recs.items():
            (ev / "runs" / run / f"{b}.json").write_text(json.dumps(r), encoding="utf-8")
    return tmp_path


def test_scripts_stop_cleanly_without_claude_runs(tmp_path, capsys):
    ev = tmp_path / "data" / "eval"
    ev.mkdir(parents=True)
    (ev / "labels.csv").write_text("bill_id,image_file,bill_type,segment,supplier_name,bill_date,invoice_number,"
                                   "total_amount,due_date,notes\nB001,B001.jpg,printed,kirana,X,,,100,,\n", encoding="utf-8")
    assert load("r07", "07_router.py").main([], root=tmp_path) == 1
    assert load("c08", "08_cascade_eval.py").main([], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert out.count("Run RUN_STEP1_eval.bat first") == 2


def test_hand_label_is_never_a_feature():
    assert not {"bill_type", "segment", "handwritten", "printed"} & set(router.FEATURES)


def test_router_end_to_end(world, capsys):
    pytest.importorskip("sklearn")
    assert load("r07", "07_router.py").main([], root=world) == 0
    out = world / "outputs" / "eval"
    for f in ("router_cv.csv", "router_oof.csv", "router_choice.json", "router_tree.png", "router_model.pkl"):
        assert (out / f).exists(), f
    with open(out / "router_oof.csv", newline="", encoding="utf-8") as f:
        oof = list(csv.DictReader(f))
    assert len(oof) == 24 and all(0 <= float(r["p_router"]) <= 1 for r in oof)
    choice = json.loads((out / "router_choice.json").read_text(encoding="utf-8"))
    assert choice["model"] in ("logreg", "tree")
    assert len(choice["nested"]["fold_thresholds"]) == 5  # one threshold per outer fold, picked inside it
    assert {"sonnet_nested", "sonnet_same_predictions", "threshold_nested"} <= set(oof[0])
    # the hard photos are noisy and low-confidence: grouped CV should still catch most of Haiku's misses
    hard = [r for r in oof if r["haiku_ok"] == "0"]
    assert sum(float(r["p_router"]) < 0.5 for r in hard) >= len(hard) // 2
    assert (world / "data" / "eval" / "split.csv").exists()
    assert "CROSS-VALIDATED" in capsys.readouterr().out


def test_cascade_end_to_end(world, capsys):
    pytest.importorskip("sklearn")
    load("r07", "07_router.py").main([], root=world)
    assert load("c08", "08_cascade_eval.py").main([], root=world) == 0
    out = world / "outputs" / "eval"
    with open(out / "cascade_table.csv", newline="", encoding="utf-8") as f:
        rows = {(r["reader"], r["scope"]): r for r in csv.DictReader(f)}
    with open(out / "cascade_routes.csv", newline="", encoding="utf-8") as f:
        routes = {r["bill_id"]: r for r in csv.DictReader(f)}
    assert routes["B002"]["route"] == "pdf_text" and routes["B003"]["route"] == "pdf_text"
    assert routes["B005"]["route"] == "ocr_haiku" and routes["B005"]["ocr_agree"] == "True"
    assert routes["B008"]["route"] == "ocr_haiku" and routes["B008"]["ocr_agree"] == "False"
    cascade = rows[("cascade", "all")]
    assert float(cascade["pct_free"]) == pytest.approx(2 / 24, abs=1e-3)
    assert float(cascade["haiku_vs_ocr_agree"]) == pytest.approx(2 / 3, abs=1e-3) and cascade["agree_n"] == "3"
    # Haiku alone is silently wrong on the 8 hard bills; Sonnet alone is right; the cascade sends the hard
    # ones to Sonnet (router), so it matches Sonnet's silent errors at a lower cost
    assert float(rows[("haiku_v2", "all")]["silent_error_rate"]) == pytest.approx(8 / 24, abs=1e-3)
    assert float(rows[("sonnet_v2", "all")]["silent_error_rate"]) == 0
    assert float(cascade["silent_error_rate"]) <= 0.01 + 1e-9
    assert float(cascade["pct_sent_to_sonnet"]) > 0
    assert float(cascade["cost_per_bill_inr"]) < float(rows[("sonnet_v2", "all")]["cost_per_bill_inr"])
    assert all(routes[f"B{i + 1:03d}"]["route"] == "sonnet" for i in range(0, 24, 3))  # every hard bill
    saved = json.loads((world / "data" / "eval" / "runs" / "cascade" / "B008.json").read_text(encoding="utf-8"))
    assert saved["route"] == "ocr_haiku" and "total_amount" in saved["fields"]["unsure_fields"]  # disagree -> ask
    assert "CASCADE" in capsys.readouterr().out


def test_route_bill_rules():
    c08 = load("c08", "08_cascade_eval.py")
    no_pdf = {"error": "not_pdf", "fields": {}}
    ocr_none = {"fields": {"total_amount": None, "unsure_fields": []}}
    h = rec("B1", "claude-haiku-5-5", {"total_amount": 500, "unsure_fields": ["bill_date"]})
    s = rec("B1", "claude-sonnet-5-5", {"total_amount": 500})
    assert c08.route_bill(no_pdf, ocr_none, h, s, False)["route"] == "haiku"
    assert c08.route_bill(no_pdf, ocr_none, h, s, True)["route"] == "sonnet"
    esc = c08.route_bill(no_pdf, ocr_none, h, s, False, escalate=True)
    assert esc["route"] == "haiku_then_sonnet" and [c["model"] for c in esc["calls"]] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    pdf = {"error": None, "fields": {"total_amount": 100, "unsure_fields": []}}
    assert c08.route_bill(pdf, ocr_none, h, s, True)["calls"] == []


def test_nested_threshold_never_sees_the_bills_it_routes(world, monkeypatch):
    """Every threshold must be picked from training bills only: record which bills each pick looked at."""
    pytest.importorskip("sklearn")
    r07 = load("r07", "07_router.py")
    seen = []
    real = r07.threshold_table

    def spy(labels, p, haiku, sonnet, cfg):
        seen.append({l["bill_id"] for l in labels})
        return real(labels, p, haiku, sonnet, cfg)
    monkeypatch.setattr(r07, "threshold_table", spy)
    assert r07.main([], root=world) == 0
    with open(world / "outputs" / "eval" / "router_oof.csv", newline="", encoding="utf-8") as f:
        oof = list(csv.DictReader(f))
    inner = seen[1:]  # seen[0] = the single all-bills threshold kept for the saved model / optimistic comparison
    assert len(inner) == 5 and all(len(b) < 24 for b in inner)
    # the bills in each outer test fold never appear in the threshold pick for that fold
    import numpy as np
    X = np.zeros((24, 1))
    y = np.array([int(r["haiku_ok"]) for r in oof])
    groups = [r["group"] for r in oof]
    for k, (tr, te) in enumerate(r07.group_splits(X, y, groups)):
        test_bills = {oof[i]["bill_id"] for i in te}
        assert not (test_bills & inner[k])


def test_policies_end_to_end(world, capsys):
    pytest.importorskip("sklearn")
    p09 = load("p09", "09_policies_eval.py")
    assert p09.main(["--today", "2026-10-28"], root=world) == 0
    out = world / "outputs" / "eval"
    with open(out / "policy_table.csv", newline="", encoding="utf-8") as f:
        rows = {(r["policy"], r["scope"]): r for r in csv.DictReader(f)}
    for p in ("P0", "P1", "P2", "P3", "P4", "P5", "P5_no_ocr", "P5_trusted_ocr", "haiku_v2_only", "sonnet_v2_only"):
        assert (p, "all") in rows, p
    assert rows[("sonnet_v2_only", "all")]["silent_upper95"].startswith("< ")  # 0 of 24 -> "< 3/24"
    choice = json.loads((out / "policy_choice.json").read_text(encoding="utf-8"))
    assert choice["rule"] and choice["ocr_decision"]["default"] in ("P5_trusted_ocr", "P5_no_ocr")
    assert "READ POLICIES" in capsys.readouterr().out


def test_upper_bound():
    p09 = load("p09", "09_policies_eval.py")
    assert p09.upper95(0, 100) == "< 3.0% (3/100)"
    assert p09.upper95(2, 136).endswith("%") and float(p09.upper95(2, 136)[:-1]) > 1.5
