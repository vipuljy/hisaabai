"""Step 11: real voice notes -> Sarvam speech-to-text -> the live bot. You label the truth, then score.

1. Put voice notes in data/voice_test/ (.ogg .opus .m4a .mp3 .wav .aac .flac; any file names). WhatsApp Web: open the
   chat > hover the voice note > Download.
2. python src/11_voice_eval.py            (RUN_STEP11_voice_eval.bat)  -> outputs/eval/voice_results.csv
3. Fill the three columns on the right (Excel is fine; keep the header row; save as CSV):
     true_intent    what you MEANT, one of:
                      bill        a supplier's bill / goods came        payment     you PAID a supplier
                      received    money came IN from a supplier         future      you WILL pay (not paid yet)
                      question    you asked something                   chatter     nothing to record
                      correction  change a saved entry's amount         undo        remove / check a saved entry
                    several things in one note: join with +, e.g. payment+bill (in the order you said them)
     true_amount    whole rupees, digits only (5000). correction: the NEW amount. undo: the saved entry's amount.
                    Blank for question / chatter. Several: 2000+1500
     true_supplier  the supplier as you said it (Sharma, or Sharma Traders). Blank for question / chatter.
                    Several: Sharma+Verma
4. python src/11_voice_eval.py --score    (RUN_STEP11b_voice_score.bat)

What each note goes through is exactly the live bot (bot/conversation.py) on a fake shop with the suppliers and recent
entries listed in SUPPLIERS / RECENT below (so "Kapoor Agency wala 8000 nahi 8500 tha" has an entry to fix):
decode, refuse notes over 2 minutes, cut into pieces of at most 29.5 s at pauses (Sarvam's limit is 30 s per request),
Sarvam (SARVAM_API_KEY in .env), join, then the text engine (bot/entries.py); if the rules find nothing and there is
no question word, the Haiku classifier (ANTHROPIC_API_KEY; its answer is cached per transcript). Nothing is ever
saved without the owner's tap; the eval looks at the cards the bot would show.

Scores, the same rules as the typed eval (src/12_entries_eval.py): correct / partial (one more tap, or a gap) / safe
(nothing recorded, nothing wrong shown) / WRONG (a card with the wrong type, supplier or amount; a card for chatter or
a question; money received / still to pay shown as a payment; one of several entries lost). Notes over 2 minutes are
"safe": the bot asks for a shorter note.

Cost guard: at most 30 NEW files per run (--limit, max 30). Transcripts and Haiku answers are cached in
data/eval/runs/voice_sarvam/ (one file per audio, by content hash), so a re-run only pays for new notes. Sarvam bills
per second of audio (about Rs 29.40 per hour, pay as you go); the script prints the seconds it sent.

Files: data/voice_test/ and the cache stay private (data/ is gitignored); outputs/eval/voice_results.csv has the
transcripts, so outputs/ is gitignored too.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import itertools
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import entries as E  # noqa: E402
from bot import questions as Q  # noqa: E402
from bot import voice as V  # noqa: E402
from bot.conversation import Bot  # noqa: E402
from bot.store import MemoryStore  # noqa: E402

AUDIO_EXT = (".ogg", ".opus", ".m4a", ".mp3", ".wav", ".aac", ".flac")
MAX_FILES = 30
RS_PER_HOUR = 29.40         # Sarvam speech-to-text, pay as you go, before GST (docs, Oct 2026)
FILL = ("true_intent", "true_amount", "true_supplier")
COLUMNS = ["file", "seconds", "pieces", "over_30s", "transcript", "bot_did", "route", "error", *FILL]
INTENTS = ("bill", "payment", "received", "future", "question", "chatter", "correction", "undo")
TOO_LONG = "too long (bot asks for a shorter note)"
OUTCOMES = ("correct", "partial", "safe", "WRONG")
IST = timezone(timedelta(hours=5, minutes=30))
OWNER = "910000000002"  # a fake number  # secret-scan: ignore
SUPPLIERS = ["Sharma Traders", "Gupta Agency", "Verma Medical", "Bansal Hardware", "Kapoor Agency", "Mittal Store",
             "Singh Distributors", "Rawat Traders"]
RECENT = [  # (kind, supplier, amount, days ago, hour IST): saved entries the notes may fix; the LAST one is the newest
    ("payment", "Gupta Agency", 1000, 3, 11), ("bill", "Mittal Store", 2500, 2, 12), ("payment", "Kapoor Agency", 8000, 1, 18),
]
_ids = itertools.count(1)


def _e12():
    spec = importlib.util.spec_from_file_location("e12", ROOT / "src" / "12_entries_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E12 = _e12()  # the typed eval: same fake WhatsApp, same way of reading what the bot showed, same scoring


# ---------------------------------------------------------------- what the bot does with a transcript
class VoiceWA(E12.FakeWA):
    async def download_media(self, media_id):
        return b"not audio"  # the fake transcriber ignores the bytes and returns the cached transcript


def fake_shop(today: date, entry_parser=None):
    store = MemoryStore()
    shop = store.create_shop(OWNER, shop_code="EVAL03", status="active", shop_name="Gupta General Store")
    now = datetime.now(timezone.utc)
    store.update_shop(shop["id"], consent_at=now, onboarded_at=now)
    sup = {n: store.find_or_create_supplier(shop["id"], n) for n in SUPPLIERS}
    for kind, name, amount, ago, hour in RECENT:
        day = today - timedelta(days=ago)
        at = datetime(day.year, day.month, day.day, hour, tzinfo=IST).astimezone(timezone.utc)
        if kind == "bill":
            b = store.create_bill(shop["id"], {"supplier_name": name, "bill_date": day.isoformat(), "total_amount": amount,
                                               "invoice_number": None, "due_date": None, "unsure_fields": []},
                                  {"model": "typed", "source": "typed"})
            store.confirm_bill(b["id"], sup[name]["id"])
            store.bills[b["id"]]["created_at"] = store.bills[b["id"]]["confirmed_at"] = at
        else:
            store.add_payment(shop["id"], sup[name]["id"], amount, "voice")
            store.payments[-1].update(paid_on=day, created_at=at, confirmed_at=at)
    bot = Bot(store, VoiceWA(), extractor=None, pilot_open=False, image_dir=ROOT / "outputs" / "eval" / "_tmp",
              question_llm=None, today_fn=lambda: today, entry_parser=entry_parser)
    return bot, store, shop


def understand(transcript: str, entry_parser=None, today: date | None = None) -> dict:
    """The live bot's voice step for one transcript (fake Sarvam returns it). Returns what the owner would see."""
    today = today or datetime.now(IST).date()
    bot, store, shop = fake_shop(today, entry_parser)

    async def heard(_audio):
        return transcript
    bot.transcriber = heard
    seeded_b, seeded_p = set(store.bills), {p["id"] for p in store.payments}
    before_ev = len(store.events)
    asyncio.run(bot.handle({"from": OWNER, "id": f"wamid.v{next(_ids)}", "type": "audio", "audio": {"id": "a"}}))
    r = E12.collect(bot, store, shop, seeded_b, seeded_p, before_ev, 0)
    events = [e for e in store.events[before_ev:]]
    by = next((e["props"].get("understood_by") for e in events if e["event"] == "entry_parsed"), None)
    r["route"] = ("llm" if by == "llm" else "rules") if by else ("question" if r["question"] else
                                                                 "fix" if "fix_asked" in r["events"] else "not understood")
    r["bot_did"] = describe(r)
    return r


