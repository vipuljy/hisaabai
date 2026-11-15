"""Step 3: Play Store reviews of India's small-business bookkeeping apps (same method as FreshHold).

Apps: Khatabook, OkCredit, Vyapar, myBillBook. Each star rating (1-5) is fetched separately,
newest first, back to --since (default 2024-01-01) or up to --cap reviews per star.
Only review text, stars, date, thumbs-up and app version are kept: no user names.

Run from the project folder (needs normal internet; double-click RUN_STEP2_review_mining.bat):
  python src/03_scrape_bookkeeping_reviews.py
  python src/03_scrape_bookkeeping_reviews.py --apps khatabook --cap 3000
Output: data/raw/reviews/<app>_reviews.csv (never committed)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
from google_play_scraper import Sort, app, reviews, search

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "reviews"
COLS = ["reviewId", "content", "score", "at", "thumbsUpCount", "appVersion"]
BATCH = 200

# Known ids are tried first; if one fails, the Play Store search fallback finds the right app.
APPS = {
    "khatabook": {"ids": ["com.vaibhavkalpe.android.khatabook"], "search": "Khatabook", "match": "khata"},
    "okcredit": {"ids": ["in.okcredit.merchant"], "search": "OkCredit", "match": "okcredit"},
    "vyapar": {"ids": ["in.android.vyapar"], "search": "Vyapar billing", "match": "vyapar"},
    "mybillbook": {"ids": ["com.flobooks.flobooks"], "search": "myBillBook", "match": "billbook"},
}


def resolve_app_id(name, cfg):
    for pid in cfg["ids"]:
        try:
            info = app(pid, lang="en", country="in")
            print(f"[{name}] using {pid} ({info['title']})")
            return pid
        except Exception:
            continue
    for h in search(cfg["search"], lang="en", country="in", n_hits=5):
        if cfg["match"] in h["title"].lower().replace(" ", "") or cfg["match"] in h["appId"].lower():
            print(f"[{name}] resolved via search: {h['appId']} ({h['title']})")
            return h["appId"]
    print(f"[{name}] could not find the app. Skipping. (Edit APPS in this script.)")
    return None


def fetch_star(app_id, star, since, cap, label):
    rows, token = [], None
    while len(rows) < cap:
        batch = None
        for attempt in range(5):
            try:
                batch, token = reviews(app_id, lang="en", country="in", sort=Sort.NEWEST,
                                       count=BATCH, filter_score_with=star, continuation_token=token)
                break
            except Exception as err:
                wait = 5 * (attempt + 1)
                print(f"   {label} retry in {wait}s ({type(err).__name__})")
                time.sleep(wait)
        if not batch:
            break
        rows.extend(batch)
        oldest = min(r["at"] for r in batch)
        print(f"   {label} {len(rows):>6,} reviews, oldest {oldest:%Y-%m-%d}", end="\r")
        if oldest < since or token is None:
            break
        time.sleep(0.4)
    print()
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", nargs="*", default=list(APPS))
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--cap", type=int, default=6000, help="max reviews per app per star rating")
    args = ap.parse_args(argv)
    RAW.mkdir(parents=True, exist_ok=True)
    since = pd.Timestamp(args.since)
    summary = []
    for name in args.apps:
        app_id = resolve_app_id(name, APPS[name])
        if not app_id:
            continue
        rows = []
        for star in (1, 2, 3, 4, 5):
            rows += fetch_star(app_id, star, since, args.cap, f"[{name} {star}*]")
        df = pd.DataFrame(rows)
        if df.empty:
            print(f"[{name}] no reviews fetched")
            continue
        df = df[[c for c in COLS if c in df.columns]]
        df["at"] = pd.to_datetime(df["at"])
        df = df[df["at"] >= since].drop_duplicates(subset=["content", "score", "at"]).sort_values("at")
        df.to_csv(RAW / f"{name}_reviews.csv", index=False)
        summary.append((name, len(df), df["at"].min(), df["at"].max()))
    print("\nSUMMARY")
    for name, n, lo, hi in summary:
        print(f"{name:<11} {n:>7,} reviews  {lo:%Y-%m-%d} to {hi:%Y-%m-%d}")
    return 0 if summary else 1


if __name__ == "__main__":
    sys.exit(main())
