"""Step 5: How well does the bot understand voice notes? (small check, 25 notes)

Record the 25 lines in src/templates/voice_script.csv as WhatsApp voice notes (ideally 2-3 different
people, normal shop noise is fine), save them as data/voice/V01.ogg ... V25.ogg (any of .ogg .opus .mp3
.wav .m4a), then run this (RUN_STEP3_voice_check.bat).

Each note goes through exactly what the bot does: Sarvam speech-to-text, then the rule-based payment
parser, then the Claude text fallback, then supplier matching against the 5 suppliers in the script.

Scores:
  intent_correct    payment / dues / other recognised correctly
  amount_correct    for payments: the exact rupee amount
  supplier_correct  for payments: matched to the right supplier
  fully_correct     all of the above: the owner just taps "Haan, sahi"
Every payment is confirmed with a button, so a wrong reading is caught, not saved silently.

Outputs: data/voice/voice_results.csv (with transcripts: private, never committed)
         outputs/voice/voice_summary.csv (rates only)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.conversation import DUES_RE, match_supplier, parse_payment  # noqa: E402
from bot.extract import norm_supplier  # noqa: E402

AUDIO_EXT = (".ogg", ".opus", ".mp3", ".wav", ".m4a")


def load_script(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_audio(voice_dir: Path, vid: str) -> Path | None:
    return next((voice_dir / f"{vid}{ext}" for ext in AUDIO_EXT if (voice_dir / f"{vid}{ext}").exists()), None)


def understand(transcript: str, suppliers: list[dict], voice_parser=None) -> dict:
    """Same order as Bot._on_voice."""
    if DUES_RE.search(transcript.lower()):
        return {"intent": "dues", "amount": None, "supplier": None, "via": "rules"}
    payment, via = parse_payment(transcript), "rules"
    if payment is None and voice_parser is not None:
        try:
            payment, via = voice_parser(transcript), "llm"
        except Exception:
            payment = None
    if not payment:
        return {"intent": "other", "amount": None, "supplier": None, "via": via}
    amount, name = payment
    match = match_supplier(name, suppliers)
    return {"intent": "payment", "amount": int(amount), "supplier": match["name"] if match else None, "via": via}


def score(rows: list[dict]) -> dict:
    n = len(rows)
    pays = [r for r in rows if r["expected_intent"] == "payment"]
    rate = lambda xs, key: round(sum(x[key] for x in xs) / len(xs), 3) if xs else None  # noqa: E731
    return {"notes": n, "intent_correct": rate(rows, "intent_ok"),
            "amount_correct": rate(pays, "amount_ok"), "supplier_correct": rate(pays, "supplier_ok"),
            "fully_correct": rate(rows, "fully_ok"),
            "used_llm_fallback": sum(r["via"] == "llm" for r in rows)}


def main(argv=None, root: Path = ROOT, transcriber=None, voice_parser=None) -> int:
    ap = argparse.ArgumentParser(description="Score voice-note understanding")
    ap.parse_args(argv or [])
    voice_dir = root / "data" / "voice"
    script = load_script(ROOT / "src" / "templates" / "voice_script.csv")
    todo = [(s, find_audio(voice_dir, s["id"])) for s in script]
    todo = [(s, p) for s, p in todo if p]
    if not todo:
        print(f"No recordings found. Save them as {voice_dir / 'V01.ogg'} ... V25.ogg")
        return 1
    if transcriber is None:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
        key = os.getenv("SARVAM_API_KEY", "").strip()
        if not key:
            print("SARVAM_API_KEY is empty in .env")
            return 1
        from bot.voice import SarvamTranscriber, llm_payment_parser
        transcriber = SarvamTranscriber(key)
        if voice_parser is None and os.getenv("ANTHROPIC_API_KEY", "").strip():
            voice_parser = lambda t: llm_payment_parser(t, model=os.getenv("BILL_MODEL", "claude-haiku-5-5"))  # noqa: E731

    names = sorted({s["supplier"] for s in script if s["supplier"]})
    suppliers = [{"id": n, "name": n, "name_norm": norm_supplier(n)} for n in names]
    results = []
    for s, path in todo:
        try:
            transcript = asyncio.run(transcriber(path.read_bytes()))
        except Exception as err:
            transcript = ""
            print(f"{s['id']}: transcription failed ({type(err).__name__})")
        got = understand(transcript, suppliers, voice_parser)
        want_amount = int(s["amount"]) if s["amount"] else None
        r = {"id": s["id"], "said": s["say_this"], "transcript": transcript, "expected_intent": s["intent"],
             "intent": got["intent"], "amount": got["amount"], "supplier": got["supplier"], "via": got["via"],
             "intent_ok": got["intent"] == s["intent"],
             "amount_ok": got["amount"] == want_amount, "supplier_ok": got["supplier"] == (s["supplier"] or None)}
        r["fully_ok"] = r["intent_ok"] and (s["intent"] != "payment" or (r["amount_ok"] and r["supplier_ok"]))
        results.append(r)
        print(f"{s['id']}  {'OK  ' if r['fully_ok'] else 'MISS'}  heard: {transcript[:70]}")

    summary = score(results)
    with (voice_dir / "voice_results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    out_dir = root / "outputs" / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "voice_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary))
        w.writeheader()
        w.writerow(summary)
    print("\nVOICE CHECK")
    for k, v in summary.items():
        shown = v if v is None or k in ("notes", "used_llm_fallback") else f"{v:.0%}"
        print(f"  {k:<18} {shown}")
    print('\nTell Claude: "voice done" and paste the VOICE CHECK lines.')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
