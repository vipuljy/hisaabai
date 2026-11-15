"""Step 8: the cascade. Read most bills cheaply, send only the hard ones to Sonnet, without more silent errors.

Route per bill (cached results only, no API calls):
  1. pdf_text   the original PDF has a text layer and a total -> free; the owner confirms the supplier with a tap.
  2. ocr_haiku  RapidOCR has a TRUSTED total (words / maths proof) -> Haiku still reads the other fields; the OCR
                total is a free second read: same total = double-confirmed, different = ask the owner.
  3. router     Haiku or Sonnet as decided by step 07 with a NESTED threshold (picked inside each training fold,
                never on the bills it routes): the honest number.
Variant cascade_escalate: like cascade, but a Haiku reading with any unsure field is re-read by Sonnet.
Variant cascade_same_pred: the router threshold picked on the same predictions it is scored on (optimistic, for
comparison only).
Compared with: pdf_text alone, ocr_rapid alone, Haiku for everything, Sonnet for everything.

Usage (from the project root, after RUN_STEP1_eval.bat, RUN_STEP6_free_readers.bat and src/07_router.py):
  python src/08_cascade_eval.py
Outputs: outputs/eval/cascade_table.csv (one row per reader x scope), outputs/eval/cascade_routes.csv (per bill),
  data/eval/runs/cascade/<bill_id>.json (same cache format as every reader, plus "route").
Cost uses the prices in src/eval_config.json; until they are filled in, the cost column is empty.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import cascadekit as K  # noqa: E402
import evalkit  # noqa: E402

SCOPES = {
    "all": lambda l, sp: True,
    "printed": lambda l, sp: l["bill_type"] == "printed",
    "printed_TEST": lambda l, sp: l["bill_type"] == "printed" and sp == "test",
    "handwritten": lambda l, sp: l["bill_type"] == "handwritten",
}


def _with_total(fields: dict, total, unsure: bool) -> dict:
    f = dict(fields)
    f["total_amount"] = total
    u = [x for x in (f.get("unsure_fields") or []) if x != "total_amount"]
    f["unsure_fields"] = u + (["total_amount"] if unsure else [])
    return f


def route_bill(pdf: dict, ocr: dict, haiku: dict, sonnet: dict, to_sonnet: bool, escalate: bool = False) -> dict:
    """{"route", "fields", "calls": [cached Claude records used], "ocr_agree": True/False/None}."""
    if pdf.get("error") is None and (pdf.get("fields") or {}).get("total_amount") is not None:
        return {"route": "pdf_text", "fields": pdf["fields"], "calls": [], "ocr_agree": None}
    if K.total_trusted(ocr):
        ocr_total = ocr["fields"]["total_amount"]
        same = haiku["fields"].get("total_amount") == ocr_total
        return {"route": "ocr_haiku", "fields": _with_total(haiku["fields"], ocr_total, unsure=not same),
                "calls": [haiku], "ocr_agree": same}
    if not to_sonnet:
        if escalate and (haiku["fields"].get("unsure_fields") or haiku.get("error")):
            return {"route": "haiku_then_sonnet", "fields": sonnet["fields"], "calls": [haiku, sonnet], "ocr_agree": None}
        return {"route": "haiku", "fields": haiku["fields"], "calls": [haiku], "ocr_agree": None}
    return {"route": "sonnet", "fields": sonnet["fields"], "calls": [sonnet], "ocr_agree": None}


def summarise(name: str, scope: str, labels: list[dict], results: dict, cfg: dict) -> dict | None:
    if not labels:
        return None
    n = len(labels)
    right = total_right = silent = haiku = sonnet = free = 0
    cost, priced = 0.0, True
    agree = [results[l["bill_id"]]["ocr_agree"] for l in labels if results[l["bill_id"]]["ocr_agree"] is not None]
    for l in labels:
        r = results[l["bill_id"]]
        o = K.outcomes(l, r["fields"])
        right += all(s["outcome"] in ("correct", "correct_blank") for s in o.values())
        total_right += o["total_amount"]["outcome"] in ("correct", "correct_blank")
        silent += any(s["silent_error"] for s in o.values())
        models = [c.get("model", "") for c in r["calls"]]
        haiku += any("haiku" in m for m in models)
        sonnet += any("sonnet" in m for m in models)
        free += not r["calls"]
        for c in r["calls"]:
            v = K.call_cost_inr(c, cfg)
            priced &= v is not None
            cost += v or 0
    return {"reader": name, "scope": scope, "bills": n, "all_fields_right": round(right / n, 4),
            "total_amount_right": round(total_right / n, 4), "silent_error_rate": round(silent / n, 4),
            "pct_free": round(free / n, 4), "pct_sent_to_haiku": round(haiku / n, 4),
            "pct_sent_to_sonnet": round(sonnet / n, 4), "cost_per_bill_inr": round(cost / n, 3) if priced else None,
            "haiku_vs_ocr_agree": round(sum(agree) / len(agree), 4) if agree else None, "agree_n": len(agree)}


def main(argv=None, root: Path = ROOT) -> int:
    labels = evalkit.load_labels(root / "data" / "eval" / "labels.csv")
    missing = K.missing_runs(root, labels, K.CLAUDE_RUNS)
    if missing:
        print(K.NEED_CLAUDE.format(missing=", ".join(missing)))
        return 1
    missing = K.missing_runs(root, labels, K.FREE_RUNS)
    if missing:
        print(K.NEED_FREE.format(missing=", ".join(missing)))
        return 1
    out_dir = root / "outputs" / "eval"
    oof_path, choice_path = out_dir / "router_oof.csv", out_dir / "router_choice.json"
    if not oof_path.exists() or not choice_path.exists():
        print("Router results not found. Run: python src/07_router.py   then run this again.")
        return 1
    with open(oof_path, newline="", encoding="utf-8") as f:
        oof = {r["bill_id"]: r for r in csv.DictReader(f)}
    if not oof or "sonnet_nested" not in next(iter(oof.values())):
        print("Router results are from an older version. Run: python src/07_router.py   then run this again.")
        return 1
    p_router = {b: float(r["p_router"]) for b, r in oof.items()}
    nested = {b: r["sonnet_nested"] == "1" for b, r in oof.items()}
    same_pred = {b: r["sonnet_same_predictions"] == "1" for b, r in oof.items()}
    choice = json.loads(choice_path.read_text(encoding="utf-8"))
    threshold = "nested " + ",".join(str(t) for t in choice["nested"]["fold_thresholds"])
    runs = {r: K.load_run(root, r, labels) for r in K.CLAUDE_RUNS + K.FREE_RUNS}
    split = {b: r.get("split") for b, r in K.load_split(root, labels).items()}
    cfg = K.config(root)

    readers = {}
    for name, decide, escalate in (("cascade", nested, False), ("cascade_escalate", nested, True),
                                   ("cascade_same_pred", same_pred, False)):
        readers[name] = {l["bill_id"]: route_bill(runs["pdf_text"][l["bill_id"]], runs["ocr_rapid"][l["bill_id"]],
                                                  runs["haiku_v2"][l["bill_id"]], runs["sonnet_v2"][l["bill_id"]],
                                                  decide.get(l["bill_id"], False), escalate)
                         for l in labels}
    for run, calls in (("haiku_v2", True), ("sonnet_v2", True), ("pdf_text", False), ("ocr_rapid", False)):
        readers[run] = {b: {"route": run, "fields": rec["fields"], "calls": [rec] if calls else [], "ocr_agree": None}
                        for b, rec in runs[run].items()}

    rows = []
    for name in ("pdf_text", "ocr_rapid", "haiku_v2", "sonnet_v2", "cascade", "cascade_escalate", "cascade_same_pred"):
        for scope, keep in SCOPES.items():
            row = summarise(name, scope, [l for l in labels if keep(l, split.get(l["bill_id"]))], readers[name], cfg)
            if row:
                rows.append(row)
    evalkit.write_csv(out_dir / "cascade_table.csv", rows)
    evalkit.write_csv(out_dir / "cascade_routes.csv", [
        {"bill_id": l["bill_id"], "bill_type": l["bill_type"], "split": split.get(l["bill_id"]),
         "route": readers["cascade"][l["bill_id"]]["route"], "p_router": p_router.get(l["bill_id"]),
         "ocr_agree": readers["cascade"][l["bill_id"]]["ocr_agree"]} for l in labels])
    cache = root / "data" / "eval" / "runs" / "cascade"
    cache.mkdir(parents=True, exist_ok=True)
    for l in labels:
        r = readers["cascade"][l["bill_id"]]
        (cache / f"{l['bill_id']}.json").write_text(json.dumps({
            "bill_id": l["bill_id"], "model": "cascade", "prompt": "-", "fields": r["fields"], "raw_text": "",
            "error": None, "input_tokens": sum(c.get("input_tokens", 0) for c in r["calls"]),
            "output_tokens": sum(c.get("output_tokens", 0) for c in r["calls"]),
            "latency_ms": sum(c.get("latency_ms", 0) for c in r["calls"]), "route": r["route"],
            "calls": [c.get("model") for c in r["calls"]]}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"CASCADE (router threshold {threshold}; share of bills). Quote 'cascade' (nested); "
          "'cascade_same_pred' is the optimistic comparison.")
    print(f"{'reader':<19}{'scope':<13}{'bills':>6}{'all fields':>11}{'total Rs':>9}{'silent err':>11}"
          f"{'free':>6}{'Haiku':>7}{'Sonnet':>8}{'Rs/bill':>9}{'H=OCR':>7}")
    for r in rows:
        cost = "-" if r["cost_per_bill_inr"] is None else f"{r['cost_per_bill_inr']:.2f}"
        ag = "-" if r["haiku_vs_ocr_agree"] is None else f"{r['haiku_vs_ocr_agree']:.0%}"
        print(f"{r['reader']:<19}{r['scope']:<13}{r['bills']:>6}{r['all_fields_right']:>11.0%}{r['total_amount_right']:>9.0%}"
              f"{r['silent_error_rate']:>11.0%}{r['pct_free']:>6.0%}{r['pct_sent_to_haiku']:>7.0%}"
              f"{r['pct_sent_to_sonnet']:>8.0%}{cost:>9}{ag:>7}")
    if any(r["cost_per_bill_inr"] is None for r in rows):
        print("\nCost is empty: fill the prices and usd_to_inr in src/eval_config.json (Anthropic pricing page).")
    print(f"\nSaved: {out_dir / 'cascade_table.csv'} and cascade_routes.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
