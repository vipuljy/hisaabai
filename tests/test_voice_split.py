"""Live voice notes over 30 s (bot/voice.py): cut at pauses into <= 29.5 s pieces for Sarvam, joined, then the
normal parser; notes over 2 minutes get a polite reply. Fake audio, fake Sarvam: no network."""

import asyncio
import itertools
from array import array

import pytest
from conftest import OWNER, _events, onboard

from bot import messages as M
from bot import voice as V

RATE = 16000
_ids = itertools.count(1)


def pcm(*parts):
    """parts: (seconds, loud?) -> 16-bit mono PCM: a square wave when loud, silence otherwise."""
    out = array("h")
    for seconds, loud in parts:
        n = int(seconds * RATE)
        out.extend(([3000, -3000] * (n // 2 + 1))[:n] if loud else [0] * n)
    return out.tobytes()


def secs(piece):
    return len(piece) / 2 / RATE


def test_cut_lands_in_the_pause():
    audio = pcm((27.0, True), (0.6, False), (20.0, True))
    pieces = V.split_at_pauses(audio, RATE)
    assert len(pieces) == 2 and 27.0 <= secs(pieces[0]) <= 27.6
    assert sum(map(secs, pieces)) == pytest.approx(47.6, abs=0.01)


def test_no_pause_still_cuts_under_the_limit_and_loses_nothing():
    audio = pcm((65.0, True))
    pieces = V.split_at_pauses(audio, RATE)
    assert len(pieces) == 3 and all(secs(p) <= V.MAX_PIECE_SECONDS for p in pieces)
    assert b"".join(pieces) == audio
    assert V.split_at_pauses(pcm((12.0, True)), RATE) == [pcm((12.0, True))]  # short notes: one piece


def test_transcribe_note_joins_pieces_and_counts_each_call():
    said = iter(["sharma traders ko", "5000", "diye"])
    sent, calls = [], []

    async def sarvam(audio):
        sent.append(audio)
        return next(said)
    text, info = asyncio.run(V.transcribe_note(sarvam, V.wav_bytes(pcm((70.0, True))), on_call=lambda: calls.append(1)))
    assert text == "sharma traders ko 5000 diye" and info == {"seconds": 70.0, "pieces": 3, "decoded": True}
    assert len(calls) == 3 and all(a[:4] == b"RIFF" for a in sent)


def test_note_over_two_minutes_is_refused_before_any_call():
    async def sarvam(audio):
        raise AssertionError("must not be called")
    with pytest.raises(V.NoteTooLong) as err:
        asyncio.run(V.transcribe_note(sarvam, V.wav_bytes(pcm((121.0, True)))))
    assert err.value.seconds == pytest.approx(121.0)


def test_sarvam_request_names_wav_pieces_as_wav(monkeypatch):
    seen = []

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, data, files):
            seen.append(files["file"][0::2])

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"transcript": " ok "}
            return R()
    monkeypatch.setattr(V.httpx, "AsyncClient", Client)
    t = V.SarvamTranscriber("fake-key")
    assert asyncio.run(t(V.wav_bytes(pcm((1.0, True))))) == "ok"
    asyncio.run(t(b"OggS fake opus"))
    assert seen == [("voice.wav", "audio/wav"), ("voice.ogg", "audio/ogg")]


# ---------------------------------------------------------------- the live bot
def voice_note(world, audio: bytes, said):
    async def download(media_id):
        return audio
    world.wa.download_media = download
    calls = []

    async def sarvam(a):
        calls.append(a)
        return next(said)
    world.bot.transcriber = sarvam
    world.run({"id": f"wamid.vsplit{next(_ids)}", "from": OWNER, "type": "audio", "audio": {"id": "aud"}})
    return calls


def test_bot_long_note_is_split_joined_and_understood(world):
    from conftest import photo, tap
    onboard(world)
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}  # a known supplier first
    world.run(photo())
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("ok:"))))
    calls = voice_note(world, V.wav_bytes(pcm((40.0, True), (0.5, False), (25.0, True))),
                       iter(["sharma traders ko", "2000", "diye"]))
    assert len(calls) == 3
    events = _events(world.store)
    assert [e["event"] for e in events].count("ai_call") >= 3
    got = next(e["props"] for e in reversed(events) if e["event"] == "voice_received")
    assert got["pieces"] == 3 and got["seconds"] == 65.5
    assert "2,000" in world.wa.last["body"] and world.wa.last["kind"] == "buttons"  # payment proposed, confirm button


def test_bot_note_over_two_minutes_gets_a_polite_reply(world):
    onboard(world)
    calls = voice_note(world, V.wav_bytes(pcm((130.0, True))), iter([]))
    assert calls == [] and world.wa.last["body"] == M.VOICE_TOO_LONG
    names = [e["event"] for e in _events(world.store)]
    assert "voice_too_long" in names and "ai_call" not in names
