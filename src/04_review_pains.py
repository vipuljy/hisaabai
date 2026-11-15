"""Step 4: What do small-business owners complain about in bookkeeping apps?

Tags each review with pain themes (regex, multi-label; English + Hinglish), then answers:
  - Which pain is most common among unhappy (1-3 star) reviews, per app and overall?
  - Our three wedges compared: supplier bills / customer udhaar / daily cash + UPI.
  - Is manual entry effort a real complaint (the thing a photo or voice note removes)?

Inputs:  data/raw/reviews/<app>_reviews.csv   (from step 3)
Outputs: outputs/reviews/pain_by_app.csv          share of 1-3 star reviews per theme, per app
         outputs/reviews/pain_overall.csv         same, all apps pooled, with counts
         outputs/reviews/theme_quotes.csv         most-liked example per theme (for the case study)
         outputs/reviews/hand_label_sample.csv    200 tagged reviews to hand-check tagger precision
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "reviews"
OUT = ROOT / "outputs" / "reviews"


def rx(words):
    return re.compile(r"\b(?:" + "|".join(words) + r")", re.IGNORECASE)


# Wedge themes first (the decision), then the rest (context). Patterns favour precision:
# a missed mention costs less than a false one, and the hand-label sample measures it.
THEMES = {
    "W_supplier_bills": rx([r"supplier", r"vendor", r"purchase (?:bill|entry|entries|invoice|order|return)",
                            r"payable", r"wholesaler", r"distributor", r"stock ?ist", r"maal (?:liya|aaya)",
                            r"bill entry", r"enter(?:ing)? (?:the )?bills?"]),
    "W_customer_udhaar": rx([r"udh?aa?r", r"credit (?:given|to customer|book|entries)", r"customers? (?:not|never|don.?t) pay",
                             r"payment reminder", r"reminder (?:sms|message)", r"recover(?:y)? (?:of )?(?:money|payment|dues)",
                             r"collect(?:ing)? (?:money|payment|dues)", r"payment collection",
                             r"due from customer", r"baki ?(?:paise|payment)", r"lena (?:hai|baaki)"]),
    "W_cash_upi": rx([r"cash ?book", r"daily (?:sale|cash|closing)", r"closing balance", r"reconcil", r"upi (?:payment|entry|entries|match)",
                      r"cash (?:mismatch|not match|tally|in hand)", r"galla", r"day ?book", r"tally (?:nahi|not)"]),
    "manual_entry_effort": rx([r"manual(?:ly)?", r"typing", r"time.?consuming", r"takes? (?:too much|so much|a lot of|lot of|bahut) time", r"time (?:waste|lagta)",
                               r"too many (?:steps|clicks)", r"lengthy", r"tedious", r"har (?:entry|bill)", r"one by one"]),
    "wants_scan_or_voice": rx([r"scan", r"\bocr\b", r"photo (?:of|se) (?:the )?bill", r"camera", r"voice", r"bol ?kar",
                               r"auto(?:matic)?(?:ally)? (?:entry|read|fill)"]),
    "data_loss_trust": rx([r"data (?:lost|loss|gone|deleted|missing)", r"entries? (?:missing|deleted|gone|disappear)",
                           r"lost (?:all|my) (?:data|entries)", r"wrong balance", r"backup", r"not sync", r"sync (?:issue|problem)"]),
    "gst_compliance": rx([r"\bgst", r"gstr", r"e-?invoice", r"e-?way ?bill", r"\bitc\b", r"\btax"]),
    "pricing": rx([r"subscription", r"premium", r"expensive", r"costly", r"price (?:is|too)", r"paid (?:version|plan|feature)",
                   r"charg(?:e|es|ing) (?:money|extra|for)", r"\bplan (?:price|cost)"]),
    "bugs_login": rx([r"crash", r"\bbug", r"not (?:working|opening|loading)", r"\botp\b", r"login", r"hang", r"slow\b", r"\bads?\b"]),
    "language_ease": rx([r"\bhindi", r"language", r"complicated", r"confusing", r"difficult to (?:use|understand)", r"not user.?friendly"]),
}
WEDGES = ["W_supplier_bills", "W_customer_udhaar", "W_cash_upi"]


def load(raw_dir: Path = RAW) -> pd.DataFrame:
    frames = []
    for f in sorted(raw_dir.glob("*_reviews.csv")):
        d = pd.read_csv(f, usecols=lambda c: c in {"content", "score", "at", "thumbsUpCount"}, low_memory=False)
        d["app"] = f.name.removesuffix("_reviews.csv")
        frames.append(d)
    if not frames:
        raise FileNotFoundError(f"No review files in {raw_dir}. Run step 3 first.")
    df = pd.concat(frames, ignore_index=True)
    df["content"] = df["content"].fillna("").astype(str)
    df["thumbsUpCount"] = pd.to_numeric(df.get("thumbsUpCount", 0), errors="coerce").fillna(0)
    return df[df["content"].str.len() >= 15].reset_index(drop=True)  # drop "good app", "nice"


def tag(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for theme, pattern in THEMES.items():
        df[theme] = df["content"].str.contains(pattern)
    df["unhappy"] = df["score"] <= 3
    return df


def summarise(df: pd.DataFrame):
    unhappy = df[df["unhappy"]]
    by_app = (unhappy.groupby("app")[list(THEMES)].mean().round(4)
              .assign(unhappy_reviews=unhappy.groupby("app").size()).reset_index())
    overall = pd.DataFrame({
        "theme": list(THEMES),
        "unhappy_mentions": [int(unhappy[t].sum()) for t in THEMES],
        "share_of_unhappy": [round(unhappy[t].mean(), 4) for t in THEMES],
        "share_of_happy": [round(df[~df["unhappy"]][t].mean(), 4) if (~df["unhappy"]).any() else 0 for t in THEMES],
    }).sort_values("unhappy_mentions", ascending=False)
    quotes = []
    for t in THEMES:
        hit = unhappy[unhappy[t]].sort_values("thumbsUpCount", ascending=False).head(3)
        for _, r in hit.iterrows():
            quotes.append({"theme": t, "app": r["app"], "stars": r["score"], "likes": int(r["thumbsUpCount"]),
                           "review": r["content"][:400]})
    return by_app, overall, pd.DataFrame(quotes)


def hand_label_sample(df: pd.DataFrame, n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Half from tagged unhappy reviews (precision check), half untagged (what the tagger misses)."""
    unhappy = df[df["unhappy"]]
    any_tag = unhappy[list(THEMES)].any(axis=1)
    tagged = unhappy[any_tag].sample(min(n // 2, int(any_tag.sum())), random_state=seed)
    untagged = unhappy[~any_tag].sample(min(n // 2, int((~any_tag).sum())), random_state=seed)
    s = pd.concat([tagged, untagged])
    s["tags"] = s[list(THEMES)].apply(lambda r: ",".join(t for t in THEMES if r[t]), axis=1)
    s["correct_tags_yes_no"] = ""
    s["missed_theme"] = ""
    return s[["app", "score", "content", "tags", "correct_tags_yes_no", "missed_theme"]]


def main(raw_dir: Path = RAW, out_dir: Path = OUT) -> int:
    try:
        df = tag(load(raw_dir))
    except FileNotFoundError as err:
        print(err)
        return 1
    by_app, overall, quotes = summarise(df)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_app.to_csv(out_dir / "pain_by_app.csv", index=False)
    overall.to_csv(out_dir / "pain_overall.csv", index=False)
    quotes.to_csv(out_dir / "theme_quotes.csv", index=False)
    hand_label_sample(df).to_csv(out_dir / "hand_label_sample.csv", index=False)

    n_unhappy = int(df["unhappy"].sum())
    print(f"\n{len(df):,} reviews ({n_unhappy:,} with 1-3 stars) from {df['app'].nunique()} apps\n")
    print("WEDGES (share of unhappy reviews mentioning it)")
    for t in WEDGES:
        row = overall[overall["theme"] == t].iloc[0]
        print(f"  {t:<20} {row['share_of_unhappy']:>6.1%}  ({row['unhappy_mentions']:,})")
    print("\nOTHER THEMES")
    for _, row in overall[~overall["theme"].isin(WEDGES)].iterrows():
        print(f"  {row['theme']:<20} {row['share_of_unhappy']:>6.1%}  ({row['unhappy_mentions']:,})")
    print(f"\nFiles in {out_dir}. Tell Claude: \"reviews done\" and paste the lines above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
