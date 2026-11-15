"""Before the rules read a message: Hindi script -> Roman letters, and near-miss spellings of the words that carry
the meaning -> the word itself. Rules only, no AI, nothing saved. Used for typed messages and voice transcripts.

  to_roman("शर्मा जी को 2000 दिए")      -> "sharma ji ko 2000 diye"
  to_roman("कितना बाकी है")             -> "kitna baaki hai"
  fix_typos("sharma ka hiasb", [...])   -> ("sharma ka hisaab", [("hiasb", "hisaab")])

Why both engines (bot/entries.py, bot/questions.py) get the same cleaned text: one place to make a spelling count,
so 'hasib' cannot be a question in one engine and a supplier's name in the other.

Safety rules (a wrong "fix" is worse than no fix):
  * only words that change the meaning are fixed (hisaab, baaki, udhaar, purana, payment, wapas, ...), never
    names, verbs that look alike ('bhejo' / 'bheje') or short words;
  * a word must start with the same letter, be at most 1 letter away (2 for words of 7+ letters: 'pement'), and be
    closer to one target than to any other;
  * a word that is part of one of the shop's supplier names is never touched (a supplier called 'Hasib Traders'
    stays Hasib), and neither is any word the rules already know.
Transliteration: letter rules with Hindi's silent-'a' rule (कितना -> kitna, not kitana), a small list of English
loan words written in Hindi script (पेमेंट -> payment), and where a vowel can be written two ways (बाकी: baki /
baaki) the spelling the rules know is picked.
"""

from __future__ import annotations

import itertools
import re

_DEV = re.compile(r"[ऀ-ॿ‌‍]+")
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "dh", "फ़": "f", "य़": "y",   # precomposed nukta letters
}
_NUKTA_OF = {"क": "k", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}
_MATRA = {"ा": ("a", "aa"), "ि": ("i",), "ी": ("i", "ee"), "ु": ("u",), "ू": ("u", "oo"), "ृ": ("ri",), "े": ("e",),
          "ै": ("ai",), "ो": ("o",), "ौ": ("au",), "ॉ": ("o",), "ॅ": ("e",)}
_VOWEL = {"अ": ("a",), "आ": ("aa", "a"), "इ": ("i",), "ई": ("i", "ee"), "उ": ("u",), "ऊ": ("u", "oo"), "ऋ": ("ri",),
          "ए": ("e",), "ऐ": ("ai",), "ओ": ("o",), "औ": ("au",), "ऑ": ("o",)}
_HALANT, _NUKTA, _NASAL, _VISARGA = "्", "़", ("ं", "ँ"), "ः"
_INH = "INH"   # the consonant's own 'a' (silent at the end of a word, and in the middle by the schwa rule)

# English words owners write in Hindi script, and Hindi words whose common Roman spelling the letter rules miss
LOAN = {
    "पेमेंट": "payment", "पेमेन्ट": "payment", "कैश": "cash", "यूपीआई": "upi", "ऑनलाइन": "online", "ओनलाइन": "online",
    "बिल": "bill", "रिमाइंडर": "reminder", "ट्रेडर्स": "traders", "ट्रेडर": "trader", "ट्रेडिंग": "trading",
    "एजेंसी": "agency", "एजेंसीज": "agencies", "एजेन्सी": "agency", "मेडिकल": "medical", "स्टोर": "store",
    "स्टोर्स": "stores", "एंटरप्राइजेज": "enterprises", "एंटरप्राइज़ेज़": "enterprises", "इंटरप्राइजेज": "enterprises",
    "ब्रदर्स": "brothers", "डिस्ट्रीब्यूटर्स": "distributors", "डिस्ट्रीब्यूटर": "distributor", "जनरल": "general",
    "एडवांस": "advance", "टोटल": "total", "इनवॉइस": "invoice", "इनवाइस": "invoice", "रिफंड": "refund",
    "रिटर्न": "return", "क्रेडिट": "credit", "नोट": "note", "डिलीट": "delete", "डेटा": "data", "डाटा": "data", "प्रूफ": "proof", "फोटो": "photo",
    "ट्रांसफर": "transfer", "चेक": "cheque", "ऑर्डर": "order", "स्टॉक": "stock", "डेट": "date", "मेन्यू": "menu",
    "रुपये": "rupaye", "रुपए": "rupaye", "रूपये": "rupaye", "हज़ार": "hazaar", "हजार": "hazaar", "लाख": "lakh",
    "में": "mein", "मैं": "main", "नहीं": "nahi", "नही": "nahi", "हाँ": "haan", "हां": "haan", "मुझे": "mujhe",
    "हिसाब": "hisaab", "बाकी": "baaki", "बाक़ी": "baaki", "उधार": "udhaar", "पिछला": "pichhla", "पिछले": "pichhle",
    "दिए": "diye", "दिये": "diye", "लिए": "liye", "लिये": "liye", "गए": "gaye", "गये": "gaye", "किए": "kiye",
    "किये": "kiye", "आए": "aaye", "आये": "aaye", "डेढ़": "dedh", "ढाई": "dhai", "साढ़े": "saade", "सवा": "sava",
    "पौने": "paune", "जी": "ji", "भाई": "bhai", "शर्मा": "sharma", "वर्मा": "verma", "गुप्ता": "gupta",
}


