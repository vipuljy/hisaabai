"""The text engine: what does a typed message (or a voice-note transcript) want to RECORD? Rules only, no AI.

parse(text, suppliers, today, recent) -> Understood, one of:
  question   a reminder request (set / list / cancel): the question flow handles it (bot/questions.py)
  fix        change or remove an entry already saved: "8000 nahi 8500", "last wala galat hai", "Balaji ka bill hatao"
  entries    up to 3 parts, each one of
               bill      "Sharma Traders ka bill aaya 8000 ka, 15 din ka udhaar", "Verma Traders 4580"
               payment   "Sharma ko 5000 diye", "paid 4200 to Balaji"
               received  "Verma se 2000 aaye"      (money came IN: never saved as a payment without asking)
               future    "Gupta ko 5000 dena hai"  (not paid yet: never saved as a payment)
               kind      "Sharma ko 5000"          (amount + name but no verb: the owner picks bill / payment)
  none       nothing to record: the caller tries the question rules, then the menu

Order, per message: reminder words -> fix (correction / undo) -> split into parts (a part needs its own amount)
-> per part: question words (not an entry) -> received -> future -> payment -> bill -> verbless.
A self-correction inside a NEW entry ("Sharma ko teen hazaar... nahi nahi, chaar hazaar diye") keeps the last
amount; "Agarwal ko 3000 nahi 3500 diye the" is a FIX when a 3000 entry for Agarwal was saved in the last 7 days.
Numbers that are not money are taken out first: credit periods ("15 din ka udhaar", "ek mahine"), dates
("05/10", "28 tarikh"), invoice numbers ("bill no 2231"), counts ("2 bill", "50 customer"), phone numbers.
Nothing here saves anything: every entry becomes a card the owner confirms (bot/conversation.py).
"""

from __future__ import annotations

import calendar
import difflib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from bot import numbers as N
from bot import questions as Q
from bot.extract import norm_supplier

MAX_PARTS = 3
MAX_AMOUNT = 10_000_000   # Rs 1 crore: anything bigger is a misreading, never a card
SEP = "<sep>"

# ---------------------------------------------------------------- vocabulary
PAY_PAST = {"diye", "diya", "diyaa", "dia", "paid", "bheje", "bheja", "bheji", "chukaye", "chukaya", "chukayi", "chukta",
            "bhara", "bhare", "bhari", "transferred", "sent", "jama", "cleared", "pay", "payment", "transfer", "upi",
            "neft", "rtgs", "imps", "gpay", "paytm", "phonepe", "cheque"}
PAY_DONE = {"kiya", "kiye", "kiyaa", "kia", "ki", "kar", "karke", "kardiya", "kardiye", "diya", "diye", "hua", "ho",
            "gaya", "gya", "gaye", "done", "hogaya", "hogya", "kiyaa", "clear"}
PAY_ACTION = {"pay", "payment", "transfer", "upi", "neft", "rtgs", "imps", "gpay", "paytm", "phonepe", "online",
              "cheque", "jama", "clear", "cash", "bank", "bhugtan", "bhugtaan", "bhuktan"}
STRONG_PAST = {"diye", "diya", "diyaa", "dia", "diyo", "paid", "bheje", "bheja", "bheji", "chukaye", "chukaya", "chukayi",
               "bhara", "bhare", "bhari", "transferred", "sent", "cleared", "lauta", "lautaye", "lautaya", "lautai"}
REGIONAL_GAVE = {"dile", "dila", "dili", "didhu", "didha", "didhi", "aapya", "aapyu", "ditte", "dite", "ditta", "ditti",
                 "dittey"}  # Marathi / Gujarati / Punjabi 'gave' (with 'la' / 'nu' = 'to')
TOOK = (("le", "liye"), ("le", "liya"), ("le", "li"), ("le", "gaye"), ("le", "gaya"), ("le", "gye"), ("le", "gya"))
RELATIVE = {"baaki", "baki", "bacha", "bache", "bachi", "pending", "total", "discount", "gst", "tax", "remaining",
            "balance", "outstanding"}           # '5000 baaki', 'total 8000', 'gst 1440': not a new entry by itself
PART_OF = {"aadha", "adha", "aadhe", "half", "percent", "pratishat", "chauthai"}  # 'uska aadha de diya': amount unknown
NOT_PAYMENT_GIVE = {"time", "udhaar", "udhar", "credit", "bill", "bil", "invoice", "parchi", "maal", "saman", "samaan",
                    "order", "challan"}  # 'do hafte ka time diya', 'ne bill diya': diya, but not money paid
FUTURE = {"dena", "dene", "denge", "dunga", "doonga", "dungi", "dega", "degi", "deni", "chukana", "chukaenge",
          "chukayenge", "bhejna", "bhejunga", "bhejenge", "bhejni", "karna", "karenge", "karunga", "karungi", "dalna",
          "padega", "padenge", "hoga", "baad", "bad", "will"}
FUTURE_STRONG = {"dena", "dene", "denge", "dunga", "doonga", "dungi", "dega", "degi", "deni", "chukana", "chukaenge",
                 "chukayenge", "bhejna", "bhejunga", "bhejenge", "bhejni", "karenge", "karunga", "karungi", "will"}
RECEIVED = {"aaye", "aaya", "aayi", "aai", "aye", "aya", "ayi", "mile", "mila", "mili", "received", "receive", "recieved",
            "wapas", "vapas", "waapas", "return", "returned", "refund", "lautaye", "lautaya", "lautai", "lauta", "mil"}
MONEY_IN = {"wapas", "vapas", "waapas", "return", "returned", "refund", "lautaye", "lautaya", "lautai", "lauta"}
GOODS = {"maal", "mal", "saman", "samaan", "saaman", "stock", "dawai", "dawa", "dawaiyan", "goods", "items", "piece",
         "pieces", "peti", "box", "katte", "bori"}
SEND_BACK = {"kiya", "kiye", "kar", "kardiya", "diya", "diye", "diyo", "bheja", "bheje", "bhej", "lautaya", "lautaye",
             "lauta", "de"}               # 'maal wapas kiya / bheja / de diya': goods sent back
CASH = {"cash", "paise", "paisa", "nakad", "nakd", "rupaye", "rupay", "upi", "online", "gpay", "paytm", "phonepe"}
ME = {"mujhe", "mujhko", "humko", "hamko", "hame", "hamein", "humein", "hume"}
OLD_DUES = (("purana", "baaki"), ("purana", "baki"), ("pichla", "baaki"), ("pichhla", "baaki"), ("opening", "balance"),
            ("old", "balance"), ("purana", "udhaar"), ("pichla", "udhaar"), ("purana", "hisaab"))
# old dues said any common way: 'purana / pichla / pehle ka' + 'baaki / hisaab / udhaar', or 'baaki jodo / add karo'
OLD_WORDS = {"purana", "purane", "purani", "pichla", "pichhla", "pichle", "pichhle", "pichli", "pichhli", "old",
             "opening", "pehle", "pahle", "pehla", "pichhala"}
DUES_WORDS = {"baaki", "baki", "bakaya", "bakaaya", "hisaab", "hisab", "udhaar", "udhar", "udhari", "balance", "khata"}
ADD_WORDS = {"jodo", "jod", "joddo", "jodna", "joda", "jodiye", "add", "chadhao", "chadhado", "chadha", "chadhana"}
TOOK_GOODS = {"liya", "liye", "li", "lia", "liyaa", "uthaya", "uthaye", "uthaaya"}   # 'Gupta se 500 ka bartan liya'
DAMAGED = {"expiry", "expire", "expired", "kharab", "tuta", "tute", "toota", "toote", "damage", "damaged", "defective",
           "sada", "sade", "leak", "fata", "fate"}
OVERPAID = {"zyada", "jyada", "zada", "extra", "galti", "galat", "double", "dobara"}
BILL_WORDS = {"bill", "bil", "bills", "invoice", "parchi", "purchase", "kharida", "kharide", "kharidi", "kharid",
              "mangaya", "mangwaya", "mangvaya", "manga", "maal", "saman", "samaan", "saaman", "stock", "dawai", "dawa",
              "dawaiyan", "dawaiyaan", "goods", "order", "challan"}  # not 'liya': 'zyada le liye the' is money
