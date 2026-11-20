"""Understanding the owner's questions about their hisaab: rules first, never numbers.

parse_question(text, suppliers, today) -> Parsed: which of a FIXED list of question types
(intents) it is, which supplier, which period / reminder day, and what is still unclear.
It never answers anything: every number comes from a database query (bot/answers.py).
Rules cover Hinglish keywords, common misspellings (baki, bakaya), date words (aaj, kal,
is hafte, pichhle mahine, Monday / somvaar, 28 tarikh). If something is unclear the bot
shows a menu instead of guessing. An optional Haiku fallback (llm_question_parser) may only
pick an intent + supplier + period from the same fixed lists.
"""

from __future__ import annotations

import calendar
import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from bot.extract import SMALL_CALL_MAX_TOKENS, norm_supplier, parse_model_json

INTENTS = ["dues_all", "dues_supplier", "paid_period", "bills_period", "top_dues", "last_payment",
           "set_reminder", "list_reminders", "cancel_reminder"]
PERIOD_KEYS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "all"]

# ---------------------------------------------------------------- vocabulary (after normalising)
_SPELLING = {  # misspellings and synonyms -> one word
    **dict.fromkeys(["baki", "baaqi", "baqi", "bakaya", "bakaaya", "bakiya", "baqaya", "baaky", "baky",
                     "udhar", "udhaar", "udhari", "udhaari", "due", "dues", "balance", "outstanding", "pending"], "baaki"),
    **dict.fromkeys(["kitana", "kitni", "ketna", "kitnaa"], "kitna"),
    **dict.fromkeys(["hisab", "hisaab", "hissab", "hishab"], "hisaab"),
    **dict.fromkeys(["dia", "diyaa"], "diya"),
    **dict.fromkeys(["yad", "yaaad"], "yaad"),
    **dict.fromkeys(["jyada", "zada", "jada", "zyaada", "jyaada", "ziada", "adhik", "max", "maximum"], "zyada"),
    **dict.fromkeys(["pichle", "pichhle", "pichla", "pichhla", "pichli", "pichhli", "picchle", "last", "previous"], "pichhle"),
    **dict.fromkeys(["hafte", "hafta", "haftey", "week", "saptah"], "hafte"),
    **dict.fromkeys(["mahine", "mahina", "mahiney", "month"], "mahine"),
    **dict.fromkeys(["iss", "this"], "is"),
    **dict.fromkeys(["aj", "today"], "aaj"),
    **dict.fromkeys(["parson"], "parso"),
}
Q_CUES = {"kitna", "kitne", "kab", "kiska", "kiske", "kiski", "kisko", "kise", "kaun", "kaunsa", "konsa", "kon",
          "kya", "kyaa", "total", "kul", "batao", "bata", "btao", "bataiye", "dikhao", "dikha", "check", "how",
          "much", "when", "?"}
PAY_WORDS = {"diya", "diye", "di", "paid", "payment", "payments", "bheja", "bheje", "chukaya", "chukaye", "jama",
             "pay", "kiya"}
BILL_WORDS = {"bill", "bills", "bil", "maal", "purchase", "kharida", "kharide", "kharid", "invoice", "invoices"}
ARRIVE_WORDS = {"aaye", "aye", "aya", "aaya", "aayi", "aai", "mile", "mila"}
LAST_WORDS = {"pichhle", "aakhri", "akhri", "aakhiri", "latest", "recent"}
REMIND_WORDS = {"remind", "reminder", "reminders", "yaad"}
CANCEL_WORDS = {"cancel", "hatao", "hata", "hatado", "delete", "radd", "mita", "mitao", "remove", "mat", "band"}
LIST_WORDS = {"list", "dikhao", "dikha", "kaun", "konse", "kaunse", "sab", "sare", "saare", "kitne", "kya", "mere",
              "show", "batao", "kaunsa", "konsa"}
TOP_WORDS = {"zyada", "top", "bada", "badaa"}
# "all suppliers" words: never a supplier's name ('sabka kitna baaki' = everyone's dues; step-10 eval Q001, Q003)
DAY_WORDS_Q = {"aaj", "kal", "parso", "yesterday"}   # 'aaj ka payment', 'kal ke bill' ('today' is spelled 'aaj')
NOT_ASKING = {"karna", "karunga", "karenge", "dunga", "denge", "dena", "dene", "bhejna", "dalna", "karni", "dungi",
              "aayega", "aayegi", "aayenge", "aega", "ayega", "ayegi", "ayenge", "bhejega", "bhejegi", "bhejenge",
              "dega", "degi", "lekar", "laayega", "layega"}   # plans and arrivals still to come are not questions