def has_devanagari(text: str) -> bool:
    return bool(text) and _DEV.search(text) is not None


def _units(word: str) -> list[dict]:
    units: list[dict] = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in _CONS:
            c = _CONS[ch]
            if i + 1 < len(word) and word[i + 1] == _NUKTA:
                c = _NUKTA_OF.get(ch, c)
                i += 1
            units.append({"c": c, "v": _INH})
        elif ch in _MATRA:
            if units and units[-1]["v"] == _INH:
                units[-1]["v"] = _MATRA[ch]
            else:
                units.append({"c": "", "v": _MATRA[ch]})
        elif ch == _HALANT:
            if units:
                units[-1]["v"] = None
        elif ch in _VOWEL:
            alts = _VOWEL[ch]
            if ch in ("ए", "ई") and units and units[-1]["v"] is not None:
                alts = tuple("y" + a for a in alts)        # दिए -> diye, गई -> gayi
            units.append({"c": "", "v": alts})
        elif ch in _NASAL:
            if units:
                units[-1]["n"] = True
        elif ch == _VISARGA:
            if units:
                units[-1]["h"] = True
        i += 1
    # the silent 'a': at the end of a word, then (right to left) between two sounded syllables: कितना -> kitna
    if len(units) >= 2 and units[-1]["v"] == _INH and units[-1]["c"] and not units[-1].get("n"):
        units[-1]["v"] = None
    for k in range(len(units) - 2, 0, -1):
        u, prev, nxt = units[k], units[k - 1], units[k + 1]
        if u["v"] == _INH and u["c"] and not u.get("n") and prev["v"] is not None and nxt["c"] and nxt["v"] is not None:
            u["v"] = None
    return units


def _spellings(units: list[dict]) -> list[str]:
    slots = []
    for u in units:
        v = u["v"]
        vs = ("",) if v is None else ("a",) if v == _INH else v
        tail = ("n" if u.get("n") else "") + ("h" if u.get("h") else "")
        slots.append(tuple(u["c"] + x + tail for x in vs))
    if sum(len(s) > 1 for s in slots) > 4:
        return ["".join(s[0] for s in slots)]
    return ["".join(p) for p in itertools.product(*slots)]


def _word(word: str) -> str:
    if word in LOAN:
        return LOAN[word]
    options = _spellings(_units(word))
    known = _known()
    return next((o for o in options if o in known), options[0])


def to_roman(text: str) -> str:
    """Hindi-script words -> Hinglish Roman letters; everything else (digits, Roman words, ₹) stays as it was."""
    if not has_devanagari(text):
        return text
    t = text.translate(_DIGITS).replace("।", ".").replace("॥", ".")
    return _DEV.sub(lambda m: " ".join(_word(w) for w in m.group(0).replace("‌", " ").replace("‍", "").split()), t)


