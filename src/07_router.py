"""Step 7: the router. Should this photo go to Haiku (cheap) or Sonnet (stronger)?

Target: haiku_ok = Haiku (cached run haiku_v2) read BOTH the total and the bill date right.
Features: free signals only (bot/router.py): image blur / brightness / contrast / ink, Tesseract's script and
orientation check, RapidOCR confidence, words, digits, table-like lines, GSTIN, which proofs passed.
The hand label (printed / handwritten) is never a feature: the live bot does not have it.
Models: logistic regression and a depth-3 decision tree, scored with 5-fold GROUPED cross-validation (bills from
the same supplier or original file stay in one fold), so no bill is ever scored by a model that saw it or its
near-copies. The model that catches more "Haiku gets it wrong" bills (recall) wins.
Threshold: the cheapest one whose silent-error rate is at most Sonnet-only's + 1 point. It is chosen NESTED:
inside each outer training fold, an inner grouped CV gives out-of-fold predictions for the training bills only,
the threshold is picked on those, and then applied to the outer fold's held-out bills (honest number). For
comparison the old way is also reported: one threshold picked on the same predictions it is scored on (optimistic).
The final model + one threshold picked on all bills are saved for the live bot.

Usage (from the project root, after RUN_STEP1_eval.bat and RUN_STEP6_free_readers.bat):
  python src/07_router.py
Outputs (outputs/eval/): router_cv.csv (models), router_thresholds.csv, router_oof.csv (used by step 08),
  router_choice.json, router_tree.png, router_model.pkl. Image features are cached in data/eval/runs/router_features/.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import cascadekit as K  # noqa: E402
import evalkit  # noqa: E402
from bot import router  # noqa: E402

SEED = 20261026
THRESHOLDS = [round(i * 0.05, 2) for i in range(21)] + [1.01]  # Sonnet if P(haiku_ok) < threshold
SILENT_SLACK = 0.01


def features(root: Path, labels: list[dict], ocr: dict) -> list[dict]:
    """One feature row per bill. Image features are cached (computing them reads every photo once)."""
    cache = root / "data" / "eval" / "runs" / "router_features"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for l in labels:
        f = cache / f"{l['bill_id']}.json"
        if f.exists():
            img = json.loads(f.read_text(encoding="utf-8"))
        else:
            path = root / "data" / "eval" / "images" / l["image_file"]
            img = router.photo_features(path.read_bytes()) if path.exists() else dict.fromkeys(router.IMAGE_FEATURES)
            f.write_text(json.dumps(img), encoding="utf-8")
        rows.append(img | router.ocr_features(ocr.get(l["bill_id"])))
    return rows


def _matrix(rows):
    import numpy as np
    return np.array([[np.nan if r.get(c) is None else float(r[c]) for c in router.FEATURES] for r in rows])


def models(seed: int = SEED) -> dict:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.tree import DecisionTreeClassifier

    # keep_empty_features: a feature that is missing for every bill (e.g. no Tesseract) stays a column
    imp = lambda: SimpleImputer(strategy="median", keep_empty_features=True)  # noqa: E731
    return {
        "logreg": make_pipeline(imp(), StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000)),
        "tree": make_pipeline(imp(), DecisionTreeClassifier(max_depth=3, class_weight="balanced", random_state=seed)),
    }


def group_splits(X, y, groups, folds: int = 5, seed: int = SEED) -> list:
    """Grouped folds (a group never spans two folds), stratified by the target when possible."""
    from sklearn.model_selection import GroupKFold, StratifiedGroupKFold

    k = max(2, min(folds, len(set(groups))))
    try:
        return list(StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=seed).split(X, y, groups))
    except ValueError:
        return list(GroupKFold(n_splits=k).split(X, y, groups))


def grouped_oof(X, y, groups, folds: int = 5, seed: int = SEED, splits=None, names=None) -> dict:
    """Out-of-fold P(haiku_ok) per model. A fold whose training part has one class predicts that class's rate."""
    import numpy as np
    from sklearn.base import clone

    splits = splits if splits is not None else group_splits(X, y, groups, folds, seed)
    out = {}
    for name, model in models(seed).items():
        if names and name not in names:
            continue
        p = np.zeros(len(y))
        for tr, te in splits:
            if len(set(y[tr])) < 2:
                p[te] = float(np.mean(y[tr]))
                continue
            m = clone(model).fit(X[tr], y[tr])
            p[te] = m.predict_proba(X[te])[:, list(m.classes_).index(1)]
        out[name] = p
    return out


def model_scores(y, p) -> dict:
    import numpy as np
    from sklearn.metrics import roc_auc_score

    bad = y == 0
    return {"recall_haiku_wrong": float(np.mean(p[bad] < 0.5)) if bad.any() else None,
            "accuracy": float(np.mean((p >= 0.5) == (y == 1))),
            "auc": float(roc_auc_score(y, p)) if len(set(y)) == 2 else None}