ALL_WORDS = {"sab", "sabka", "sabke", "sabki", "sabko", "sabhi", "sabhee", "saare", "sare", "saara", "saari"}
FILLER = {
    "ka", "ki", "ke", "ko", "se", "ne", "hai", "hain", "tha", "the", "thi", "h", "he", "hi", "ho", "hua", "hue", "huye",
    "mera", "mere", "meri", "mujhe", "maine", "mene", "humne", "hamara", "hamare", "apna", "apne", "aapne", "abhi",
    "ab", "tak", "bhai", "bhaiya", "yaar", "ji", "sir", "please", "plz", "pls", "jaldi", "zara", "ek", "do", "aur",
    "or", "and", "the", "to", "of", "for", "me", "mein", "main", "par", "pe", "wala", "wale", "wali", "waale", "sabse",
    "kar", "karo", "karna", "kardo", "kr", "de", "dena", "dene", "dilana", "dilao", "dila", "dilado", "dilaana",
    "karana", "kara", "rupaye", "rupay", "rupees", "rs", "paisa", "paise", "amount", "supplier", "suppliers", "ko",
    "din", "tarikh", "tareekh", "date", "kitna", "is", "hafte", "mahine", "aaj", "kal", "parso", "pichhle", "baaki",
    "hisaab", "bataye", "bataen", "dekho", "dekhna", "chahiye", "abtak", "shuru", "aage", "agle", "next", "on", "at",
    "what", "is", "my", "i", "did", "pay", "many", "who", "owe", "most", "ya", "nahi", "nahin", "hamko", "humko",
    "ha", "haan", "han", "haa", "achha", "acha", "accha", "ok", "okay", "baad", "bad", "dino", "dinon", "agle", "agla",
    "agli", "pehle", "dijiye", "dijie", "dena", "lagao", "laga", "lagana", "set", "karna", "batana",
} | Q_CUES | PAY_WORDS | BILL_WORDS | ARRIVE_WORDS | LAST_WORDS | REMIND_WORDS | CANCEL_WORDS | LIST_WORDS | TOP_WORDS \
  | ALL_WORDS