# ---------------------------------------------------------------- spelling slips
# spelling (what a slip is measured against) -> the word the rules get
TARGETS = {"hisaab": "hisaab", "hisab": "hisaab", "baaki": "baaki", "bakaya": "bakaya", "udhaar": "udhaar",
           "purana": "purana", "pichhla": "pichhla", "payment": "payment", "kitna": "kitna", "wapas": "wapas",
           "vapas": "vapas", "refund": "refund", "reminder": "reminder", "transfer": "transfer", "advance": "advance",
           "delete": "delete", "proof": "proof", "dikhao": "dikhao", "chukaya": "chukaya", "chukaye": "chukaye"}
NAME_MARKERS = {"ko", "ne", "se", "ji", "sahab", "saab", "bhai", "traders", "trader", "agency", "store", "stores"}


def _skeleton(word: str) -> str:
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouy]", "", word))


def _dl(a: str, b: str, cap: int = 3) -> int:
    """Damerau-Levenshtein distance (swapped neighbours count as one slip: hiasb -> hisab)."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


_KNOWN: set[str] = set()


def _known() -> set[str]:
    """Every word the rules already understand (never 'fixed'): all word sets of the two engines and the numbers."""
    if not _KNOWN:
        from bot import entries as E
        from bot import numbers as N
        from bot import questions as Q
        for mod in (E, Q, N):
            for value in vars(mod).values():
                if isinstance(value, (set, frozenset)) and all(isinstance(x, str) for x in value):
                    _KNOWN.update(value)
                elif isinstance(value, dict) and value and all(isinstance(x, str) for x in value):
                    _KNOWN.update(value)
                elif isinstance(value, tuple) and value and all(isinstance(x, tuple) for x in value):
                    _KNOWN.update(w for pair in value for w in pair if isinstance(w, str))
        _KNOWN.update({"bhejo", "dekho", "rakho", "likho", "jodo", "batao", "karo", "hatao", "kitni", "kitne", "sharma",
                       "verma", "gupta", "bhejna", "mangao", "lelo", "dedo", "kardo", "proof", "photo", "aaj", "kal"})
    return _KNOWN


def _target_for(word: str, before_name_marker: bool = False) -> str | None:
    best, best_d, tie = None, 99, False
    for spelling, canon in TARGETS.items():
        if word[0] != spelling[0] or word == spelling:
            continue
        limit = 2 if len(spelling) >= 7 else 1
        d = _dl(word, spelling, cap=limit)
        if d <= limit:
            if d < best_d:
                best, best_d, tie = canon, d, False
            elif d == best_d and canon != best and not {canon, best} <= {"wapas", "vapas"}:
                tie = True
    if tie:
        return None
    if best is None and not before_name_marker and 4 <= len(word) <= 6 and _skeleton(word) == "hsb":
        return "hisaab"   # vowels swapped: 'hasib', 'hasab', 'hsab'. Not before 'ko' / 'ji': 'Hasib ko 500' is a name
    return best


def fix_typos(text: str, suppliers: list[dict] | None = None) -> tuple[str, list[tuple[str, str]]]:
    """('sharma ka hiasb', ...) -> ('sharma ka hisaab', [('hiasb', 'hisaab')]). Only TARGETS, only safe slips."""
    protect = {w for s in (suppliers or []) for w in str(s.get("name_norm") or "").split()}
    known = _known()
    changed: list[tuple[str, str]] = []

    def swap(m):
        w = m.group(0)
        low = w.lower()
        if len(low) < 4 or low in known or low in protect:
            return w
        nxt = re.match(r"\s*([A-Za-z]+)", m.string[m.end():])
        t = _target_for(low, before_name_marker=bool(nxt and nxt.group(1).lower() in NAME_MARKERS))
        if t is None:
            return w
        changed.append((low, t))
        return t

    out = re.sub(r"[A-Za-z]+", swap, text or "")
    return out, changed


def clean(text: str, suppliers: list[dict] | None = None) -> tuple[str, list[tuple[str, str]]]:
    """Both steps: Hindi script to Roman letters, then spelling slips."""
    return fix_typos(to_roman(text), suppliers)