def threshold_table(labels, p, haiku, sonnet, cfg) -> list[dict]:
    """For each threshold: share sent to Sonnet, silent-error rate, all-fields accuracy, cost (out-of-fold)."""
    rows = []
    for t in THRESHOLDS:
        silent = right = to_sonnet = 0
        cost, priced = 0.0, True
        for i, l in enumerate(labels):
            rec = sonnet[l["bill_id"]] if p[i] < t else haiku[l["bill_id"]]
            to_sonnet += p[i] < t
            o = K.outcomes(l, rec["fields"])
            silent += any(s["silent_error"] for s in o.values())
            right += all(s["outcome"] in ("correct", "correct_blank") for s in o.values())
            c = K.call_cost_inr(rec, cfg)
            priced &= c is not None
            cost += c or 0
        n = len(labels)
        rows.append({"threshold": t, "pct_sonnet": round(to_sonnet / n, 4), "silent_error_rate": round(silent / n, 4),
                     "all_fields_right": round(right / n, 4), "cost_per_bill_inr": round(cost / n, 3) if priced else None})
    return rows


def pick_threshold(table: list[dict]) -> dict:
    """Cheapest threshold with silent errors <= Sonnet-only + 1 point (cost, else share sent to Sonnet)."""
    sonnet_only = table[-1]["silent_error_rate"]
    ok = [r for r in table if r["silent_error_rate"] <= sonnet_only + SILENT_SLACK + 1e-9]
    key = (lambda r: (r["cost_per_bill_inr"], r["silent_error_rate"])) if all(
        r["cost_per_bill_inr"] is not None for r in ok) else (lambda r: (r["pct_sonnet"], r["silent_error_rate"]))
    return min(ok, key=key)


def nested_thresholds(X, y, groups, labels, haiku, sonnet, cfg, model_name, splits, folds: int = 5) -> dict:
    """For each OUTER fold: an inner grouped CV on the training bills only -> pick the threshold there ->
    apply it to the held-out bills. Returns per-bill {"threshold", "to_sonnet"} and the per-fold thresholds."""
    import numpy as np

    p_outer = grouped_oof(X, y, groups, splits=splits, names=[model_name])[model_name]
    per_bill, fold_thresholds = {}, []
    for tr, te in splits:
        tr_groups = [groups[i] for i in tr]
        if len(set(y[tr])) < 2 or len(set(tr_groups)) < 2:
            t = 0.0 if y[tr].mean() >= 0.5 else 1.01  # one class only: everything to Haiku, or to Sonnet
        else:
            inner = grouped_oof(X[tr], y[tr], tr_groups, folds=folds, names=[model_name])[model_name]
            t = pick_threshold(threshold_table([labels[i] for i in tr], inner, haiku, sonnet, cfg))["threshold"]
        fold_thresholds.append(t)
        for i in te:
            per_bill[i] = {"threshold": t, "to_sonnet": bool(p_outer[i] < t)}
    return {"per_bill": per_bill, "fold_thresholds": fold_thresholds, "p": p_outer}


def routed_scores(labels, to_sonnet, haiku, sonnet, cfg) -> dict:
    """Silent errors / accuracy / cost when bill i goes to Sonnet if to_sonnet[i], else Haiku."""
    n = len(labels)
    silent = right = 0
    cost, priced = 0.0, True
    for i, l in enumerate(labels):
        rec = sonnet[l["bill_id"]] if to_sonnet[i] else haiku[l["bill_id"]]
        o = K.outcomes(l, rec["fields"])
        silent += any(s["silent_error"] for s in o.values())
        right += all(s["outcome"] in ("correct", "correct_blank") for s in o.values())
        c = K.call_cost_inr(rec, cfg)
        priced &= c is not None
        cost += c or 0
    return {"pct_sonnet": round(sum(to_sonnet) / n, 4), "silent_error_rate": round(silent / n, 4),
            "all_fields_right": round(right / n, 4), "cost_per_bill_inr": round(cost / n, 3) if priced else None}