WEEKDAYS = {  # Monday = 0. Hindi names only in their "...vaar" form: "Ravi", "Guru", "Mangal" are also people's names.
    **dict.fromkeys(["monday", "mon", "somvaar", "somvar", "somwar", "somwaar"], 0),
    **dict.fromkeys(["tuesday", "tue", "tues", "mangalvaar", "mangalvar", "mangalwar", "mangalwaar"], 1),
    **dict.fromkeys(["wednesday", "wed", "budhvaar", "budhvar", "budhwar", "budhwaar"], 2),
    **dict.fromkeys(["thursday", "thu", "thurs", "guruvaar", "guruvar", "guruwar", "veervaar", "veervar",
                     "brihaspativaar", "brihaspativar"], 3),
    **dict.fromkeys(["friday", "fri", "shukravaar", "shukravar", "shukrawar", "shukarvar"], 4),
    **dict.fromkeys(["saturday", "sat", "shanivaar", "shanivar", "shaniwar", "shaniwaar"], 5),
    **dict.fromkeys(["sunday", "sun", "ravivaar", "ravivar", "raviwar", "itvaar", "itvar", "itwar", "etwar"], 6),
}
NUMBER_WORDS = {"ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6,
                "chhah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10}
FILLER |= set(NUMBER_WORDS)
_AMOUNT = re.compile(r"(?:₹|\brs\.?\s*)\s*\d|\b\d[\d,]{2,}\b")  # a money amount: ₹50, rs 500, 5000, 1,200


@dataclass
class Period:
    key: str          # today, yesterday, this_week, last_week, this_month, last_month, all, day
    start: date
    end: date


@dataclass
class Parsed:
    intent: str | None = None
    supplier: dict | None = None                     # matched supplier (from the shop's list)
    candidates: list = field(default_factory=list)   # 2+ suppliers match the typed name: owner must pick
    supplier_text: str | None = None                 # a name was typed but no supplier matched
    period: Period | None = None
    remind_on: date | None = None
    note: str | None = None                          # reminder text when it is not about a known supplier
    hisaab_like: bool = False                        # mentions bills / dues / payments / a supplier

    @property
    def unclear(self) -> list[str]:
        out = []
        if self.intent is None:
            out.append("intent")
        if self.candidates:
            out.append("supplier_ambiguous")
        if self.supplier_text and not self.supplier and not self.candidates and not self.note:
            out.append("supplier_not_found")
        if self.intent == "dues_supplier" and not self.supplier and not self.candidates and not self.supplier_text:
            out.append("supplier")
        if self.intent in ("paid_period", "bills_period", "period_summary") and self.period is None:
            out.append("period")
        if self.intent == "set_reminder" and self.remind_on is None:
            out.append("day")
        return out


# ---------------------------------------------------------------- text helpers
def tokens(text: str) -> list[str]:
    t = (text or "").lower().replace("?", " ? ")
    t = re.sub(r"[^\w?/₹ ]+", " ", t)
    return [_SPELLING.get(w, w) for w in t.split()]


def _has_phrase(toks: list[str], *phrase: str) -> bool:
    n = len(phrase)
    return any(tuple(toks[i:i + n]) == phrase for i in range(len(toks) - n + 1))


# ---------------------------------------------------------------- periods and days
def period(key: str, today: date) -> Period:
    """Fixed period maths. A week runs Monday to Sunday."""
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    return {
        "today": Period("today", today, today),
        "yesterday": Period("yesterday", today - timedelta(days=1), today - timedelta(days=1)),
        "this_week": Period("this_week", monday, today),
        "last_week": Period("last_week", monday - timedelta(days=7), monday - timedelta(days=1)),
        "this_month": Period("this_month", first, today),
        "last_month": Period("last_month", prev_last.replace(day=1), prev_last),
        "all": Period("all", date(2000, 1, 1), today),
    }[key]


def _day_of_month(n: int, today: date, future: bool) -> date | None:
    """'28 tarikh': the next 28th (reminders) or the most recent 28th (questions)."""
    y, m = today.year, today.month
    for _ in range(3):
        if 1 <= n <= calendar.monthrange(y, m)[1]:
            d = date(y, m, n)
            if (future and d > today) or (not future and d <= today):
                return d
        y, m = (y + (m == 12), m % 12 + 1) if future else (y - (m == 1), (m - 2) % 12 + 1)
    return None


def find_day(toks: list[str], today: date, future: bool) -> tuple[date | None, Period | None]:
    """A single day or a period named in the text. future=True for reminders ('kal' = tomorrow)."""
    joined = " ".join(toks)
    rel = re.search(r"\b(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s+(din|dino|dinon|hafte)\b(?:\s+(me|mein|baad|bad|pehle))?", joined)
    if rel and (future or rel.group(3) == "pehle"):
        n = int(rel.group(1)) if rel.group(1).isdigit() else NUMBER_WORDS[rel.group(1)]
        n *= 7 if rel.group(2) == "hafte" else 1
        day = today + timedelta(days=n) if future else today - timedelta(days=n)
        return day, Period("day", day, day)
    if future and (_has_phrase(toks, "agle", "hafte") or _has_phrase(toks, "agla", "hafte") or _has_phrase(toks, "next", "hafte")):
        day = today + timedelta(days=7 - today.weekday())  # next Monday
        return day, Period("day", day, day)
    if future and (_has_phrase(toks, "agle", "mahine") or _has_phrase(toks, "next", "mahine")):
        day = (today.replace(day=28) + timedelta(days=4)).replace(day=1)  # 1st of next month
        return day, Period("day", day, day)
    if not future:
        if re.search(r"\b(ab|abhi|aaj) tak\b|\bshuru se\b|\babtak\b", joined):
            return None, period("all", today)
        if _has_phrase(toks, "is", "hafte"):
            return None, period("this_week", today)
        if _has_phrase(toks, "pichhle", "hafte"):
            return None, period("last_week", today)
        if _has_phrase(toks, "is", "mahine"):
            return None, period("this_month", today)
        if _has_phrase(toks, "pichhle", "mahine"):
            return None, period("last_month", today)
    day = None
    for i, w in enumerate(toks):
        if w == "aaj":
            day = today
        elif w in ("kal", "kl", "tomorrow", "yesterday"):
            day = today + timedelta(days=1) if (future or w == "tomorrow") and w != "yesterday" else today - timedelta(days=1)
        elif w == "parso":
            day = today + timedelta(days=2 if future else -2)
        elif w in WEEKDAYS:
            diff = (WEEKDAYS[w] - today.weekday()) % 7
            day = today + timedelta(days=diff or 7) if future else today - timedelta(days=(today.weekday() - WEEKDAYS[w]) % 7)
        elif re.fullmatch(r"\d{1,2}/\d{1,2}(/\d{2,4})?", w):
            parts = [int(p) for p in w.split("/")]
            y = parts[2] + (2000 if len(parts) == 3 and parts[2] < 100 else 0) if len(parts) == 3 else today.year
            try:
                day = date(y, parts[1], parts[0])
            except ValueError:
                day = None
            if day and len(parts) == 2 and future and day <= today:
                try:
                    day = day.replace(year=today.year + 1)
                except ValueError:  # 29/02 in a year with no 29 Feb
                    day = None
        elif re.fullmatch(r"\d{1,2}", w) and i + 1 < len(toks) and toks[i + 1] in ("tarikh", "tareekh", "date"):
            day = _day_of_month(int(w), today, future)
        if day:
            break
    if day is None:
        return None, None
    key = {today: "today", today - timedelta(days=1): "yesterday"}.get(day, "day")
    return day, Period(key, day, day)


# ---------------------------------------------------------------- suppliers
def _score(span: str, cand: str) -> float:
    """Same tiers as conversation.match_supplier, plus misspelt words ('sharmaa' ~ 'sharma traders')."""
    if span == cand:
        return 1.0
    st, ct = set(span.split()), set(cand.split())
    if st and st <= ct:
        return 0.9
    if st and all(any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.85 for b in ct) for a in st):
        return 0.85
    ratio = difflib.SequenceMatcher(None, span, cand).ratio()
    return ratio if ratio >= 0.9 else 0.0  # 'sharma traders' vs 'verma traders' is 0.82: a different supplier


