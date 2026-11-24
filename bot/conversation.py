"""What the bot does with each incoming WhatsApp message.

Routing, in order:
  1. Unknown number        -> pilot is invite-only (or auto-register on the test number: PILOT_OPEN=1)
  2. Duplicate delivery    -> ignored (Meta can send the same message twice)
  3. Opted out             -> only START brings it back
  4. No consent yet        -> welcome + consent buttons; nothing is processed before "Haan"
  5. STOP                  -> opt out
  6. Button / list tap     -> confirm / edit / cancel a bill or payment card, pick a supplier, fix an entry,
                              proof photo answers (bot/entry_flow.py), question lists (q:, qs:, qp:, qd:, qr:)
  7. Photo                 -> within 30 min of a voice / typed bill: "is this its proof?"; else daily limits,
                              read the bill, own-sales-bill check, ask for anything missing, supplier step,
                              then "Sahi hai / Badlo / Cancel"
  8. Text, in order        -> answer to a pending question (field, supplier name, shop name), "naam: ...", menu
                              ("menu", "hisaab"), then the text engine (bot/entries.py): a reminder request ->
                              questions; a fix of a saved entry ("8000 nahi 8500", "last wala galat hai"); entries
                              (bill / payment / received / future / no verb, up to 3 per message); else a question
                              about the hisaab (bot/questions.py, answered from the database by bot/answers.py),
                              an unclear hisaab question (optional Haiku classifier, else the list menu), else decline
  9. Voice note            -> Sarvam transcript -> the same text engine; if the rules find nothing, the optional
                              Haiku classifier (bot/voice.py) may fill cards, which the owner still confirms
Every step logs an event, so the funnel, retention and experiment come straight from SQL.
Every AI call (bill photo, voice note) logs an "ai_call" event; the daily limits count those.
"""

from __future__ import annotations

import asyncio
import time
import contextvars
import logging
import re
import time as _time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bot import answers
from bot import entries as E
from bot import messages as M
from bot import policy as P
from bot import questions as Q
from bot import read_pdf
from bot import understand as U
from bot import wa_templates as W
from bot.voice import NoteTooLong, transcribe_note
from bot.entry_flow import EntryFlow
from bot import feedback_flow as F
from bot.feedback_flow import FeedbackFlow
from bot.extract import NORMALISERS, norm_amount, norm_date, norm_supplier, same_supplier
from bot.photos import LocalPhotos
from bot.store import DB_TIMER
from bot.whatsapp import last_error as wa_last_error

log = logging.getLogger("hisaabai")
IST = timezone(timedelta(hours=5, minutes=30))

REQUIRED = ["supplier_name", "total_amount"]
STOP_WORDS = {"stop", "band", "band karo", "unsubscribe", "ruko"}
START_WORDS = {"start", "shuru", "shuru karo", "haan", "chalu"}
YES_WORDS = {"haan", "ha", "yes", "ok", "shuru", "haan ji", "han", "haa", "shuru karo"}  # typed consent: whole message only
MENU_WORDS = {"hi", "hii", "hello", "helo", "namaste", "namaskar", "menu", "help", "madad", "?"}
# 23 Nov review: owners answer a card by typing, not tapping ('ok', 'haan'), and say thanks after a save. With ONE
# open card a yes-word saves it like the button; thanks and greetings get a short, warm reply (not "maaf kijiye").
CARD_YES_WORDS = {"haan", "ha", "haa", "han", "haan ji", "ha ji", "han ji", "ji haan", "ji ha", "ji", "yes", "ok", "okay",
                  "okk", "ok ji", "okay ji", "sahi", "sahi hai", "sahi h", "sahi he", "haan sahi hai", "ha sahi hai",
                  "theek hai", "thik hai", "theek h", "thik h", "thik he", "done", "save", "save karo", "save kro",
                  "save kar do", "kar do", "kardo", "👍", "✅", "👌"}
THANKS_WORDS = {"shukriya", "sukriya", "shukria", "dhanyavad", "dhanyawad", "dhanyavaad", "thank you", "thankyou",
                "thanks", "thanku", "thank u", "thnx", "thx", "tq", "ty", "🙏", "👍", "👌", "ok",
                "okay", "okk", "ok ji", "achha", "acha", "accha", "achha ji", "theek hai", "thik hai", "ji", "nice",
                "good", "badhiya", "badiya", "super", "very good", "thank you ji", "thanks ji", "shukriya ji"}
REACTION_YES = {"\U0001f44d", "\u2705", "\U0001f44c"}
GREET_WORDS = {"good morning", "gm", "good night", "gn", "good evening", "good afternoon", "kaise ho", "kaise hain",
               "kaise hai", "kya haal", "kya haal hai", "kya hal hai", "kya hal", "ram ram", "ram ram ji",
               "jai shree krishna", "jai shri krishna", "radhe radhe", "sat sri akal", "jai mata di", "salaam", "salam",
               "assalamualaikum", "jai hind", "namaste ji", "namaskar ji", "pranam"}
# 24 Nov: an owner may not want the tour, or may get lost in it
TOUR_SKIP_WORDS = {"baad mein", "baad me", "bad mein", "bad me", "baad main", "skip", "skip karo", "later", "abhi nahi",
                   "tour band", "tour nahi", "tour nahi chahiye", "nahi seekhna", "pata hai", "mujhe pata hai"}
GUIDE_WORDS = {"sikhao", "sikha do", "sikhaiye", "seekhna hai", "tour", "demo", "guide", "features", "kaise use kare",
               "kaise use karein", "kaise use karu", "kaise chalaye", "kaise chalayein", "kaise chalate hai",
               "kya kya kar sakte ho", "kya kar sakte ho", "kya kya hota hai", "kya kya kar sakta hai"}
_SKIN = re.compile("[\U0001F3FB-\U0001F3FF\uFE0F]")


def small_words(lower: str) -> str:
    """'Ok 👍🏻!!' -> 'ok 👍' style key for the word sets above: no skin tones, no repeated marks, single spaces."""
    t = _SKIN.sub("", lower)
    t = re.sub(r"([!.?])\1+", r"\1", t).strip(" .!?,")
    t = re.sub(r"\s+", " ", t)
    words = re.sub("[\U0001F44D\U0001F44C\U0001F64F\u2705]+", " ", t).strip(" .!?,")   # 'ok 👍' -> 'ok'
    return re.sub(r"\s+", " ", words) if words else re.sub(r"(.)\1+", r"\1", t.replace(" ", ""))   # '🙏🙏' -> '🙏'
HISAAB_WORDS = {"hisaab", "hisab", "hisaab dekhein", "hisaab dikhao", "hisab dikhao", "hisaab batao", "hisab batao"}
DAY_WORDS = {"aaj": 0, "today": 0, "aaj ki": 0, "kal": 1, "kal ki": 1, "yesterday": 1, "parso": 2, "parson": 2}
CANCEL_WORDS = {"cancel", "rehne do", "rehne de", "chhodo", "chodo", "chhod do", "mat karo", "kuch nahi", "nahi chahiye"}
SUPPLIER_SLOT = {"dues_supplier", "paid_period", "bills_period", "last_payment", "set_reminder"}
# 'mera data delete karo', 'DELETE', 'saara hisaab mita do' (whole message only: 'last entry delete karo' is a fix)
# the verb as owners type it: karo / kro / kar do / krdo / kar dijiye / karna / kijiye (16 Nov: 'kro' was missed)
_DEL_VERB = r"(?:\s+(?:k(?:a)?r(?:o|do|na|dena)?(?:\s+(?:do|dijiye|dena|dijie))?|kijiye|kijie|dijiye|do|please|plz))?"
DELETE_DATA_RE = re.compile(
    r"^(?:delete|delete (?:my |mera |all )?data|data delete" + _DEL_VERB + "|"
    r"(?:mera|meri|mere|sara|saara|sab|poora|pura|apna|saari)\s+(?:data|hisaab|hisab|khata|record)\s+"
    r"(?:delete|mita|mitao|mita do|hata|hatao|hata do|saaf)" + _DEL_VERB + ")$")
PROOF_RE = re.compile(r"\b(proof|pruf|saboot|sabut|praman|pramaan)\b")
SLOW_REPLY_MS = 5000
# Per-message reply timing (with DB_TIMER in bot/store.py): WhatsApp sends [count, seconds], and the "typing..." call
# started when the message arrived (it runs while the reply is being made, and is finished before the first send).
WA_TIMER: contextvars.ContextVar = contextvars.ContextVar("wa_timer", default=None)
TYPING: contextvars.ContextVar = contextvars.ContextVar("typing_task", default=None)
SLOW_TYPES = {"image", "audio", "document"}   # bill photos, voice notes, PDFs: replies of 4-10 s
DUES_RE = re.compile(r"\b(baaki|baki|bakaya|dues?|balance|kitna\s+dena|hisaab|hisab|outstanding)\b")
PAY_VERBS = (r"\b(de\s*diye|de\s*diya|diye|diya|di|paid|pay\s*kiye|pay\s*kiya|pay\s*kar\s*diya|bheje|bheja|"
             r"bhej\s*diye|bhej\s*diya|chukaye|chukaya|jama\s*kiye|jama\s*kiya|transfer\s*kiye|transfer\s*kiya)\b")
PAY_FILLER = r"\b(ko|ji|ne|maine|mene|humne|aaj|kal|rs|rupaye|rupay|rupees|ka|ki|ke|payment|cash|upi|online|se|total)\b"


def parse_payment(text: str) -> tuple[int, str] | None:
    """'Sharma ji ko 5,000 diye' -> (5000, 'sharma'). None if it is not a payment message."""
    t = text.lower()
    if not re.search(PAY_VERBS, t):
        return None
    m = re.search(r"(?:₹|rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)", t)
    if not m:
        return None
    amount = norm_amount(m.group(1))
    if not amount or amount <= 0:
        return None
    rest = t[: m.start()] + " " + t[m.end():]
    rest = re.sub(PAY_VERBS, " ", rest)
    rest = re.sub(r"[₹/\-.,!?]", " ", rest)
    rest = re.sub(PAY_FILLER, " ", rest)
    name = re.sub(r"\s+", " ", rest).strip()
    return amount, name


TYPED_BILL_RE = re.compile(
    r"^(?:bill\s*[:\-]?\s*)?(?P<name>[a-z][a-z .&'/-]*?)\s*[:\-,]?\s*(?:(?:₹|\brs\.?)\s*)?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)"
    r"(?:\s*(?:/-|rs|rupaye|rupees))?(?:\s+(?P<date>\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?))?\s*$", re.I)
NAME_CMD_RE = re.compile(r"^(?:naam|name|dukaan ka naam|dukan ka naam|shop name)\s*[:\-]\s*(.+)$", re.I)