GOODS_ARRIVE = {"aaya", "aayi", "aaye", "aai", "aya", "ayi", "aye", "liya", "liye", "aa", "mila", "mile", "mili"}
QUESTION = {"kya", "kyaa", "kab", "kitna", "kitne", "kitni", "kaun", "kiska", "kisko", "kiski", "kiske", "kaunsa",
            "konsa", "?", "batao", "btao", "bataiye", "dikhao", "kyun", "kyon"}
QUESTION_PHRASES = (("ya", "nahi"), ("ya", "nahin"), ("ki", "nahi"), ("ki", "nahin"), ("hua", "kya"))  # 'pay hua ya nahi' 
UNDO = {"hata", "hatao", "hatado", "hataa", "delete", "mita", "mitao", "mitado", "cancel", "radd", "remove", "nikalo",
        "hatana", "mitana"}
FIX = {"galat", "galt", "galti", "galati", "badlo", "badal", "badalna", "change", "correct", "edit", "sudhar",
       "sudharo"}
FIX_PHRASES = (("sahi", "karo"), ("sahi", "kar"), ("theek", "karo"), ("thik", "karo"), ("ki", "jagah"),
               ("ke", "badle"), ("ke", "jagah"))
NEGATION = {"nahi", "nahin", "nai", "nhi", "na", "no", "not"}
LAST_WORDS = {"last", "pichla", "pichhla", "pichle", "pichhle", "aakhri", "akhri", "abhi", "latest", "wala", "wali", "wale"}
PRONOUNS = {"unko", "unhe", "unhen", "usko", "use", "unka", "uska", "inko", "inhe", "isko", "unki", "unke", "uske",
            "unhone", "usne", "usme", "usmein", "unme", "unmein", "isme"}
MARKERS_PAY = {"ko", "to", "kou", "la", "nu", "noo"}
MARKERS_FROM = {"se", "from", "ne"}
REMINDER = {"yaad", "remind", "reminder", "reminders"}
UNITS = {"customer", "customers", "grahak", "log", "logo", "piece", "pieces", "pcs", "pc", "peti", "box", "boxes", "kilo",
         "kg", "kgs", "packet", "packets", "bori", "katta", "carton", "cartons", "dabba", "dabbe", "bottle", "bottles",
         "strip", "strips", "litre", "liter", "ltr", "gram", "gm", "quintal", "ton", "bag", "bags", "nag", "dozen",
         "item", "items", "saal", "baje", "minute", "minat", "ghante", "ghanta", "percent", "pratishat", "bill", "bills",
         "parchi", "parchiyan", "order", "orders", "tarikh", "tareekh", "number", "no", "admi", "ladke", "baar", "bar",
         "din", "dino", "dinon", "days", "day", "hafte", "hafta", "mahine", "mahina", "months", "month", "saal", "year"}
DAY_WORDS = {"din", "dino", "dinon", "days", "day", "divas"}
MONTH_WORDS = {"mahine", "mahina", "month", "months", "mahinon"}
CREDIT_CUES = {"udhaar", "udhar", "udhari", "credit", "baad", "bad", "me", "mein", "mai", "tak", "payment", "dena",
               "time", "ka", "ki", "ke", "due"}
INVOICE_CUES = {"invoice", "inv", "challan", "#"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
                                     start=1)}
MONTHS |= {"january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9,
           "sept": 9, "october": 10, "november": 11, "december": 12}
SPLIT_WORDS = {"aur", "and", "phir", "fir", "tatha", "also"}

# English, as some owners type it ('gupta gave me 500', 'paid sharma 2000', 'returned goods 500 to gupta'): a few
# verbs and the direction words around them. Used only when the message has no Hinglish grammar word (then the
# Hinglish rules decide). 16 Nov phone test: 'gupta gave me 500' had become a bill for "Gupta Gave".
EN_GIVE = {"gave", "give", "gives", "given", "giving"}
EN_PAID = {"paid", "pay", "pays", "sent", "send", "transferred", "transfer"}
EN_BACK = {"returned", "return", "returns", "refunded", "refund", "back"}
EN_GOT = {"got", "get", "gets", "received", "receive", "recieved", "recived"}
EN_BOUGHT = {"bought", "buy", "purchased", "ordered"}
EN_TOOK = {"took", "take", "taken", "takes"}
EN_OWE = {"owe", "owes", "owed"}
EN_ME = {"me", "us"}
EN_I = {"i", "we"}
EN_VERBS = EN_GIVE | EN_PAID | EN_BACK | EN_GOT | EN_BOUGHT | EN_TOOK | EN_OWE
EN_GRAMMAR = {"the", "of", "for", "worth", "my", "our", "his", "him", "them", "it", "is", "was", "has", "have", "had",
              "will", "just", "only", "with", "via"}
HINGLISH_GRAMMAR = {"ko", "ne", "se", "ka", "ki", "ke", "kiya", "kiye", "kia", "diya", "diye", "dia", "liya", "liye",
                    "lia", "mile", "mila", "mili", "aaye", "aaya", "aayi", "wapas", "vapas", "hai", "hain", "tha",
                    "thi", "dena", "dene", "maine", "mujhe", "kar", "karo", "karna", "gaya", "gaye", "hua"}
# a partial return: '500 ka maal wapas kiya, 300 hi kata' (the supplier accepted less than the goods were worth)
ACCEPTED = {"kata", "kaata", "kati", "kaati", "kaate", "kate", "katke", "kaatke", "mana", "maana", "maane", "mane",
            "mani", "maani", "accept", "accepted", "credit"}
ONLY = {"hi", "sirf", "bas", "only", "just"}

# words that are never part of a supplier's name
FILLER = (Q.FILLER | PAY_PAST | PAY_DONE | PAY_ACTION | FUTURE | RECEIVED | BILL_WORDS | QUESTION | UNDO | FIX
          | NEGATION | LAST_WORDS | PRONOUNS | MARKERS_PAY | MARKERS_FROM | N.CURRENCY | DAY_WORDS | MONTH_WORDS
          | CREDIT_CUES | INVOICE_CUES | SPLIT_WORDS | set(N.MULTIPLIERS) | set(N.FRACTIONS) | {
              "maine", "mene", "humne", "hamne", "naya", "nayi", "naye", "new", "total", "amount", "bhi", "the", "tha",
              "thi", "hai", "hain", "h", "ho", "gaya", "gayi", "gaye", "rakho", "likho", "likh", "save", "daal", "dal",
              "dala", "daala", "diya", "entry", "net", "grand", "aaj", "kal", "parso", "abhi", "matlab", "sorry",
              "haan", "ha", "ok", "okay", "accha", "acha", "achha", "bas", "toh", "to", "par", "pe", "wala", "wali",
              "wale", "waala", "waale", "rupay", "rupaye", "rupees", "ji", "sahab", "saab", "sir", "bhaiya", "bhai",
              "aapne", "mujhe", "hum", "main", "mai", "mera", "meri", "mere", "humara", "hamara", "uncle", "number",
              "num", "no", "nahi", "date", "tarikh", "dated", "rs", "sab", "upar", "neeche", "kuch", "aise", "waise",
              "hi", "please", "pls", "plz", "yaar", "ek", "do", "sahi", "theek", "thik", "karo", "jagah", "badle",
              "today", "yesterday", "kardo", "kar", "do", "ke", "ki", "ka", "aa", "gya", "gyi", "hua", "hue", "huye",
              "suno", "sunno", "suniye", "sunie", "sunna", "dekho", "dekhiye", "bolo", "arey", "are", "arre", "oye",
              "advance", "advanced", "done", "le", "liya", "liye", "li", "lene", "zyada", "jyada", "kam", "wo", "woh",
              "vo", "actually", "asal", "asli", "jo", "subah", "shaam", "sham", "raat", "dopahar", "abhi", "baaki",
              "remaining", "bacha", "bache", "aur", "fir", "phir", "unka", "unki", "unke", "naya", "purana", "wala",
              "time", "expiry", "kharab", "tuta", "tute", "first", "second", "st", "nd", "rd", "th", "pehle", "pahle",
              "ago", "hogaya", "hogya", "clear", "pay", "ye", "yeh", "jot", "lo", "likho", "likh", "daalo", "dalo",
              "jod", "jodo", "note", "karlo", "amt", "week", "weeks", "usme", "usmein", "isme", "ismein", "unme",
              "unmein", "credit", "pending", "discount", "gst", "tax", "remaining", "balance", "paise", "paisa",
              "entry", "che", "chhe", "hu", "hoon", "bhejta", "gaye", "gayi", "utare", "bana", "bani", "jaana",
              "jana", "aadha", "adha", "half", "katte", "tempo"} | REGIONAL_GAVE | OLD_WORDS | DUES_WORDS | ADD_WORDS
          | TOOK_GOODS | DAMAGED | OVERPAID | EN_VERBS | EN_ME | EN_I | EN_GRAMMAR | ACCEPTED | ONLY)