# Words many suppliers share: a match made ONLY of these ('traders') does not identify anyone.
GENERIC_NAME_WORDS = {"traders", "trader", "trading", "agency", "agencies", "enterprises", "enterprise", "store",
                      "stores", "medical", "medicals", "medicos", "pharma", "pharmacy", "distributors", "distributor",
                      "company", "co", "brothers", "bros", "sons", "general", "kirana", "hardware", "and", "the",
                      "industries", "suppliers", "supplier", "wholesale", "mart", "centre", "center", "house"}


def resolve_supplier(words: list[str], suppliers: list[dict], cutoff: float = 0.8):
    """(match, candidates, typed_text). Never picks one of two equally good matches."""
    words = [w for w in words if len(w) >= 2 and not w.isdigit()]
    if not words:
        return None, [], None
    typed = " ".join(words)
    best: dict[str, tuple[float, dict]] = {}
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            span = norm_supplier(" ".join(words[i:i + n])) or ""
            if len(span) < 3 or set(span.split()) <= GENERIC_NAME_WORDS:
                continue  # 'traders' alone must not pick 'Verma Traders' for 'Sharma Traders
            for s in suppliers:
                sc = _score(span, s["name_norm"])
                if sc:  # within a tier, a match on more typed words wins ('shrma tradrs': Traders, not Medical)
                    sc += 0.01 * (n - 1)
                if sc > best.get(s["id"], (0, None))[0]:
                    best[s["id"]] = (sc, s)
    if not best:
        return None, [], typed
    top = max(sc for sc, _ in best.values())
    if top < cutoff:
        return None, [], typed
    tied = [s for sc, s in best.values() if sc >= top - 1e-9]
    if len(tied) == 1:
        return tied[0], [], typed
    return None, sorted(tied, key=lambda s: s["name"]), typed


# ---------------------------------------------------------------- the rules
def looks_like_payment(text: str) -> bool:
    """'Sharma ko 5000 diye' is a PAYMENT to save (with a confirm button), never a question."""
    toks = tokens(text)
    return bool(_AMOUNT.search(text.lower())) and bool(PAY_WORDS & set(toks))


