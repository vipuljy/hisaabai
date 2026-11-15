"""Step 11 voice eval (src/11_voice_eval.py): no network. Fake Sarvam + fake Haiku, tiny generated WAV files."""

import csv
import importlib.util
import io
import wave
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 28)
SAID = {  # file stem -> what the fake Sarvam "hears"
    "n01": "sharma traders ko 5000 diye",
    "n02": "verma ko half lakh diye",                  # an amount the rules do not read ('half') -> Haiku fallback
    "n06": "वर्मा को पांच हज़ार दिए",                    # Hindi script: turned into Roman letters, read by the rules
    "n03": "kitna baaki hai",                          # a question, never a payment
    "n04": "aaj dukaan jaldi band karni hai",          # nothing
    "n05": "balaji ko 2000 bheje",                     # a supplier the fake shop does not have
}


def load():
    spec = importlib.util.spec_from_file_location("v11", ROOT / "src" / "11_voice_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wav(path: Path, seconds: float):
    """A WAV whose samples depend on the file name (identical audio would share one cache entry, by design)."""
    tone = (sum(path.stem.encode()) % 200 + 1).to_bytes(2, "little")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(tone * int(16000 * seconds))
    path.write_bytes(buf.getvalue())


class FakeSarvam:
    def __init__(self):
        self.calls = []

    async def __call__(self, audio, filename="voice.ogg", mime="audio/ogg"):
        self.calls.append(filename)
        stem = filename.rsplit("_", 1)[0]
        return SAID.get(stem, "hmm") if filename.endswith("_0.wav") else "aur kuch nahi"


class FakeHaiku:
    def __init__(self):
        self.calls = []

    def __call__(self, transcript, supplier_names, today):
        self.calls.append(transcript)
        if "half lakh" in transcript:
            return {"entries": [{"type": "payment", "supplier": "Verma", "amount": 50000}]}
        return {"entries": []}


def setup(tmp_path, n_extra=0, long_note=False):
    d = tmp_path / "data" / "voice_test"
    d.mkdir(parents=True)
    for stem in SAID:
        wav(d / f"{stem}.wav", 2)
    for k in range(n_extra):
        wav(d / f"x{k:02d}.wav", 1 + k / 100)  # different lengths = different content hashes
    if long_note:
        wav(d / "n99.wav", 65)
    return tmp_path


def rows(root):
    with open(root / "outputs" / "eval" / "voice_results.csv", newline="", encoding="utf-8-sig") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


def test_run_goes_through_the_live_bot_and_keeps_your_labels(tmp_path, capsys):
    v = load()
    root, sarvam, haiku = setup(tmp_path), FakeSarvam(), FakeHaiku()
    assert v.main([], root=root, transcriber=sarvam, entry_parser=haiku, today=TODAY) == 0
    got = rows(root)
    assert list(got["n01.wav"])[:len(v.COLUMNS)] == v.COLUMNS
    assert got["n01.wav"]["bot_did"] == "payment 5000 Sharma Traders" and got["n01.wav"]["route"] == "rules"
    assert got["n02.wav"]["route"] == "llm" and got["n02.wav"]["bot_did"] == "payment 50000 Verma Medical"
    assert got["n06.wav"]["route"] == "rules" and got["n06.wav"]["bot_did"] == "payment 5000 Verma Medical"
    assert got["n03.wav"]["route"] == "question" and got["n03.wav"]["bot_did"] == "question"
    assert got["n04.wav"]["bot_did"].startswith("nothing")
    assert got["n05.wav"]["bot_did"] == "payment 2000 new:Balaji"
    assert haiku.calls == ["verma ko half lakh diye", "aaj dukaan jaldi band karni hai"]  # only where rules found nothing
    assert "seconds of audio sent" in capsys.readouterr().out
    # you fill the truth; a re-run is free (Sarvam AND Haiku answers cached) and keeps it
    path = root / "outputs" / "eval" / "voice_results.csv"
    filled = list(got.values())
    truth = {"n01.wav": ("payment", "5000", "Sharma Traders"), "n02.wav": ("payment", "50,000", "Verma"),
             "n03.wav": ("question", "", ""), "n04.wav": ("chatter", "", ""), "n05.wav": ("payment", "2500", "Balaji"),
             "n06.wav": ("payment", "5000", "Verma")}
    for r in filled:
        r["true_intent"], r["true_amount"], r["true_supplier"] = truth[r["file"]]
    v._write_csv(path, filled)
    calls, hcalls = len(sarvam.calls), len(haiku.calls)
    assert v.main([], root=root, transcriber=sarvam, entry_parser=haiku, today=TODAY) == 0
    assert len(sarvam.calls) == calls and len(haiku.calls) == hcalls  # nothing sent again
    assert rows(root)["n05.wav"]["true_amount"] == "2500"
    capsys.readouterr()
    assert v.main(["--score"], root=root, today=TODAY) == 0
    out = capsys.readouterr().out
    import re
    counts = dict(m.groups() for m in re.finditer(r"^  (correct|partial|safe|WRONG)\s+(\d+)", out, re.M))
    assert counts == {"correct": "5", "partial": "0", "safe": "0", "WRONG": "1"}  # n05: the amount was 2500
    assert "n05.wav" in out and "BY INTENT" in out and "read by rules / by Haiku: 3 / 1" in out


def test_cost_guard_and_long_notes(tmp_path, capsys):
    v = load()
    root, sarvam = setup(tmp_path, n_extra=30, long_note=True), FakeSarvam()
    assert v.main(["--limit", "99"], root=root, transcriber=sarvam, entry_parser=FakeHaiku(), today=TODAY) == 0
    assert len(rows(root)) == v.MAX_FILES  # 37 files, at most 30 new per run
    assert "7 file(s) left for the next run" in capsys.readouterr().out
    assert v.main([], root=root, transcriber=sarvam, entry_parser=FakeHaiku(), today=TODAY) == 0
    got = rows(root)
    assert len(got) == 37 and got["n99.wav"]["over_30s"] == "yes"
    assert sum(c.startswith("n99_") for c in sarvam.calls) == 3  # 65 s -> 29 + 29 + 7
    assert got["n99.wav"]["transcript"].endswith("aur kuch nahi aur kuch nahi")


def test_score_needs_labels(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # never a real call from a test (bot.app loads .env)
    v = load()
    root = setup(tmp_path)
    assert v.main(["--score"], root=root) == 1
    v.main([], root=root, transcriber=FakeSarvam(), entry_parser=None, today=TODAY)
    assert rows(root)["n02.wav"]["route"] == "not understood"  # no key: no classifier, the owner gets an example
    assert v.main(["--score"], root=root) == 1
    assert "No rows labelled yet" in capsys.readouterr().out


def test_whatsapp_style_opus_note_is_decoded_to_16k_wav(tmp_path):
    from bot import voice as V
    av = __import__("pytest").importorskip("av")
    import numpy as np
    path = tmp_path / "note.ogg"
    with av.open(str(path), "w", format="ogg") as box:
        stream = box.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        t = np.arange(48000 * 3) / 48000
        pcm = (np.sin(2 * np.pi * 440 * t) * 8000).astype("int16").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = 48000
        for packet in stream.encode(frame):
            box.mux(packet)
        for packet in stream.encode(None):
            box.mux(packet)
    samples, rate = V.decode_audio(path.read_bytes())
    assert rate == 16000 and 2.8 < len(samples) / 2 / rate < 3.2
    assert len(V.split_at_pauses(samples, rate)) == 1


def test_haiku_fallback_has_room_to_think_and_drops_a_cut_off_answer():
    from types import SimpleNamespace as NS

    from bot.voice import llm_payment_parser

    class Client:
        def __init__(self, stop):
            self.stop, self.kw = stop, None
            self.messages = self

        def create(self, **kw):
            self.kw = kw
            return NS(stop_reason=self.stop, content=[NS(text='{"is_payment": true, "amount": 5000, "supplier": "verma"}')])
    ok = Client("end_turn")
    assert llm_payment_parser("verma ko paanch hazaar", client=ok) == (5000, "verma") and ok.kw["max_tokens"] >= 1000
    assert llm_payment_parser("verma ko paanch hazaar", client=Client("max_tokens")) is None


def test_entry_classifier_has_room_to_think_and_drops_a_cut_off_answer():
    from types import SimpleNamespace as NS

    from bot.voice import llm_entry_parser

    class Client:
        def __init__(self, stop):
            self.stop, self.kw = stop, None
            self.messages = self

        def create(self, **kw):
            self.kw = kw
            return NS(stop_reason=self.stop,
                      content=[NS(text='{"entries": [{"type": "bill", "supplier": "Om Steel", "amount": 7000}]}')])
    ok = Client("end_turn")
    got = llm_entry_parser("om steel ka saat hazaar ka bill", ["Gupta Agency"], TODAY, client=ok)
    assert got["entries"][0]["amount"] == 7000 and ok.kw["max_tokens"] >= 1000 and "Gupta Agency" in ok.kw["messages"][0]["content"]
    assert llm_entry_parser("x", [], TODAY, client=Client("max_tokens")) is None



# ---------------------------------------------------------------- multi, labels, too long
def test_multi_and_label_checks(tmp_path, capsys):
    v = load()
    path = tmp_path / "outputs" / "eval" / "voice_results.csv"
    base = {c: "" for c in v.COLUMNS}
    rows_ = [
        base | {"file": "a.ogg", "transcript": "Sharma Traders ko 2000 aur Gupta ko 1500 diye", "route": "rules",
                "true_intent": "payment+payment", "true_amount": "2000+1500", "true_supplier": "Sharma+Gupta"},
        base | {"file": "b.ogg", "transcript": "Gupta ko 5000 dena hai", "route": "rules",
                "true_intent": "future", "true_amount": "5000", "true_supplier": "Gupta"},
        base | {"file": "c.ogg", "transcript": "kapoor agency wala 8000 nahi 8500 tha", "route": "fix",
                "true_intent": "correction", "true_amount": "8500", "true_supplier": "Kapoor Agency"},
        base | {"file": "d.ogg", "route": v.TOO_LONG, "true_intent": "payment", "true_amount": "900",
                "true_supplier": "Mittal"},
        base | {"file": "e.ogg", "transcript": "x", "true_intent": "recieved", "true_amount": "5"},
        base | {"file": "f.ogg", "transcript": "x", "true_intent": "payment+bill", "true_amount": "5"},
    ]
    v._write_csv(path, rows_)
    assert v.main(["--score"], root=tmp_path, today=TODAY) == 0
    out = capsys.readouterr().out
    assert "VOICE EVAL: 4 labelled of 6 notes" in out
    import re
    counts = dict(m.groups() for m in re.finditer(r"^  (correct|partial|safe|WRONG)\s+(\d+)", out, re.M))
    assert counts == {"correct": "3", "partial": "0", "safe": "1", "WRONG": "0"}
    assert "e.ogg: true_intent 'recieved' is not one of" in out and "f.ogg: 2 intents but 1 amounts" in out


def test_note_over_two_minutes_is_not_sent(tmp_path):
    v = load()
    root, sarvam = setup(tmp_path), FakeSarvam()
    wav(root / "data" / "voice_test" / "n77.wav", 125)
    assert v.main([], root=root, transcriber=sarvam, entry_parser=FakeHaiku(), today=TODAY) == 0
    assert not any(c.startswith("n77_") for c in sarvam.calls)
    assert rows(root)["n77.wav"]["route"] == "too long" and rows(root)["n77.wav"]["bot_did"] == v.TOO_LONG