def parse_typed_bill(text: str) -> dict | None:
    """'Sharma Traders 4580' or 'bill: Sharma Traders ₹4,580 05/10' -> bill fields. None if it is not one."""
    m = TYPED_BILL_RE.match(text.strip())
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip(" .-/")
    words = name.lower().split()
    if not words or not norm_supplier(name) or words[-1] in {"ko", "se", "ne", "ka", "ki"}:
        return None  # 'Sharma ko 5000' is a payment with the verb missing, not a bill
    if name.lower() in MENU_WORDS | START_WORDS | STOP_WORDS or sum(c.isalpha() for c in name) < 2:
        return None
    amount = norm_amount(m.group("amt"))
    if not amount or amount <= 0:
        return None
    return {"is_bill": True, "supplier_name": name, "total_amount": amount,
            "bill_date": norm_date(m.group("date")) if m.group("date") else None,
            "invoice_number": None, "due_date": None, "unsure_fields": []}


QUESTION_WORDS = {"kya", "kaun", "kab", "kaise", "kitna", "kitne", "kyun", "kyon", "kahan", "what", "who", "how",
                  "when", "why", "where"}


def looks_like_shop_name(text: str) -> bool:
    """'Gupta General Store' yes; 'aaj cricket match kaun jeetega?' no."""
    words = text.lower().split()
    return (0 < len(words) <= 6 and len(text) <= 60 and "?" not in text
            and not QUESTION_WORDS & set(words) and sum(c.isalpha() for c in text) >= 2)


@dataclass
class Limits:
    """Cost and abuse guards. Each bill photo or voice note is one AI call."""
    per_shop_per_day: int = 60    # a busy shop gets 10-30 supplier bills a day
    total_per_day: int = 1000     # whole bot; at roughly ₹0.5 a call that caps a bad day near ₹500
    max_image_mb: float = 8.0
    max_pdf_mb: float = 10.0


def match_supplier(name: str, suppliers: list[dict]) -> dict | None:
    """The one supplier this name means, by bot.extract.same_supplier (the same rule the eval scores with):
    exact, one name's words at the start of the other ('sharma' ~ 'sharma traders', a city appended), or a
    near-identical spelling (ratio >= 0.9). 'Sharma Traders' never matches 'Verma Traders' (0.82).
    None if nothing matches, or if two suppliers match equally ('sharma' with two Sharma suppliers)."""
    target = norm_supplier(name)
    if not target or not suppliers:
        return None
    exact = [s for s in suppliers if s["name_norm"] == target]
    if len(exact) == 1:
        return exact[0]
    hits = [s for s in suppliers if same_supplier(target, s["name_norm"])]
    return hits[0] if len(hits) == 1 else None


def _clean_text(msg: dict) -> str:
    return (msg.get("text") or {}).get("body", "").strip()