def describe(r: dict) -> str:
    out = []
    for g in r["got"]:
        if g["intent"] in ("bill", "payment", "received", "future", "kind", "correction", "undo"):
            out.append(f"{g['intent']} {g.get('amount', '')} {g.get('supplier', '')}".strip())
        else:
            out.append(g["intent"])
    if r["question"]:
        out.append("question")
    return "; ".join(out) or "nothing (example / menu)"


# ---------------------------------------------------------------- run
def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel opens Hindi text correctly
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def transcribe_file(raw: bytes, transcriber, stem: str) -> dict:
    """The live bot's steps (bot/voice.py), with each piece named after the file (handy for debugging)."""
    rec = {"seconds": None, "pieces": 0, "transcript": "", "error": None, "too_long": False}
    try:
        pcm, rate = V.decode_audio(raw)
    except ImportError:
        rec["error"] = "pip install av   (needed to read .ogg / .opus / .m4a / .mp3)"
        return rec
    except Exception as err:
        rec["error"] = f"cannot read audio: {type(err).__name__}"
        return rec
    rec["seconds"] = round(len(pcm) / 2 / rate, 2)
    if rec["seconds"] > V.MAX_NOTE_SECONDS:
        rec["too_long"] = True  # the bot replies VOICE_TOO_LONG and sends nothing to Sarvam
        return rec
    pieces = V.split_at_pauses(pcm, rate)
    rec["pieces"] = len(pieces)
    try:
        texts = [asyncio.run(transcriber(V.wav_bytes(p, rate), f"{stem}_{k}.wav", "audio/wav"))
                 for k, p in enumerate(pieces)]
    except Exception as err:  # Sarvam error: shown in the CSV, retried next run
        rec["error"] = f"{type(err).__name__}: {str(err)[:120]}"
        return rec
    rec["transcript"] = " ".join((t or "").strip() for t in texts if t and t.strip())
    return rec