# ---------------------------------------------------------------- tokens
_TOKEN = re.compile(r"\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?(?!\d)|\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}(?:st|nd|rd|th)\b"
                    r"|\d+(?:,\d+)*(?:\.\d+)?k?"
                    r"|[a-zऀ-ॿ]+|₹|\?|[,;\n|]|\.{1,}|#", re.I)
_DATE_TOK = re.compile(r"\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|\d{1,2}\.\d{1,2}\.\d{2,4}")  # '1.5' is a number


def tokenize(text: str) -> tuple[list[str], list[str]]:
    """(lower-case tokens, original-case tokens). Punctuation that ends a clause becomes SEP."""
    t = (text or "").translate(N._DEVANAGARI_DIGITS).replace("/-", " ")
    t = re.sub(r"(?<=[A-Za-z₹])\.?(?=\d)", " ", t)
    low, orig = [], []
    for m in _TOKEN.finditer(t):
        tok = m.group(0)
        if tok in {",", ";", "\n", "|"} or tok.startswith("."):
            if low and low[-1] != SEP:
                low.append(SEP)
                orig.append(SEP)
            continue
        low.append(tok.lower())
        orig.append(tok)
    while low and low[-1] == SEP:
        low.pop()
        orig.pop()
    return low, orig


def _phrase(toks, *words) -> bool:
    n = len(words)
    return any(tuple(toks[i:i + n]) == words for i in range(len(toks) - n + 1))


# ---------------------------------------------------------------- results
@dataclass
class Part:
    intent: str                        # bill | payment | received | future | kind
    amount: int | None = None
    supplier: dict | None = None       # one of the shop's suppliers, matched
    candidates: list = field(default_factory=list)   # 2+ suppliers fit: the owner picks
    name: str | None = None            # the name as written (for a new supplier), original case
    credit_days: int | None = None
    bill_date: date | None = None      # bills: the bill's date; payments: the day it was paid
    invoice: str | None = None
    day: date | None = None            # future: the day the owner said ("kal dena hai")
    pronoun: bool = False              # "unko": the supplier of the part before
    corrected_from: int | None = None  # "teen hazaar... nahi, chaar hazaar": 3000
    joined: bool = False               # came after 'aur' / 'phir' (not after a comma)
    llm_amount: bool = False           # the AI classifier gave an amount our number reader did not find
    marker: str | None = None          # 'ko' (to: a payment) or 'ka' / 'se' (of / from: a bill) in the message
    ask: str | None = None             # received: 'what' (goods or money back?) / 'dir' (did you give, or get?)
    why: str | None = None             # refund: 'goods' (goods were sent back) / 'over' (paid too much) / None: ask
    raw: str = ""
    amounts: tuple = ()                # partial return: (goods value, what the supplier accepted), in any order: asked
    note: str | None = None            # one extra line for the card ('₹500 ka maal, supplier ne ₹300 maana')
    cash_checked: bool = False         # goods back: the owner already said money came in hand (not cut from the baaki)


@dataclass
class Fix:
    action: str                        # edit | undo
    new_amount: int | None = None
    old_amount: int | None = None
    supplier: dict | None = None
    candidates: list = field(default_factory=list)
    name: str | None = None
    hint: str | None = None            # bill | payment | None
    raw: str = ""


@dataclass
class Understood:
    kind: str                          # question | fix | entries | too_many | none
    parts: list = field(default_factory=list)
    fix: Fix | None = None             # 'entries' may also carry one fix ('... 6000 kar do aur Sharma ko 2000 diye')
    skipped: list = field(default_factory=list)   # pieces with an amount that recorded nothing: the owner is told
    order: list = field(default_factory=list)     # ('fix', Fix) / ('part', Part) in the order the owner said them
    reason: str = ""


# ---------------------------------------------------------------- numbers that are not money
_ORDINAL = re.compile(r"(\d{1,2})(?:st|nd|rd|th)")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _date_from_token(tok: str, today: date) -> date | None:
    """'05/10', '05/10/26', '05-10-2026', '5.10.26' (day first). A year must have 2 or 4 digits; a day-month date
    later than tomorrow is last year's; a full date later than tomorrow is refused."""
    parts = re.split(r"[/\-.]", tok)
    if len(parts) == 3 and len(parts[2]) not in (2, 4):
        return None
    nums = [int(p) for p in parts]
    d, m = nums[0], nums[1]
    if len(nums) == 3:
        y = nums[2] + (2000 if nums[2] < 100 else 0)
        out = _safe_date(y, m, d)
        return out if out and out <= today + timedelta(days=1) else None
    out = _safe_date(today.year, m, d)
    if out and out > today + timedelta(days=1):
        out = _safe_date(today.year - 1, m, d)
    return out


def _day_of_month(n: int, today: date, future: bool = False) -> date | None:
    """'28 tarikh': the most recent 28th (a bill, a payment) or the next one ('28 tarikh tak dena')."""
    y, m = today.year, today.month
    for _ in range(3):
        if 1 <= n <= calendar.monthrange(y, m)[1]:
            d = date(y, m, n)
            if (future and d >= today) or (not future and d <= today):
                return d
        y, m = (y + (m == 12), m % 12 + 1) if future else (y - (m == 1), (m - 2) % 12 + 1)
    return None


def _month_day(n: int, month: int, today: date, future: bool) -> date | None:
    d = _safe_date(today.year, month, n)
    if d is None:
        return None
    if future and d < today:
        return _safe_date(today.year + 1, month, n)
    if not future and d > today + timedelta(days=1):
        return _safe_date(today.year - 1, month, n)
    return d


@dataclass
class _Scan:
    """What one pass over the tokens found besides money."""
    skip: set = field(default_factory=set)       # token positions that are not money / not a name
    credit_days: int | None = None               # '15 din ka udhaar', 'credit 45 days', 'ek mahine'
    plain_days: int | None = None                # '30 din' with no cue: credit days on a bill, nothing elsewhere
    past_days: int | None = None                 # '3 din pehle'
    dates: list = field(default_factory=list)    # (date, kind) in the order written. kind: 'any' (05/10, 28 tarikh),
    #                                              'future' ('20 tarikh tak', 'due 05/11'), 'past' / 'later' ('1st nov')
    invoice: str | None = None


def _credit(value) -> int | None:
    return int(value) if value is not None and 0 <= value <= 365 else None


