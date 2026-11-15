"""Voice notes: speech to text (Sarvam), then 'is this a payment?'.

Step 1: Sarvam turns the WhatsApp voice note (OGG/Opus) into Roman-script Hinglish text. Sarvam's API takes at
        most 30 s per request, so the note is decoded (PyAV) to 16 kHz mono WAV and cut into pieces of at most
        29.5 s, at the quietest moment near each cut (a pause between words), and the texts are joined. Notes over
        2 minutes are not sent at all: the owner is asked for a shorter note. The step-11 eval uses the same code.
Step 2: the same text engine as typed messages (bot/entries.py: bills, payments, fixes, questions) tries first (free).
Step 3: only if the rules find nothing and there is no question word, a small Claude text call (llm_entry_parser)
        sorts the transcript into bill / payment / received / future entries. bot/entries.from_llm checks its answer
        (known kinds only, positive amounts, a supplier the owner actually said; an amount our own number reader cannot
        find in the transcript is flagged ⚠️ on the card).
The owner always confirms with a button before anything is saved.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from array import array

import httpx

from bot.extract import SMALL_CALL_MAX_TOKENS, norm_amount, parse_model_json

SARVAM_URL = "https://api.sarvam.ai/speech-to-text"
RATE = 16000              # Sarvam reads 16 kHz mono 16-bit WAV best
MAX_PIECE_SECONDS = 29.5  # Sarvam's limit is 30 s per request; keep a little margin
SEARCH_SECONDS = 5.0      # look for a pause in the last 5 s before each cut
FRAME_SECONDS = 0.1       # loudness is measured per 100 ms
MAX_NOTE_SECONDS = 120    # longer notes: ask for a shorter one (or typing); nothing is sent to Sarvam


class NoteTooLong(ValueError):
    """The voice note is longer than MAX_NOTE_SECONDS; .seconds says how long."""

    def __init__(self, seconds: float):
        super().__init__(f"{seconds:.0f} s")
        self.seconds = seconds


# ---------------------------------------------------------------- audio: decode, cut at pauses, WAV
def decode_audio(data: bytes) -> tuple[bytes, int]:
    """Any voice note (ogg/opus, m4a, mp3, wav...) -> (16-bit mono PCM, sample rate). Uses PyAV (pip install av);
    a 16-bit mono WAV also works without it. Raises if the bytes are not audio."""
    try:
        import av
    except ImportError:
        if not data.startswith(b"RIFF"):
            raise
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise ValueError("WAV must be 16-bit mono without PyAV")
            return w.readframes(w.getnframes()), w.getframerate()
    out = bytearray()
    with av.open(io.BytesIO(data)) as box:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in box.decode(audio=0):
            for f in resampler.resample(frame):
                out += bytes(f.planes[0])[: f.samples * 2]
        for f in resampler.resample(None):
            out += bytes(f.planes[0])[: f.samples * 2]
    return bytes(out), RATE


def _loudness(pcm: bytes, rate: int, start: int, end: int) -> list[tuple[float, int]]:
    """(mean square, frame start in samples) for each FRAME_SECONDS frame between two sample positions."""
    samples = array("h")
    samples.frombytes(pcm[start * 2:end * 2])
    step = max(1, int(rate * FRAME_SECONDS))
    out = []
    for i in range(0, len(samples) - step + 1, step):
        frame = samples[i:i + step]
        out.append((sum(s * s for s in frame) / step, start + i))
    return out


def split_at_pauses(pcm: bytes, rate: int, max_seconds: float = MAX_PIECE_SECONDS) -> list[bytes]:
    """Cut 16-bit mono PCM into pieces of at most max_seconds. Each cut goes in the middle of the quietest
    100 ms in the last SEARCH_SECONDS before the limit, so a word is rarely cut in half."""
    n, limit = len(pcm) // 2, int(max_seconds * rate)
    pieces, start = [], 0
    while n - start > limit:
        frames = _loudness(pcm, rate, start + limit - int(SEARCH_SECONDS * rate), start + limit)
        cut = start + limit
        if frames:
            quiet, at = min(frames, key=lambda f: (f[0], -f[1]))  # quietest; on a tie the latest (fewer pieces)
            cut = min(at + int(rate * FRAME_SECONDS) // 2, start + limit)
        pieces.append(pcm[start * 2:cut * 2])
        start = cut
    pieces.append(pcm[start * 2:])
    return [p for p in pieces if p] or [pcm]


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def transcribe_note(transcriber, audio: bytes, on_call=None, max_seconds: float = MAX_NOTE_SECONDS) -> tuple[str, dict]:
    """The live bot's voice step: decode, refuse notes over max_seconds (NoteTooLong, before any cost), cut into
    pieces of at most 29.5 s at pauses, transcribe each (on_call() before each request: the daily AI cap counts
    them), join. Audio that cannot be decoded is sent once as it is (Sarvam reads OGG itself)."""
    try:
        pcm, rate = await asyncio.to_thread(decode_audio, audio)  # CPU work off the event loop
    except Exception:
        if on_call:
            on_call()
        return (await transcriber(audio) or "").strip(), {"seconds": None, "pieces": 1, "decoded": False}
    seconds = len(pcm) / 2 / rate
    if seconds > max_seconds:
        raise NoteTooLong(seconds)
    texts = []
    pieces = await asyncio.to_thread(split_at_pauses, pcm, rate)
    for piece in pieces:
        if on_call:
            on_call()
        texts.append((await transcriber(wav_bytes(piece, rate)) or "").strip())
    return " ".join(t for t in texts if t), {"seconds": round(seconds, 1), "pieces": len(pieces), "decoded": True}


class SarvamTranscriber:
    def __init__(self, api_key: str, model: str = "saaras:v3", mode: str = "translit"):
        self.api_key = api_key
        self.model = model
        self.mode = mode  # translit = Roman script, matches our Hinglish text parser

    async def __call__(self, audio: bytes, filename: str | None = None, mime: str | None = None) -> str:
        """One request = at most 30 seconds of audio (Sarvam's limit for this API). WAV pieces are named as WAV."""
        if filename is None:
            filename, mime = ("voice.wav", "audio/wav") if audio[:4] == b"RIFF" else ("voice.ogg", "audio/ogg")
        data = {"model": self.model, "language_code": "hi-IN"}
        if self.model.startswith("saaras:v3"):
            data["mode"] = self.mode
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(SARVAM_URL, headers={"api-subscription-key": self.api_key},
                                     data=data, files={"file": (filename, audio, mime)})
        resp.raise_for_status()
        return (resp.json().get("transcript") or "").strip()


PAYMENT_PROMPT = """A small shop owner in India sent this voice note (transcribed, Hindi or Hinglish, any script):
"{transcript}"

Does it say the owner PAID money to a supplier? Reply with JSON only:
{{"is_payment": true, "amount": <number in rupees>, "supplier": "<supplier name in Latin letters>"}}
or {{"is_payment": false}}
Rules: spoken numbers count ("paanch hazaar" = 5000, "dhai sau" = 250). If amount or supplier is unclear, is_payment is false."""


def llm_payment_parser(transcript: str, client=None, model: str = "claude-haiku-5-5") -> tuple[int, str] | None:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    resp = client.messages.create(model=model, max_tokens=SMALL_CALL_MAX_TOKENS,
                                  messages=[{"role": "user", "content": PAYMENT_PROMPT.format(transcript=transcript[:500])}])
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return None  # cut off: never trust a half answer
    text = "".join(getattr(b, "text", "") for b in resp.content)
    try:
        data = parse_model_json(text)
    except (ValueError, json.JSONDecodeError):
        return None
    if not data.get("is_payment"):
        return None
    amount = norm_amount(data.get("amount"))
    supplier = str(data.get("supplier") or "").strip()
    if not amount or amount <= 0 or not supplier:
        return None
    return amount, supplier


ENTRY_PROMPT = """A small shop owner in India sent this voice note about their SUPPLIER ledger (transcribed; Hindi or
Hinglish, any script): "{transcript}"
Today: {today}. The shop's suppliers: {suppliers}.

List what the owner wants RECORDED. Types:
  bill      a supplier's bill / goods came on credit ("Sharma ka 8000 ka maal aaya")
  payment   the owner PAID a supplier ("Sharma ko paanch hazaar diye")
  refund    the supplier gave money BACK to the owner ("Verma se 2000 wapas aaye", "Sharma ne mujhe 500 diye")
  return    the owner sent goods BACK to the supplier / a credit note ("Gupta ko 500 ka maal wapas kiya")
  received  something came from a supplier but it is unclear if goods or money ("Verma se 2000 aaye")
  future    the owner WILL pay later, not paid yet ("Gupta ko 5000 dena hai")
Reply with JSON only:
{{"entries": [{{"type": "bill|payment|refund|return|received|future", "supplier": "<name as said, Latin letters>",
  "amount": <rupees>, "credit_days": <days of credit for a bill, or null>}}]}}
Rules: spoken numbers count ("paanch hazaar" = 5000, "dhai sau" = 250, "saade teen hazaar" = 3500). Never guess:
if the amount or the supplier is unclear, leave that entry out. A question or chatter: {{"entries": []}}."""


def llm_entry_parser(transcript: str, supplier_names: list[str], today, client=None,
                     model: str = "claude-haiku-5-5") -> dict | None:
    """Classifies only. bot/entries.from_llm validates the answer; the owner confirms every card."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    prompt = ENTRY_PROMPT.format(transcript=transcript[:600].replace('"', "'"), today=str(today),
                                 suppliers=json.dumps(supplier_names[:50], ensure_ascii=False))
    resp = client.messages.create(model=model, max_tokens=SMALL_CALL_MAX_TOKENS,
                                  messages=[{"role": "user", "content": prompt}])
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return None  # cut off: never trust a half answer
    try:
        return parse_model_json("".join(getattr(b, "text", "") for b in resp.content))
    except (ValueError, json.JSONDecodeError):
        return None
