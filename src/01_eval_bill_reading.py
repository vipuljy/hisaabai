"""Step 1: how well does the bot read bills?

Runs every model x prompt combination in src/eval_config.json over the hand-labelled
bills in data/eval/, scores each field, and writes results to outputs/eval/.

Inputs (never committed):
  data/eval/labels.csv   one row per bill, the correct answers (template: src/templates/eval_labels.csv)
  data/eval/images/      the bill photos, file names as in labels.csv

Usage (from the project root):
  python src/01_eval_bill_reading.py --check        validate labels + images, no API calls
  python src/01_eval_bill_reading.py --limit 5      try 5 bills per run first
  python src/01_eval_bill_reading.py                full run (cached: re-running costs nothing)
  python src/01_eval_bill_reading.py --runs haiku_v2
A run may set "effort" (low / medium / high / off): how much the model thinks first (bot/ai_client.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import evalkit  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # so ANTHROPIC_API_KEY in .env is used

EVAL_DIR = ROOT / "data" / "eval"
OUT_DIR = ROOT / "outputs" / "eval"


def main(argv=None, client=None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate labels and images only")
    ap.add_argument("--limit", type=int, help="only the first N bills")
    ap.add_argument("--runs", nargs="*", help="run names from eval_config.json (default: all)")
    args = ap.parse_args(argv)

    eval_dir, out_dir = root / "data" / "eval", root / "outputs" / "eval"
    config = json.loads((root / "src" / "eval_config.json").read_text(encoding="utf-8"))
    runs = [r for r in config["runs"] if not args.runs or r["name"] in args.runs]
    if not runs:
        print("No matching runs in src/eval_config.json")
        return 1

    labels_path = eval_dir / "labels.csv"
    if not labels_path.exists():
        print(f"Missing {labels_path}. Copy src/templates/eval_labels.csv there and fill it in.")
        return 1
    try:
        labels = evalkit.load_labels(labels_path)
    except ValueError as err:
        print(err)
        return 1
    images_dir = eval_dir / "images"
    missing = [l["image_file"] for l in labels if not (images_dir / l["image_file"]).exists()]
    if missing:
        print(f"{len(missing)} image(s) not found in {images_dir}: {missing[:5]}")
        return 1
    if args.limit:
        labels = labels[: args.limit]

    n_hand = sum(l["bill_type"] == "handwritten" for l in labels)
    print(f"Labels OK: {len(labels)} bills ({len(labels) - n_hand} printed, {n_hand} handwritten).")
    print(f"Runs: {', '.join(r['name'] for r in runs)}  ->  up to {len(labels) * len(runs)} model calls (cached ones are free).")
    if args.check:
        return 0

    scored, records = [], []
    for run in runs:
        cache_dir = eval_dir / "runs" / run["name"]
        for i, label in enumerate(labels, start=1):
            rec = evalkit.run_one(label, images_dir, cache_dir, run["model"], run["prompt"], client=client,
                                  effort=run.get("effort"))
            records.append(rec | {"run": run["name"]})
            for s in evalkit.score_bill(label, rec["fields"]):
                scored.append(s | {"run": run["name"], "bill_id": label["bill_id"], "bill_type": label["bill_type"]})
            print(f"  {run['name']}: {i}/{len(labels)}", end="\r")
        print()

    by_field, by_run = evalkit.summarise(scored, records, runs, config.get("prices", {}), config.get("usd_to_inr"))
    evalkit.write_csv(out_dir / "per_field_detail.csv", scored)
    evalkit.write_csv(out_dir / "summary_by_field.csv", by_field)
    evalkit.write_csv(out_dir / "summary_by_run.csv", by_run)

    print("\nSUMMARY (share of bills)")
    print(f"{'run':<14}{'all fields':>11}{'total Rs':>10}{'silent err':>12}{'ms':>7}{'Rs/bill':>9}"
          f"{'think tok':>11}{'text tok':>10}{'errors':>8}{'cut off':>9}")
    for r in by_run:
        cost = "-" if r["cost_per_bill_inr"] is None else f"{r['cost_per_bill_inr']:.2f}"
        think = "-" if r["avg_thinking_tokens"] is None else str(r["avg_thinking_tokens"])
        text = "-" if r["avg_text_tokens"] is None else str(r["avg_text_tokens"])
        print(f"{r['run']:<14}{r['all_fields_right']:>11.0%}{r['total_amount_right']:>10.0%}"
              f"{r['bills_with_silent_error']:>12.0%}{r['avg_latency_ms']:>7}{cost:>9}"
              f"{think:>11}{text:>10}{r['json_errors']:>8}{r['truncated']:>9}")
    print("Rs/bill includes thinking tokens (billed as output). Bills with an error are re-read on the next run.")
    print(f"\nDetail: {out_dir}")
    print("Tell Claude: \"eval done\" and paste the SUMMARY lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