def _scan(toks: list[str], orig: list[str], today: date) -> _Scan:
    s = _Scan()
    amounts = N.find_amounts(toks)
    n = len(toks)
    # invoice numbers: 'bill no 2231', 'invoice 4471'. Only when another amount is there to be the money:
    # 'Verma traders invoice 11k' is an invoice OF 11k.
    invoice_spans = []
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < n else ""
        if tok in INVOICE_CUES or (tok in {"bill", "bil"} and nxt in {"no", "number", "num", "nambar", "#"}):
            j = i + 1
            while j < n and toks[j] in {"no", "number", "num", "nambar", "#", SEP}:
                j += 1
            if j < n and re.search(r"\d", toks[j]) and not _DATE_TOK.fullmatch(toks[j]) \
                    and not toks[j].endswith("k") and "," not in toks[j]:
                invoice_spans.append((i + 1, j + 1, orig[j]))
    money_like = [a for a in amounts if not any(x <= a.start < y for x, y, _ in invoice_spans)]
    if money_like:
        for x, y, inv in invoice_spans:
            s.skip.update(range(x, y))
            s.invoice = s.invoice or inv
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < n else ""
        prev = toks[i - 1] if i > 0 else ""
        if _DATE_TOK.fullmatch(tok):
            s.skip.add(i)
            d = _date_from_token(tok, today) if not (nxt == "tak" or prev in {"due", "tak"}) else \
                _future_from_token(tok, today)
            if d:
                s.dates.append((d, "future" if (nxt == "tak" or prev in {"due", "tak"}) else "any"))
            continue
        m = _ORDINAL.fullmatch(tok)
        if m:  # '1st nov', '2nd'
            s.skip.add(i)
            if nxt in MONTHS:
                s.skip.add(i + 1)
                for kind, fut in (("past", False), ("later", True)):
                    d = _month_day(int(m.group(1)), MONTHS[nxt], today, fut)
                    if d:
                        s.dates.append((d, kind))
            continue
    for a in amounts:
        span = set(range(a.start, a.end))
        if span & s.skip:
            continue
        after = toks[a.end] if a.end < n else ""
        after2 = toks[a.end + 1] if a.end + 1 < n else ""
        after3 = toks[a.end + 2] if a.end + 2 < n else ""
        before = toks[a.start - 1] if a.start > 0 else ""
        digits = "".join(c for c in "".join(toks[a.start:a.end]) if c.isdigit())
        if len(digits) >= 10 and not a.words:
            s.skip |= span       # a phone number
            continue
        if after in DAY_WORDS | MONTH_WORDS | {"hafte", "hafta", "week", "weeks"}:
            unit = 30 if after in MONTH_WORDS else 7 if after in {"hafte", "hafta", "week", "weeks"} else 1
            s.skip |= span | {a.end}
            if after2 in {"pehle", "pahle", "ago", "purana", "purani"}:
                s.skip.add(a.end + 1)
                s.past_days = s.past_days or a.value * unit       # '3 din pehle'
            elif after2 in CREDIT_CUES or before in {"credit", "udhaar", "udhar"}:
                s.credit_days = s.credit_days or _credit(a.value * unit)
            elif after2 in {"baad"}:
                pass
            else:
                s.plain_days = s.plain_days or _credit(a.value * unit)
            continue
        if after in {"tarikh", "tareekh", "date"} and 1 <= a.value <= 31 and not a.words:
            s.skip |= span | {a.end}
            fut = after2 == "tak" or after3 == "tak"
            d = _day_of_month(a.value, today, future=fut)
            if d:
                s.dates.append((d, "future" if fut else "any"))
            continue
        if after in MONTHS and 1 <= a.value <= 31 and not a.words:
            s.skip |= span | {a.end}
            for kind, fut in (("past", False), ("later", True)):  # '1st nov': before or after today, by the verb
                d = _month_day(a.value, MONTHS[after], today, fut)
                if d:
                    s.dates.append((d, kind))
            continue
        if after in UNITS - {"bill", "bills", "parchi"} or after in {"%"}:
            s.skip |= span
            continue
        if after in {"bill", "bills", "parchi", "parchiyan"} and a.value < 100 and not a.currency:
            s.skip |= span       # '2 bill aaye': a count
            continue
    # word periods: 'ek mahine ka udhaar', 'dedh mahine', 'do hafte ka time'
    for i, tok in enumerate(toks):
        if tok in MONTH_WORDS | DAY_WORDS | {"hafte", "hafta"} and i > 0 and i not in s.skip:
            prev = toks[i - 1]
            val = N.WORD_VALUES.get(prev)
            if val is not None:
                nxt = toks[i + 1] if i + 1 < n else ""
                s.skip |= {i - 1, i}
                days = int(val * (30 if tok in MONTH_WORDS else 1 if tok in DAY_WORDS else 7))
                if nxt in {"pehle", "pahle"}:
                    s.past_days = s.past_days or days
                elif nxt in CREDIT_CUES:
                    s.credit_days = s.credit_days or _credit(days)
                else:
                    s.plain_days = s.plain_days or _credit(days)
    return s


def _future_from_token(tok: str, today: date) -> date | None:
    parts = re.split(r"[/\-.]", tok)
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        if len(parts[2]) not in (2, 4):
            return None
        return _safe_date(nums[2] + (2000 if nums[2] < 100 else 0), nums[1], nums[0])
    d = _safe_date(today.year, nums[1], nums[0])
    return d if d and d >= today else _safe_date(today.year + 1, nums[1], nums[0])


def _money(toks: list[str], skip: set) -> list[N.Amount]:
    return [a for a in N.find_amounts(toks) if not set(range(a.start, a.end)) & skip]


# ---------------------------------------------------------------- supplier names
def _soft_score(a: str, b: str) -> float:
    """How alike two normalised names look (for the 'did you mean' list, never for a silent match). Only the
    distinctive words count: 'Sharma Traders' and 'Verma Traders' share just 'traders', so they are not alike;
    'Sharma Trading' and 'Sharma Traders', or 'Sharmaa' and 'Sharma Medical', are."""
    if not a or not b:
        return 0.0
    at, bt = set(a.split()) - Q.GENERIC_NAME_WORDS, set(b.split()) - Q.GENERIC_NAME_WORDS
    if not at or not bt:
        return 0.0
    best = max(difflib.SequenceMatcher(None, x, y).ratio() for x in at for y in bt)
    return best if best >= 0.8 else 0.0


