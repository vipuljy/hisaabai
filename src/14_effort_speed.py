"""Step 14: can the AI read bills faster by thinking less, without reading worse? (21 Nov 2026)

Why: owners wait for a photo's card (20 Nov health report: median 13.5 s; "Time jyada lagta h"). In the eval, about
408 of the ~500 tokens Haiku writes per bill are thinking. Anthropic's effort setting asks the model to think less
(bot/ai_client.py). This runs the 136 labelled bills at lower effort and compares with the same prompt (v3) at the
default effort, already cached from 19 Nov (no new cost for the baseline).

The rule, written BEFORE the run (docs/08_eval.md, 21 Nov): a lower effort is adopted only if, against its baseline,
  1. bills with ALL fields right drop by at most 2 points,
  2. bills with a SILENT error (a confident wrong value) do not go up, and
  3. replies that failed (bad JSON, cut off) do not go up.
Among the levels that pass, the fastest is recommended. Speed is the model's own time per bill (latency_ms); the
baseline's time was measured on 19 Nov, so thinking tokens per bill (fewer = faster, whatever the network) are
printed beside it as the fair comparison.

Usage (from the project root):
  python src/14_effort_speed.py --model haiku     Haiku low and off vs Haiku default  (about Rs 10-15)
  python src/14_effort_speed.py --model sonnet    Sonnet medium vs Sonnet default     (about Rs 90)
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SETS = {
    "haiku": ("haiku_v3", ["haiku_v3_low", "haiku_v3_off"], "BILL_EFFORT"),
    "sonnet": ("sonnet_v3", ["sonnet_v3_medium"], "ESCALATION_EFFORT"),
}
MAX_DROP = 0.02   # all-fields-right may drop by at most 2 points


def verdict(base: dict, cand: dict) -> tuple[bool, list[str]]:
    """(passes, reasons it fails). Rows are summary_by_run.csv rows (rates as floats, counts as ints)."""
    why = []
    if cand["all_fields_right"] < base["all_fields_right"] - MAX_DROP - 1e-9:
        why.append(f"all fields right {cand['all_fields_right']:.0%} vs {base['all_fields_right']:.0%} (drop > 2 points)")
    if cand["bills_with_silent_error"] > base["bills_with_silent_error"] + 1e-9:
        why.append(f"silent errors {cand['bills_with_silent_error']:.1%} vs {base['bills_with_silent_error']:.1%} (went up)")
    if cand["json_errors"] > base["json_errors"]:
        why.append(f"failed replies {cand['json_errors']} vs {base['json_errors']} (went up)")
    return not why, why


def read_summary(path: Path) -> dict[str, dict]:
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in ("all_fields_right", "total_amount_right", "bills_with_silent_error"):
                r[k] = float(r[k]) if r[k] not in ("", "None") else 0.0
            for k in ("json_errors", "truncated", "avg_latency_ms", "median_latency_ms", "bills", "avg_thinking_tokens"):
                r[k] = int(float(r[k])) if r.get(k) not in (None, "", "None") else 0
            rows[r["run"]] = r
    return rows


def report(rows: dict[str, dict], base_name: str, cands: list[str], env_key: str, out=print) -> str | None:
    """Prints the comparison and the recommendation; returns the effort to set (or None = keep the default)."""
    base = rows[base_name]
    out(f"\nSPEED TEST vs {base_name} (default effort): all fields {base['all_fields_right']:.0%}, silent errors "
        f"{base['bills_with_silent_error']:.1%}, median {base['median_latency_ms'] / 1000:.1f} s per bill, "
        f"thinking {base['avg_thinking_tokens']} tokens")
    best = None
    for name in cands:
        c = rows.get(name)
        if c is None:
            continue
        ok, why = verdict(base, c)
        faster = (base["median_latency_ms"] - c["median_latency_ms"]) / 1000
        out(f"SPEED TEST {name}: all fields {c['all_fields_right']:.0%}, silent errors "
            f"{c['bills_with_silent_error']:.1%}, median {c['median_latency_ms'] / 1000:.1f} s "
            f"({faster:+.1f} s), thinking {c['avg_thinking_tokens']} tokens -> {'PASS' if ok else 'FAIL: ' + '; '.join(why)}")
        if ok and faster > 0 and (best is None or c["median_latency_ms"] < rows[best]["median_latency_ms"]):
            best = name
    if best is None:
        out(f"SPEED TEST result: keep the default ({env_key} stays empty)")
        return None
    effort = rows[best]["effort"]
    out(f"SPEED TEST result: set {env_key}={effort} on Render (Environment), then a phone test with 3 bills")
    return effort


def main(argv=None, root: Path = ROOT, client=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(SETS), default="haiku")
    ap.add_argument("--limit", type=int, help="only the first N bills (a trial)")
    args = ap.parse_args(argv)
    base, cands, env_key = SETS[args.model]

    spec = importlib.util.spec_from_file_location("eval01", root / "src" / "01_eval_bill_reading.py")
    eval01 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval01)
    run_args = ["--runs", base, *cands] + (["--limit", str(args.limit)] if args.limit else [])
    code = eval01.main(run_args, client=client, root=root)
    if code != 0:
        return code
    report(read_summary(root / "outputs" / "eval" / "summary_by_run.csv"), base, cands, env_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
