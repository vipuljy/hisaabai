"""Live feedback from owners, inside the chat (20 Nov 2026, docs/04_decisions_log.md).

Three ways in:
  1. The owner says it: 'feedback ...', 'dikkat ...', 'shikayat ...', 'sujhav ...' (typed or spoken), or taps the
     menu row 'Dikkat / sujhav batayein'. A keyword alone ('feedback') asks "kya dikkat aayi?" first.
  2. The bot offers it when something looks wrong: 2 cards cancelled in 30 minutes with nothing saved, or words like
     'kaam nahi kar raha' that the bot could not read otherwise. At most once a day per shop.
  3. A 3-tap rating [Achha] [Theek-thaak] [Dikkat hai]: after the 5th saved entry, and once from day 10.

Everything is an event (feedback_given, rating_given, feedback_offered, ...): no new table, no migration, and the
evening health report (bot/health.py) lists the owners' words at the top.

Feedback never blocks work: a photo or PDF is still read as a bill, and a clear entry ('Sharma ko 2000 diye') or a
question sent while the bot waits for feedback is handled as usual. The wait lasts 15 minutes.
Owner-facing wording lives in bot/messages.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bot import entries as E
from bot import messages as M
from bot import questions as Q

FB_WAIT_MINUTES = 15
CANCELS_FOR_OFFER = 2          # cards cancelled within 30 minutes, with nothing saved, before the bot offers
RATING_AFTER_ENTRIES = 5
RATING_AFTER_DAYS = 10

_WORDS = (r"(?:feedback|feed back|fidback|shikayat|shikaayat|shikayt|complaint|complain|sujhav|sujhaav|sujhao|"
          r"suggestion|sugestion|dikkat|dikkt|dikat|problem|samasya)")
# The keyword starts the message: 'feedback: photo der se padhta hai', 'ek shikayat hai', 'mujhe feedback dena hai'
FB_START_RE = re.compile(rf"^(?:mera |meri |mere |ek |mujhe |hamari |humari )?{_WORDS}\b", re.I)
FB_ANY_RE = re.compile(rf"\b{_WORDS}\b", re.I)
# What follows a keyword when the owner only wants to start ('mujhe feedback dena hai'): ask what it is
ASK_ONLY = {"", "hai", "h", "he", "dena hai", "deni hai", "dena h", "deni h", "batana hai", "batani hai", "karna hai",
            "karni hai", "likhna hai", "likhni hai", "hai ek", "hai mujhe", "dena", "batana", "please", "plz"}
# Frustration the bot could not otherwise read ('galat' alone is also a fix command, so it counts only here, at the
# very end, after the entries engine and the question rules found nothing)
_NAHI = r"(?:nahi|nahin|nhi|nai|na)"
_KAR = r"(?:kar|kr)"
_RAHA = r"(?:raha|rha|rahi|rhi|rahe|rhe)"
# Spelled the way owners type (20 Nov: "ye kaam nhi kr rha" was missed): nahi / nhi, kar / kr, raha / rha
FRUSTRATED_RE = re.compile(
    rf"\b(?:kaam|kam|work)\s+(?:hi\s+)?{_NAHI}\s+{_KAR}|\b(?:chal|chl)\s+(?:hi\s+)?{_NAHI}\s+{_RAHA}|"
    rf"\b{_NAHI}\s+(?:chal|chl)\s+{_RAHA}|\bkuch\s+(?:bhi\s+)?{_NAHI}\s+ho\s+{_RAHA}|"
    rf"\bs(?:a?ma?|m)jh?\w*\s+(?:hi\s+)?(?:me|mein|mai)?\s*{_NAHI}\b|\b{_NAHI}\s+s(?:a?ma?|m)jh?\w*|"
    rf"\bbe+ka+r\b|\bbakwa+s\b|\bfaltu\b|\bkharab\b|not working|useless|\bgalat\b|\bslow\b|"
    rf"\bbahut\s+(?:der|time)|\b(?:time|samay)\s+(?:bahut\s+)?(?:jyada|zyada|jada|zada|bahut)|\bder\s+(?:lag|ho)|"
    rf"\bpareshaa?n", re.I)
_LONG_NUMBER = re.compile(r"\d{7,}")
# A reply to "kya dikkat aayi?" that means there is nothing to report (20 Nov: "Kuch nhi" was saved as feedback)
NOTHING_TO_SAY = {"kuch nahi", "kuch nhi", "kuchh nahi", "kuch bhi nahi", "kuch nai", "nahi", "nhi", "no", "nothing",
                  "kuch nahi hai", "kuch nhi h", "sab theek", "sab theek hai", "sab thik hai", "theek hai", "ok"}


def feedback_text(text: str) -> str | None:
    """None: not feedback. '': the owner wants to give feedback (ask what). Else: the feedback, as written."""
    t = re.sub(r"\s+", " ", text or "").strip()
    m = FB_START_RE.match(t)
    if m:
        rest = t[m.end():].strip(" :,.-!?").lower()
        return "" if rest in ASK_ONLY or len(rest) < 4 else t
    if not re.search(r"\d", t) and FB_ANY_RE.search(t):
        return t
    return None


def mask_numbers(text: str) -> str:
    """Phone numbers an owner may type into feedback stay out of reports."""
    return _LONG_NUMBER.sub("[number]", text or "")


class FeedbackFlow:
    """Mixed into bot.conversation.Bot (needs store, _text, _buttons, _set_state, _once, _today)."""

    # ------------------------------------------------------------ helpers
    def _feedback_wait(self, shop) -> dict | None:
        """The open 'kya dikkat aayi?' question, if it was asked in the last 15 minutes."""
        st = shop.get("state") or {}
        if st.get("await") != "feedback":
            return None
        try:
            at = datetime.fromisoformat(st.get("at") or "")
        except ValueError:
            return None
        return st if datetime.now(timezone.utc) - at <= timedelta(minutes=FB_WAIT_MINUTES) else None

    def _looks_like_work(self, text: str) -> bool:
        """A clear entry, fix or question: handled as usual even while feedback is awaited."""
        toks, _ = E.tokenize(text)
        return (E.parse(text, [], self._today()).kind in ("entries", "fix") and E._classify(toks) is not None) \
            or Q.parse_question(text, [], self._today()).intent is not None

    # ------------------------------------------------------------ 1. the owner gives feedback
    async def _ask_feedback(self, shop, source: str):
        self._set_state(shop, {"await": "feedback", "source": source,
                               "at": datetime.now(timezone.utc).isoformat()})
        self.store.log_event(shop["id"], "feedback_started", {"source": source})
        await self._text(shop, M.FB_ASK)

    async def _save_feedback(self, shop, words: str, source: str, via: str):
        self.store.log_event(shop["id"], "feedback_given",
                             {"text": (words or "").strip()[:1000], "source": source, "via": via})
        if (shop.get("state") or {}).get("await") == "feedback":
            self._set_state(shop, {})
        await self._text(shop, M.FB_THANKS)

    async def _on_feedback_button(self, shop, rest: str):
        if rest == "start":
            await self._ask_feedback(shop, "button")
        else:
            self.store.log_event(shop["id"], "feedback_declined", {})
            await self._text(shop, M.FB_NO_REPLY)

    # ------------------------------------------------------------ 2. the bot offers it
    async def _maybe_offer_feedback(self, shop, why: str, daily_limit: bool = True) -> bool:
        """Never over an open question. After cancels: at most once a day (the owner said nothing). After frustrated
        words: every time (the owner is asking for help). True if offered."""
        if not self.feedback_prompts or (shop.get("state") or {}).get("await"):
            return False
        if daily_limit and not self._once(shop, f"fb_offer:{self._today().isoformat()}"):
            return False
        self.store.log_event(shop["id"], "feedback_offered", {"why": why})
        await self._buttons(shop, M.FB_OFFER, [("fb:start", M.BTN_FB_START), ("fb:no", M.BTN_FB_NO)])
        return True

    async def _after_cancel(self, shop):
        if not self.feedback_prompts:
            return
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        cancels = sum(self.store.count_events(n, since, shop["id"]) for n in ("bill_cancelled", "payment_cancelled"))
        if cancels < CANCELS_FOR_OFFER:
            return
        saves = sum(self.store.count_events(n, since, shop["id"])
                    for n in ("bill_confirmed", "bill_corrected", "payment_logged", "refund_logged"))
        if saves == 0:
            await self._maybe_offer_feedback(shop, "cancels")

    # ------------------------------------------------------------ 3. the 3-tap rating
    async def _maybe_ask_rating(self, shop):
        """After a save: once at the 5th saved entry, once from day 10. One question at a time."""
        if not self.feedback_prompts:
            return
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        if self.store.pending_count(shop["id"], since):
            return
        trigger = None
        if not self.store.event_exists(shop["id"], "rating_asked", "trigger", "entries5"):
            n = self.store.shop_counts(shop["id"])
            if n["bills"] + n["payments"] >= RATING_AFTER_ENTRIES:
                trigger = "entries5"
        if trigger is None:
            start = shop.get("onboarded_at") or shop.get("consent_at")
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            if start is not None and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start is not None and datetime.now(timezone.utc) - start >= timedelta(days=RATING_AFTER_DAYS) \
                    and not self.store.event_exists(shop["id"], "rating_asked", "trigger", "day10"):
                trigger = "day10"
        if trigger is None or not self._once(shop, f"rate:{trigger}"):
            return
        self.store.log_event(shop["id"], "rating_asked", {"trigger": trigger})
        await self._buttons(shop, M.RATE_ASK, [(f"rate:good:{trigger}", M.BTN_RATE_GOOD),
                                               (f"rate:ok:{trigger}", M.BTN_RATE_OK),
                                               (f"rate:bad:{trigger}", M.BTN_RATE_BAD)])

    async def _on_rating(self, shop, rest: str):
        rating, _, trigger = rest.partition(":")
        if rating not in ("good", "ok", "bad") or not self._once(shop, f"rated:{trigger or 'x'}"):
            return   # a second tap on the same question: already counted
        self.store.log_event(shop["id"], "rating_given", {"rating": rating, "trigger": trigger})
        if rating == "bad":
            await self._ask_feedback(shop, "rating")
        else:
            await self._text(shop, M.RATE_THANKS_GOOD if rating == "good" else M.RATE_THANKS_OK)
