"""Step 9: "verify, then escalate" read policies, simulated from CACHED readings (no API calls, no cost).

Haiku reads every bill that is not a text PDF; free checks on Haiku's answer (bot/policy.py) decide whether
Sonnet re-reads it. Policies:
  P0  rule router: PDF free, printed -> Haiku, handwritten -> Sonnet (uses the HAND LABEL: a benchmark only,
      the live bot does not know printed vs handwritten)
  P1  Haiku + checks a, c, d -> Sonnet when one fails
  P2  Haiku + checks a, b, c, d -> Sonnet when one fails
  P3  P2 + Sonnet and Haiku still disagree on total or date -> ask the owner (flagged, not silent)
  P4  learned escalation: a depth-3 tree / logistic regression on the check results + the free photo features,
      nested grouped CV (threshold picked inside each training fold), + the P3 ask-owner rule
  P5  "money first": Sonnet ONLY when a check on the total fails; doubts about the date / invoice no. / due date
      are asked to the owner (flagged), plus the date-age rule (bill date > 6 months before the photo arrived)
  P5_no_date_age  P5 without the date-age rule (shows how many asks that rule adds on old archive photos)
  P5_no_ocr       P5 without the free OCR (checks a and b off): a small free host where RapidOCR is not installed
  P5_trusted_ocr  P5, but check a only when the OCR total is trusted (words = digits / maths proof); b stays
  P5e P5 + check e: invoice no. shown vs the free OCR's invoice no. (when OCR read a sane one) -> warning on it
Compared with Haiku-only, Sonnet-only and the step-08 cascade. Recommended = the cheapest deployable policy
whose silent errors (all bills) are <= Sonnet-only's.
"Asked owner" = the owner sees at least one field with a warning (the final unsure_fields), split into
asked about the TOTAL / about the date only (bill or due date) / about something else.
'today' (--today) stands for the day the photo was received (the date-age rule and the future-date check).

Usage: python src/09_policies_eval.py --today 2026-10-28   Outputs: outputs/eval/policy_table.csv, policy_routes.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import cascadekit as K  # noqa: E402
import evalkit  # noqa: E402
from bot import policy as P  # noqa: E402
from bot import router  # noqa: E402

SCOPES = {
    "all": lambda l, sp: True,
    "printed": lambda l, sp: l["bill_type"] == "printed",
    "printed_TEST": lambda l, sp: l["bill_type"] == "printed" and sp == "test",
    "handwritten": lambda l, sp: l["bill_type"] == "handwritten",
}
CHECK_FEATURES = ["ocr_has_total", "ocr_agree", "proof_pass", "proof_fail", "unsure_count", "unsure_total",
                  "unsure_date", "sanity_fail", "h_total_missing", "h_date_missing", "h_thinking_tokens", "h_output_tokens"]
THRESHOLDS = [round(i * 0.05, 2) for i in range(21)] + [1.01]
# eval-only variant (not a READ_POLICY the bot accepts): P5 without the date-age rule
P.POLICIES.setdefault("P5_no_date_age", P.POLICIES["P5"] - {"date_age"})
POLICY_NAMES = ("P0", "P1", "P2", "P3", "P4", "P5", "P5_no_date_age", "P5_no_ocr", "P5_trusted_ocr", "P5e")
TRUSTED_OCR_MAX_COST = 0.30  # decision rule set before the run (29 Oct): see main()
DEPLOYABLE = ("P1", "P2", "P3", "P4", "P5", "P5e")
DATE_FIELDS = {"bill_date", "due_date"}
CLEARLY_CHEAPER = 0.10  # P5 replaces P3 only if it is >= 10% cheaper per bill AND has no more silent errors


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def upper95(k: int, n: int) -> str:
    """One-sided 95% upper bound on a rate (Clopper-Pearson); for 0 of n the rule of three: < 3/n."""
    if n == 0:
        return "-"
    if k == 0:
        return f"< {3 / n:.1%} (3/{n})"
    from scipy.stats import beta
    return f"{beta.ppf(0.95, k + 1, n - k):.1%}"


def is_pdf(pdf: dict) -> bool:
    return pdf.get("error") is None and (pdf.get("fields") or {}).get("total_amount") is not None


def result(fields, calls, asked=(), route="", reasons=()):
    return {"fields": fields, "calls": list(calls), "asked": list(asked), "route": route, "reasons": list(reasons)}


def run_policy(name: str, label, pdf, ocr, h, s, today, escalate=None) -> dict:
    """One bill under one policy. escalate: P4's learned decision (True / False)."""
    if is_pdf(pdf):
        return result(pdf["fields"], [], route="pdf_text")
    if name == "P0":
        return result(s["fields"], [s], route="sonnet") if label["bill_type"] == "handwritten" \
            else result(h["fields"], [h], route="haiku")
    if name in P.POLICIES and not P.POLICIES[name] & P.OCR_RULES:
        ocr = None  # the policy never runs the OCR (the live reader skips it too)
    c = P.haiku_checks(h["fields"], ocr, today)
    if name == "P4":
        why, pol = (["learned"] if escalate else []), "P3"
    else:
        why, pol = P.reasons_to_escalate(name, c), name
    if not why:  # no Sonnet; P5 may still ask the owner about the date / invoice no.
        fields, asked = P.apply_policy(pol, h["fields"], None, c, today)
        return result(fields, [h], asked, route="haiku_ask_owner" if asked else "haiku")
    fields, asked = P.apply_policy(pol, h["fields"], s["fields"], c, today)
    return result(fields, [h, s], asked, route="haiku_then_sonnet", reasons=why)


