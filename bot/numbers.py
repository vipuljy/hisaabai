"""Reading money amounts the way Indian shop owners write and say them. Rules only, no AI.

find_amounts(tokens) -> every amount in a message, with the token positions it came from, so the caller can tell an
amount from a credit period ("15 din"), a date or an invoice number, and remove it before reading the supplier's name.

Understands:
  digits       8000, 8,000, 1,20,000, 8000.50, 5,000/-  (the tokens come from tokenize())
  short forms  8k, 2.5k, 1.5 lakh, 2 lakh 35 hazaar, 5 hazaar 500, 12 sau
  Hindi words  paanch hazaar, saat sau assi (780), pachaas hazaar, ek lakh bees hazaar
  fractions    dedh (1.5), dhai (2.5), sava (+1/4), saade (+1/2), paune (-1/4): saade teen hazaar = 3500,
               paune paanch hazaar = 4750, sava lakh = 125000, dhai sau = 250
  English      five thousand, two thousand five hundred, one lakh

A number WORD with no multiplier ("paanch", "assi") counts as money only when a currency word follows
("assi rupay"): "do" is also the verb "give", "saath" also means "with", "das din" is a period.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# 1-99 in Hindi / Hinglish, with the spellings Sarvam and owners use
_HINDI = {
    1: "ek ik", 2: "do", 3: "teen tin", 4: "char chaar", 5: "paanch panch paach pach", 6: "chhe che chhah chah chheh chhai",
    7: "saat sat", 8: "aath ath aat", 9: "nau", 10: "das dus", 11: "gyarah gyara gyaarah gyaara igyara", 12: "barah bara baarah baara",
    13: "terah tera", 14: "chaudah chauda chodah choda", 15: "pandrah pandra pundrah pandhra", 16: "solah sola",
    17: "satrah satra", 18: "atharah athara attharah atthara", 19: "unnis unees unnees unis", 20: "bees bis",
    21: "ikkis ikkees", 22: "baais bais baees", 23: "teis teyees teees", 24: "chaubis chaubees chobis",
    25: "pachchis pachees pacchis pachis pacchees", 26: "chhabbis chabbis chhabbees", 27: "sattais sattaees satais",
    28: "atthais athais atthaees athaees", 29: "untis unatis untees", 30: "tees tis", 31: "ikattis iktees ikatees",
    32: "battis battees", 33: "taintis tetis taitees", 34: "chauntis chautees chontis", 35: "paintis pentis paitees",
    36: "chhattis chattis chhattees", 37: "saintis setis saitees", 38: "adtis artees adtees", 39: "untalis untaalees",
    40: "chalis chaalis chalees chaalees", 41: "iktalis iktaalees", 42: "bayalis bayaalees", 43: "tentalis taintalis",
    44: "chavalis chauvalis chawalis", 45: "paintalis pentalis paitalis", 46: "chhiyalis chiyalis",
    47: "saintalis setalis", 48: "adtalis artalis", 49: "unchas unchaas", 50: "pachas pachaas pachaas",
    51: "ikyavan ikyaavan", 52: "bavan baavan", 53: "tirpan tirepan", 54: "chauvan chauvan", 55: "pachpan",
    56: "chhappan chappan", 57: "sattavan sataavan", 58: "athavan atthaavan", 59: "unsath unsaath",
    60: "saath sath", 61: "iksath iksaath", 62: "basath basaath", 63: "tirsath tirsaath", 64: "chausath chausaath",
    65: "painsath painsaath", 66: "chhiyasath chhiyasaath", 67: "sadsath sarsath", 68: "adsath arsath",
    69: "unhattar", 70: "sattar", 71: "ikhattar", 72: "bahattar", 73: "tihattar", 74: "chauhattar",
    75: "pachhattar pachattar", 76: "chhihattar", 77: "satattar sathattar", 78: "athhattar athattar", 79: "unasi unnasi",
    80: "assi assee", 81: "ikyasi", 82: "bayasi", 83: "tirasi", 84: "chaurasi", 85: "pachasi", 86: "chhiyasi",
    87: "sattasi", 88: "athasi atthasi", 89: "navasi nawasi", 90: "nabbe", 91: "ikyanve", 92: "banve", 93: "tiranve",
    94: "chauranve", 95: "pachanve", 96: "chhiyanve", 97: "sattanve", 98: "athanve", 99: "ninyanve ninyaanve",
}
_ENGLISH = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen",
    18: "eighteen", 19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
    80: "eighty", 90: "ninety",
}
WORD_VALUES: dict[str, Decimal] = {}
for _table in (_HINDI, _ENGLISH):
    for _n, _words in _table.items():
        for _w in _words.split():
            WORD_VALUES.setdefault(_w, Decimal(_n))
WORD_VALUES |= {"dedh": Decimal("1.5"), "derh": Decimal("1.5"), "dhed": Decimal("1.5"), "dhai": Decimal("2.5"),
                "dhaai": Decimal("2.5"), "adhai": Decimal("2.5"), "dhaayi": Decimal("2.5"), "dhayi": Decimal("2.5"),
                "thai": Decimal("2.5"), "dhaee": Decimal("2.5"),  # 'Thai so diye': speech-to-text hearing 'dhai sau'  "aadha": Decimal("0.5"),
                "adha": Decimal("0.5"), "aadhe": Decimal("0.5")}

MULTIPLIERS = {
    **dict.fromkeys(["sau", "so", "saw", "hundred"], 100),
    **dict.fromkeys(["hazaar", "hazar", "hajar", "hajaar", "hazzar", "hazaar", "thousand", "k", "hzr", "hzar"], 1000),
    **dict.fromkeys(["lakh", "lac", "lakhs", "lacs", "laakh", "lakha"], 100_000),
    **dict.fromkeys(["crore", "karod", "karor", "cr"], 10_000_000),
}
FRACTIONS = {"sava": Decimal("0.25"), "savaa": Decimal("0.25"), "sawa": Decimal("0.25"), "saade": Decimal("0.5"),
             "sade": Decimal("0.5"), "saadhe": Decimal("0.5"), "sadhe": Decimal("0.5"), "saarhe": Decimal("0.5"),
             "paune": Decimal("-0.25"), "pone": Decimal("-0.25"), "paun": Decimal("-0.25")}
CURRENCY = {"rs", "rupay", "rupaye", "rupaiye", "rupees", "rupee", "rupiya", "rupiye", "₹", "inr", "rupya", "rupye"}

_TOKEN = re.compile(r"\d+(?:,\d+)*(?:\.\d+)?k?|[a-z\u0900-\u097f]+|₹|\?")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalize(text: str) -> str:
    """Lower case, Devanagari digits -> 0-9, 'rs.500' / '₹500' / '5000/-' spaced out."""
    t = (text or "").translate(_DEVANAGARI_DIGITS).lower()
    t = t.replace("/-", " ")
    t = re.sub(r"(?<=[a-z₹])\.?(?=\d)", " ", t)  # rs.500 -> rs 500, ₹500 -> ₹ 500
    return t


def tokenize(text: str) -> list[str]:
    """'Sharma ko ₹5,000/- diye' -> ['sharma', 'ko', '₹', '5,000', 'diye']. Keeps 1,20,000 and 2.5k whole."""
    return _TOKEN.findall(normalize(text))


@dataclass
class Amount:
    value: int          # whole rupees (paise rounded half up)
    start: int          # first token index
    end: int            # one past the last token index
    words: bool         # read from number words (paanch hazaar), not digits
    currency: bool = False  # a currency word or ₹ next to it


def _digits(tok: str) -> tuple[Decimal, int] | None:
    """'1,20,000' -> (120000, 1); '12k' -> (12, 1000); '2.5k' -> (2.5, 1000)."""
    mult = 1
    if tok.endswith("k"):
        tok, mult = tok[:-1], 1000
    if not _NUM.fullmatch(tok):
        return None
    try:
        return Decimal(tok.replace(",", "")), mult
    except ArithmeticError:
        return None


def _rupees(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def find_amounts(toks: list[str]) -> list[Amount]:
    """Every amount in the token list, left to right. Adjacent amounts stay separate ('8000 nahi 8500',
    '2000 aur 1500'); one amount can span words ('ek lakh bees hazaar', '5 hazaar 500', 'saat sau assi')."""
    out: list[Amount] = []
    i, n = 0, len(toks)
    while i < n:
        value, end, words, multiplied = _read_amount(toks, i)
        if end == i:
            i += 1
            continue
        has_digits = any(_digits(t) for t in toks[i:end])
        currency = (i > 0 and toks[i - 1] in CURRENCY) or (end < n and toks[end] in CURRENCY)
        if not has_digits and not multiplied and not currency:
            i += 1          # 'do', 'saath', 'das' alone are not money
            continue
        if _rupees(value) > 0:  # '0.4' rounds to nothing: not money
            out.append(Amount(_rupees(value), i, end, words, currency))
        i = end
    return out


def _read_amount(toks: list[str], i: int) -> tuple[Decimal, int, bool, bool]:
    """(value, end index, used number words, used a multiplier). end == i when no amount starts at i."""
    total, group, frac = Decimal(0), Decimal(0), Decimal(0)
    last_mult = None          # the next multiplier (or number) must be smaller than this
    prev_number = False       # the previous token was a number (two numbers in a row = two amounts)
    words = multiplied = False
    j = i
    while j < len(toks):
        tok = toks[j]
        d = _digits(tok)
        if tok in FRACTIONS and group == 0 and frac == 0 and not prev_number:
            frac, words = FRACTIONS[tok], True
            j += 1
            continue
        if d is not None or tok in WORD_VALUES:
            val, kmult = d if d else (WORD_VALUES[tok], 1)
            if prev_number:
                break
            if last_mult is not None and val * kmult >= last_mult:
                break
            if d is None:
                words = True
            if kmult > 1:      # '12k', '2.5k'
                if last_mult is not None and kmult >= last_mult:
                    break
                total += (val + frac) * kmult
                group, frac, last_mult, multiplied = Decimal(0), Decimal(0), kmult, True
                prev_number = False
                j += 1
                continue
            group += val
            prev_number = True
            j += 1
            continue
        if tok in MULTIPLIERS and (group or frac) and (tok != "k" or prev_number):  # '5 k' = 5000
            m = MULTIPLIERS[tok]
            if last_mult is not None and m >= last_mult:
                break
            base = (group if group else Decimal(1)) + frac
            total += base * m
            group, frac, last_mult, multiplied = Decimal(0), Decimal(0), m, True
            prev_number, words = False, True
            j += 1
            continue
        break
    if frac and not multiplied:
        return Decimal(0), i, False, False   # 'saade teen' with no hazaar / sau: not an amount
    if total == 0 and group == 0:
        return Decimal(0), i, False, False
    return total + group, j, words, multiplied