class Bot(EntryFlow, FeedbackFlow):
    def __init__(self, store, wa, extractor, pilot_open: bool = False, image_dir: Path | str = "data/raw/bills",
                 transcriber=None, voice_parser=None, photos=None, limits: Limits | None = None,
                 question_llm=None, today_fn=None, entry_parser=None, timing_events: bool = False,
                 feedback_prompts: bool = False, coach: bool = False):
        self.store = store
        # 24 Nov: the first-day tour (photo, voice, a question) and one tip at a time after it. Live bot and simulator
        # only, like feedback_prompts, so flow tests keep their exact message order.
        self.coach = coach
        self._handled = 0   # messages handled by this process: the first one after a restart or a sleep is timed
        self._asked: dict[str, str | None] = {}   # per shop: the last thing sent was a "card" or a "question"
        # The bot ASKS for feedback (a rating after the 5th entry and on day 10; an offer after 2 cancels or
        # frustrated words) only where this is on: the live bot and the simulator. Owner-started feedback always works.
        self.feedback_prompts = feedback_prompts
        self.timing_events = timing_events  # the live bot logs a 'reply_timed' event per reply (the health report)
        self.wa = wa
        self.extractor = extractor  # callable(image_bytes) -> bot.extract.Extraction
        self.transcriber = transcriber  # async callable(audio_bytes) -> transcript, or None (voice off)
        self.voice_parser = voice_parser  # old: callable(transcript) -> (amount, supplier) or None (payments only)
        self.entry_parser = entry_parser  # callable(transcript, supplier_names, today) -> dict (bot/voice.py), or None
        self.pilot_open = pilot_open
        self.photos = photos or LocalPhotos(image_dir)  # bot.photos: local folder or private Supabase bucket
        self.limits = limits or Limits()
        self.question_llm = question_llm  # callable(text, supplier_names, today) -> dict, or None (rules + menu only)
        self.today_fn = today_fn  # tests can pin "today"; the bot uses the IST date

    def _today(self) -> date:
        return self.today_fn() if self.today_fn else datetime.now(IST).date()

    # ------------------------------------------------------------ sending (always recorded)
    async def _typing_done(self):
        """The typing call must reach WhatsApp before the reply, or the dots would show after it."""
        task = TYPING.get()
        if task is not None:
            TYPING.set(None)
            try:
                await asyncio.wait_for(task, timeout=2)
            except Exception:  # noqa: BLE001  a courtesy only: never blocks a reply
                pass

    async def _timed_send(self, send, *args, **kwargs):
        await self._typing_done()
        started = _time.perf_counter()
        try:
            return await send(*args, **kwargs)
        finally:
            timer = WA_TIMER.get()
            if timer is not None:
                timer[0] += 1
                timer[1] += _time.perf_counter() - started

    async def _text(self, shop, body):
        self._asked[str(shop["id"])] = None
        mid = await self._timed_send(self.wa.send_text, shop["wa_id"], body)
        self.store.record_message(shop["id"], "out", mid, "text", body)
        if mid is None:
            self._send_failed(shop, "text")

    async def _buttons(self, shop, body, buttons, header=None, footer=None):
        extra = {k: v for k, v in (("header", header), ("footer", footer)) if v}
        # what the owner is looking at last: a card (its first button is Sahi hai) or another question (24 Nov)
        self._asked[str(shop["id"])] = "card" if buttons and buttons[0][1] == M.BTN_OK else "question"
        mid = await self._timed_send(self.wa.send_buttons, shop["wa_id"], body, buttons, **extra)
        self.store.record_message(shop["id"], "out", mid, "interactive", body)
        if mid is None:
            self._send_failed(shop, "buttons")

    async def _list(self, shop, body, button, rows):
        self._asked[str(shop["id"])] = "question"
        mid = await self._timed_send(self.wa.send_list, shop["wa_id"], body, button, rows)
        self.store.record_message(shop["id"], "out", mid, "interactive", body)
        if mid is None:
            self._send_failed(shop, "list")

    def _send_failed(self, shop, kind: str):
        """Meta refused a reply: keep its error code, so the health report says why (21 Nov: '1 failed', no reason)."""
        err = wa_last_error() or {}
        self.store.log_event(shop["id"], "send_failed", {"kind": kind, "code": err.get("code"), "status": err.get("status")})

    async def on_delivery_failed(self, status: dict):
        """Meta accepted a message but could not deliver it (a 'failed' status webhook). Logged with the error code
        only: never the number or the text."""
        shop = self.store.get_shop_by_wa(str(status.get("recipient_id") or ""))
        if shop is None:
            return
        errors = status.get("errors") or [{}]
        code = errors[0].get("code") if isinstance(errors[0], dict) else None
        self.store.log_event(shop["id"], "delivery_failed", {"code": code if isinstance(code, int) else None})

    async def _show_menu(self, shop, body=M.MENU):
        await self._list(shop, body, M.MENU_BUTTON, [(f"q:{i}", t, d) for i, t, d in M.INTENT_ROWS]
                         + [("fb:start", M.FB_ROW_TITLE, M.FB_ROW_DESC)])

    def _set_state(self, shop, state: dict):
        shop["state"] = state
        self.store.update_shop(shop["id"], state=state)

    # ------------------------------------------------------------ entry point
    async def handle(self, msg: dict) -> None:
        start = _time.perf_counter()
        db, wa = [0, 0.0], [0, 0.0]
        tokens = (DB_TIMER.set(db), WA_TIMER.set(wa), TYPING.set(None))
        try:
            await self._handle(msg)
        finally:
            await self._typing_done()
            ms = int((_time.perf_counter() - start) * 1000)
            DB_TIMER.reset(tokens[0])
            WA_TIMER.reset(tokens[1])
            TYPING.reset(tokens[2])
            split = {"ms": ms, "type": msg.get("type", "?"), "db_q": db[0], "db_ms": int(db[1] * 1000),
                     "wa_n": wa[0], "wa_ms": int(wa[1] * 1000)}
            split["other_ms"] = max(0, ms - split["db_ms"] - split["wa_ms"])   # AI reading, voice, Python
            split["first"] = self._handled == 0   # first message since this process started (a cold start)
            self._handled += 1
            # the reply time (WhatsApp sends included), and where it went: fix the biggest part first
            log.info("handled %s in %d ms (database %d queries %d ms, whatsapp %d sends %d ms, other %d ms)",
                     split["type"], ms, split["db_q"], split["db_ms"], split["wa_n"], split["wa_ms"], split["other_ms"])
            if split["wa_n"] and self.timing_events:   # a reply's time goes into the daily health report
                self.store.log_event(None, "reply_timed", split)
            if ms >= SLOW_REPLY_MS:
                self.store.log_event(None, "slow_reply", split)

    async def _handle(self, msg: dict) -> None:
        if msg.get("type") == "button":   # a quick-reply tap on a template (reminder, Sunday summary)
            label = (msg.get("button") or {}).get("text", "")
            msg = {**msg, "type": "text", "text": {"body": W.button_as_text(label)}}
        wa_id = msg.get("from", "")
        mtype = msg.get("type", "unknown")
        text = _clean_text(msg)
        media_id = (msg.get(mtype) or {}).get("id") if mtype in ("image", "audio", "document") else None
        shop = self.store.get_shop_by_wa(wa_id)

        if shop is None and self.pilot_open:
            # 25 Nov: open sign-up. Anyone who says hi gets a W code (self-signup): counted apart from the shops Vipul
            # recruited (S) and from test phones (T); they still see the welcome and must tap Shuru karein (consent).
            shop = self.store.create_shop(wa_id, prefix="W")
            self.store.log_event(shop["id"], "self_signup", {"from_suffix": wa_id[-4:]})
        if not self.store.record_message(shop["id"] if shop else None, "in", msg.get("id"), mtype, text or None, media_id):
            return  # duplicate delivery
        if shop is None:
            self.store.log_event(None, "unknown_sender", {"from_suffix": wa_id[-4:]})
            await self.wa.send_text(wa_id, M.NOT_IN_PILOT)
            return
        typing = getattr(self.wa, "typing", None)
        if typing and msg.get("id") and mtype in SLOW_TYPES:
            # "typing..." only where the reply is slow (AI reading, voice). Measured 16 Nov: Meta's typing call takes
            # 0.7-1.2 s, so on a text reply (about 2 s) waiting for it made the reply up to 0.9 s later.
            TYPING.set(asyncio.create_task(typing(msg["id"])))
        shop["state"] = shop.get("state") or {}
        shop["_memo"] = {}   # per-message query cache (bot/entry_flow.py: _memo / _forget)
        # The WhatsApp profile name comes with every message. It is used only to SUGGEST the shop's name (one tap)
        # and is never stored: data minimisation, and it may be a person's own name.
        shop["profile_name"] = re.sub(r"\s+", " ", str(msg.get("_profile_name") or "")).strip()[:60]

        reply = msg.get("interactive") or {}
        button = ((reply.get("button_reply") or reply.get("list_reply") or {}).get("id")
                  if mtype == "interactive" else None)
        lower = text.lower().strip(" .!")
        command = U.to_roman(lower).lower().strip(" .!") if U.has_devanagari(lower) else lower

        if button and button.startswith("delall_"):
            await self._on_delete_button(shop, button)
            return
        if DELETE_DATA_RE.match(command):
            await self._ask_delete(shop)
            return

        if shop["status"] == "opted_out":
            if command in START_WORDS:
                self.store.update_shop(shop["id"], status="active")
                self.store.log_event(shop["id"], "opt_back_in", {})
                await self._text(shop, M.WELCOME_BACK)
            else:
                await self._text(shop, M.OPTED_OUT_HINT)
            return

        if shop.get("consent_at") is None:
            await self._consent_flow(shop, button, text)
            return

        if command in STOP_WORDS:
            self.store.update_shop(shop["id"], status="opted_out", state={})
            self.store.log_event(shop["id"], "opt_out", {})
            await self._text(shop, M.OPTED_OUT)
            return

        if mtype in ("image", "document") and shop["state"].get("await") == "feedback":
            self._set_state(shop, {})   # a photo while feedback was awaited is still read as a bill
        if button:
            await self._on_button(shop, button)
        elif mtype == "image":
            await self._on_photo(shop, msg.get("id"), media_id)
        elif mtype == "document":
            await self._on_document(shop, msg.get("id"), msg.get("document") or {})
        elif mtype == "audio":
            await self._on_voice(shop, media_id)
        elif text:
            await self._on_text(shop, text, lower)
        elif mtype == "reaction":   # 24 Nov: a 👍 on a card is a yes; any other reaction is not answered
            emoji = small_words(((msg.get("reaction") or {}).get("emoji") or ""))
            self.store.log_event(shop["id"], "reaction", {"yes": emoji in REACTION_YES})
            if emoji in REACTION_YES:
                await self._typed_yes(shop, shop.get("state") or {})
        elif mtype == "sticker":    # a good-morning sticker: a short thanks, not "maaf kijiye"
            self.store.log_event(shop["id"], "small_talk", {"kind": "sticker"})
            await self._text(shop, M.THANKS_REPLY)
        else:
            self.store.log_event(shop["id"], "off_topic", {"type": mtype})
            await self._text(shop, M.OFF_TOPIC)

    # ------------------------------------------------------------ consent
    async def _consent_flow(self, shop, button, text=""):
        """Full welcome only the first time; after that a short nudge with the same 2 buttons.
        Typed 'haan' / 'yes' / 'ok' ... counts as yes only when it is the WHOLE message."""
        typed_yes = button is None and _yes_only(text)
        if button == "consent_yes" or typed_yes:
            now = datetime.now(timezone.utc)
            self.store.update_shop(shop["id"], consent_at=now, onboarded_at=now, status="active")
            self.store.log_event(shop["id"], "opt_in", {"via": "typed" if typed_yes else "button"})
            self._set_state(shop, {})
            if self.coach:   # 24 Nov: a 3-step tour, so every owner tries photo, voice and a question on day 1
                self.store.log_event(shop["id"], "tour_started", {"v": 1})
                await self._text(shop, M.TOUR_1)
            else:
                await self._text(shop, M.READY)   # no questions at signup: the shop's name is asked after the first entry
        elif button == "consent_no":
            self.store.log_event(shop["id"], "consent_declined", {})
            await self._text(shop, M.CONSENT_DECLINED)
        elif button == "consent_example":
            self.store.log_event(shop["id"], "example_viewed", {})
            await self._buttons(shop, M.EXAMPLE, [("consent_yes", M.BTN_CONSENT_YES), ("consent_no", M.BTN_CONSENT_NO)])
        else:
            if (shop.get("state") or {}).get("welcome_sent"):
                await self._buttons(shop, M.CONSENT_NUDGE, [("consent_yes", M.BTN_CONSENT_YES),
                                                            ("consent_no", M.BTN_CONSENT_NO)])
            else:
                self._set_state(shop, {"welcome_sent": True})
                await self._buttons(shop, M.WELCOME, [("consent_yes", M.BTN_CONSENT_YES),
                                                      ("consent_example", M.BTN_CONSENT_EXAMPLE),
                                                      ("consent_no", M.BTN_CONSENT_NO)],
                                    header=M.WELCOME_HEADER, footer=M.WELCOME_FOOTER)

    # ------------------------------------------------------------ "mera data delete karo"
    async def _ask_delete(self, shop):
        counts = self.store.shop_counts(shop["id"])
        tok = uuid.uuid4().hex[:8]
        self.store.log_event(shop["id"], "delete_asked", {k: int(v) for k, v in counts.items()})
        await self._buttons(shop, M.delete_ask(counts), [(f"delall_yes:{tok}", M.BTN_DELETE_YES),
                                                         (f"delall_no:{tok}", M.BTN_DELETE_NO)])

    async def _on_delete_button(self, shop, button):
        kind, _, tok = button.partition(":")
        if kind == "delall_no":
            self.store.log_event(shop["id"], "delete_declined", {})
            await self._text(shop, M.DELETE_KEPT)
            return
        if not self._once(shop, f"delall:{tok}"):
            await self._text(shop, M.ENTRY_GONE)
            return
        photos_ok = True
        try:   # photos first: if this fails the rows still go, and the failure is logged for a manual clean-up
            await asyncio.to_thread(self.photos.delete_shop, shop["shop_code"])
        except Exception:
            log.exception("photo delete failed for a shop that asked to delete its data")
            photos_ok = False
        self.store.delete_shop(shop["shop_code"])   # every row of the shop (bills, payments, events, messages...)
        self.store.log_event(None, "shop_data_deleted", {"photos_ok": photos_ok})   # no shop id: nothing to link
        await self._timed_send(self.wa.send_text, shop["wa_id"], M.DELETED_ALL)

    # ------------------------------------------------------------ bills
    # ------------------------------------------------------------ limits
    async def _ai_allowed(self, shop) -> bool:
        since = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.store.count_events("ai_call", since, shop["id"]) >= self.limits.per_shop_per_day:
            self.store.log_event(shop["id"], "limit_hit", {"scope": "shop"})
            await self._text(shop, M.SHOP_DAILY_LIMIT)
            return False
        total = self.store.count_events("ai_call", since)
        if total >= self.limits.total_per_day:
            log.error("Daily AI limit reached (%s calls). If this is real use, raise AI_CALLS_PER_DAY in .env", total)
            self.store.log_event(shop["id"], "limit_hit", {"scope": "all"})
            await self._text(shop, M.BUSY)
            return False
        return True

    def _looks_like_own_bill(self, shop, supplier_name) -> bool:
        """The 'supplier' on the bill is the shop itself: probably a sales bill it gave a customer."""
        own = norm_supplier(shop.get("shop_name"))
        if not own or not supplier_name:
            return False
        return same_supplier(supplier_name, own)

    async def _save_shop_name(self, shop, name, via="typed"):
        name = re.sub(r"\s+", " ", name).strip()[:60]
        if not norm_supplier(name):
            await self._text(shop, M.MENU)
            return
        self.store.update_shop(shop["id"], shop_name=name)
        shop["shop_name"] = name
        self.store.log_event(shop["id"], "shop_name_set", {"via": via})
        await self._text(shop, M.shop_name_saved(name))

    def _suggested_name(self, shop) -> str | None:
        """The WhatsApp profile name, if it reads like a shop's name ('Ramesh Kirana' yes; '🙂', 'Papa', 'Rahul' no)."""
        name = (shop.get("profile_name") or "").strip()
        words = name.split()
        if not name or len(words) < 2 or not looks_like_shop_name(name) or not norm_supplier(name):
            return None
        if sum(c.isalpha() for c in name) < 4 or re.search(r"\d{5,}", name):
            return None
        return name

    async def _maybe_ask_shop_name(self, shop):
        """After the first saved entry, once per shop: confirm the shop's name in one tap (or type it, or skip).
        Never at signup: asking for it before the owner has seen any value was a needless first hurdle."""
        if shop.get("shop_name"):
            return False
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        if self.store.pending_count(shop["id"], since):
            return False   # other cards from the same message still wait for a tap: one question at a time
        if not self._once(shop, "ask_shop_name"):
            return False
        self.store.log_event(shop["id"], "shop_name_asked", {"suggested": self._suggested_name(shop) is not None})
        name = self._suggested_name(shop)
        if name:
            await self._buttons(shop, M.shop_name_check(name), [("sn_yes", M.BTN_SN_YES), ("sn_edit", M.BTN_SN_EDIT),
                                                                ("sn_skip", M.BTN_SN_SKIP)])
        else:   # the typed answer is awaited only after a tap: an untapped question must not swallow the next entry
            await self._buttons(shop, M.ASK_SHOP_NAME, [("sn_edit", M.BTN_SN_TYPE), ("sn_skip", M.BTN_SN_SKIP)])
        return True

    async def _on_shop_name_button(self, shop, kind):
        if shop.get("shop_name"):   # an old tap after the name was already saved
            await self._text(shop, M.shop_name_known(shop["shop_name"]))
            return
        name = self._suggested_name(shop)
        if kind == "sn_yes" and name:
            await self._save_shop_name(shop, name, via="profile")
        elif kind in ("sn_yes", "sn_edit"):
            self._set_state(shop, {"await": "shop_name"})
            await self._text(shop, M.ASK_SHOP_NAME_TYPE)
        else:
            if (shop.get("state") or {}).get("await") == "shop_name":
                self._set_state(shop, {})
            self.store.log_event(shop["id"], "shop_name_skipped", {})
            await self._text(shop, M.SHOP_NAME_SKIPPED)

    # ------------------------------------------------------------ bills
    async def _on_photo(self, shop, message_id, media_id):
        # Read first, ask after: a selfie gets "not a bill", a new bill gets its card, and only a photo that matches a
        # saved voice / typed bill gets "is this its proof?" (bot/entry_flow.py: _proof_match)
        await self._process_photo(shop, media_id)

    async def _process_photo(self, shop, media_id):
        if not await self._ai_allowed(shop):
            return
        t_download = time.perf_counter()
        download = asyncio.create_task(self.wa.download_media(media_id))   # starts while 'padh raha hoon' is sent
        await self._text(shop, M.READING)
        try:
            image = await download
        except Exception as err:
            log.exception("photo download failed")
            self.store.log_event(shop["id"], "extract_failed", {"error": type(err).__name__})
            await self._text(shop, M.READ_FAILED)
            return
        if len(image) > self.limits.max_image_mb * 1024 * 1024:
            self.store.log_event(shop["id"], "photo_too_big", {"mb": round(len(image) / 1048576, 1)})
            await self._text(shop, M.PHOTO_TOO_BIG)
            return
        await self._read_image_bill(shop, image, {"source": "photo"},
                                    download_ms=int((time.perf_counter() - t_download) * 1000))

    async def _read_image_bill(self, shop, image: bytes, props: dict, download_ms: int | None = None):
        """A bill image (a photo, or a PDF page) -> the read policy (Haiku, checks, Sonnet if needed) -> a bill."""
        # The photo is stored WHILE the AI reads it (it was one after the other: storage time was added to every bill)
        save = asyncio.create_task(self._save_photo(shop, image))
        t_read = time.perf_counter()
        try:
            self.store.log_event(shop["id"], "ai_call", {"kind": "bill"})
            if hasattr(self.extractor, "read"):  # bot.policy.PolicyReader: Haiku, checks, Sonnet if a check fails
                allow = await self._ai_quietly_allowed(shop)  # room for one more AI call today?
                ex = await asyncio.to_thread(self.extractor.read, image, allow)
            else:
                ex = await asyncio.to_thread(self.extractor, image)
            for _ in (ex.extra or {}).get("calls", [])[1:]:
                self.store.log_event(shop["id"], "ai_call", {"kind": "bill_escalation"})  # daily caps count it
        except Exception as err:  # network, model or image problems: never crash, tell the owner
            log.exception("bill reading failed")
            self.store.log_event(shop["id"], "extract_failed", {"error": type(err).__name__})
            await save
            await self._text(shop, M.READ_FAILED)
            return
        path = await save
        # Where a photo's wait goes (20 Nov: owners said "time jyada lagta h"): download, AI reading, Sonnet or not
        # api_ms = time inside the AI service (Haiku + Sonnet); read_ms - api_ms = our side (client, image prep, checks)
        self.store.log_event(shop["id"], "photo_timed", {
            "download_ms": download_ms, "read_ms": int((time.perf_counter() - t_read) * 1000),
            "api_ms": ex.latency_ms, "think_tok": ex.thinking_tokens, "effort": getattr(ex, "effort", None),
            "escalated": len((ex.extra or {}).get("calls", [])) > 1, "kb": len(image) // 1024,
            "source": props.get("source")})
        if ex.error:
            self.store.log_event(shop["id"], "extract_failed", {"error": ex.error[:80]})
        if not ex.fields.get("is_bill", True):
            self.store.log_event(shop["id"], "not_a_bill", {})
            await self._text(shop, M.NOT_A_BILL)
            return
        extra = ex.extra or {}
        await self._new_bill(shop, ex.fields, {
            "image_path": path, "model": ex.model, "prompt_version": ex.prompt_version, "source": props.get("source"),
            "input_tokens": ex.input_tokens, "output_tokens": ex.output_tokens, "latency_ms": ex.latency_ms},
            props | {"latency_ms": ex.latency_ms, "policy": extra.get("policy"),
                     "escalated": len(extra.get("calls", [])) > 1, "reasons": extra.get("reasons", []),
                     "asked": extra.get("asked", [])})

    async def _save_photo(self, shop, image: bytes):
        try:
            return await asyncio.to_thread(self.photos.save, shop["shop_code"], image)
        except Exception:  # storage down: still read the bill, just keep no copy
            log.exception("photo storage failed")
            return None

    async def _new_bill(self, shop, fields, meta, props):
        """Save the reading as a pending bill, then the usual steps: own-bill check, missing fields, confirm."""
        bill = self.store.create_bill(shop["id"], fields, meta)
        self.store.log_event(shop["id"], "bill_received", {
            "bill_id": bill["id"], "unsure": bill["unsure"],
            "missing": [f for f in REQUIRED if bill[_col(f)] is None]} | props)
        if self._looks_like_own_bill(shop, bill["supplier_name_raw"]):
            self.store.log_event(shop["id"], "own_bill_suspected", {"bill_id": bill["id"]})
            await self._buttons(shop, M.own_bill_check(bill["supplier_name_raw"]),
                                [(f"own_yes:{bill['id']}", M.BTN_OWN_YES), (f"own_no:{bill['id']}", M.BTN_OWN_NO)])
            return
        cand = self._proof_match(shop, bill)
        if cand is not None:   # looks like the bill the owner already said / typed: proof, not a second bill
            await self._ask_proof_read(shop, cand, bill)
            return
        await self._next_step(shop, bill, missing_from_ai=True)

    async def _on_document(self, shop, message_id, doc: dict):
        """A file sent as a WhatsApp document. An image -> the photo flow. A PDF: a text PDF with a trusted total is
        read free (no AI call); a scanned PDF, or one whose total is missing / doubtful, goes through the photo flow
        with the page image (multi-page scanned: the last page, where totals are printed)."""
        mime = (doc.get("mime_type") or "").lower()
        name = (doc.get("filename") or "").lower()
        if mime.startswith("image/"):
            await self._on_photo(shop, message_id, doc.get("id"))
            return
        if mime != "application/pdf" and not name.endswith(".pdf"):
            self.store.log_event(shop["id"], "pdf_rejected", {"reason": "not_pdf", "mime": mime[:40]})
            await self._text(shop, M.DOC_NOT_SUPPORTED)
            return
        await self._text(shop, M.READING_PDF)
        try:
            data = await self.wa.download_media(doc.get("id"))
        except Exception as err:
            log.exception("pdf download failed")
            self.store.log_event(shop["id"], "extract_failed", {"error": type(err).__name__})
            await self._text(shop, M.READ_FAILED)
            return
        if len(data) > self.limits.max_pdf_mb * 1024 * 1024:
            self.store.log_event(shop["id"], "pdf_rejected", {"reason": "too_big", "mb": round(len(data) / 1048576, 1)})
            await self._text(shop, M.PDF_TOO_BIG)
            return
        try:
            if not data.startswith(b"%PDF"):
                raise ValueError("not a PDF file")
            pdf = await asyncio.to_thread(read_pdf.read_whatsapp_pdf, data, self._today())
        except read_pdf.PdfTooBig as err:
            self.store.log_event(shop["id"], "pdf_rejected", {"reason": "too_many_pages", "pages": err.args[0]})
            await self._text(shop, M.PDF_TOO_BIG)
            return
        except Exception as err:  # broken file, password, unsupported
            self.store.log_event(shop["id"], "pdf_rejected", {"reason": "unreadable", "error": type(err).__name__})
            await self._text(shop, M.PDF_FAILED)
            return
        if pdf["route"] == "none":   # 24 Nov: a quotation / proforma / rate list is not a bill: nothing is saved
            self.store.log_event(shop["id"], "pdf_rejected", {"reason": pdf["reason"], "pages": pdf["pages"]})
            await self._text(shop, M.PDF_NOT_A_BILL)
            return
        props = {"pages": pdf["pages"], "page_used": pdf["page"], "pdf_route": pdf["reason"]}
        if pdf["route"] == "image":
            if not await self._ai_allowed(shop):
                return
            await self._read_image_bill(shop, pdf["image"], {"source": "pdf_scan"} | props)
            return
        try:
            path = await asyncio.to_thread(self.photos.save, shop["shop_code"], pdf["image"])
        except Exception:
            log.exception("photo storage failed")
            path = None
        await self._new_bill(shop, pdf["fields"] | {"is_bill": True},
                             {"image_path": path, "model": read_pdf.READER, "prompt_version": read_pdf.RULES_VERSION,
                              "source": "pdf_text",
                              "input_tokens": 0, "output_tokens": 0, "latency_ms": 0},
                             {"source": "pdf_text"} | props)


    async def _next_step(self, shop, bill, missing_from_ai=False):
        """Ask for the first missing required field, else show the summary with confirm buttons."""
        for f in REQUIRED:
            if _missing(bill, f):
                self._set_state(shop, {"await": "field", "bill_id": bill["id"], "field": f})
                prefix = M.MISSING_PREFIX if missing_from_ai else ""
                await self._text(shop, prefix + M.ASK_FIELD[f])
                return
        if self._needs_date_tap(bill):
            self._set_state(shop, {})
            self.store.log_event(shop["id"], "date_tap_asked", {"bill_id": bill["id"]})
            read = bill.get("bill_date")
            if read:   # the date it read gets its own button; 'kal' can be typed after 'Doosri date'
                buttons = [(f"date_ok:{bill['id']}", M.btn_date_read(read)),
                           (f"date_today:{bill['id']}", M.BTN_DATE_TODAY_ALT),
                           (f"date_other:{bill['id']}", M.BTN_DATE_OTHER)]
            else:
                buttons = [(f"date_today:{bill['id']}", M.BTN_DATE_TODAY), (f"date_yday:{bill['id']}", M.BTN_DATE_YDAY),
                           (f"date_other:{bill['id']}", M.BTN_DATE_OTHER)]
            await self._buttons(shop, M.date_tap(bill, old=P.date_is_old(read, self._today())), buttons)
            return
        if not bill.get("supplier_id") and not bill.get("new_supplier"):
            # supplier step: one clear match is used; look-alikes or 2+ matches -> a list; else '➕ naya supplier'
            await self._supplier_step(shop, "b", bill["id"], bill["supplier_name_raw"], amount=bill["total_amount"])
            return
        await self._show_photo_summary(shop, bill)

    async def _show_photo_summary(self, shop, bill):
        sup = self._own_supplier(shop, bill["supplier_id"]) if bill.get("supplier_id") else None
        before, after, notes, dup = self._card_checks(shop, "b", bill, log=True)
        self._set_state(shop, {})
        await self._buttons(shop, M.bill_summary(bill, sup["name"] if sup else None, bool(bill.get("new_supplier")),
                                                 before, after, notes), self._card_buttons("b", bill["id"], dup))

    def _needs_date_tap(self, bill) -> bool:
        """P5 ("money first"): a doubtful bill date is asked with one tap (not a Sonnet call), once per bill."""
        policy = getattr(self.extractor, "policy", None)
        return ("money_first" in P.POLICIES.get(policy, set()) and bill.get("model") != "typed"
                and "bill_date" in (bill.get("unsure") or []) and "bill_date" not in (bill.get("checked") or []))

    async def _on_date_tap(self, shop, kind, bill_id):
        bill = self._own_pending_bill(shop, bill_id)
        if bill is None:
            await self._text(shop, M.BILL_GONE)
            return
        choice = {"date_ok": "as_read", "date_today": "today", "date_yday": "yesterday", "date_other": "other"}[kind]
        self.store.log_event(shop["id"], "date_tapped", {"bill_id": bill_id, "choice": choice,
                                                         "read": str(bill.get("bill_date") or "")})
        if choice == "other":
            self._set_state(shop, {"await": "field", "bill_id": bill_id, "field": "bill_date"})
            await self._text(shop, M.ASK_FIELD["bill_date"])
            return
        if choice == "as_read" and bill.get("bill_date"):   # the owner confirmed the date it read: not a correction
            day = bill["bill_date"] if isinstance(bill["bill_date"], date) else date.fromisoformat(str(bill["bill_date"]))
        else:
            day = self._today() - timedelta(days=1 if choice == "yesterday" else 0)
        self.store.set_bill_field(bill_id, "bill_date", day.isoformat())
        await self._next_step(shop, self.store.get_bill(bill_id))

    def _own_pending_bill(self, shop, bill_id):
        bill = self.store.get_bill(bill_id)
        if bill and str(bill["shop_id"]) == str(shop["id"]) and bill["status"] == "pending_confirm":
            return bill
        return None

    async def _confirm_bill(self, shop, bill_id, dup_ok=False):
        bill = self._own_pending_bill(shop, bill_id)
        if bill is None:
            await self._gone(shop, "b", bill_id)
            return
        if any(_missing(bill, f) for f in REQUIRED):
            await self._next_step(shop, bill)
            return
        supplier = await self._resolve_for_confirm(shop, "b", bill, bill["supplier_name_raw"])
        if supplier is None:
            return  # the owner picks the supplier first (never created silently)
        if not dup_ok and bill.get("supplier_id") and self._card_checks(shop, "b", bill)[3]:
            # the same bill was saved after this card was shown (e.g. sent twice): show the card again, with the warning
            await (self._show_card(shop, "b", bill_id) if bill.get("source") in ("voice", "typed")
                   else self._show_photo_summary(shop, bill))
            return
        status = self.store.confirm_bill(bill_id, supplier["id"])
        props = {"bill_id": bill_id, "source": bill.get("source") or "photo", "kind": bill.get("kind") or "bill"}
        if status == "corrected":
            props["fields"] = self.store.corrected_fields(bill_id)
        props["dup_ok"] = dup_ok
        self.store.log_event(shop["id"], "bill_confirmed" if status == "confirmed" else "bill_corrected", props)
        self._set_state(shop, {})
        self._forget(shop)
        due = self._due_of(shop, supplier["id"])
        offer = M.PROOF_OFFER if bill.get("source") in ("voice", "typed") and bill.get("kind", "bill") == "bill" \
            and norm_supplier(supplier["name"]) != norm_supplier(M.PRACTICE_SUPPLIER) else ""   # no proof for practice
        await self._text(shop, M.bill_saved(supplier["name"], due) + offer + self._tip_line(shop))
        await self._after_save(shop)

    async def _answer_field(self, shop, state, text):
        bill = self._own_pending_bill(shop, state["bill_id"])
        if bill is None:
            self._set_state(shop, {})
            await self._text(shop, M.BILL_GONE)
            return
        field = state["field"]
        value = text.strip() if field == "supplier_name" else NORMALISERS[field](text)
        if field in ("bill_date", "due_date") and value is None:   # 'aaj', 'kal', 'parso' (typed after 'Doosri date')
            back = DAY_WORDS.get(U.to_roman(text).lower().strip(" .!") if U.has_devanagari(text) else text.lower().strip(" .!"))
            value = (self._today() - timedelta(days=back)).isoformat() if back is not None else None
        if field == "supplier_name" and not norm_supplier(value):
            value = None
        if value is None:
            await self._text(shop, M.BAD_VALUE[field])
            return
        self.store.set_bill_field(bill["id"], field, value)
        if field == "supplier_name":
            self.store.set_bill_supplier(bill["id"], None, False)  # a new name: the supplier step runs again
        await self._next_step(shop, self.store.get_bill(bill["id"]))

    # ------------------------------------------------------------ buttons
    async def _on_button(self, shop, button):
        kind, _, rest = button.partition(":")
        rest, _, flag = rest.partition(":") if kind in ("ok", "pok", "bcancel", "pcancel") else (rest, "", "")
        if kind == "ok":
            await self._confirm_bill(shop, rest, dup_ok=flag == "dup")
        elif kind == "edit":
            bill = self._own_pending_bill(shop, rest)
            if bill is None:
                await self._gone(shop, "b", rest)
                return
            if bill.get("source") in ("voice", "typed"):
                await self._edit_pending(shop, "b", rest)
                return
            await self._buttons(shop, M.EDIT_WHAT, [(f"field:{rest}:{f}", t) for f, t in M.BTN_FIELD.items()])
        elif kind == "bcancel":
            await self._cancel_bill(shop, rest, dup=flag == "dup")
        elif kind == "pok":
            await self._confirm_payment(shop, rest, dup_ok=flag == "dup")
        elif kind == "pcancel":
            await self._cancel_payment(shop, rest, dup=flag == "dup")
        elif kind in ("old_yes", "old_no"):
            await self._on_old_dues_button(shop, kind, rest)
        elif kind in ("adj", "adjc"):
            await self._on_adjust_button(shop, kind, rest)
        elif kind in ("pfy", "pfn"):
            await self._on_proof_read_button(shop, kind, rest)
        elif kind == "pedit":
            await self._edit_pending(shop, "p", rest)
        elif kind == "pf":
            await self._on_pending_field_pick(shop, rest)
        elif kind in ("sp", "spn", "spt"):
            await self._on_supplier_pick(shop, kind, rest)
        elif kind in ("kind_bill", "kind_pay", "kind_no", "rcv_yes", "rcv_pay", "rcv_bill", "rcv_refund", "rcv_no",
                      "fut_rem", "fut_paid", "fut_add", "fut_no", "rfw_g", "rfw_o", "rmaan", "rcash_cut",
                      "rcash_got"):
            await self._on_choice(shop, kind, rest)
        elif kind in ("fix_ok", "fix_no", "fsel", "ued", "udel", "ukeep", "ef"):
            await self._on_fix_button(shop, kind, rest)
        elif kind in ("proof_yes", "proof_no", "pkeep", "pfix", "pown"):
            await self._on_proof_button(shop, kind, rest)
        elif kind == "field":
            bill_id, _, field = rest.partition(":")
            if self._own_pending_bill(shop, bill_id) is None or field not in M.ASK_FIELD:
                await self._text(shop, M.BILL_GONE)
                return
            self.store.log_event(shop["id"], "edit_started", {"bill_id": bill_id, "field": field})
            self._set_state(shop, {"await": "field", "bill_id": bill_id, "field": field})
            await self._text(shop, M.ASK_FIELD[field])
        elif kind in ("own_yes", "own_no"):
            bill = self._own_pending_bill(shop, rest)
            if bill is None:
                await self._text(shop, M.BILL_GONE)
            elif kind == "own_yes":
                self.store.reject_bill(rest)
                self.store.log_event(shop["id"], "own_sales_bill", {"bill_id": rest})
                self._set_state(shop, {})
                await self._text(shop, M.OWN_BILL_SKIPPED)
            else:
                self.store.log_event(shop["id"], "own_bill_denied", {"bill_id": rest})
                await self._next_step(shop, bill, missing_from_ai=True)
        elif kind == "dsup":
            await self._supplier_detail(shop, rest)
        elif kind == "fb":
            await self._on_feedback_button(shop, rest)
        elif kind == "rate":
            await self._on_rating(shop, rest)
        elif kind in ("date_ok", "date_today", "date_yday", "date_other"):
            await self._on_date_tap(shop, kind, rest)
        elif kind in ("pay_yes", "pay_no"):
            await self._on_pay_button(shop, kind == "pay_yes")
        elif kind in ("q", "qs", "qp", "qd", "qr"):
            await self._on_question_tap(shop, kind, rest)
        elif kind == "tk":
            await self._on_practice_button(shop, rest)
        elif kind in ("sn_yes", "sn_edit", "sn_skip"):
            await self._on_shop_name_button(shop, kind)
        elif kind in ("consent_yes", "consent_no", "consent_example"):
            await self._text(shop, M.MENU)  # already consented
        else:
            await self._text(shop, M.MENU)

    # ------------------------------------------------------------ text
    async def _on_text(self, shop, text, lower):
        state = shop.get("state") or {}
        if state.get("await") == "feedback":   # "kya dikkat aayi?" was asked: the words are kept as written
            plain = U.to_roman(text) if U.has_devanagari(text) else text
            plain_lower = plain.lower().strip(" .!")
            if self._feedback_wait(shop) and plain_lower in F.NOTHING_TO_SAY:   # "kuch nahi": no feedback after all
                self._set_state(shop, {})
                self.store.log_event(shop["id"], "feedback_declined", {"via": "typed"})
                await self._text(shop, M.FB_NONE)
                return
            if self._feedback_wait(shop) and plain_lower not in MENU_WORDS | START_WORDS | CANCEL_WORDS \
                    and not self._looks_like_work(plain):
                await self._save_feedback(shop, text, state.get("source") or "asked", "typed")
                return
            self._set_state(shop, {})
            state = {}
            if plain_lower in CANCEL_WORDS:
                await self._text(shop, M.KEPT)
                return
        if U.has_devanagari(text) and state.get("await") != "shop_name":   # a shop's own name stays as written
            text = U.to_roman(text)
            lower = text.lower().strip(" .!")
            self.store.log_event(shop["id"], "hindi_script", {})
        if state.get("await") in ("sname", "pfield", "efield", "oldamt", "adjamt") \
                and self._leaves_value_state(state, text, lower):
            self._set_state(shop, {})          # the owner moved on: a new message, the menu, or "cancel"
            state = {}
            if lower in CANCEL_WORDS:
                await self._text(shop, M.KEPT)
                return
        if state.get("await") == "field":
            await self._answer_field(shop, state, text)
            return
        if state.get("await") == "sname":
            await self._on_supplier_name(shop, state, text)
            return
        if state.get("await") == "pfield":
            await self._on_pending_field_value(shop, state, text)
            return
        if state.get("await") == "efield":
            await self._on_entry_field_value(shop, state, text)
            return
        if state.get("await") == "oldamt":
            await self._on_old_dues_value(shop, state, text)
            return
        if state.get("await") == "adjamt":
            await self._on_adjust_value(shop, state, text)
            return
        if state.get("await") == "shop_name":
            self._set_state(shop, {})
            if lower in M.SKIP_WORDS:
                self.store.log_event(shop["id"], "shop_name_skipped", {})
                await self._text(shop, M.SHOP_NAME_SKIPPED)
                return
            looks_like_command = (DUES_RE.search(lower) or E.parse(text, [], self._today()).kind in ("entries", "fix")
                                  or parse_payment(text) or parse_typed_bill(text)
                                  or lower in MENU_WORDS or lower in START_WORDS or NAME_CMD_RE.match(text)
                                  or lower in HISAAB_WORDS or Q.looks_like_question(text))
            if not looks_like_command and looks_like_shop_name(text):
                await self._save_shop_name(shop, text)
                return
        name_cmd = NAME_CMD_RE.match(text)
        if name_cmd:
            await self._save_shop_name(shop, name_cmd.group(1))
            return
        suppliers = self._suppliers(shop)
        text, slips = U.fix_typos(text, suppliers)    # 'hiasb' -> 'hisaab'; supplier names are never touched
        if slips:
            lower = text.lower().strip(" .!")
            self.store.log_event(shop["id"], "typo_fixed", {"words": len(slips)})
        small = small_words(lower)
        if small in GUIDE_WORDS:   # 'sikhao': every feature in one message, any time
            self.store.log_event(shop["id"], "guide_viewed", {"in_tour": self._tour_on(shop)})
            await self._text(shop, M.GUIDE)
            return
        if self._tour_on(shop) and await self._tour_words(shop, state, small):
            return
        if small in CARD_YES_WORDS and await self._typed_yes(shop, state):
            return
        if small in THANKS_WORDS or small in GREET_WORDS:
            self.store.log_event(shop["id"], "small_talk", {"kind": "thanks" if small in THANKS_WORDS else "greeting"})
            await self._text(shop, M.THANKS_REPLY if small in THANKS_WORDS else M.GREET_REPLY)
            return
        if lower in MENU_WORDS or lower in START_WORDS or lower in HISAAB_WORDS:
            await self._show_menu(shop)
            return
        fb = F.feedback_text(text)
        if fb is not None and not (re.search(r"\d", text) and self._looks_like_work(text)):
            if fb:
                await self._save_feedback(shop, fb, "keyword", "typed")
            else:
                await self._ask_feedback(shop, "keyword")
            return
        if PROOF_RE.search(lower) and await self._proof_from_text(shop, text, suppliers):
            return
        if await self._entries_from_text(shop, text, suppliers, "text"):
            return
        parsed = Q.parse_question(text, suppliers, self._today())
        # A pending list question can be answered by typing ("is mahine", "Sharma Traders"), but only when the
        # new message is not a question of its own: "aur sharma ko 3 din me yaad dilana" is a NEW reminder.
        if parsed.intent is None and state.get("await") == "q" and await self._fill_slot_from_text(shop, state, text):
            return
        if parsed.intent:
            await self._start_question(shop, parsed, "rules", "text")
            return
        if parsed.hisaab_like:
            await self._unclear_question(shop, text, parsed, suppliers, "text")
            return
        if F.FRUSTRATED_RE.search(lower):   # 'kaam nahi kar raha', 'bekar': ask what went wrong (once a day)
            self.store.log_event(shop["id"], "off_topic", {"type": "text", "frustrated": True,
                                                           "text": F.mask_numbers(text)[:200]})
            if await self._maybe_offer_feedback(shop, "words", daily_limit=False):
                return
        else:   # the words are kept (numbers masked) for the mistakes report: each repeated miss becomes a rule
            self.store.log_event(shop["id"], "off_topic", {"type": "text", "text": F.mask_numbers(text)[:200]})
        await self._show_menu(shop, M.OFF_TOPIC)

    # ------------------------------------------------------------ first-day tour and tips (24 Nov)
    def _tour_on(self, shop) -> bool:
        """The tour was started and is not finished yet."""
        return self.coach and self.store.event_exists(shop["id"], "tour_started", "v", 1) \
            and not self.store.event_exists(shop["id"], "tour_done", "v", 1)

    def _tour_step_now(self, shop) -> int:
        """The step the owner was last shown: 1, 2 or 3."""
        for step in (3, 2):
            if self.store.event_exists(shop["id"], "tour_step", "step", step):
                return step
        return 1

    async def _end_tour(self, shop, how: str, text: str, ask_name: bool = True):
        """Every way out of the tour logs the same 'tour_done' (v 1) with how it ended, so tips and the shop-name
        question start again. how: finished / any_order / saved_instead / skipped.
        25 Nov: the tour is a practice round. Entries for the practice supplier "Demo" are removed by themselves;
        anything else saved during the tour (a real bill photo) is kept or removed by one tap (kept if he never taps).
        Then one clear line: from now on, what he confirms is saved for real."""
        self.store.log_event(shop["id"], "tour_done", {"v": 1, "how": how, "step": self._tour_step_now(shop)})
        entries = self._tour_entries(shop)
        demo = [e for e in entries if norm_supplier(e.get("supplier") or "") == norm_supplier(M.PRACTICE_SUPPLIER)]
        for e in demo:
            self.store.delete_entry(shop["id"], e["type"], e["id"], "button")   # schema allows voice/typed/button/proof; the reason is in the tour_practice event
        if demo:
            self.store.log_event(shop["id"], "tour_practice_cleared", {"n": len(demo)})
        other = [e for e in entries if e not in demo]
        if other:
            self.store.log_event(shop["id"], "tour_keep_asked", {"n": len(other)})
            self._set_state(shop, {"await": "tour_keep", "text": text, "ask_name": ask_name, "cleared": len(demo)})
            lines = [f"{e.get('supplier') or '?'}: {M.inr(e['amount'])}"
                     + (" (payment)" if e["type"] == "payment" else "") for e in other]
            await self._buttons(shop, M.practice_keep_ask(lines, len(demo)),
                                [("tk:del", M.BTN_PRACTICE_DEL), ("tk:keep", M.BTN_PRACTICE_KEEP)])
            return
        await self._finish_tour_text(shop, text, len(demo), ask_name)

    def _tour_entries(self, shop) -> list[dict]:
        """Bills and payments confirmed since the owner tapped Shuru karein (the tour starts there)."""
        since = shop.get("consent_at") or (datetime.now(timezone.utc) - timedelta(days=1))
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        return self.store.recent_entries(shop["id"], since)

    async def _finish_tour_text(self, shop, text: str, cleared: int, ask_name: bool):
        summary = M.practice_summary(cleared, len(self._tour_entries(shop)))
        parts = [text] + ([summary] if summary else []) + [M.REAL_START]
        await self._text(shop, "\n\n".join(parts))
        if ask_name:
            await self._maybe_ask_shop_name(shop)

    async def _on_practice_button(self, shop, choice: str):
        state = shop.get("state") or {}
        if state.get("await") != "tour_keep":
            await self._text(shop, M.MENU)
            return
        self._set_state(shop, {})
        removed = 0
        if choice == "del":
            for e in self._tour_entries(shop):
                self.store.delete_entry(shop["id"], e["type"], e["id"], "button")   # schema allows voice/typed/button/proof; the reason is in the tour_practice event
                removed += 1
        self.store.log_event(shop["id"], "tour_practice_choice", {"choice": choice, "n": removed})
        await self._finish_tour_text(shop, state.get("text") or M.TOUR_DONE,
                                     int(state.get("cleared") or 0) + removed, bool(state.get("ask_name", True)))

    async def _tour_words(self, shop, state, small: str) -> bool:
        """During the tour: 'baad mein' ends it politely; 'hi' / 'menu' / a greeting shows the step he is on.
        'chhodo' / 'rehne do' count as a skip only when no card or question is waiting (else they cancel that)."""
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        busy = bool(state.get("await")) or bool(self.store.pending_count(shop["id"], since)) \
            or self._asked.get(str(shop["id"])) == "question"
        if small in TOUR_SKIP_WORDS or (small in CANCEL_WORDS and not busy):
            await self._end_tour(shop, "skipped", M.TOUR_SKIPPED, ask_name=False)
            return True
        if not busy and (small in MENU_WORDS or small in GREET_WORDS):
            self.store.log_event(shop["id"], "tour_reminded", {"step": self._tour_step_now(shop)})
            await self._text(shop, M.TOUR_REMIND.format(step=M.TOUR_STEP[self._tour_step_now(shop)]))
            return True
        return False

    async def _after_save(self, shop):
        """After a saved entry: the next tour step, or else the shop-name question, or else the rating.
        The tour follows the owner, never the other way round: a question asked early counts as step 3, and a save
        where the question was asked for ends the tour (with the question shown once more), so it never hangs."""
        if self._tour_on(shop):
            since = datetime.now(timezone.utc) - timedelta(minutes=30)
            if self.store.pending_count(shop["id"], since):
                return   # other cards from the same message still wait: one thing at a time
            asked = self.store.event_exists(shop["id"], "tour_step", "step", "q")
            if not self.store.event_exists(shop["id"], "tour_step", "step", 2):
                self.store.log_event(shop["id"], "tour_step", {"step": 2})
                await self._text(shop, M.TOUR_2)
            elif asked:
                await self._end_tour(shop, "any_order", M.TOUR_DONE)
            elif not self.store.event_exists(shop["id"], "tour_step", "step", 3):
                self.store.log_event(shop["id"], "tour_step", {"step": 3})
                await self._text(shop, M.TOUR_3)
            else:
                await self._end_tour(shop, "saved_instead", M.TOUR_DONE_NO_QUESTION)
            return
        if not await self._maybe_ask_shop_name(shop):   # the proof offer is a hint, not a question: no clash
            await self._maybe_ask_rating(shop)

    async def _coach_after_question(self, shop):
        """The tour ends with the first question answered after step 3; then the shop's name is asked.
        A question asked before step 3 is remembered, so step 3 is not asked again later."""
        if not self._tour_on(shop):
            return
        if self.store.event_exists(shop["id"], "tour_step", "step", 3):
            await self._end_tour(shop, "finished", M.TOUR_DONE)
        elif not self.store.event_exists(shop["id"], "tour_step", "step", "q"):
            self.store.log_event(shop["id"], "tour_step", {"step": "q"})

    def _tip_line(self, shop) -> str:
        """One tip under a save after the tour: at saves 3, 5, 7, 9, 11, at most one a day, each once."""
        if not self.coach or self._tour_on(shop):
            return ""
        n = self.store.shop_counts(shop["id"])
        saves = n["bills"] + n["payments"]
        if saves not in M.TIP_AT_SAVES:
            return ""
        i = M.TIP_AT_SAVES.index(saves)
        if i >= len(M.TIPS) or not self._once(shop, f"tip:{i}") or not self._once(shop, f"tipday:{self._today()}"):
            return ""
        self.store.log_event(shop["id"], "tip_shown", {"tip": i})
        return "\n\n" + M.TIPS[i]

    async def _typed_yes(self, shop, state) -> bool:
        """'ok' / 'haan' typed under a card: with exactly one card open (last 30 minutes) and no question waiting, it
        is saved as if 'Sahi hai' was tapped (the duplicate check still runs, so a duplicate is shown again, never
        saved on a word). Several cards, or a question waiting: ask to tap the right button. False: no card open."""
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        cards = self.store.pending_entries(shop["id"], since)
        if self._asked.get(str(shop["id"])) == "question":
            # 24 Nov: 'ok' under "pehle ka koi baaki tha?" or "Kyun?" must not save the card behind that question
            self.store.log_event(shop["id"], "typed_yes", {"result": "answer_buttons"})
            await self._text(shop, M.ANSWER_THE_BUTTONS)
            return True
        if not cards:
            if state.get("await"):   # 'ok' while a choice question waits: point at its buttons (24 Nov)
                self.store.log_event(shop["id"], "typed_yes", {"result": "answer_buttons"})
                await self._text(shop, M.ANSWER_THE_BUTTONS)
                return True
            return False
        if len(cards) > 1 or state.get("await"):
            self.store.log_event(shop["id"], "typed_yes", {"result": "ask_tap", "open": len(cards)})
            await self._text(shop, M.TAP_THE_BUTTON)
            return True
        card = cards[0]
        self.store.log_event(shop["id"], "typed_yes", {"result": "confirm", "t": card["t"]})
        if card["t"] == "b":
            await self._confirm_bill(shop, card["id"])
        else:
            await self._confirm_payment(shop, card["id"])
        return True

    def _leaves_value_state(self, state, text, lower) -> bool:
        """While the bot waits for a typed value (a name, an amount, a date), the owner moved on only if: a menu /
        cancel word, or a message that is clearly something else (a verb: 'Sharma ko 2000 diye'; a question) AND is
        not a valid answer itself ('Bill no 4471' for the bill number, 'Shree 786 Kirana' for a name stay answers)."""
        if lower in MENU_WORDS | START_WORDS | HISAAB_WORDS | CANCEL_WORDS:
            return True
        field = state.get("field") or ("amount" if state.get("await") in ("oldamt", "adjamt") else "supplier_name")
        if state.get("await") != "sname" and field not in ("supplier_name", "supplier_id") \
                and self._parse_value(field, text) is not None:
            return False
        toks, _ = E.tokenize(text)
        return (E.parse(text, [], self._today()).kind in ("entries", "fix") and E._classify(toks) is not None) \
            or Q.parse_question(text, [], self._today()).intent is not None

    async def _on_voice(self, shop, media_id):
        if self.transcriber is None:
            self.store.log_event(shop["id"], "voice_received", {"transcribed": False})
            await self._text(shop, M.VOICE_SOON)
            return
        if not await self._ai_allowed(shop):
            return
        try:
            audio = await self.wa.download_media(media_id)
            # notes over 30 s are cut at pauses into <= 29.5 s pieces (Sarvam's limit); each piece is one AI call
            transcript, info = await transcribe_note(
                self.transcriber, audio, on_call=lambda: self.store.log_event(shop["id"], "ai_call", {"kind": "voice"}))
        except NoteTooLong as err:
            self.store.log_event(shop["id"], "voice_too_long", {"seconds": round(err.seconds)})
            await self._text(shop, M.VOICE_TOO_LONG)
            return
        except Exception as err:
            log.exception("voice transcription failed")
            self.store.log_event(shop["id"], "voice_failed", {"error": type(err).__name__})
            await self._text(shop, M.VOICE_FAILED)
            return
        self.store.log_event(shop["id"], "voice_received", {"transcribed": True, "chars": len(transcript)} | info)
        if not transcript:
            await self._text(shop, M.VOICE_FAILED)
            return
        roman = U.to_roman(transcript) if U.has_devanagari(transcript) else transcript
        if self._feedback_wait(shop) and not self._looks_like_work(roman):
            await self._save_feedback(shop, transcript, shop["state"].get("source") or "asked", "voice")
            return
        fb = F.feedback_text(roman)
        if fb is not None and not (re.search(r"\d", roman) and self._looks_like_work(roman)):
            if fb:
                await self._save_feedback(shop, transcript, "keyword", "voice")
            else:
                await self._ask_feedback(shop, "keyword")
            return
        if (shop.get("state") or {}).get("await") == "feedback":
            self._set_state(shop, {})
        suppliers = self._suppliers(shop)
        heard = transcript                                    # shown to the owner as it was heard
        transcript, _ = U.clean(transcript, suppliers)        # Hindi script -> Roman, spelling slips -> the word
        if await self._entries_from_text(shop, transcript, suppliers, "voice", heard=heard):
            return
        parsed = Q.parse_question(transcript, suppliers, self._today())
        if parsed.intent:
            await self._start_question(shop, parsed, "rules", "voice")
            return
        if not Q.has_question_cue(transcript) and await self._entries_from_llm(shop, transcript, suppliers):
            return
        if parsed.hisaab_like and self.question_llm is not None:
            await self._unclear_question(shop, transcript, parsed, suppliers, "voice")
        else:
            self.store.log_event(shop["id"], "voice_not_understood", {})
            await self._text(shop, M.voice_not_understood(transcript))

    async def _entries_from_text(self, shop, text, suppliers, source, heard=None) -> bool:
        """The text engine (bot/entries.py). True if it handled the message (a fix, entries, or too many entries)."""
        u = E.parse(text, suppliers, self._today(), self._lazy_recent(shop))
        if u.kind == "fix":
            await self._start_fix(shop, u.fix, source, heard)
            await self._say_skipped(shop, u.skipped)
            return True
        if u.kind == "too_many":
            self.store.log_event(shop["id"], "entry_parsed", {"source": source, "understood_by": "rules",
                                                              "intents": [p.intent for p in u.parts],
                                                              "parts": len(u.parts), "too_many": True})
            await self._say_heard(shop, heard)
            await self._text(shop, M.TOO_MANY)
            return True
        if u.kind == "entries":
            await self._start_entries(shop, u.parts, source, heard, fix=u.fix, order=u.order)
            await self._say_skipped(shop, u.skipped)
            return True
        return False

    async def _entries_from_llm(self, shop, transcript, suppliers) -> bool:
        """Voice only, when the rules found nothing: the Haiku classifier may fill cards (checked by entries.from_llm;
        the owner still confirms each one). The old payments-only parser (voice_parser) still works."""
        if self.entry_parser is None and self.voice_parser is None:
            return False
        if not await self._ai_quietly_allowed(shop):
            return False
        self.store.log_event(shop["id"], "ai_call", {"kind": "voice_text"})
        try:
            if self.entry_parser is not None:
                data = await asyncio.to_thread(self.entry_parser, transcript, [s["name"] for s in suppliers],
                                               self._today())
                u = E.from_llm(data, transcript, suppliers, self._today())
            else:
                got = await asyncio.to_thread(self.voice_parser, transcript)
                u = E.from_llm({"entries": [{"type": "payment", "amount": got[0], "supplier": got[1]}]} if got else None,
                               transcript, suppliers, self._today())
        except Exception:
            log.exception("voice entry classifier failed")
            return False
        if u.kind == "too_many":
            await self._say_heard(shop, transcript)
            await self._text(shop, M.TOO_MANY)
            return True
        if u.kind != "entries":
            return False
        await self._start_entries(shop, u.parts, "voice", heard=transcript, understood_by="llm")
        return True

    async def _send_dues(self, shop, understood_by="rules", source="text"):
        """dues_all keeps its own event name (dues_asked) so older reports still work; it carries the same props."""
        rows = self.store.dues(shop["id"])
        self.store.log_event(shop["id"], "dues_asked", {"suppliers": len(rows), "intent": "dues_all",
                                                        "understood_by": understood_by, "source": source})
        if (shop.get("state") or {}).get("await") == "q":
            self._set_state(shop, {})
        if not rows:
            await self._text(shop, M.NO_DUES)
            return
        body = M.dues_all(rows) + M.DUES_DETAIL_HINT
        if len(body) > 1024:   # a WhatsApp list's text is at most 1024 characters: plain text, names can be typed
            await self._text(shop, M.dues_all(rows))
        else:   # 18 Nov phone test: one tap for any supplier's detail (it was only reachable by typing the name)
            await self._list(shop, body, M.LIST_BTN_DETAIL,
                             [(f"dsup:{r['supplier_id']}", r["supplier"][:24], M.dues_row_note(r)) for r in rows[:10]])
            self._asked[str(shop["id"])] = None   # an answer with an optional tap, not a question: 'ok' = thanks
        await self._coach_after_question(shop)

    async def _supplier_detail(self, shop, supplier_id):
        """A tap on a supplier in the 'kitna baaki' list = the question 'is supplier ka kitna baaki' (its detail)."""
        if self._supplier_by_id(shop, supplier_id) is None:
            await self._send_dues(shop, "menu", "tap")
            return
        st = {"await": "q", "intent": "dues_supplier", "by": "menu", "source": "tap", "supplier_id": supplier_id,
              "supplier_set": True, "period": None, "pstart": None, "pend": None, "remind_on": None, "note": None}
        self.store.log_event(shop["id"], "question_asked", {"intent": "dues_supplier", "source": "tap",
                                                            "understood_by": "menu", "unclear": []})
        await self._finish_q(shop, st)

    # ------------------------------------------------------------ questions about the hisaab
    # Understanding: bot/questions.py (rules, optional Haiku classifier). Numbers: bot/answers.py (database only).
    # Anything unclear -> a WhatsApp list to pick from, never a guess.
    def _group(self, shop):
        return shop.get("experiment_group") or "unassigned"

    def _supplier_by_id(self, shop, supplier_id):
        return next((s for s in self._suppliers(shop) if str(s["id"]) == str(supplier_id)), None)

    def _new_q_state(self, parsed: "Q.Parsed", by: str, source: str) -> dict:
        per = parsed.period
        return {"await": "q", "intent": parsed.intent, "by": by, "source": source,
                "supplier_id": parsed.supplier["id"] if parsed.supplier else None,
                "supplier_set": not (parsed.candidates or "supplier_not_found" in parsed.unclear
                                     or "supplier" in parsed.unclear),
                "period": per.key if per else None, "pstart": per.start.isoformat() if per else None,
                "pend": per.end.isoformat() if per else None,
                "remind_on": parsed.remind_on.isoformat() if parsed.remind_on else None, "note": parsed.note}

    def _understood(self, shop, st) -> list[tuple[str, str]]:
        out = [("Sawaal", M.INTENT_LABEL[st["intent"]])]
        sup = self._supplier_by_id(shop, st["supplier_id"]) if st.get("supplier_id") else None
        if sup:
            out.append(("Supplier", sup["name"]))
        if st.get("period"):
            out.append(("Samay", M.period_label(st["period"], date.fromisoformat(st["pstart"]),
                                                 date.fromisoformat(st["pend"]))))
        if st.get("remind_on"):
            out.append(("Din", M.day_label(date.fromisoformat(st["remind_on"]))))
        return out

    async def _start_question(self, shop, parsed: "Q.Parsed", by: str, source: str):
        if parsed.intent == "dues_all":
            await self._send_dues(shop, by, source)
            return
        unclear = parsed.unclear
        self.store.log_event(shop["id"], "question_asked", {"intent": parsed.intent, "source": source,
                                                            "understood_by": "none" if unclear else by,
                                                            "unclear": unclear})
        st = self._new_q_state(parsed, by, source)
        if parsed.candidates:
            st["by"] = "menu"
            self._set_state(shop, st)
            body = M.clarify(self._understood(shop, st),
                             M.supplier_ambiguous(parsed.supplier_text or "", len(parsed.candidates)))
            await self._list(shop, body, M.LIST_BTN_SUPPLIER, [(f"qs:{c['id']}", c["name"], "") for c in parsed.candidates[:10]])
            return
        if "supplier_not_found" in unclear:
            await self._ask_supplier(shop, st, M.supplier_not_found(parsed.supplier_text))
            return
        await self._continue_q(shop, st)

    async def _ask_supplier(self, shop, st, problem: str = ""):
        rows = []
        if st["intent"] == "set_reminder":
            rows.append(("qs:none", M.NO_SUPPLIER_ROW, M.GENERAL_REMINDER))
        elif st["intent"] != "dues_supplier":
            rows.append(("qs:all", M.ALL_SUPPLIERS_ROW, ""))
        dues = {str(r["supplier_id"]): r["due"] for r in self.store.dues(shop["id"])}
        sups = sorted(self._suppliers(shop), key=lambda s: (-dues.get(str(s["id"]), 0), s["name"]))
        if not sups and st["intent"] == "dues_supplier":
            self._set_state(shop, {})
            await self._text(shop, M.NO_SUPPLIERS)
            return
        rows += [(f"qs:{s['id']}", s["name"], f"Baaki {M.inr(dues[str(s['id'])])}" if str(s["id"]) in dues else "")
                 for s in sups[: 10 - len(rows)]]
        first_ask = st["source"] != "menu" and not st.get("asked")
        st["asked"], st["by"] = True, "menu"
        self._set_state(shop, st)
        body = M.clarify(self._understood(shop, st), problem) if (first_ask or problem) else M.ASK_SUPPLIER
        await self._list(shop, body, M.LIST_BTN_SUPPLIER, rows)

    async def _continue_q(self, shop, st):
        """Ask for the next missing piece (supplier, period, day) with a list; answer when nothing is missing."""
        intent, today = st["intent"], self._today()
        first_ask = st["source"] != "menu" and not st.get("asked")
        if intent == "cancel_reminder":
            rows = [r for r in self.store.list_reminders(shop["id"], today)
                    if not st.get("supplier_id") or str(r["supplier_id"]) == str(st["supplier_id"])]
            if not rows:
                self._set_state(shop, {})
                self.store.log_event(shop["id"], "question_answered", {"intent": intent, "via": st["by"]})
                await self._text(shop, M.NO_REMINDERS)
                return
            self._set_state(shop, st)
            await self._list(shop, M.ASK_CANCEL, M.LIST_BTN_REMINDER,
                             [(f"qr:{r['id']}", M.day_label(r["remind_on"]), r["text"]) for r in rows[:10]])
            return
        if intent in SUPPLIER_SLOT and not st.get("supplier_set"):
            await self._ask_supplier(shop, st)
            return
        if intent in ("paid_period", "bills_period", "period_summary") and not st.get("period"):
            st["asked"], st["by"] = True, "menu"
            self._set_state(shop, st)
            body = M.clarify(self._understood(shop, st)) if first_ask else M.ASK_PERIOD
            rows = []
            for key, title in M.PERIOD_ROWS:
                per = Q.period(key, today)
                rows.append((f"qp:{key}", title, "" if key == "all" else M.span(per.start, per.end)))
            await self._list(shop, body, M.LIST_BTN_PERIOD, rows)
            return
        if intent == "set_reminder" and not st.get("remind_on"):
            st["asked"], st["by"] = True, "menu"
            self._set_state(shop, st)
            body = M.clarify(self._understood(shop, st)) if first_ask else M.ASK_DAY
            next_monday = today + timedelta(days=7 - today.weekday())
            choices = [("Kal", today + timedelta(days=1)), ("Parso", today + timedelta(days=2)),
                       ("3 din baad", today + timedelta(days=3)), ("Agle hafte", next_monday)]
            rows, seen = [], set()
            for title, d in choices:
                if d not in seen:
                    seen.add(d)
                    rows.append((f"qd:{d.isoformat()}", title, M.reminder_date(d)))
            rows.append(("qd:more", M.DAY_MORE_ROW, ""))
            await self._list(shop, body, M.LIST_BTN_DAY, rows)
            return
        await self._finish_q(shop, st)

    async def _finish_q(self, shop, st):
        intent, today = st["intent"], self._today()
        supplier = self._supplier_by_id(shop, st["supplier_id"]) if st.get("supplier_id") else None
        self._set_state(shop, {})
        if intent == "set_reminder":
            remind_on = date.fromisoformat(st["remind_on"])
            about = M.reminder_about(supplier["name"] if supplier else None, st.get("note"))
            r = self.store.add_reminder(shop["id"], supplier["id"] if supplier else None, about[:120], remind_on)
            self.store.log_event(shop["id"], "reminder_set", {"reminder_id": r["id"], "group": self._group(shop),
                                                              "has_supplier": supplier is not None,
                                                              "days_ahead": (remind_on - today).days})
            due = next((d["due"] for d in self.store.dues(shop["id"])
                        if supplier and str(d["supplier_id"]) == str(supplier["id"])), None)
            body = M.reminder_saved(remind_on, about, due)
        else:
            per = Q.Period(st["period"], date.fromisoformat(st["pstart"]), date.fromisoformat(st["pend"])) \
                if st.get("period") else None
            body = answers.answer(self.store, shop["id"], intent, today, supplier=supplier, period=per)
        self.store.log_event(shop["id"], "question_answered", {"intent": intent, "via": st["by"]})
        await self._text(shop, body)
        await self._coach_after_question(shop)

    async def _on_question_tap(self, shop, kind, value):
        st = dict(shop.get("state") or {})
        today = self._today()
        if kind == "q":
            if value not in Q.INTENTS:
                await self._show_menu(shop)
                return
            if value == "dues_all":
                await self._send_dues(shop, "menu", "menu")
                return
            self.store.log_event(shop["id"], "question_asked", {"intent": value, "source": "menu",
                                                                "understood_by": "menu", "unclear": []})
            st = {"await": "q", "intent": value, "by": "menu", "source": "menu", "supplier_id": None,
                  "supplier_set": value not in SUPPLIER_SLOT, "period": None, "remind_on": None, "note": None}
            await self._continue_q(shop, st)
            return
        if kind == "qr":
            gone = not _is_uuid(value)
            r = None if gone else self.store.cancel_reminder(shop["id"], value)
            if st.get("await") == "q" and st.get("intent") == "cancel_reminder":
                self._set_state(shop, {})
            if r is None:
                await self._text(shop, M.REMINDER_GONE)
                return
            self.store.log_event(shop["id"], "reminder_cancelled", {"reminder_id": value, "group": self._group(shop)})
            self.store.log_event(shop["id"], "question_answered", {"intent": "cancel_reminder", "via": "menu"})
            await self._text(shop, M.reminder_cancelled(r))
            return
        if st.get("await") != "q":
            await self._show_menu(shop, M.QUESTION_EXPIRED)
            return
        st["by"] = "menu"
        if kind == "qs":
            if value in ("all", "none"):
                st["supplier_id"] = None
            elif _is_uuid(value) and self._supplier_by_id(shop, value):
                st["supplier_id"] = value
            else:
                await self._show_menu(shop, M.QUESTION_EXPIRED)
                return
            st["supplier_set"] = True
        elif kind == "qp":
            if value not in Q.PERIOD_KEYS:
                await self._show_menu(shop, M.QUESTION_EXPIRED)
                return
            per = Q.period(value, today)
            st.update(period=per.key, pstart=per.start.isoformat(), pend=per.end.isoformat())
        elif kind == "qd" and value == "more":
            self._set_state(shop, st)
            rows = [(f"qd:{(today + timedelta(days=n)).isoformat()}", M.reminder_date(today + timedelta(days=n)), "")
                    for n in range(1, 11)]
            await self._list(shop, M.ASK_DAY_MORE, M.LIST_BTN_DAY, rows)
            return
        elif kind == "qd":
            try:
                d = date.fromisoformat(value)
            except ValueError:
                d = None
            if d is None or d <= today:
                await self._show_menu(shop, M.QUESTION_EXPIRED)
                return
            st["remind_on"] = d.isoformat()
        await self._continue_q(shop, st)

    async def _fill_slot_from_text(self, shop, st, text) -> bool:
        """The bot asked for a supplier / period / day and the owner TYPED it instead of tapping."""
        toks, today = Q.tokens(text), self._today()
        if not st.get("supplier_set") and st.get("intent") in SUPPLIER_SLOT:
            match, cands, _ = Q.resolve_supplier(toks, self._suppliers(shop))
            if match and not cands:
                st.update(supplier_id=match["id"], supplier_set=True, by="menu")
                await self._continue_q(shop, st)
                return True
            return False
        if st.get("intent") in ("paid_period", "bills_period", "period_summary") and not st.get("period"):
            _, per = Q.find_day(toks, today, future=False)
            if per:
                st.update(period=per.key, pstart=per.start.isoformat(), pend=per.end.isoformat(), by="menu")
                await self._continue_q(shop, st)
                return True
        if st.get("intent") == "set_reminder" and not st.get("remind_on"):
            day, _ = Q.find_day(toks, today, future=True)
            if day and day > today:
                st.update(remind_on=day.isoformat(), by="menu")
                await self._continue_q(shop, st)
                return True
        return False

    async def _ai_quietly_allowed(self, shop) -> bool:
        since = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        return (self.store.count_events("ai_call", since, shop["id"]) < self.limits.per_shop_per_day
                and self.store.count_events("ai_call", since) < self.limits.total_per_day)

    async def _unclear_question(self, shop, text, parsed, suppliers, source):
        """Looks like a hisaab question, but the rules could not tell which. Optional Haiku classifier, else the menu."""
        today = self._today()
        if self.question_llm is not None and await self._ai_quietly_allowed(shop):
            self.store.log_event(shop["id"], "ai_call", {"kind": "question"})
            try:
                data = await asyncio.to_thread(self.question_llm, text, [s["name"] for s in suppliers], today)
            except Exception:
                log.exception("question classifier failed")
                data = None
            llm = Q.from_llm(data, suppliers, today)
            if llm.intent:
                await self._start_question(shop, llm, "llm", source)
                return
        self.store.log_event(shop["id"], "question_asked", {"intent": None, "source": source,
                                                            "understood_by": "none", "unclear": ["intent"]})
        understood = [("Supplier", parsed.supplier["name"])] if parsed.supplier else []
        await self._show_menu(shop, M.clarify(understood))

    # ------------------------------------------------------------ payments
    async def _propose_payment(self, shop, raw, amount, name, source="text"):
        supplier = match_supplier(name, self._suppliers(shop))
        if supplier is None:
            self.store.log_event(shop["id"], "payment_unmatched", {})
            await self._text(shop, M.PAY_NO_SUPPLIER)
            return
        self._set_state(shop, {"await": "payment", "supplier_id": supplier["id"], "supplier": supplier["name"],
                               "amount": amount, "raw": raw[:200], "source": source})
        await self._buttons(shop, M.pay_confirm(supplier["name"], amount),
                            [("pay_yes", M.BTN_PAY_YES), ("pay_no", M.BTN_PAY_NO)])

    async def _on_pay_button(self, shop, yes: bool):
        state = shop.get("state") or {}
        if state.get("await") != "payment":
            await self._text(shop, M.MENU)
            return
        self._set_state(shop, {})
        if not yes:
            self.store.log_event(shop["id"], "payment_cancelled", {})
            await self._text(shop, M.PAY_CANCELLED)
            return
        source = state.get("source", "text")
        self.store.add_payment(shop["id"], state["supplier_id"], state["amount"], source, state["raw"])
        self.store.log_event(shop["id"], "payment_logged", {"amount": state["amount"], "source": source})
        due = next((r["due"] for r in self.store.dues(shop["id"]) if str(r["supplier_id"]) == str(state["supplier_id"])), 0)
        await self._text(shop, M.pay_saved(state["supplier"], due))


def _col(field):
    return "supplier_name_raw" if field == "supplier_name" else field


def _missing(bill, field) -> bool:
    """A required field the bill still lacks. A supplier picked from the list (or a new one tapped) counts as the
    supplier even when no name was written ('5000 ka bill aaya' -> picked Sharma Traders)."""
    if field == "supplier_name" and (bill.get("supplier_id") or bill.get("new_supplier")):
        return False
    return bill[_col(field)] is None


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _yes_only(text: str) -> bool:
    """'Haan!', 'OK.', 'haan ji' -> True; 'aur ha sharma ko yaad dilana' -> False (the whole message must be a yes)."""
    t = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip() in YES_WORDS