def ocr_check_audit(labels, runs, today) -> dict:
    """How often check a (any OCR total) and check a-trusted (proved OCR total only) would flag Haiku's total,
    and whether Haiku's total was really wrong (caught) or right (false alarm). Photos only (text PDFs skip Claude)."""
    out = {}
    checks = (("a: any OCR total", lambda c: c["ocr_agree"] is False),
              ("a_trusted: proved OCR only", lambda c: c["ocr_agree_trusted"] is False),
              ("b: proof on OCR text fails", lambda c: c["proof"] == "fail"))
    for key, fires_on in checks:
        fires = caught = only = false = wrong = trusted = 0
        for l in labels:
            b = l["bill_id"]
            if is_pdf(runs["pdf_text"][b]):
                continue
            h = runs["haiku_v2"][b]
            c = P.haiku_checks(h["fields"], runs["ocr_rapid"][b], today)
            ok = K.outcomes(l, h["fields"])["total_amount"]["outcome"] in ("correct", "correct_blank")
            wrong += not ok
            trusted += bool(c["ocr_trusted"])
            if fires_on(c):
                fires += 1
                caught += not ok
                false += ok
                # a catch no free check without OCR would make (Haiku unsure of the total / total sanity)
                only += not ok and not P.reasons_to_escalate("P5_no_ocr", c)
        out[key] = {"fires": fires, "caught": caught, "caught_only_by_this": only, "false": false,
                    "haiku_wrong": wrong, "ocr_trusted": trusted}
    return out


def check_features(h: dict, ocr: dict | None, today) -> dict:
    c = P.haiku_checks(h["fields"], ocr, today)
    f = h["fields"]
    unsure = set(c["unsure"])
    return {"ocr_has_total": float(c["ocr_total"] is not None),
            "ocr_agree": None if c["ocr_agree"] is None else float(c["ocr_agree"]),
            "proof_pass": float(c["proof"] == "pass"), "proof_fail": float(c["proof"] == "fail"),
            "unsure_count": float(len(unsure)), "unsure_total": float("total_amount" in unsure),
            "unsure_date": float("bill_date" in unsure), "sanity_fail": float(c["sanity"] == "fail"),
            "h_total_missing": float(f.get("total_amount") is None), "h_date_missing": float(f.get("bill_date") is None),
            "h_thinking_tokens": h.get("thinking_tokens"), "h_output_tokens": h.get("output_tokens")}