class CachedClassifier:
    """The Haiku classifier, called at most once per transcript: its JSON answer is kept in the note's cache file."""

    def __init__(self, live=None):
        self.live, self.answers, self.calls = live, {}, 0

    def __call__(self, transcript, supplier_names, today):
        if transcript in self.answers:
            return self.answers[transcript]
        if self.live is None:
            return None
        self.calls += 1
        self.answers[transcript] = self.live(transcript, supplier_names, today)
        return self.answers[transcript]


def run(root: Path, limit: int, transcriber=None, entry_parser=None, today: date | None = None) -> int:
    voice_dir, cache_dir = root / "data" / "voice_test", root / "data" / "eval" / "runs" / "voice_sarvam"
    out_path = root / "outputs" / "eval" / "voice_results.csv"
    files = sorted(p for p in voice_dir.glob("*") if p.suffix.lower() in AUDIO_EXT) if voice_dir.exists() else []
    if not files:
        print(f"No voice notes found. Put them in {voice_dir} ({' '.join(AUDIO_EXT)})")
        return 1
    limit = max(1, min(limit, MAX_FILES))
    if transcriber is None or entry_parser is None:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    if transcriber is None:
        key = os.getenv("SARVAM_API_KEY", "").strip()
        if not key:
            print("SARVAM_API_KEY is empty in .env (get a key at dashboard.sarvam.ai, then add the line SARVAM_API_KEY=...)")
            return 1
        transcriber = V.SarvamTranscriber(key)
    if entry_parser is None and os.getenv("ANTHROPIC_API_KEY", "").strip():
        model = os.getenv("BILL_MODEL", "claude-haiku-5-5")
        entry_parser = lambda t, names, d: V.llm_entry_parser(t, names, d, model=model)  # noqa: E731
    classifier = CachedClassifier(entry_parser)

    old = {r["file"]: r for r in _load_csv(out_path)}
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows, new_files, sent_seconds, skipped = [], 0, 0.0, []
    for path in files:
        raw = path.read_bytes()
        cache_file = cache_dir / f"{hashlib.sha1(raw).hexdigest()}.json"
        rec = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
        if rec is None or rec.get("error"):
            if new_files >= limit:
                skipped.append(path.name)
                continue
            new_files += 1
            rec = {"file": path.name} | transcribe_file(raw, transcriber, path.stem)
            if not rec["too_long"] and not rec["error"]:
                sent_seconds += rec["seconds"] or 0
        if rec.get("too_long"):
            did, route = TOO_LONG, "too long"
        elif not rec.get("transcript"):
            did, route = "no transcript", "no transcript"
        else:
            if "entry_llm" in rec:  # cached classifier answer for this transcript (None = it found nothing)
                classifier.answers[rec["transcript"]] = rec["entry_llm"]
            r = understand(rec["transcript"], classifier, today)
            did, route = r["bot_did"], r["route"]
            if rec["transcript"] in classifier.answers:
                rec["entry_llm"] = classifier.answers[rec["transcript"]]
        cache_file.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        secs = rec.get("seconds")
        row = {"file": path.name, "seconds": secs if secs is not None else "", "pieces": rec.get("pieces") or "",
               "over_30s": "yes" if secs and secs > 30 else "no", "transcript": rec.get("transcript") or "",
               "bot_did": did, "route": route, "error": rec.get("error") or ""}
        row |= {k: (old.get(path.name) or {}).get(k, "") for k in FILL}  # keep what you already filled in
        rows.append(row)
        print(f"  {path.name:<14} {str(secs or '?'):>6}s  {route:<15} {did[:70]}")
    _write_csv(out_path, rows)
    print(f"\nSarvam: {sent_seconds:.1f} seconds of audio sent this run ({new_files} new file(s)), "
          f"about Rs {sent_seconds / 3600 * RS_PER_HOUR:.2f}. Cached files cost nothing. "
          f"Haiku classifier calls this run: {classifier.calls}.")
    if skipped:
        print(f"Cost guard: {len(skipped)} file(s) left for the next run (max {limit} new per run).")
    print(f"Saved: {out_path}\nNext: fill {', '.join(FILL)} (formats: python src/11_voice_eval.py --help), "
          "then run with --score.")
    return 0


# ---------------------------------------------------------------- score
def _split(value) -> list[str]:
    return [v.strip() for v in str(value or "").split("+")]


