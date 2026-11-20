"""Scoring for the bill-reading eval. Used by src/01_eval_bill_reading.py and the tests.

Supplier names match by bot.extract.same_supplier (exact, one name's words at the start of the other, or
difflib ratio >= 0.9); every other field is compared exactly after normalisation.
Each field of each bill gets one outcome:
  correct        truth present, model matches
  correct_blank  nothing to read (e.g. no due date printed), model said null
  wrong          both present, different
  missed         truth present, model said null  -> the bot asks the owner (safe)
  invented       truth absent, model made one up -> dangerous
A "silent error" is wrong or invented AND not flagged in unsure_fields:
the owner would see a confident wrong value. That is the number to drive down.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.extract import FIELDS, NORMALISERS, extract_bill, same_supplier  # noqa: E402

LABEL_COLUMNS = ["bill_id", "image_file", "bill_type", "segment", *FIELDS, "notes"]
BILL_TYPES = {"printed", "handwritten"}


def load_labels(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        # Rows whose notes start with SKIP are kept in the file (so numbering stays complete) but not scored:
        # ledger statements, second photos of the same bill, continuation pages of multi-page PDFs.
        rows = [r for r in csv.DictReader(f) if not (r.get("notes") or "").strip().upper().startswith("SKIP")]
    problems = []
    for i, r in enumerate(rows, start=2):
        if not r.get("bill_id"):
            problems.append(f"row {i}: bill_id empty")
        if r.get("bill_type") not in BILL_TYPES:
            problems.append(f"row {i}: bill_type must be printed or handwritten, got {r.get('bill_type')!r}")
        for d in ("bill_date", "due_date"):
            if r.get(d) and NORMALISERS[d](r[d]) is None:
                problems.append(f"row {i}: {d} {r[d]!r} is not a date (use DD/MM/YYYY)")
        if r.get("total_amount") and NORMALISERS["total_amount"](r["total_amount"]) is None:
            problems.append(f"row {i}: total_amount {r['total_amount']!r} is not a number")
    ids = [r["bill_id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate bill_id values")
    if problems:
        raise ValueError("labels.csv problems:\n  " + "\n  ".join(problems))
    return rows


def score_field(name: str, truth_raw, pred_norm, unsure: list[str]) -> dict:
    truth = NORMALISERS[name](truth_raw)
    pred = pred_norm
    if truth is None and pred is None:
        outcome = "correct_blank"
    elif truth is None:
        outcome = "invented"
    elif pred is None:
        outcome = "missed"
    elif truth == pred or (name == "supplier_name" and same_supplier(truth, pred)):
        outcome = "correct"  # supplier: city appended or a near-identical spelling counts (bot.extract.same_supplier)
    else:
        outcome = "wrong"
    flagged = name in unsure
    return {
        "field": name,
        "truth": truth,
        "pred": pred,
        "outcome": outcome,
        "flagged_unsure": flagged,
        "silent_error": outcome in ("wrong", "invented") and not flagged,
    }


def score_bill(label: dict, fields: dict) -> list[dict]:
    unsure = fields.get("unsure_fields") or []
    return [score_field(f, label.get(f), fields.get(f), unsure) for f in FIELDS]


def run_one(label: dict, images_dir: Path, cache_dir: Path, model: str, prompt: str, client=None,
            effort: str | None = None) -> dict:
    """Read one bill, using the cached reply if this run already did it (no double paying).
    A cached reply WITH an error (truncated, bad JSON) is re-read once per run instead of being reused;
    the new reply replaces it in the cache (and keeps the previous error in "retried_after")."""
    cache_file = cache_dir / f"{label['bill_id']}.json"
    previous = None
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if not cached.get("error"):
            return cached
        previous = cached["error"]
    image_bytes = (images_dir / label["image_file"]).read_bytes()
    ex = extract_bill(image_bytes, model=model, prompt_version=prompt, client=client, effort=effort)
    record = {
        "bill_id": label["bill_id"], "model": model, "prompt": prompt, "effort": effort,
        "fields": ex.fields, "raw_text": ex.raw_text, "error": ex.error,
        "input_tokens": ex.input_tokens, "output_tokens": ex.output_tokens, "latency_ms": ex.latency_ms,
        "stop_reason": ex.stop_reason, "block_types": ex.block_types, "thinking_tokens": ex.thinking_tokens,
    }
    if previous:
        record["retried_after"] = previous
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarise(scored: list[dict], records: list[dict], runs: list[dict], prices: dict, usd_to_inr) -> tuple[list, list]:
    """Returns (by_field rows, by_run rows)."""
    by_field = []
    groups = defaultdict(list)
    for s in scored:
        for bt in (s["bill_type"], "all"):
            groups[(s["run"], bt, s["field"])].append(s)
    for (run, bt, fld), rows in sorted(groups.items()):
        n = len(rows)
        cnt = defaultdict(int)
        for r in rows:
            cnt[r["outcome"]] += 1
        by_field.append({
            "run": run, "bill_type": bt, "field": fld, "n": n,
            "accuracy": _rate(cnt["correct"] + cnt["correct_blank"], n),
            "missed_rate": _rate(cnt["missed"], n),
            "silent_error_rate": _rate(sum(r["silent_error"] for r in rows), n),
        })

    by_run = []
    run_meta = {r["name"]: r for r in runs}
    for name in run_meta:
        recs = [r for r in records if r["run"] == name]
        if not recs:
            continue
        bills = defaultdict(list)
        for s in scored:
            if s["run"] == name:
                bills[s["bill_id"]].append(s)
        n_bills = len(bills)
        all_right = sum(all(x["outcome"] in ("correct", "correct_blank") for x in v) for v in bills.values())
        total_right = sum(any(x["field"] == "total_amount" and x["outcome"] == "correct" for x in v) for v in bills.values())
        silent_bills = sum(any(x["silent_error"] for x in v) for v in bills.values())
        model = run_meta[name]["model"]
        p = prices.get(model) or {}
        in_tok = sum(r["input_tokens"] for r in recs)
        out_tok = sum(r["output_tokens"] for r in recs)  # includes thinking tokens: billed as output
        think = [r["thinking_tokens"] for r in recs if r.get("thinking_tokens") is not None]
        cost_usd = None
        if p.get("input_usd_per_mtok") is not None and p.get("output_usd_per_mtok") is not None:
            cost_usd = (in_tok * p["input_usd_per_mtok"] + out_tok * p["output_usd_per_mtok"]) / 1e6
        per_bill_inr = round(cost_usd / len(recs) * usd_to_inr, 3) if cost_usd is not None and usd_to_inr else None
        by_run.append({
            "run": name, "model": model, "prompt": run_meta[name]["prompt"],
            "effort": run_meta[name].get("effort") or "default", "bills": n_bills,
            "all_fields_right": _rate(all_right, n_bills),
            "total_amount_right": _rate(total_right, n_bills),
            "bills_with_silent_error": _rate(silent_bills, n_bills),
            "json_errors": sum(1 for r in recs if r.get("error")),
            "truncated": sum(1 for r in recs if r.get("error") == "truncated"),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in recs) / len(recs)),
            "median_latency_ms": sorted(r["latency_ms"] for r in recs)[len(recs) // 2],
            "avg_input_tokens": round(in_tok / len(recs)),
            "avg_output_tokens": round(out_tok / len(recs)),
            # thinking vs answer text per bill (only for replies that report thinking tokens)
            "avg_thinking_tokens": round(sum(think) / len(think)) if think else None,
            "avg_text_tokens": round(sum(r["output_tokens"] - r["thinking_tokens"] for r in recs
                                         if r.get("thinking_tokens") is not None) / len(think)) if think else None,
            "cost_per_bill_inr": per_bill_inr,
        })
    return by_field, by_run


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