def skeleton(word: str) -> str:
    """Consonants only, with sounds speech-to-text confuses made equal: 'burma' / 'verma' -> 'brm', 'thakur' /
    'dhakur' -> 'tkr'. Used only to OFFER a supplier in the list, never to pick one."""
    w = word.lower()
    for a, b in (("ph", "f"), ("bh", "b"), ("dh", "d"), ("th", "t"), ("kh", "k"), ("gh", "g"), ("sh", "s"), ("ch", "c"),
                 ("jh", "j"), ("v", "b"), ("w", "b"), ("z", "j"), ("q", "k"), ("ck", "k"), ("x", "ks")):
        w = w.replace(a, b)
    w = re.sub(r"[aeiouy]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def _sounds_alike(a: str, b: str) -> bool:
    at, bt = set(a.split()) - Q.GENERIC_NAME_WORDS, set(b.split()) - Q.GENERIC_NAME_WORDS
    return any(len(skeleton(x)) >= 2 and skeleton(x) == skeleton(y) for x in at for y in bt)


def close_suppliers(name: str, suppliers: list[dict], k: int = 3, cutoff: float = 0.8) -> list[dict]:
    """Suppliers that look like the typed name ('Sharma Trading' -> Sharma Traders): offered in a list, never picked."""
    target = norm_supplier(name) or ""
    scored = sorted(((max(_soft_score(target, s["name_norm"]), 0.8 if _sounds_alike(target, s["name_norm"]) else 0), s)
                     for s in suppliers), key=lambda x: (-x[0], x[1]["name"]))
    return [s for sc, s in scored if sc >= cutoff][:k]


def _runs(toks: list[str], skip: set) -> list[tuple[int, int]]:
    """Runs of tokens that could be a name: letters, not filler, not skipped."""
    runs, start = [], None
    for i, tok in enumerate(toks + [SEP]):
        ok = (i < len(toks) and i not in skip and tok != SEP and tok not in FILLER and tok not in N.WORD_VALUES
              and re.fullmatch(r"[a-zऀ-ॿ][a-zऀ-ॿ&.']*", tok) is not None and len(tok) >= 2)
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            runs.append((start, i))
            start = None
    return runs


def strict_match(words: list[str], suppliers: list[dict]):
    """(match, candidates) for a name the owner wrote or a bill printed. A supplier is matched without asking only if
    it is the same name by the eval's rule (exact, one name the start of the other, near-identical spelling), or if
    EVERY word written is in that supplier's name ('balaji' -> Balaji Enterprises, 'shiv medical' -> Shiv Medical
    Distributors). 'Shree Balaji Medical Agency' never silently becomes 'Balaji Enterprises', nor 'Sharma Brothers'
    'Sharma Traders': those are offered in the list instead."""
    from bot.conversation import match_supplier
    name = " ".join(words)
    m = match_supplier(name, suppliers)
    if m:
        return m, []
    match, cands, _ = Q.resolve_supplier(words, suppliers)
    if match:
        typed = set((norm_supplier(name) or "").split())
        have = set(match["name_norm"].split())
        if all(any(difflib.SequenceMatcher(None, w, h).ratio() >= 0.85 for h in have) for w in typed):
            return match, []
        return None, [match]
    return None, cands


def find_supplier(toks: list[str], orig: list[str], skip: set, suppliers: list[dict]):
    """(match, candidates, name as written). The name is the run of name-like words, preferring one that matches a
    known supplier (also inside a longer run: 'sunno balaji enterprises'), then the one right before 'ko / ka / se /
    ne' or right after 'to / from', then the first."""
    runs = _runs(toks, skip)
    if not runs:
        return None, [], None
    scored = []
    for a, b in runs:
        match, cands = strict_match(toks[a:b][-5:], suppliers)
        sa, sb = a, b
        if not match:  # a supplier's FULL name inside a longer run ('accha balaji enterprises'): only an exact name
            for size in range(b - a - 1, 0, -1):
                hit = next(((x, sup) for x in range(a, b - size + 1) for sup in suppliers
                            if norm_supplier(" ".join(toks[x:x + size])) == sup["name_norm"]), None)
                if hit:
                    match, cands, sa, sb = hit[1], [], hit[0], hit[0] + size
                    break
        after = toks[b] if b < len(toks) else ""
        before = toks[a - 1] if a > 0 else ""
        marker = after in MARKERS_PAY | MARKERS_FROM | {"ka", "ki", "ke", "wala", "wale", "wali", "ji"} \
            or before in {"to", "from"}
        rank = (0 if match else 1 if cands else 2, 0 if marker else 1, a)
        scored.append((rank, sa, sb, match, cands))
    scored.sort(key=lambda x: x[0])
    _, a, b, match, cands = scored[0]
    a = max(a, b - 5)
    if set(toks[a:b]) <= Q.GENERIC_NAME_WORDS:
        return None, [], None  # 'traders' alone names nobody
    name = " ".join(w if any(c.isupper() for c in w) else w.title() for w in orig[a:b])
    return match, cands, name


# ---------------------------------------------------------------- one part
def _action_done(toks: list[str]) -> bool:
    """'payment kar diya', 'upi kiya', 'cash diya', 'transfer kiye', 'payment ki', 'payment done', 'kari didhu'."""
    for i, t in enumerate(toks):
        if t in PAY_ACTION:
            nxt = toks[i + 1:i + 4]
            if set(nxt) & (PAY_DONE | REGIONAL_GAVE | {"kari", "kidhu", "kela"}) and not set(nxt) & NEGATION:
                return True
    return False


def _denied(toks: list[str]) -> bool:
    """A verb that the owner says did NOT happen: 'Sharma ko 2000 nahi diye'."""
    return any(t in STRONG_PAST | REGIONAL_GAVE | {"aaya", "aayi", "aaye", "kiya", "kiye", "mila", "mile"}
               and _negated_at(toks, i) for i, t in enumerate(toks))


def _negated_at(toks: list[str], i: int) -> bool:
    """'nahi diye', 'abhi nahi diya', 'nahi bhi diya': the verb at i is denied. Not '3000 nahi 3500 diye' (there
    'nahi' corrects the amount)."""
    if i > 0 and toks[i - 1] in NEGATION:
        return True
    return i > 1 and toks[i - 1] in {"abhi", "bhi", "to", "toh", "tak", "koi", "kuch"} and toks[i - 2] in NEGATION


def _gave_money(toks: list[str]) -> bool:
    """A past 'gave' verb that is about money: not 'do hafte ka time diya', not 'ne bill diya', not 'nahi diya'."""
    for i, t in enumerate(toks):
        if t in STRONG_PAST | REGIONAL_GAVE:
            if _negated_at(toks, i):
                continue
            if t in {"diya", "diye", "dia", "diyaa", "diyo"} and set(toks[max(0, i - 3):i]) & NOT_PAYMENT_GIVE \
                    and not (i > 0 and toks[i - 1] in {"bhar", "chuka", "jama", "clear", "pay", "chukta"}):
                continue  # 'bill bhar diya' / 'chuka diya' IS a payment
            return True
    return False


def _amount_tok(t: str) -> bool:
    return any(c.isdigit() for c in t) or t in N.CURRENCY or t in set(N.MULTIPLIERS)


def _english(toks: list[str]) -> str | None:
    """The same intents for a message typed in English. None: not English (or not clear): the other rules decide."""
    s = set(toks)
    if not s & EN_VERBS or s & HINGLISH_GRAMMAR:
        return None
    vi = next(i for i, t in enumerate(toks) if t in EN_VERBS)
    before, after = set(toks[:vi]), toks[vi + 1:]
    goods, back, cash = bool(s & GOODS), bool(s & EN_BACK), bool(s & CASH)
    i_said = vi == 0 or bool(before & EN_I)            # 'paid gupta 500', 'i gave 500 to gupta'
    shorthand = vi > 0 and _amount_tok(toks[vi - 1])   # 'gupta 500 paid'
    to_me = bool(set(after[:4]) & EN_ME)               # 'gupta gave me 500', 'gupta paid us 500'
    to_them = "to" in after
    if s & EN_OWE:
        return "future"                                # 'i owe gupta 500'
    if s & EN_GOT:                                     # 'got 500 from gupta': goods or money?
        return "refund" if back or (cash and not goods) else "bill" if goods else "received"
    if s & EN_BOUGHT:
        return "bill"                                  # 'bought 500 from gupta'
    if s & EN_TOOK:
        if back:
            return "return" if goods else None         # 'gupta took back the goods': the dues go down
        return "bill" if goods else "received" if "from" in s else None
    if to_me:
        return "refund"                                # 'gupta gave me 500', 'gupta paid me 500'
    if back and goods and (to_them or i_said):
        return "return"                                # 'returned goods 500 to gupta', 'sent back the goods'
    if back and ("from" in s or not i_said):
        return "refund"                                # 'gupta returned 500 (for expired goods)', 'refund 500 from gupta'
    if back:
        return None                                    # 'i returned 500 to gupta': money or goods? asked
    if i_said or to_them or shorthand:
        return "payment"                               # 'paid gupta 500', 'sent 500 to gupta', 'gupta 500 paid'
    return "received_ne"                               # 'gupta gave 500', 'gupta paid 500': which way? asked


def _classify(toks: list[str]) -> str | None:
    """bill | udhaar | return | payment | refund | received | received_ne | future | None.
    received = 'Gupta se 500 aaye' (goods or money? the owner picks); received_ne = 'Sharma ne 500 diya' (to or by
    Sharma? asked); refund = money clearly came back ('wapas aaye', 'mujhe diye'); return = goods sent back."""
    s = set(toks)
    en = _english(toks)
    if en:
        return en
    if _old_dues(toks) and not (_gave_money(toks) or _action_done(toks)):
        return "udhaar"         # 'Gupta ka purana baaki 5000', 'Sharma ka 1000 pichla hisab jodo': old dues, no bill
    credit_note = _phrase(toks, "credit", "note")
    if (s & GOODS and s & (MONEY_IN - {"refund"}) and s & SEND_BACK and not s & CASH and not s & {"se", "ne"}) \
            or (credit_note and not s & CASH):
        return "return"         # 'Gupta ko 500 ka maal wapas kiya', 'credit note 500 ka': the dues go DOWN
        #                         (not 'X ne ... refund diya': the supplier gave money back, a refund)
    if s & GOODS and s & (MONEY_IN - {"refund"}) and (s & TOOK_GOODS or any(_phrase(toks, *p) for p in TOOK)) \
            and not s & CASH and "se" not in s:
        return "return"         # 'Gupta ne maal wapas liya / le liya': the supplier took the goods back (16 Nov)
    has_bill = bool(s & BILL_WORDS)
    gave = _gave_money(toks)
    pay_past = gave or _action_done(toks) or _phrase(toks, "de", "di") or (("di" in s) and bool(s & MARKERS_PAY))
    future = bool(s & FUTURE_STRONG) or any(t in PAY_ACTION and set(toks[i + 1:i + 4]) & {"karna", "karenge", "karunga"}
                                            for i, t in enumerate(toks)) \
        or _phrase(toks, "baad", "mein", "denge") or (bool(s & {"baad", "bad"}) and bool(s & (FUTURE - {"baad", "bad"})))
    received = bool(s & RECEIVED) and bool(s & MARKERS_FROM)
    money_in = bool(s & MONEY_IN)
    subject_ne = "ne" in s
    if future and not gave:
        return "future"
    if (money_in or (credit_note and s & CASH)) and (not has_bill or "se" in s or subject_ne):
        return "refund"         # 'se 2000 wapas aaye (expiry maal ke)', 'ne 300 lauta diye': money back, dues go UP
    if subject_ne and any(_phrase(toks, *p) for p in TOOK) and not has_bill:
        return "payment"        # 'Sharma Medical ne paise le liye': the supplier TOOK the money
    if subject_ne and pay_past and not has_bill:
        if s & ME:
            return "refund"     # 'Sharma ne mujhe 5000 diye': money came to the owner
        return "received_ne"    # 'Sharma ne 5000 diye': gave TO or BY Sharma? (both are said): the owner is asked
    if subject_ne and has_bill:
        return "bill"           # 'Gupta Agency ne 7500 ka bill diya'
    if pay_past and "ko" in s:
        return "payment"
    if pay_past and not received:
        if has_bill and not (s & (PAY_ACTION | {"bhar", "bhara", "bhari", "chuka", "chukaya", "chukaye"})) \
                and not (s & STRONG_PAST - {"diye", "diya", "dia", "diyaa"}):
            return "payment"
        return "payment"
    if "se" in s and s & TOOK_GOODS and not subject_ne and not s & CASH and not money_in:
        if has_bill or _item_named(toks) or s & {"udhaar", "udhar", "credit"}:
            return "bill"       # 'Gupta se 500 ka bartan liya', 'Gupta se 2000 udhaar liya': bought (on credit)
        return "received"       # 'Gupta se 500 liye': goods or money? the owner picks
    if has_bill and future is False and not any(t in GOODS_ARRIVE and _negated_at(toks, i) for i, t in enumerate(toks)):
        return "bill"           # (but 'bill abhi nahi aaya' is not a bill; 'bill ... abhi tak nahi diya' still is)
    if received or (s & {"mil"} and "se" in s):
        return "received"       # 'Gupta se 500 aaye': goods or money? the owner picks
    if s & PAY_ACTION - {"payment", "pay", "bank", "clear", "jama"} and s & MARKERS_PAY and not _denied(toks):
        return "payment"        # 'verma traders ko saade saat sau upi'
    return None


def _old_dues(toks: list[str]) -> bool:
    """'purana baaki', 'pichle mahine ka hisab', 'pehle ka udhaar', 'Sharma ka baaki jodo 5000'."""
    for i, t in enumerate(toks):
        if t in DUES_WORDS:
            if set(toks[max(0, i - 3):i]) & OLD_WORDS or any(_phrase(toks, *p) for p in OLD_DUES):
                return True
            if set(toks) & ADD_WORDS or _phrase(toks, "add", "karo") or _phrase(toks, "add", "kar"):
                return True
    return any(_phrase(toks, *p) for p in OLD_DUES)


def _item_named(toks: list[str]) -> bool:
    """'500 ka bartan', '2000 ki dawai': a thing named after 'ka / ki / ke' (not 'ka liya', not a number)."""
    for i, t in enumerate(toks[:-1]):
        nxt = toks[i + 1]
        if t in {"ka", "ki", "ke"} and nxt != SEP and nxt not in FILLER and nxt not in N.WORD_VALUES \
                and re.fullmatch(r"[a-z]+", nxt) and len(nxt) >= 3:
            return True
    return False


CONFIRM = {"theek", "thik", "hai", "h", "ok", "okay", "sahi", "na", "haina", "hain", "samjhe", "bhai", "ji", "?", "achha",
           "accha", "acha", "bas"}


def _is_question(toks: list[str]) -> bool:
    """A question word or 'ya nahi'. A trailing 'Theek hai?' after an entry is not a question about it."""
    pieces, cur = [], []
    for t in toks + [SEP]:
        if t == SEP:
            pieces.append(cur)
            cur = []
        else:
            cur.append(t)
    for piece in pieces:
        if not piece or set(piece) <= CONFIRM:
            continue
        if set(piece) & QUESTION or any(_phrase(piece, *p) for p in QUESTION_PHRASES):
            return True
    return False


def _part(toks, orig, suppliers, today, s: _Scan, whole: bool) -> Part | None:
    """One segment -> a Part, or None if it records nothing."""
    if _is_question(toks):
        return None
    money = _money(toks, s.skip)
    if not money:
        return None
    corrected_from = None
    name_toks = toks
    if len(money) == 2:
        between = toks[money[0].end:money[1].start]
        if set(between) & NEGATION:
            corrected_from = money[0].value
            neg = money[0].end + max(i for i, t in enumerate(between) if t in NEGATION)
            if _runs(toks[neg + 1:], set()):  # 'Sharma ko 3000 nahi Verma ko 4000': the name after 'nahi'
                name_toks = [SEP] * (neg + 1) + toks[neg + 1:]
            money = [money[1]]
    if len(money) != 1:
        return None
    amt = money[0]
    if amt.value > MAX_AMOUNT or set(toks) & PART_OF:
        return None            # too big, or 'uska aadha de diya': the real amount is not written
    intent = _classify(toks)
    ask = None
    if intent == "received_ne":
        intent, ask = "received", "dir"
    elif intent == "received":
        ask = "what"
    if intent is None and _denied(toks):
        return None            # 'Sharma ko 2000 nahi diye': nothing happened
    skip = s.skip | set(range(amt.start, amt.end))
    match, cands, name = find_supplier(name_toks, orig, skip, suppliers)
    pronoun = bool(set(toks) & PRONOUNS)
    if intent is None:
        has_marker = bool(set(toks) & (MARKERS_PAY | MARKERS_FROM | {"ka", "ki", "ke"}))
        if whole and not has_marker and (match or cands or name) and _typed_bill_shape(toks, skip) \
                and not set(toks) & EN_VERBS:
            intent = "bill"    # 'Verma Traders 4580', 'bill: Balaji Enterprises ₹12,350'
        elif set(toks) & RELATIVE:
            return None        # '5000 baaki', 'total 8000', 'gst 1440': about another entry, not a new one
        elif match or cands or name or pronoun:
            intent = "kind"    # 'Sharma ko 5000': bill or payment? the owner picks
        elif not whole:
            intent = "kind"    # '..., 500 aur': may take the verb / supplier of the part next to it (see parse)
        else:
            return None
    st = set(toks)
    p = Part(intent=intent, amount=amt.value, supplier=match, candidates=cands, name=name,
             pronoun=not (match or cands or name), corrected_from=corrected_from,
             marker="ko" if st & MARKERS_PAY else "ka" if st & {"ka", "ki", "ke", "se"} else None, ask=ask,
             raw=" ".join(t for t in orig if t != SEP))
    if intent == "refund":
        p.why = "goods" if st & (GOODS | DAMAGED) else "over" if st & OVERPAID else None
    found_day, _ = Q.find_day([t for i, t in enumerate(toks) if i not in s.skip and t not in {"sun", "sat"}],
                              today, future=(intent == "future"))  # 'Sun bhai' = listen, 'sat' = 7: not weekdays
    if intent == "future":
        later = [d for d, kind in s.dates if kind in ("future", "later", "any") and d > today]
        p.day = later[0] if later else (found_day if found_day and found_day > today else None)
    else:
        past = [d for d, kind in s.dates if kind in ("past", "any") and d <= today]
        day = past[0] if past else (today - timedelta(days=s.past_days)) if s.past_days else \
            (found_day if found_day and found_day <= today else None)
        if intent in ("bill", "udhaar", "return"):
            p.bill_date = day
            p.credit_days = s.credit_days if s.credit_days is not None else s.plain_days
            if intent == "return":
                p.credit_days = None
            due = [d for d, kind in s.dates if kind == "future" and d > today]
            if p.credit_days is None and due:
                p.credit_days = _credit((due[0] - (day or today)).days)
            p.invoice = s.invoice
        elif intent in ("payment", "kind", "received", "refund"):
            p.bill_date = day if day and (today - day).days <= 60 else None
    return p


def _typed_bill_shape(toks, skip) -> bool:
    """Only ONE name, an amount and maybe a date / invoice / 'bill' word: the old typed-bill format
    ('Verma Traders 4580'). An unknown word besides the name ('agarwal kirana la 6500 dile') is probably a verb the
    rules do not know: then the owner is asked bill or payment, never a guess."""
    return len(_runs(toks, skip)) == 1 and len(toks) <= 14


# ---------------------------------------------------------------- fixes
CHANGE_TO = {"karo", "kar", "kardo", "kijiye", "kariye", "krdo", "karna", "tha", "the", "thi", "hai", "h", "hona",
             "chahiye", "likho", "rakho"}
THEN_WORDS = {"wo", "woh", "vo", "actually", "asal", "asli", "sahi", "matlab"}


def _fix(toks, orig, suppliers, today, recent, s: _Scan) -> Fix | None:
    st = set(toks)
    if st & MONEY_IN and st & (OVERPAID - {"galat"}) and not st & UNDO and len(_money(toks, s.skip)) == 1:
        return None   # 'ne galti se zyada liye the, 300 wapas kiye': money came back (a refund), not a correction
    undo = bool(st & UNDO) or _phrase(toks, "galti", "se") and bool(st & {"daal", "dal", "dala", "daala", "likha", "save"})
    fix_words = bool(st & FIX) or any(_phrase(toks, *p) for p in FIX_PHRASES)
    money = _money(toks, s.skip)
    old = new = None
    negated = False
    if len(money) >= 2:
        a, b = money[-2], money[-1]
        between = toks[a.end:b.start]
        after = toks[b.end:b.end + 3]
        negated = bool(set(between) & NEGATION)
        linked = (negated or _phrase(between, "ki", "jagah") or _phrase(between, "ke", "badle")
                  or _phrase(between, "ke", "jagah") or between in (["se"], ["ko"])
                  or (set(between) & {"wala", "wali", "wale", "waala", "waali"} and set(after) & CHANGE_TO)
                  or (set(between) & THEN_WORDS and set(after) & CHANGE_TO)
                  or (fix_words and set(after) & CHANGE_TO)        # '15150 galat hai, 15,500 karo'
                  or (set(after) & {"karo", "kar", "kardo", "karna", "kijiye"}
                      and any(int(e["amount"]) == a.value for e in (recent or []))))  # '15150 ka bill 15,510 karna hai'
        if linked:
            old, new = a.value, b.value
    elif len(money) == 1:
        new = money[0].value
    if not (undo or fix_words or old is not None):
        return None
    if (new or 0) > MAX_AMOUNT:
        return None
    hint = "bill" if st & {"bill", "bil", "maal", "invoice", "parchi"} else \
        "payment" if st & (PAY_PAST | {"payment", "diye", "diya"}) else None
    skip = s.skip | {i for a in money for i in range(a.start, a.end)}
    match, cands, name = find_supplier(toks, orig, skip, suppliers)
    raw = " ".join(t for t in orig if t != SEP)
    if undo:
        amount = new if old is None else old
        return Fix("undo", old_amount=amount, supplier=match, candidates=cands, name=name, hint=hint, raw=raw)
    if old is not None:
        # 'Agarwal ko 3000 nahi 3500 diye the' is a fix only if such an entry exists; else it is a new entry
        # (a self-correction). '8000 ki jagah 800 karo' is always a fix.
        named = bool(match or cands or name)
        if negated and named and not _recent_has(recent, old, match, cands):
            return None        # 'Sharma ne teen hazaar nahi nahi chaar hazaar': a new entry that corrects itself
        return Fix("edit", new_amount=new, old_amount=old, supplier=match, candidates=cands, name=name, hint=hint,
                   raw=raw)
    if fix_words and new is not None and len(money) == 1:
        return Fix("edit", new_amount=new, supplier=match, candidates=cands, name=name, hint=hint, raw=raw)
    if fix_words and not money:
        return Fix("undo", supplier=match, candidates=cands, name=name, hint=hint, raw=raw)
    return None


def _recent_has(recent, amount, match, cands) -> bool:
    ids = {str(match["id"])} if match else {str(c["id"]) for c in cands}
    return any(int(e["amount"]) == amount and (not ids or str(e["supplier_id"]) in ids) for e in (recent or []))


# ---------------------------------------------------------------- the whole message
VERB_END = STRONG_PAST | FUTURE_STRONG | {"aaya", "aayi", "aaye", "mila", "mile", "kiya", "kiye", "done", "hai", "tha"}


def _clauses(toks: list[str]) -> list[tuple[int, int]]:
    out, start = [], 0
    for i, tok in enumerate(toks + [SEP]):
        if tok == SEP or tok in SPLIT_WORDS:
            if i > start:
                out.append((start, i))
            start = i + 1
    return out


def _split_two_amounts(toks, a, b, skip) -> list[tuple[int, int]]:
    """'sharma ko das hazaar diye verma ko paanch hazaar' (no comma, no 'aur'): one clause, two entries.
    Cut after the verb that follows the first amount, or before the name in front of the second amount."""
    money = [m for m in N.find_amounts(toks[a:b]) if not {i + a for i in range(m.start, m.end)} & skip]
    if len(money) < 2:
        return [(a, b)]
    out, start = [], a
    for m1, m2 in zip(money, money[1:]):
        between = toks[a + m1.end:a + m2.start]
        if set(between) & (NEGATION | THEN_WORDS | {"jagah", "badle", "wala", "wali", "wale"}) or between in (["se"], ["ko"]):
            continue
        cut = next((a + m1.end + k + 1 for k, t in enumerate(between) if t in VERB_END), None)
        if cut is None:
            runs = _runs(toks[a + m1.end:a + m2.start], set())
            if not runs:
                continue
            cut = a + m1.end + runs[-1][0]
        out.append((start, cut))
        start = cut
    out.append((start, b))
    return [(x, y) for x, y in out if y > x]


def _drop_self_correction(toks: list[str], orig: list[str], skip: set) -> tuple[list[str], list[str], int | None]:
    """'teen hazaar... nahi nahi, chaar hazaar' -> drop 'teen hazaar ... nahi nahi ,' and remember 3000."""
    money = [a for a in N.find_amounts(toks) if not set(range(a.start, a.end)) & skip]
    for a, b in zip(money, money[1:]):
        between = toks[a.end:b.start]
        words = [t for t in between if t != SEP]
        if words and set(words) <= NEGATION | {"matlab", "sorry", "galti", "galat", "se", "bas"} and set(words) & NEGATION:
            keep = [i for i in range(len(toks)) if not (a.start <= i < b.start)]
            return [toks[i] for i in keep], [orig[i] for i in keep], a.value
    return toks, orig, None


def _segments(toks: list[str], scan: _Scan) -> list[tuple[int, int, list]]:
    """Clauses -> segments, each holding exactly one amount clause, as lists of token ranges. A clause without
    money ('15 din ka udhaar', 'Mahalaxmi Traders se maal aaya.') joins the amount clause next to it: forward if it
    names someone and the next amount clause names no one, else backward. A clause that ends in 'nahi' is dropped."""
    clauses = []
    for a, b in _clauses(toks):
        clauses += _split_two_amounts(toks, a, b, scan.skip)
    info = []
    for a, b in clauses:
        has_money = any(not {i + a for i in range(m.start, m.end)} & scan.skip for m in N.find_amounts(toks[a:b]))
        negated_end = toks[b - 1] in NEGATION
        names = bool(_runs(toks[a:b], {i - a for i in scan.skip if a <= i < b}))
        info.append((a, b, has_money, negated_end, names))
    info = [x for x in info if not (x[2] and x[3] and any(y[2] for y in info if y[0] > x[0]))]
    segs: list[list] = []
    pending: list = []
    for k, (a, b, has_money, _, names) in enumerate(info):
        if has_money:
            segs.append(pending + [(a, b)])
            pending = []
            continue
        nxt = next((y for y in info[k + 1:] if y[2]), None)
        if segs and not (names and nxt is not None and not nxt[4]):
            segs[-1].append((a, b))
        else:
            pending.append((a, b))
    if pending and segs:
        segs[-1].extend(pending)
    return [(rs[0][0], rs[-1][1], rs) for rs in segs]


def _seg_tokens(toks, orig, ranges):
    t, o = [], []
    for a, b in ranges:
        if t:
            t.append(SEP)
            o.append(SEP)
        t += toks[a:b]
        o += orig[a:b]
    return t, o


def _partial_return(toks, orig, suppliers, scan) -> Part | None:
    """'Gupta ko 500 ka maal wapas kiya, 300 hi kata', 'Gupta ne 500 ke maal ke 300 hi wapas diye': goods went back
    and the supplier accepted less. Two amounts: the owner picks which one the supplier accepted (bot/entry_flow.py),
    because the baaki changes by that, not by what the goods cost."""
    s = set(toks)
    if not (s & GOODS and s & (MONEY_IN | EN_BACK) and s & (ACCEPTED | ONLY)) or s & NEGATION:
        return None
    money = _money(toks, scan.skip)
    if len(money) != 2 or money[0].value == money[1].value or max(m.value for m in money) > MAX_AMOUNT:
        return None
    intent = _classify([t for t in toks if t != SEP])
    if intent not in ("return", "refund"):
        return None
    skip = scan.skip | set(range(money[0].start, money[0].end)) | set(range(money[1].start, money[1].end))
    match, cands, name = find_supplier(toks, orig, skip, suppliers)
    lo, hi = sorted(m.value for m in money)
    return Part(intent=intent, amount=lo, supplier=match, candidates=cands, name=name, amounts=(hi, lo),
                why="goods" if intent == "refund" else None, raw=" ".join(t for t in orig if t != SEP))


def parse(text: str, suppliers: list[dict], today: date, recent: list[dict] | None = None) -> Understood:
    toks, orig = tokenize(text)
    if not toks:
        return Understood("none")
    st = set(toks)
    if st & REMINDER and (Q._wants_reminder(Q.tokens(text), today) or st & {"reminder", "reminders"}) \
            and not (st & {"rakhna", "rakho"} and N.find_amounts(toks) and _classify(toks) == "future"):
        return Understood("question", reason="reminder")
    scan = _scan(toks, orig, today)
    partial = _partial_return(toks, orig, suppliers, scan)
    if partial:
        return Understood("entries", parts=[partial], order=[("part", partial)])
    n_money = len(_money(toks, scan.skip))
    if n_money <= 3:     # one fix for the whole message: '8000 nahi 8500', 'balaji wala bill 15150 galat hai, 15,500 karo'
        fix = _fix(toks, orig, suppliers, today, recent, scan)
        if fix and (fix.old_amount is not None and fix.new_amount is not None or n_money <= 1
                    or (fix.action == "undo" and n_money <= 1)):
            return Understood("fix", fix=fix)
    toks2, orig2, self_fixed = _drop_self_correction(toks, orig, scan.skip)
    if self_fixed is not None:
        toks, orig = toks2, orig2
        scan = _scan(toks, orig, today)
    segs = _segments(toks, scan)
    whole = len(segs) == 1
    found, fixes, skipped, order = [], [], [], []
    for a, b, ranges in segs:
        t, o = _seg_tokens(toks, orig, ranges)
        local = _scan(t, o, today)
        if not whole:
            f = _fix(t, o, suppliers, today, recent, local) if len(segs) > 1 else None
            if f and (f.action == "edit" and f.old_amount is not None or f.action == "undo"):
                fixes.append(f)        # 'verma ki 8000 wali payment 6000 kar do aur sharma ko 2000 diye'
                order.append(("fix", f))
                continue
        p = _part(t, o, suppliers, today, local, whole)
        if p:
            p.joined = a > 0 and toks[a - 1] in SPLIT_WORDS   # '... 2000 aur Verma ko 1500 diye' (not a comma)
            found.append(p)
            order.append(("part", p))
        elif not _is_question(t):
            skipped.append(" ".join(x for x in o if x != SEP))
    if not found and not fixes:
        return Understood("none")
    # a part with no verb takes the verb of its neighbour ('Sharma ko 2000 aur Verma ko 1500 diye') only if it
    # names its own supplier or is joined by 'aur'; '..., 5000 baaki' / '..., total 8000' never become cards
    for k, p in enumerate(found):
        if p.intent == "kind" and len(found) > 1:
            own = bool(p.supplier or p.candidates or p.name)
            nxt_joined = k + 1 < len(found) and getattr(found[k + 1], "joined", False)
            if not (own or getattr(p, "joined", False) or nxt_joined):
                continue
            near = [q.intent for q in found[k + 1:k + 2] + found[max(0, k - 1):k] if q.intent in ("payment", "bill", "future")]
            want = "payment" if p.marker == "ko" else "bill" if p.marker == "ka" else None
            other = want if want in near else (near[0] if near else None)
            if other:
                p.intent = other
    # no name, or 'unko': the supplier of the item before (a part or a fix: '8800 kar do, aur aaj unko 1200 diye')
    for k, (kind, item) in enumerate(order):
        if kind == "part" and item.pronoun and not (item.supplier or item.candidates or item.name) and k > 0:
            prev = order[k - 1][1]
            item.supplier, item.candidates, item.name = prev.supplier, list(prev.candidates), prev.name
    # a verbless part with no supplier left over: say it was not understood, never a guess
    lost = [p for p in found if p.intent == "kind" and not (p.supplier or p.candidates or p.name) and len(found) > 1]
    for p in lost:
        skipped.append(p.raw)
    found = [p for p in found if p not in lost]
    order = [x for x in order if not (x[0] == "part" and x[1] in lost)]
    if len(found) == 1 and found[0].intent == "kind" and not (found[0].supplier or found[0].candidates or found[0].name):
        return Understood("none")      # '5000' alone: nothing to ask about
    if self_fixed is not None and len(found) == 1:
        found[0].corrected_from = self_fixed
    if len(found) + len(fixes) > MAX_PARTS:
        return Understood("too_many", parts=found)
    if len(fixes) > 1:   # one fix per message: the others are named as not done
        skipped += [f.raw for f in fixes[1:]]
        order = [x for x in order if x[0] == "part" or x[1] is fixes[0]]
    if fixes and not found:
        return Understood("fix", fix=fixes[0], skipped=skipped, order=order)
    return Understood("entries", parts=found, fix=fixes[0] if fixes else None, skipped=skipped, order=order)


# ---------------------------------------------------------------- optional AI fallback (voice notes only)
LLM_KINDS = {"bill", "payment", "received", "future", "refund", "return"}


def from_llm(data, transcript: str, suppliers: list[dict], today: date) -> Understood:
    """Check the classifier's JSON: only known kinds, positive amounts, names that appear in what was said.
    Amounts our own number reader cannot find in the transcript are kept but flagged (llm_amount) for the card."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return Understood("none")
    toks, _ = tokenize(transcript)
    said = {a.value for a in N.find_amounts(toks)}
    parts = []
    for e in data["entries"][:MAX_PARTS + 1]:
        if not isinstance(e, dict) or e.get("type") not in LLM_KINDS:
            continue
        try:
            amount = int(round(float(str(e.get("amount")).replace(",", ""))))
        except (TypeError, ValueError):
            continue
        if amount <= 0 or amount > MAX_AMOUNT:
            continue
        name = str(e.get("supplier") or "").strip()[:60]
        name_toks = Q.tokens(name)
        roman = not re.search(r"[\u0900-\u097f]", transcript)  # a Devanagari transcript cannot be checked word by word
        if not name or (roman and not all(any(difflib.SequenceMatcher(None, w, t).ratio() >= 0.75
                                              for t in Q.tokens(transcript)) for w in name_toks)):
            continue  # a supplier the owner never said
        match, cands = strict_match(name_toks, suppliers)
        p = Part(intent=e["type"], amount=amount, supplier=match, candidates=cands, ask="what" if e["type"] == "received" else None,
                 name=" ".join(w.title() for w in name.split()), raw=transcript[:200])
        if p.intent == "bill":
            cd = e.get("credit_days")
            p.credit_days = int(cd) if isinstance(cd, (int, float)) and 0 < cd <= 365 else None
        p.llm_amount = amount not in said
        parts.append(p)
    if not parts:
        return Understood("none")
    if len(parts) > MAX_PARTS:
        return Understood("too_many", parts=parts)
    return Understood("entries", parts=parts, reason="llm")