def _intent(toks: list[str]) -> str | None:
    s = set(toks)
    q = bool(Q_CUES & s)
    if REMIND_WORDS & s and ("yaad" not in s or s & {"dila", "dilana", "dilao", "dilado", "dilaana", "karana", "kara",
                                                      "rakhna", "reminder", "reminders"}):
        if CANCEL_WORDS & s:
            return "cancel_reminder"
        if s & {"reminder", "reminders"} and (LIST_WORDS & s or len(s) <= 2):
            return "list_reminders"
        return "set_reminder"
    if "sabse" in s and TOP_WORDS & s and ("baaki" in s or s & {"kiska", "kiske", "kiski", "dena"}):
        return "top_dues"
    if s & {"top"} and "baaki" in s:
        return "top_dues"
    in_period = _has_phrase(toks, "pichhle", "hafte") or _has_phrase(toks, "pichhle", "mahine")
    named_period = in_period or _has_phrase(toks, "is", "hafte") or _has_phrase(toks, "is", "mahine") \
        or bool(s & DAY_WORDS_Q and not s & NOT_ASKING)   # 'aaj ka payment' asks; 'aaj payment karna hai' does not
    if PAY_WORDS & s and ("kab" in s or (LAST_WORDS & s and not in_period)):  # 'pichhle hafte' is a period
        return "last_payment"
    if PAY_WORDS & s and (q or named_period):
        return "paid_period"
    if "baaki" in s or ("kitna" in s and s & {"dena", "dene"}):
        return "dues"
    if s & {"kis", "kiska", "kiske", "kiski", "kisko", "kaun", "kinka", "kinko"} and s & {"dena", "dene", "paisa", "paise"}:
        return "dues"  # 'kis kis ka paisa dena hai': whom do I owe
    if BILL_WORDS & s and (q or ARRIVE_WORDS & s or named_period):  # 'shiv medical ke bill is mahine' (Q023)
        return "bills_period"
    return None


def _wants_reminder(toks: list[str], today: date) -> bool:
    """'yaad dilana', 'yaad dila dena', 'remind', 'reminder' always; 'bata dena' only with a future day
    ('kal bata dena'), because 'kitna baaki hai bata dena' is an ordinary question."""
    s = set(toks)
    if s & {"remind", "reminder", "reminders"} or ("yaad" in s and s & {"dila", "dilana", "dilao", "dilado", "dilaana",
                                                                        "karana", "kara", "rakhna"}):
        return True
    if _has_phrase(toks, "bata", "dena") or _has_phrase(toks, "bata", "dijiye") or _has_phrase(toks, "batana"):
        return find_day(toks, today, future=True)[0] is not None
    return False


_NOTE_STOP = {
    "aur", "ha", "haan", "han", "haa", "ji", "achha", "acha", "accha", "ok", "okay", "bhai", "yaar", "please", "plz",
    "ko", "ka", "ki", "ke", "se", "me", "mein", "main", "pe", "par", "hai", "h", "hain", "mujhe", "is", "ye", "wala",
    "wale", "wali", "yaad", "dilana", "dila", "dilao", "dilado", "dilaana", "dena", "de", "remind", "reminder", "karna",
    "karo", "kar", "lagao", "laga", "lagana", "bata", "batana", "dijiye", "set", "aaj", "kal", "parso", "din", "dino",
    "dinon", "baad", "bad", "hafte", "mahine", "agle", "agla", "agli", "next", "tarikh", "tareekh", "date", "pehle",
    "on", "in", "me", "to", "the", "baaki", "hisaab",  # the reminder already shows the baaki
} | set(NUMBER_WORDS) | set(WEEKDAYS)


def reminder_note(toks: list[str], match: dict | None) -> str | None:
    """Words left after the supplier's name, the day and the reminder words: 'vapas krne', 'gst bharna', 'payment'."""
    names = set(match["name_norm"].split()) if match else set()
    rest = [w for w in toks if w not in _NOTE_STOP and not re.fullmatch(r"[\d/₹?]+", w) and len(w) >= 2
            and not any(difflib.SequenceMatcher(None, w, n).ratio() >= 0.85 for n in names)]
    return " ".join(rest) or None