def scores(labels, results, cfg) -> dict:
    n = len(labels)
    right = total_right = silent = asked = free = haiku = sonnet = 0
    ask_any = ask_total = ask_date = ask_other = 0
    cost, priced = 0.0, True
    for l in labels:
        r = results[l["bill_id"]]
        o = K.outcomes(l, r["fields"])
        right += all(x["outcome"] in ("correct", "correct_blank") for x in o.values())
        total_right += o["total_amount"]["outcome"] in ("correct", "correct_blank")
        silent += any(x["silent_error"] for x in o.values())
        asked += bool(r["asked"])
        flagged = set(r["fields"].get("unsure_fields") or [])
        ask_any += bool(flagged)
        ask_total += "total_amount" in flagged
        ask_date += bool(flagged) and flagged <= DATE_FIELDS
        ask_other += bool(flagged) and "total_amount" not in flagged and not flagged <= DATE_FIELDS
        models = [c.get("model", "") for c in r["calls"]]
        free += not models
        haiku += any("haiku" in m for m in models)
        sonnet += any("sonnet" in m for m in models)
        for c in r["calls"]:
            v = K.call_cost_inr(c, cfg)
            priced &= v is not None
            cost += v or 0
    return {"bills": n, "total_right": round(total_right / n, 4), "all_fields_right": round(right / n, 4),
            "silent_n": silent, "silent_pct": round(silent / n, 4), "silent_upper95": upper95(silent, n),
            "asked_owner_pct": round(ask_any / n, 4), "ask_total_pct": round(ask_total / n, 4),
            "ask_date_only_pct": round(ask_date / n, 4), "ask_other_pct": round(ask_other / n, 4),
            "policy_ask_pct": round(asked / n, 4), "pct_free": round(free / n, 4), "pct_haiku": round(haiku / n, 4),
            "pct_sonnet": round(sonnet / n, 4), "cost_per_bill_inr": round(cost / n, 3) if priced else None}


def learned_escalation(labels, runs, feats, groups, today, cfg, r07) -> tuple[dict, dict]:
    """P4: P(Haiku reads total + date right) from check + photo features; escalate below a threshold that is
    picked INSIDE each training fold (cheapest with silent errors <= Sonnet-only on those training bills)."""
    import numpy as np

    cols = router.FEATURES + CHECK_FEATURES
    X = np.array([[np.nan if f.get(c) is None else float(f[c]) for c in cols] for f in feats])
    y = np.array([K.haiku_ok(l, runs["haiku_v2"][l["bill_id"]]) for l in labels])
    splits = r07.group_splits(X, y, groups)
    oof = r07.grouped_oof(X, y, groups, splits=splits)
    cv = {m: r07.model_scores(y, oof[m]) for m in oof}
    model = max(cv, key=lambda m: ((cv[m]["recall_haiku_wrong"] or 0), (cv[m]["auc"] or 0)))

    def sim(idx, p, t):
        res = {labels[i]["bill_id"]: run_policy("P4", labels[i], *(runs[r][labels[i]["bill_id"]] for r in
               ("pdf_text", "ocr_rapid", "haiku_v2", "sonnet_v2")), today, escalate=bool(p[k] < t))
               for k, i in enumerate(idx)}
        return scores([labels[i] for i in idx], res, cfg)

    def sonnet_only(idx):
        return scores([labels[i] for i in idx], {labels[i]["bill_id"]: result(runs["sonnet_v2"][labels[i]["bill_id"]]["fields"],
                      [runs["sonnet_v2"][labels[i]["bill_id"]]]) for i in idx}, cfg)["silent_pct"]

    decision, fold_t = {}, []
    for tr, te in splits:
        tr_groups = [groups[i] for i in tr]
        if len(set(y[tr])) < 2 or len(set(tr_groups)) < 2:
            t = 0.0 if y[tr].mean() >= 0.5 else 1.01
        else:
            inner = r07.grouped_oof(X[tr], y[tr], tr_groups, names=[model])[model]
            target = sonnet_only(tr)
            table = [(t, sim(tr, inner, t)) for t in THRESHOLDS]
            ok = [(t, s) for t, s in table if s["silent_pct"] <= target + 1e-9] or [table[-1]]
            t = min(ok, key=lambda ts: (ts[1]["cost_per_bill_inr"] or 0, ts[1]["silent_pct"]))[0]
        fold_t.append(t)
        for i in te:
            decision[labels[i]["bill_id"]] = bool(oof[model][i] < t)
    return decision, {"model": model, "fold_thresholds": fold_t, "cv": cv}