def save_tree_png(model, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.tree import plot_tree

    fig, ax = plt.subplots(figsize=(18, 8))
    plot_tree(model[-1], feature_names=router.FEATURES, class_names=["Haiku wrong", "Haiku ok"], filled=True,
              rounded=True, impurity=False, proportion=True, ax=ax, fontsize=9)
    ax.set_title("Router (depth-3 tree): send to Haiku or Sonnet? Trained on all labelled bills")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args(argv)

    labels = evalkit.load_labels(root / "data" / "eval" / "labels.csv")
    missing = K.missing_runs(root, labels, K.CLAUDE_RUNS)
    if missing:
        print(K.NEED_CLAUDE.format(missing=", ".join(missing)))
        return 1
    if K.missing_runs(root, labels, ["ocr_rapid"]):
        print(K.NEED_FREE.format(missing="ocr_rapid"))
        return 1
    import numpy as np

    haiku, sonnet = K.load_run(root, "haiku_v2", labels), K.load_run(root, "sonnet_v2", labels)
    ocr = K.load_run(root, "ocr_rapid", labels)
    split = K.load_split(root, labels)
    rows = features(root, labels, ocr)
    X = _matrix(rows)
    y = np.array([K.haiku_ok(l, haiku[l["bill_id"]]) for l in labels])
    groups = [split.get(l["bill_id"], {}).get("group") or l["bill_id"] for l in labels]
    cfg = K.config(root)
    out_dir = root / "outputs" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Bills: {len(labels)}  Haiku right (total + date): {int(y.sum())}  wrong: {int(len(y) - y.sum())}  "
          f"groups: {len(set(groups))}")
    if len(set(y)) < 2:
        model_name = "none"
        p = np.full(len(y), float(y[0]))
        cv_rows = [{"model": "none", "note": "Haiku was always " + ("right" if y[0] else "wrong")}]
        print("Only one outcome: no router needed (" + ("everything to Haiku" if y[0] else "everything to Sonnet") + ").")
        oof = {"logreg": p, "tree": p}
    else:
        splits = group_splits(X, y, groups, args.folds)
        oof = grouped_oof(X, y, groups, splits=splits)
        cv_rows = [{"model": m} | model_scores(y, oof[m]) for m in oof]
        best = max(cv_rows, key=lambda r: ((r["recall_haiku_wrong"] or 0), (r["auc"] or 0)))
        model_name = best["model"]
        p = oof[model_name]
        final = models()[model_name].fit(X, y)
        with open(out_dir / "router_model.pkl", "wb") as f:
            pickle.dump({"model": final, "features": router.FEATURES, "name": model_name}, f)
        save_tree_png(models()["tree"].fit(X, y), out_dir / "router_tree.png")

    table = threshold_table(labels, p, haiku, sonnet, cfg)
    choice = pick_threshold(table)  # one threshold on all out-of-fold predictions: for the saved model (live bot)
    if model_name == "none":
        nested = {"per_bill": {i: {"threshold": choice["threshold"], "to_sonnet": bool(p[i] < choice["threshold"])}
                               for i in range(len(labels))}, "fold_thresholds": [choice["threshold"]]}
    else:
        nested = nested_thresholds(X, y, groups, labels, haiku, sonnet, cfg, model_name, splits, args.folds)
    to_sonnet_nested = [nested["per_bill"][i]["to_sonnet"] for i in range(len(labels))]
    honest = routed_scores(labels, to_sonnet_nested, haiku, sonnet, cfg)
    same = routed_scores(labels, [bool(x < choice["threshold"]) for x in p], haiku, sonnet, cfg)
    evalkit.write_csv(out_dir / "router_cv.csv", cv_rows)
    evalkit.write_csv(out_dir / "router_thresholds.csv", table)
    evalkit.write_csv(out_dir / "router_oof.csv", [
        {"bill_id": l["bill_id"], "group": groups[i], "haiku_ok": int(y[i]), "p_logreg": round(float(oof["logreg"][i]), 4),
         "p_tree": round(float(oof["tree"][i]), 4), "p_router": round(float(p[i]), 4),
         "threshold_nested": nested["per_bill"][i]["threshold"], "sonnet_nested": int(to_sonnet_nested[i]),
         "sonnet_same_predictions": int(p[i] < choice["threshold"])} for i, l in enumerate(labels)])
    (out_dir / "router_choice.json").write_text(json.dumps(
        {"model": model_name, "threshold": choice["threshold"], "folds": args.folds, "seed": SEED,
         "rule": "Sonnet if P(haiku_ok) < threshold; threshold = cheapest with silent errors <= Sonnet-only + 1 point",
         "nested": {"fold_thresholds": nested["fold_thresholds"], "scores": honest},
         "same_predictions_optimistic": {"threshold": choice["threshold"], "scores": same},
         "out_of_fold": choice}, indent=1), encoding="utf-8")

    print("\nCROSS-VALIDATED (grouped, out-of-fold)")
    for r in cv_rows:
        if "auc" in r:
            auc = "-" if r["auc"] is None else f"{r['auc']:.2f}"
            rec = "-" if r["recall_haiku_wrong"] is None else f"{r['recall_haiku_wrong']:.0%}"
            print(f"  {r['model']:<7} catches Haiku's wrong bills: {rec:>5}   accuracy {r['accuracy']:.0%}   AUC {auc}")
    print(f"Chosen model: {model_name}. Haiku-only silent errors {table[0]['silent_error_rate']:.0%}, "
          f"Sonnet-only {table[-1]['silent_error_rate']:.0%}")
    for name, sc, thr in (("NESTED (honest)", honest, ", ".join(str(t) for t in nested["fold_thresholds"])),
                          ("same predictions (optimistic)", same, str(choice["threshold"]))):
        cost = "-" if sc["cost_per_bill_inr"] is None else f"Rs {sc['cost_per_bill_inr']:.2f}/bill"
        print(f"  {name:<30} threshold {thr}: {sc['pct_sonnet']:.0%} to Sonnet, silent errors "
              f"{sc['silent_error_rate']:.0%}, all fields right {sc['all_fields_right']:.0%}, {cost}")
    print(f"Saved: {out_dir / 'router_oof.csv'}, router_choice.json, router_cv.csv, router_thresholds.csv, router_tree.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
