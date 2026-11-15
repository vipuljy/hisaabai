"""The voice check script (src/05_voice_check.py), with a fake speech-to-text and a fake LLM fallback."""

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("voice_check", ROOT / "src" / "05_voice_check.py")
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)

SCRIPT = {r["id"]: r for r in vc.load_script(ROOT / "src" / "templates" / "voice_script.csv")}
WORDS = {"paanch hazaar": 5000, "dhai hazaar": 2500}


def fake_llm(transcript):
    for words, amount in WORDS.items():
        if words in transcript.lower():
            return amount, transcript.split(" ko ")[0]
    return None


def test_script_is_well_formed():
    assert len(SCRIPT) == 25
    assert {r["intent"] for r in SCRIPT.values()} == {"payment", "dues", "other"}
    assert all(r["amount"] and r["supplier"] for r in SCRIPT.values() if r["intent"] == "payment")


def test_scores_a_small_run(tmp_path, capsys):
    voice = tmp_path / "data" / "voice"
    voice.mkdir(parents=True)
    for vid in ("V01", "V02", "V03", "V04", "V10"):
        (voice / f"{vid}.ogg").write_bytes(vid.encode())

    async def fake_stt(audio):  # "hears" exactly what the script says, except V02 is misheard
        vid = audio.decode()
        return "Gupta Agency ko 2800 rupaye de diye" if vid == "V02" else SCRIPT[vid]["say_this"]

    assert vc.main([], root=tmp_path, transcriber=fake_stt, voice_parser=fake_llm) == 0
    rows = list(csv.DictReader((voice / "voice_results.csv").open(encoding="utf-8")))
    ok = {r["id"]: r["fully_ok"] == "True" for r in rows}
    assert ok == {"V01": True, "V02": False, "V03": True, "V04": True, "V10": True}
    summary = next(csv.DictReader((tmp_path / "outputs" / "voice" / "voice_summary.csv").open()))
    assert summary["notes"] == "5" and float(summary["fully_correct"]) == 0.8
    assert float(summary["amount_correct"]) == round(2 / 3, 3)  # V02 amount wrong
    assert "VOICE CHECK" in capsys.readouterr().out


def test_no_recordings(tmp_path):
    assert vc.main([], root=tmp_path, transcriber=lambda a: None) == 1