def main(argv=None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", help="date for the 'not in the future' check (default: today)")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()

    labels = evalkit.load_labels(root / "data" / "eval" / "labels.csv")
    missing = K.missing_runs(root, labels, K.CLAUDE_RUNS)
    if missing:
        print(K.NEED_CLAUDE.format(missing=", ".join(missing)))
        return 1
    missing = K.missing_runs(root, labels, K.FREE_RUNS)
    if missing:
        print(K.NEED_FREE.format(missing=", ".join(missing)))
        return 1
    runs = {r: K.load_run(root, r, labels) for r in K.CLAUDE_RUNS + K.FREE_RUNS}
    split_rows = K.load_split(root, labels)
    split = {b: r.get("split") for b, r in split_rows.items()}
    groups = [split_rows.get(l["bill_id"], {}).get("group") or l["bill_id"] for l in labels]
    cfg = K.config(root)
    r07 = _load("r07", "07_router.py")

    feats = []
    photo = r07.features(root, labels, runs["ocr_rapid"])
    for l, ph in zip(labels, photo):
        feats.append(ph | check_features(runs["haiku_v2"][l["bill_id"]], runs["ocr_rapid"][l["bill_id"]], today))
    p4, p4_info = learned_escalation(labels, runs, feats, groups, today, cfg, r07)

    readers = {}
    for name in POLICY_NAMES:
        readers[name] = {l["bill_id"]: run_policy(name, l, runs["pdf_text"][l["bill_id"]], runs["ocr_rapid"][l["bill_id"]],
                                                  runs["haiku_v2"][l["bill_id"]], runs["sonnet_v2"][l["bill_id"]], today,
                                                  escalate=p4.get(l["bill_id"]))
                         for l in labels}
    for run in ("haiku_v2", "sonnet_v2"):
        readers[run + "_only"] = {b: result(r["fields"], [r]) for b, r in runs[run].items()}
    oof_path = root / "outputs" / "eval" / "router_oof.csv"
    if oof_path.exists():  # the step-08 cascade (photo-feature router), for comparison
        c08 = _load("c08", "08_cascade_eval.py")
        with open(oof_path, newline="", encoding="utf-8") as f:
            nested = {r["bill_id"]: r.get("sonnet_nested") == "1" for r in csv.DictReader(f)}
        readers["cascade_08"] = {}
        for l in labels:
            b = l["bill_id"]
            r = c08.route_bill(runs["pdf_text"][b], runs["ocr_rapid"][b], runs["haiku_v2"][b], runs["sonnet_v2"][b],
                               nested.get(b, False))
            asked = ["total_amount"] if r["ocr_agree"] is False else []
            readers["cascade_08"][b] = result(r["fields"], r["calls"], asked, r["route"])

    rows = []
    for name, res in readers.items():
        for scope, keep in SCOPES.items():
            sub = [l for l in labels if keep(l, split.get(l["bill_id"]))]
            if sub:
                rows.append({"policy": name, "scope": scope} | scores(sub, res, cfg))
    out = root / "outputs" / "eval"
    evalkit.write_csv(out / "policy_table.csv", rows)
    evalkit.write_csv(out / "policy_routes.csv", [
        {"bill_id": l["bill_id"], "bill_type": l["bill_type"], "split": split.get(l["bill_id"])}
        | {f"{p}_route": readers[p][l["bill_id"]]["route"] for p in DEPLOYABLE}
        | {f"{p}_{k}": "+".join(readers[p][l["bill_id"]][k]) for p in ("P3", "P5", "P5e") for k in ("reasons", "asked")}
        | {f"{p}_flagged": "+".join(readers[p][l["bill_id"]]["fields"].get("unsure_fields") or []) for p in ("P5", "P5e")}
        for l in labels])

    review = []  # every silent error, for a human look at the photo (label question or model error?)
    for name in ("haiku_v2_only", "sonnet_v2_only", "cascade_08", "P3", "P4", "P5", "P5_no_ocr", "P5_trusted_ocr", "P5e"):
        for l in labels if name in readers else []:
            r = readers[name][l["bill_id"]]
            for field, x in K.outcomes(l, r["fields"]).items():
                if x["silent_error"]:
                    review.append({"run": name, "bill_id": l["bill_id"], "field": field, "truth": x.get("truth"),
                                   "model_read": x.get("pred"), "outcome": x["outcome"], "bill_type": l["bill_type"],
                                   "split": split.get(l["bill_id"]), "route": r["route"],
                                   "image_file": l["image_file"], "label_notes": l.get("notes", "")})
    evalkit.write_csv(out / "silent_errors_review.csv", review or [{"run": "none"}])
    sonnet_all = next(r for r in rows if r["policy"] == "sonnet_v2_only" and r["scope"] == "all")
    deployable = [r for r in rows if r["scope"] == "all" and r["policy"] in DEPLOYABLE
                  and r["silent_n"] <= sonnet_all["silent_n"]]
    best = min(deployable, key=lambda r: r["cost_per_bill_inr"] or 0) if deployable else None
    row = {(r["policy"], r["scope"]): r for r in rows}
    p3, p5 = row[("P3", "all")], row[("P5", "all")]
    p5_saving = 1 - (p5["cost_per_bill_inr"] or 0) / (p3["cost_per_bill_inr"] or 1)
    p5_vs_p3 = {"p5_as_safe": p5["silent_n"] <= p3["silent_n"], "p5_cheaper_by": round(p5_saving, 4),
                "clearly_cheaper_rule": f">= {CLEARLY_CHEAPER:.0%}",
                "p5_replaces_p3": p5["silent_n"] <= p3["silent_n"] and p5_saving >= CLEARLY_CHEAPER}
    (out / "policy_choice.json").write_text(json.dumps({
        "recommended": best["policy"] if best else None, "today": today.isoformat(),
        "rule": "cheapest of P1-P5 with silent errors (all bills) <= Sonnet-only; P0 uses the hand label",
        "P5_vs_P3": p5_vs_p3,
        "sonnet_only": sonnet_all, "recommended_row": best, "P4": {k: v for k, v in p4_info.items() if k != "cv"},
        "P4_cv": p4_info["cv"]}, indent=1, default=str), encoding="utf-8")

    print(f"READ POLICIES (cached readings; 'today' = {today}; Rs/bill includes thinking tokens)")
    print(f"{'policy':<16}{'scope':<13}{'n':>4}{'total':>7}{'all f.':>7}{'silent':>9}{'95% upper':>17}{'ask':>6}"
          f"{'ask Rs':>7}{'ask dt':>7}{'free':>6}{'Haiku':>7}{'Sonnet':>8}{'Rs/bill':>9}")
    for r in rows:
        cost = "-" if r["cost_per_bill_inr"] is None else f"{r['cost_per_bill_inr']:.3f}"
        sil = f"{r['silent_n']} ({r['silent_pct']:.1%})"
        print(f"{r['policy']:<16}{r['scope']:<13}{r['bills']:>4}{r['total_right']:>7.0%}{r['all_fields_right']:>7.0%}"
              f"{sil:>9}{r['silent_upper95']:>17}{r['asked_owner_pct']:>6.0%}{r['ask_total_pct']:>7.0%}"
              f"{r['ask_date_only_pct']:>7.0%}{r['pct_free']:>6.0%}{r['pct_haiku']:>7.0%}{r['pct_sonnet']:>8.0%}{cost:>9}")
    print("\nP3 / P4 / P5 / P5e (ask = owner sees a warning; ask Rs = about the total; ask dt = about the date only)")
    for scope in SCOPES:
        for name in ("P3", "P4", "P5", "P5_no_date_age", "P5_no_ocr", "P5_trusted_ocr", "P5e"):
            r = row.get((name, scope))
            if r:
                save = 1 - (r["cost_per_bill_inr"] or 0) / row[("sonnet_v2_only", scope)]["cost_per_bill_inr"]
                print(f"  {scope:<13}{name:<16}silent {r['silent_n']:>2} (95% up {r['silent_upper95']:>16})  "
                      f"ask {r['asked_owner_pct']:>4.0%} = total {r['ask_total_pct']:>4.0%} + date {r['ask_date_only_pct']:>4.0%}"
                      f" + other {r['ask_other_pct']:>4.0%}  Sonnet {r['pct_sonnet']:>4.0%}  "
                      f"Rs {r['cost_per_bill_inr']:.3f}/bill  {save:>4.0%} cheaper than Sonnet-only")
    verdict = "P5 REPLACES P3" if p5_vs_p3["p5_replaces_p3"] else "keep P3"
    print(f"P5 vs P3: as safe = {p5_vs_p3['p5_as_safe']}, cheaper by {p5_saving:.0%} (rule >= {CLEARLY_CHEAPER:.0%}) -> {verdict}")
    audit = ocr_check_audit(labels, runs, today)
    tr, nt = row[("P5_trusted_ocr", "all")], row[("P5_no_ocr", "all")]
    trusted_wins = tr["silent_n"] <= nt["silent_n"] and (tr["cost_per_bill_inr"] or 0) <= TRUSTED_OCR_MAX_COST
    decision = {"rule": f"P5_trusted_ocr if silent <= P5_no_ocr and Rs/bill <= {TRUSTED_OCR_MAX_COST}, else P5_no_ocr",
                "P5_trusted_ocr": {k: tr[k] for k in ("silent_n", "silent_upper95", "pct_sonnet", "cost_per_bill_inr")},
                "P5_no_ocr": {k: nt[k] for k in ("silent_n", "silent_upper95", "pct_sonnet", "cost_per_bill_inr")},
                "default": "P5_trusted_ocr" if trusted_wins else "P5_no_ocr", "check_a_audit": audit}
    choice = json.loads((out / "policy_choice.json").read_text(encoding="utf-8"))
    (out / "policy_choice.json").write_text(json.dumps(choice | {"ocr_decision": decision}, indent=1, default=str),
                                            encoding="utf-8")
    print("\nCheck a on Haiku's total (bills with a photo; caught = Haiku's total was wrong, false = it was right)")
    for k, v in audit.items():
        print(f"  {k:<26} fires on {v['fires']:>3}: caught {v['caught']} (only this check: {v['caught_only_by_this']}),"
              f" false alarms {v['false']}"
              f"   (Haiku totals wrong in all: {v['haiku_wrong']}; OCR total trusted on {v['ocr_trusted']})")
    print(f"OCR DECISION ({decision['rule']}): default = {decision['default']}")

    print(f"\nP4 learned escalation: model {p4_info['model']}, nested thresholds {p4_info['fold_thresholds']}")
    if best:
        saving = 1 - (best["cost_per_bill_inr"] or 0) / sonnet_all["cost_per_bill_inr"]
        print(f"RECOMMENDED: {best['policy']}  silent {best['silent_n']} vs Sonnet-only {sonnet_all['silent_n']}, "
              f"Rs {best['cost_per_bill_inr']:.3f}/bill vs Rs {sonnet_all['cost_per_bill_inr']:.3f} ({saving:.0%} cheaper)")
    else:
        print("No deployable policy matches Sonnet-only's silent errors.")
    print(f"Saved: {out / 'policy_table.csv'}, policy_routes.csv, policy_choice.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