def parse_question(text: str, suppliers: list[dict], today: date) -> Parsed:
    """Rules only. Returns Parsed(intent=None) for payments, typed bills and anything else.
    A reminder word ('yaad dilana', 'remind', 'kal bata dena') beats every other question type."""
    toks = tokens(text)
    s = set(toks)
    p = Parsed()
    leftover = [w for w in toks if w not in FILLER and w not in WEEKDAYS and not re.fullmatch(r"[\d/₹?]+", w)]
    match, cands, typed = resolve_supplier(leftover, suppliers)
    p.hisaab_like = bool(s & ({"baaki", "hisaab"} | PAY_WORDS | BILL_WORDS | REMIND_WORDS)) or bool(match or cands)
    if looks_like_payment(text):
        return p
    intent = "set_reminder" if _wants_reminder(toks, today) and not (CANCEL_WORDS & s or (
        s & {"reminder", "reminders"} and (LIST_WORDS & s or len(s) <= 2))) else _intent(toks)
    if intent is None and "hisaab" in s and not (match or cands or typed) and not s & NOT_ASKING \
            and find_day(toks, today, future=False)[1] is not None:
        intent = "period_summary"   # 'aaj ka hisaab', 'is hafte ka hisaab': bills, payments and the baaki together
    if intent is None and "hisaab" in s and (match or cands or typed or ALL_WORDS & s):
        intent = "dues"  # 'Sharma ka hisaab', 'saare supplier ka hisaab'; 'hisaab' alone opens the menu
    if intent is None:
        return p
    future = intent == "set_reminder"
    day, per = find_day(toks, today, future=future)
    if intent == "dues":
        intent = "dues_supplier" if (match or cands or typed) else "dues_all"
    p.intent = intent
    p.supplier, p.candidates, p.supplier_text = match, cands, typed if not match else None
    if intent == "set_reminder":
        p.remind_on = day if day and day > today else None
        if not match and not cands and typed:
            p.note, p.supplier_text = reminder_note(toks, None), None  # 'kal yaad dilana GST bharna': a note
        elif match:  # 'Sharma ko 3 din me yaad dilana vapas karne hai': keep 'vapas karne' as the note
            p.note = reminder_note(toks, match)
    elif intent in ("paid_period", "bills_period", "period_summary"):
        p.period = per
    if intent in ("dues_all", "top_dues", "list_reminders"):
        p.supplier = None
        p.candidates, p.supplier_text = [], None
    return p


def has_question_cue(text: str) -> bool:
    return bool(Q_CUES & set(tokens(text)))


def looks_like_question(text: str) -> bool:
    return parse_question(text, [], date.today()).intent is not None


# ---------------------------------------------------------------- optional AI fallback (classification only)
LLM_PROMPT = """You sort ONE message from a small Indian shop owner about their supplier ledger into a fixed question type.
You never calculate, guess or state any amount.
Today: {today}. The shop's suppliers: {suppliers}.
Message: "{text}"
Types: dues_all (total owed to all suppliers), dues_supplier (owed to one supplier), paid_period (how much was paid in a period),
bills_period (bills received in a period), top_dues (who is owed the most), last_payment (when was the last payment),
set_reminder (remind me on a day), list_reminders, cancel_reminder, unknown.
Reply with JSON only:
{{"intent": "<type>", "supplier": "<a name copied exactly from the list>" or null,
 "period": "today|yesterday|this_week|last_week|this_month|last_month|all" or null, "remind_on": "YYYY-MM-DD" or null}}
If you are not sure, intent is "unknown"."""


def llm_question_parser(text: str, supplier_names: list[str], today: date, client=None,
                        model: str = "claude-haiku-5-5") -> dict | None:
    """Haiku classifies only. Its output is checked against the fixed lists by from_llm()."""
    if client is None:
        from bot import ai_client
        client = ai_client.get()  # the shared client (bot/ai_client.py)
    prompt = LLM_PROMPT.format(today=today.isoformat(), suppliers=json.dumps(supplier_names[:50], ensure_ascii=False),
                               text=text[:300].replace('"', "'"))
    resp = client.messages.create(model=model, max_tokens=SMALL_CALL_MAX_TOKENS,
                                  messages=[{"role": "user", "content": prompt}])
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return None
    try:
        return parse_model_json("".join(getattr(b, "text", "") for b in resp.content))
    except (ValueError, json.JSONDecodeError):
        return None


def from_llm(data: dict | None, suppliers: list[dict], today: date) -> Parsed:
    """Validate the model's JSON: unknown intents, periods, suppliers or far-off days are dropped."""
    p = Parsed(hisaab_like=True)
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        return p
    p.intent = data["intent"]
    name = data.get("supplier")
    if isinstance(name, str) and name.strip() and p.intent not in ("dues_all", "top_dues", "list_reminders"):
        p.supplier, p.candidates, typed = resolve_supplier(tokens(name), suppliers)
        p.supplier_text = None if p.supplier else typed
    if data.get("period") in PERIOD_KEYS and p.intent in ("paid_period", "bills_period"):
        p.period = period(data["period"], today)
    if p.intent == "set_reminder" and isinstance(data.get("remind_on"), str):
        try:
            d = date.fromisoformat(data["remind_on"])
            p.remind_on = d if today < d <= today + timedelta(days=366) else None
        except ValueError:
            pass
    if p.intent == "dues_supplier" and not (p.supplier or p.candidates):
        p.intent = None  # the model named no known supplier: do not answer for the wrong one
    return p
