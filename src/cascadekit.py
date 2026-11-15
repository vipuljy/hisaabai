"""Shared helpers for steps 07 (router) and 08 (cascade): load cached runs, check what exists, score, cost.

Both steps use CACHED results only (data/eval/runs/<run>/<bill_id>.json): no API calls, no OCR.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import evalkit  # noqa: E402

CLAUDE_RUNS = ("haiku_v2", "sonnet_v2")
FREE_RUNS = ("pdf_text", "ocr_rapid")
NEED_CLAUDE = "Claude runs not found ({missing}). Run RUN_STEP1_eval.bat first, then run this again."
NEED_FREE = "Free-reader runs not found ({missing}). Run RUN_STEP6_free_readers.bat first, then run this again."


def missing_runs(root: Path, labels: list[dict], runs) -> list[str]:
    """Runs whose cache folder is missing or lacks any labelled bill."""
    out = []
    for run in runs:
        folder = root / "data" / "eval" / "runs" / run
        if not folder.is_dir() or any(not (folder / f"{l['bill_id']}.json").exists() for l in labels):
            out.append(run)
    return out


def load_run(root: Path, run: str, labels: list[dict]) -> dict:
    folder = root / "data" / "eval" / "runs" / run
    return {l["bill_id"]: json.loads((folder / f"{l['bill_id']}.json").read_text(encoding="utf-8")) for l in labels}


def load_split(root: Path, labels: list[dict]) -> dict:
    """bill_id -> {"split": dev/test, "group": ...}. Made by step 06 if it does not exist yet (seed 20261026)."""
    path = root / "data" / "eval" / "split.csv"
    if not path.exists():
        spec = importlib.util.spec_from_file_location("eval06", Path(__file__).with_name("06_free_readers_eval.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.load_or_make_split(root, labels)
    with open(path, newline="", encoding="utf-8") as f:
        return {r["bill_id"]: r for r in csv.DictReader(f)}


def config(root: Path) -> dict:
    return json.loads((root / "src" / "eval_config.json").read_text(encoding="utf-8"))


def call_cost_inr(rec: dict, cfg: dict) -> float | None:
    """Rupees for one cached Claude call, from src/eval_config.json prices. None if prices are not filled in."""
    p = (cfg.get("prices") or {}).get(rec.get("model")) or {}
    if p.get("input_usd_per_mtok") is None or p.get("output_usd_per_mtok") is None or not cfg.get("usd_to_inr"):
        return None
    usd = (rec.get("input_tokens", 0) * p["input_usd_per_mtok"] + rec.get("output_tokens", 0) * p["output_usd_per_mtok"]) / 1e6
    return usd * cfg["usd_to_inr"]


def outcomes(label: dict, fields: dict) -> dict:
    """field -> scored row (evalkit), for one bill."""
    return {s["field"]: s for s in evalkit.score_bill(label, fields)}


def haiku_ok(label: dict, rec: dict) -> int:
    """The router's target: Haiku read BOTH the total and the bill date right (or rightly blank)."""
    o = outcomes(label, rec["fields"])
    return int(all(o[f]["outcome"] in ("correct", "correct_blank") for f in ("total_amount", "bill_date")))


def total_trusted(rec: dict | None) -> bool:
    f = (rec or {}).get("fields") or {}
    return f.get("total_amount") is not None and "total_amount" not in (f.get("unsure_fields") or [])