def expected_of(row: dict) -> tuple[str | None, str | None]:
    """The filled columns -> the typed eval's 'expected' string, or (None, problem)."""
    intents = [i.lower() for i in _split(row.get("true_intent")) if i]
    if not intents:
        return None, None
    bad = [i for i in intents if i not in INTENTS]
    if bad:
        return None, f"true_intent '{'+'.join(bad)}' is not one of {' / '.join(INTENTS)} (join several with +)"
    if intents in (["question"], ["chatter"]):
        return ("question" if intents == ["question"] else "other"), None
    if {"question", "chatter"} & set(intents):
        return None, "question / chatter cannot be joined with + to other kinds"
    amounts, names = _split(row.get("true_amount")), _split(row.get("true_supplier"))
    if len(amounts) != len(intents) or len(names) != len(intents):
        return None, f"{len(intents)} intents but {len(amounts)} amounts / {len(names)} suppliers (join with +)"
    sups = [{"id": str(k), "name": n, "name_norm": E.norm_supplier(n)} for k, n in enumerate(SUPPLIERS)]
    parts = []
    for intent, amount, name in zip(intents, amounts, names):
        digits = "".join(c for c in amount if c.isdigit() or c == ".")
        if not digits:
            return None, f"true_amount '{amount}' is not a number"
        if not name:
            return None, "true_supplier is empty"
        match, _ = E.strict_match(Q.tokens(name), sups)
        supplier = match["name"] if match else f"new:{name}"
        kind = {"correction": "correction", "undo": "undo"}.get(intent, intent)
        parts.append(f"{kind}|{supplier}|{int(round(float(digits)))}|?|")  # ?: credit days are not labelled
    return ";".join(parts), None


def score(root: Path, today: date | None = None) -> int:
    today = today or datetime.now(IST).date()
    path = root / "outputs" / "eval" / "voice_results.csv"
    rows = _load_csv(path)
    if not rows:
        print(f"Nothing to score: run the eval first ({path} not found)")
        return 1
    cache = {}
    cache_dir = root / "data" / "eval" / "runs" / "voice_sarvam"
    for f in cache_dir.glob("*.json") if cache_dir.exists() else []:
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("transcript") and "entry_llm" in rec:
            cache[rec["transcript"]] = rec["entry_llm"]
    problems, results = [], []
    for row in rows:
        expected, problem = expected_of(row)
        if problem:
            problems.append(f"{row['file']}: {problem}")
        if expected is None:
            continue
        if (row.get("route") or "").startswith("too long"):
            results.append((row, expected, "safe", "too long: asked for a shorter note"))
            continue
        if not row.get("transcript"):
            results.append((row, expected, "safe", "no transcript"))
            continue
        clf = CachedClassifier()
        clf.answers = dict(cache)
        r = understand(row["transcript"], clf, today)
        out, why = E12.judge({"expected": expected}, r, today)
        results.append((row, expected, out, why))
    if not results:
        print(f"No rows labelled yet: fill {', '.join(FILL)} in {path}")
        for p in problems:
            print("  label check: " + p)
        return 1
    counts = Counter(o for _, _, o, _ in results)
    print(f"VOICE EVAL: {len(results)} labelled of {len(rows)} notes")
    for o in OUTCOMES:
        print(f"  {o:<8} {counts[o]:>3}" + ("   <- a wrong card was shown" if o == "WRONG" and counts[o] else ""))
    print(f"  read by rules / by Haiku: {sum(r.get('route') == 'rules' for r, *_ in results)} / "
          f"{sum(r.get('route') == 'llm' for r, *_ in results)}")
    by = defaultdict(Counter)
    for row, _, o, _ in results:
        by[(row.get("true_intent") or "").strip().lower()][o] += 1
    print("\nBY INTENT" + "".join(f"{o:>9}" for o in OUTCOMES))
    for k in sorted(by):
        print(f"  {k:<16}" + "".join(f"{by[k][o]:>9}" for o in OUTCOMES))
    print("\nNot correct:")
    for row, exp, o, why in results:
        if o != "correct":
            print(f"  {row['file']:<10} {o:<8} {why[:60]:<60} heard: {row['transcript'][:70]!r}")
    if problems:
        print("\nLabel check (fix these in the CSV):")
        for p in problems:
            print("  " + p)
    return 0


def main(argv=None, root: Path = ROOT, transcriber=None, entry_parser=None, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--score", action="store_true", help="score voice_results.csv after you filled the columns")
    ap.add_argument("--limit", type=int, default=MAX_FILES, help=f"new files per run (max {MAX_FILES})")
    args = ap.parse_args(argv)
    if args.score:
        return score(root, today)
    return run(root, args.limit, transcriber, entry_parser, today)


if __name__ == "__main__":
    sys.exit(main())
