"""Step 6: how well do the FREE readers read bills? (no AI, no API key, no cost)

Readers:
  pdf_text   text layer of the original PDF (bot/read_pdf.py)
  ocr_rules  Tesseract OCR on the photo (bot/read_ocr.py)
  ocr_rapid  RapidOCR (PP-OCR via onnxruntime) on the photo, same rules (optional: requirements-ocr-extra.txt)
Both use the same rules (bot/field_rules.py) and checks (bot/checks.py), cache each bill in
data/eval/runs/<reader>/ in the same format as the Claude runs, and are scored with src/evalkit.py.
Bills a reader cannot read come back blank: "missed" (safe: the bot asks), never silent errors.
"silent err" = with all guards (OCR confidence, proof gate, supplier); "rules only" = rules + checks, no guards.
"total trusted" = total read and NOT marked unsure; "trusted wrong" = share of trusted totals that are wrong
(the number that matters for the cascade: a trusted free read skips Claude).
"proof gate" = total read AND the amount in words equals the digits: the same test for every engine (their
confidence scales differ), so it is the fair comparison between engines. "s/photo" = seconds per photo.

Usage (from the project root):
  python src/06_free_readers_eval.py                     both readers (OCR is cached: re-runs take seconds)
  python src/06_free_readers_eval.py --readers pdf_text  only the PDF reader
  python src/06_free_readers_eval.py --fresh             OCR every photo again
Output: outputs/eval/free_readers_summary.csv (one row per reader x scope)
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import evalkit  # noqa: E402
from bot import read_ocr, read_pdf  # noqa: E402

READERS = {
    "pdf_text": {"name": read_pdf.READER, "model": read_pdf.READER, "prompt": read_pdf.RULES_VERSION},
    "ocr_rules": {"name": read_ocr.READER, "model": read_ocr.READER, "prompt": read_ocr.RULES_VERSION},
    "ocr_rapid": {"name": "ocr_rapid", "model": "ocr_rapid", "prompt": read_ocr.RULES_VERSION},
    "ocr_rapid_clean": {"name": "ocr_rapid_clean", "model": "ocr_rapid_clean", "prompt": read_ocr.RULES_VERSION},
    "ocr_doctr": {"name": "ocr_doctr", "model": "ocr_doctr", "prompt": read_ocr.RULES_VERSION},
    "ocr_agree": {"name": "ocr_agree", "model": "ocr_agree", "prompt": read_ocr.RULES_VERSION},
}
OCR_ENGINE = {"ocr_rules": "tesseract", "ocr_rapid": "rapidocr", "ocr_rapid_clean": "rapidocr_clean",
              "ocr_doctr": "doctr"}
OCR_READERS = ["ocr_rules", "ocr_rapid", "ocr_rapid_clean", "ocr_doctr"]
DEFAULT_READERS = ["pdf_text", "ocr_rules", "ocr_rapid", "ocr_rapid_clean", "ocr_doctr"]  # missing engines are skipped
# ocr_agree = RapidOCR (default engine) and docTR read the same total; built when both runs exist
SPLIT = {}  # bill_id -> dev / test (data/eval/split.csv), filled by main()
SCOPES = {
    "all": lambda label, rec: True,
    "printed": lambda label, rec: label["bill_type"] == "printed",
    "printed_dev": lambda label, rec: label["bill_type"] == "printed" and SPLIT.get(label["bill_id"]) == "dev",
    "printed_TEST": lambda label, rec: label["bill_type"] == "printed" and SPLIT.get(label["bill_id"]) == "test",
    "handwritten": lambda label, rec: label["bill_type"] == "handwritten",
    "text_pdf_only": lambda label, rec: rec["model"] == read_pdf.READER and rec["error"] is None,
}


SPLIT_SEED = 20261026


def make_split(labels: list[dict], source_map: dict, seed: int = SPLIT_SEED) -> list[dict]:
    """DEV / TEST halves, separately for printed and handwritten. Bills that share a supplier (hand label) or an
    original file are one group and always land in the same half, so near-copies never cross over.
    Groups are shuffled with a fixed seed, then the biggest go first into whichever half is smaller."""
    import random

    from bot.extract import norm_supplier
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for l in labels:
        b = "bill:" + l["bill_id"]
        find(b)
        sup = norm_supplier(l.get("supplier_name"))
        src = (source_map.get(l["bill_id"]) or {}).get("file")
        for key in ([f"sup:{sup}"] if sup else []) + ([f"file:{src}"] if src else []):
            parent[find(b)] = find(key)
    rows = []
    rng = random.Random(seed)
    for bill_type in ("printed", "handwritten"):
        groups: dict[str, list] = {}
        for l in labels:
            if l["bill_type"] == bill_type:
                groups.setdefault(find("bill:" + l["bill_id"]), []).append(l["bill_id"])
        keys = sorted(groups)
        rng.shuffle(keys)
        keys.sort(key=lambda k: -len(groups[k]))  # stable: equal sizes keep the shuffled order
        size = {"dev": 0, "test": 0}
        for k in keys:
            half = "dev" if size["dev"] <= size["test"] else "test"
            size[half] += len(groups[k])
            rows += [{"bill_id": b, "bill_type": bill_type, "group": k.split(":", 1)[1], "split": half}
                     for b in groups[k]]
    return sorted(rows, key=lambda r: r["bill_id"])


def load_or_make_split(root: Path, labels: list[dict]) -> dict:
    """data/eval/split.csv is written once and then only read (never overwritten), so TEST stays TEST."""
    import csv

    path = root / "data" / "eval" / "split.csv"
    if not path.exists():
        source_map = read_pdf.load_source_map(root / "data" / "eval" / "source_map.csv")
        evalkit.write_csv(path, make_split(labels, source_map))
        print(f"Wrote {path} (seed {SPLIT_SEED})")
    with open(path, newline="", encoding="utf-8") as f:
        return {r["bill_id"]: r["split"] for r in csv.DictReader(f)}


def run_pdf(labels, root, today):
    eval_dir = root / "data" / "eval"
    source_map = read_pdf.load_source_map(eval_dir / "source_map.csv")
    inbox, cache = root / "data" / "raw" / "bills_inbox" / "done", eval_dir / "runs" / read_pdf.READER
    return {l["bill_id"]: read_pdf.read_bill(l["bill_id"], source_map, inbox, cache, today=today) for l in labels}


def run_ocr(labels, root, today, fresh=False, workers=2, reader="ocr_rules"):
    eval_dir = root / "data" / "eval"
    engine = OCR_ENGINE[reader]
    cache = eval_dir / "runs" / reader
    done = []

    def one(l):
        rec = read_ocr.read_bill(l["bill_id"], eval_dir / "images" / l["image_file"], cache, fresh=fresh,
                                 today=today, engine=engine)
        done.append(1)
        print(f"  {reader}: {len(done)}/{len(labels)}", end="\r")
        return l["bill_id"], rec

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        recs = dict(pool.map(one, labels))
    print()
    if any(r["error"] in ("no_tesseract", "no_engine") for r in recs.values()):
        how = {"tesseract": "install Tesseract (see RUN_STEP6_free_readers.bat)",
               "doctr": "pip install -r requirements-ocr-doctr.txt"}.get(engine, "pip install -r requirements-ocr-extra.txt")
        print(f"{engine} not available: skipping {reader}. To include it: {how}, then re-run.")
        return None
    return recs


def _silent_bills(labels, recs, guard: bool) -> int:
    n = 0
    for l in labels:
        rec = recs[l["bill_id"]]
        fields = rec["fields"] if guard else rec["fields"] | {"unsure_fields": rec.get("unsure_rules_only", rec["fields"]["unsure_fields"])}
        n += any(s["silent_error"] for s in evalkit.score_bill(l, fields))
    return n


def _total(label: dict, rec: dict) -> dict:
    return next(s for s in evalkit.score_bill(label, rec["fields"]) if s["field"] == "total_amount")


def with_memory(labels: list[dict], recs: dict, today=None) -> dict:
    """Supplier memory, leave-one-out: each bill may use templates learned from OTHER confirmed bills of the same
    supplier in the SAME split half (seller GSTIN, else name), never from itself. Re-analyses cached word boxes only."""
    from bot import templates

    mem = {half: templates.SupplierMemory() for half in ("dev", "test", None)}
    for l in labels:
        rec = recs[l["bill_id"]]
        if rec.get("error") or not rec.get("raw_text"):
            continue
        keys = templates.keys(rec.get("supplier_gstin"), l.get("supplier_name"))
        mem[SPLIT.get(l["bill_id"])].add(keys, l["bill_id"], templates.learn(rec["raw_text"], l.get("total_amount")))
    out = {}
    for l in labels:
        rec = recs[l["bill_id"]]
        words = (rec.get("ocr") or {}).get("words")
        if words is None:
            out[l["bill_id"]] = rec
            continue
        keys = templates.keys(rec.get("supplier_gstin"), rec["fields"].get("supplier_name"))
        tpls = mem[SPLIT.get(l["bill_id"])].lookup(keys, exclude_bill=l["bill_id"])
        a = read_ocr.analyse(words, today=today, memory=tpls)
        out[l["bill_id"]] = rec | {"fields": a["fields"], "checks": a["checks"], "total_proof": a["total_proof"],
                                   "total_source": a["total_source"], "model": rec["model"] + "_mem"}
    return out


def agree(labels: list[dict], a: dict, b: dict) -> dict:
    """'Both engines agree' as a proof: start from engine A's reading; if A's total is not trusted but A and B read
    the SAME total (and it passes the sanity + balance checks on A's text), trust it with proof 'agree'."""
    from bot import checks

    out = {}
    for l in labels:
        ra, rb = a[l["bill_id"]], b[l["bill_id"]]
        fa, fb = ra["fields"], rb["fields"]
        rec = ra | {"model": "ocr_agree", "fields": dict(fa), "latency_ms": ra["latency_ms"] + rb["latency_ms"]}
        t = fa.get("total_amount")
        if (t is not None and "total_amount" in fa["unsure_fields"] and t == fb.get("total_amount")
                and checks.total_is_safe(ra.get("raw_text") or "", t)):
            rec["fields"]["unsure_fields"] = [f for f in fa["unsure_fields"] if f != "total_amount"]
            rec["total_proof"], rec["total_source"] = "agree", "agree"
        out[l["bill_id"]] = rec
    return out


def trusted_wrong(labels: list[dict], recs: dict, bill_type: str = "printed") -> list[dict]:
    """Bills whose total the reader TRUSTED (read, not unsure) but got wrong: these would skip Claude wrongly."""
    out = []
    for l in labels:
        s = _total(l, recs[l["bill_id"]])
        if l["bill_type"] == bill_type and s["pred"] is not None and not s["flagged_unsure"] and s["outcome"] != "correct":
            out.append({"bill_id": l["bill_id"], "read": s["pred"], "truth": s["truth"], "split": SPLIT.get(l["bill_id"]),
                        "proof": recs[l["bill_id"]].get("total_proof"), "source": recs[l["bill_id"]].get("total_source")})
    return out


def score_scope(reader: str, scope: str, labels: list[dict], recs: dict) -> tuple[dict | None, list[dict]]:
    keep = [l for l in labels if SCOPES[scope](l, recs[l["bill_id"]])]
    if not keep:
        return None, []
    run = READERS[reader]
    scored, records = [], []
    for l in keep:
        rec = recs[l["bill_id"]]
        records.append(rec | {"run": run["name"]})
        for s in evalkit.score_bill(l, rec["fields"]):
            scored.append(s | {"run": run["name"], "bill_id": l["bill_id"], "bill_type": l["bill_type"]})
    by_field, by_run = evalkit.summarise(scored, records, [run], prices={}, usd_to_inr=None)
    r = by_run[0]
    totals = {l["bill_id"]: _total(l, recs[l["bill_id"]]) for l in keep}
    trusted = [s for s in totals.values() if s["pred"] is not None and not s["flagged_unsure"]]
    proof = [s for b, s in totals.items()
             if s["pred"] is not None and (recs[b].get("checks") or {}).get("words_match_total") == "pass"]
    by_proof = {"words": 0, "maths": 0, "memory": 0, "agree": 0, "recovered": 0}
    for b, s in totals.items():
        if s["pred"] is not None and not s["flagged_unsure"]:
            src = recs[b].get("total_source")
            kind = "memory" if src == "memory" else "agree" if src == "agree" else recs[b].get("total_proof")
            if kind in by_proof:
                by_proof[kind] += 1
            if src == "recovered":
                by_proof["recovered"] += 1
    confs = [rec["ocr"]["page_conf"] for rec in records if rec.get("ocr") and rec["ocr"].get("page_conf") is not None]
    row = {
        "reader": reader, "scope": scope, "bills": r["bills"],
        "text_found": evalkit._rate(sum(rec["error"] is None for rec in records), len(records)),
        "all_fields_right": r["all_fields_right"], "total_amount_right": r["total_amount_right"],
        "bills_with_silent_error": r["bills_with_silent_error"],
        "silent_error_rules_only": evalkit._rate(_silent_bills(keep, recs, guard=False), len(keep)),
        "trusted_n": len(trusted),
        "trusted_by_words": by_proof["words"], "trusted_by_maths": by_proof["maths"],
        "trusted_by_memory": by_proof["memory"], "trusted_by_agree": by_proof["agree"],
        "trusted_recovered": by_proof["recovered"],
        "proof_gate": evalkit._rate(len(proof), len(keep)),
        "proof_gate_wrong_n": sum(s["outcome"] != "correct" for s in proof),
        "total_trusted": evalkit._rate(len(trusted), len(keep)),
        "trusted_total_wrong": evalkit._rate(sum(s["outcome"] != "correct" for s in trusted), len(trusted)),
        "trusted_total_wrong_n": sum(s["outcome"] != "correct" for s in trusted),
        "supplier_gstin_found": evalkit._rate(sum(bool(recs[l["bill_id"]].get("supplier_gstin")) for l in keep), len(keep)),
        "avg_ocr_conf": round(sum(confs) / len(confs), 1) if confs else None,
        "avg_latency_ms": r["avg_latency_ms"], "sec_per_photo": round(r["avg_latency_ms"] / 1000, 1),
        "cost_per_bill_inr": 0.0,
    }
    return row, [f for f in by_field if f["bill_type"] == "all"]


def wrong_totals(labels: list[dict], recs: dict, bill_type: str = "printed", limit: int = 5) -> list[dict]:
    """Bills where the reader read a total but not the right one, with the OCR line it came from."""
    out = []
    for l in labels:
        if l["bill_type"] != bill_type:
            continue
        rec = recs[l["bill_id"]]
        s = next(x for x in evalkit.score_bill(l, rec["fields"]) if x["field"] == "total_amount")
        if s["outcome"] != "wrong":
            continue
        loc = read_ocr.field_rules.locate_total(rec["raw_text"])
        lines = rec["raw_text"].splitlines()
        line = " | ".join(lines[i] for i in sorted({loc["label_line"], loc["number_line"]})) if loc else ""
        out.append({"bill_id": l["bill_id"], "read": s["pred"], "truth": s["truth"],
                    "flagged": s["flagged_unsure"], "label": loc["label"] if loc else "", "line": line[:90]})
    return out[:limit]


def main(argv=None, root: Path = ROOT, today=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readers", nargs="*", default=DEFAULT_READERS, choices=list(READERS))
    ap.add_argument("--only", choices=["printed", "printed_dev", "printed_TEST", "handwritten"],
                    help="read and score only these bills (for slow experiments)")
    ap.add_argument("--fresh", action="store_true", help="OCR every photo again (ignore the cache)")
    ap.add_argument("--workers", type=int, default=2, help="photos OCRed in parallel")
    args = ap.parse_args(argv)

    labels = evalkit.load_labels(root / "data" / "eval" / "labels.csv")
    SPLIT.clear()
    SPLIT.update(load_or_make_split(root, labels))
    if args.only:
        labels = [l for l in labels if SCOPES[args.only](l, {})]
    all_recs = {}
    if "pdf_text" in args.readers:
        all_recs["pdf_text"] = run_pdf(labels, root, today)
    for reader in OCR_READERS:
        if reader in args.readers:
            recs = run_ocr(labels, root, today, fresh=args.fresh, workers=args.workers, reader=reader)
            if recs:
                all_recs[reader] = recs
                READERS.setdefault(reader + "_mem", {"name": reader + "_mem", "model": reader + "_mem",
                                                     "prompt": read_ocr.RULES_VERSION})
                all_recs[reader + "_mem"] = with_memory(labels, recs, today)
    if "ocr_rapid" in all_recs and "ocr_doctr" in all_recs:
        all_recs["ocr_agree"] = agree(labels, all_recs["ocr_rapid"], all_recs["ocr_doctr"])

    rows, field_tables = [], {}
    for reader, recs in all_recs.items():
        for scope in (s for s in SCOPES if reader == "pdf_text" or s != "text_pdf_only"):
            row, fields = score_scope(reader, scope, labels, recs)
            if row:
                rows.append(row)
            if fields and scope == ("text_pdf_only" if reader == "pdf_text" else "all"):
                field_tables[f"{reader} ({scope})"] = fields
    out_dir = root / "outputs" / "eval"
    evalkit.write_csv(out_dir / "free_readers_summary.csv", rows)

    print("\nFREE READERS (share of bills)")
    print(f"{'reader':<15}{'scope':<14}{'bills':>6}{'total Rs':>9}{'trusted':>13}{'trust wrong':>12}"
          f"{'words/maths/mem/agree':>23}{'recov':>6}{'silent err':>11}{'s/photo':>8}")
    for r in rows:
        tw = "-" if r["trusted_total_wrong"] is None else f"{r['trusted_total_wrong_n']}"
        by = f"{r['trusted_by_words']}/{r['trusted_by_maths']}/{r['trusted_by_memory']}/{r['trusted_by_agree']}"
        tr = f"{r['total_trusted']:.0%} ({r['trusted_n']})"
        print(f"{r['reader']:<15}{r['scope']:<14}{r['bills']:>6}{r['total_amount_right']:>9.0%}{tr:>13}{tw:>12}"
              f"{by:>23}{r['trusted_recovered']:>6}{r['bills_with_silent_error']:>11.0%}{r['sec_per_photo']:>8.1f}")
    for title, fields in field_tables.items():
        print(f"\nPer field: {title}")
        print(f"{'field':<16}{'accuracy':>10}{'missed':>8}{'silent err':>12}")
        for f in fields:
            print(f"{f['field']:<16}{f['accuracy']:>10.0%}{f['missed_rate']:>8.0%}{f['silent_error_rate']:>12.0%}")
    for reader, recs in all_recs.items():
        tw = trusted_wrong(labels, recs) + trusted_wrong(labels, recs, "handwritten")
        print(f"\nBills where {reader} TRUSTED a wrong total: {len(tw)}")
        for w in tw:
            print(f"  {w['bill_id']} ({w['split']})  read {w['read']}  truth {w['truth']}  proof={w['proof']} via {w['source']}")
    for reader in OCR_READERS:
        if reader not in all_recs:
            continue
        wrong = wrong_totals(labels, all_recs[reader])
        print(f"\nPrinted bills where {reader} read the total WRONG (first 5)")
        for w in wrong:
            print(f"  {w['bill_id']}  read {w['read']}  truth {w['truth']}  flagged={w['flagged']}  [{w['label']}] {w['line']}")
        if not wrong:
            print("  none")
    print(f"\nSaved: {out_dir / 'free_readers_summary.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
