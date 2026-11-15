"""The text engine (bot/entries.py) and the number reader (bot/numbers.py): rules only, no store, no AI."""

from datetime import date

import pytest

from bot import entries as E
from bot.extract import norm_supplier
from bot.numbers import find_amounts, tokenize

TODAY = date(2026, 10, 28)
SUPS = [{"id": str(k), "name": n, "name_norm": norm_supplier(n)} for k, n in enumerate(
    ["Sharma Traders", "Sharma Medical", "Verma Traders", "Balaji Enterprises", "Gupta Agency", "Agarwal Kirana"])]


def sup(name):
    return next(s for s in SUPS if s["name"] == name)


# ---------------------------------------------------------------- numbers
@pytest.mark.parametrize("text, want", [
    ("8000", [8000]), ("8,000", [8000]), ("1,20,000", [120000]), ("8000.50", [8001]), ("5,000/- diye", [5000]),
    ("8k", [8000]), ("2.5k", [2500]), ("1.5 lakh", [150000]), ("2 lakh 35 hazaar", [235000]), ("5 hazaar 500", [5500]),
    ("12 sau", [1200]), ("barah sau", [1200]), ("paanch hazaar", [5000]), ("saat sau assi rupay", [780]),
    ("pachaas hazaar", [50000]), ("ek lakh bees hazaar", [120000]), ("dedh hazaar", [1500]), ("dhai sau", [250]),
    ("saade teen hazaar", [3500]), ("paune paanch hazaar", [4750]), ("sava lakh", [125000]), ("sava do sau", [225]),
    ("five thousand", [5000]), ("two thousand five hundred", [2500]), ("8000 nahi 8500", [8000, 8500]),
    ("2000 aur 1500", [2000, 1500]), ("5 hazaar 6000", [5000, 6000]), ("rs.500", [500]), ("₹5000", [5000]),
    ("१२०० रुपये", [1200]), ("Rs 1,235", [1235]), ("assi rupay", [80]),
    ("de do", []), ("ke saath", []), ("das din", []), ("bees", []), ("ek mahine", []),  # words alone are not money
    ("teen hazaar... nahi nahi, chaar hazaar diye", [3000, 4000]), ("6000, Agarwal", [6000]),
])
def test_amounts(text, want):
    assert [a.value for a in find_amounts(tokenize(text))] == want


# ---------------------------------------------------------------- entries
def one(text, recent=None, suppliers=SUPS):
    u = E.parse(text, suppliers, TODAY, recent or [])
    assert u.kind == "entries" and len(u.parts) == 1, u
    return u.parts[0]


def test_bill_with_credit_days_and_supplier():
    p = one("Sharma Traders ka bill aaya 8000 ka, 15 din ka udhaar")
    assert (p.intent, p.amount, p.supplier["name"], p.credit_days) == ("bill", 8000, "Sharma Traders", 15)


@pytest.mark.parametrize("text, days", [("balaji ka 1.5 lakh ka bill aaya ek mahine ka udhaar", 30),
                                        ("verma ka bill 2000, 30 din credit", 30),
                                        ("verma ka 2000 ka maal aaya 45 din me payment", 45),
                                        ("verma ka bill 2000 dedh mahine ka udhaar", 45)])
def test_credit_periods_are_not_money(text, days):
    p = one(text)
    assert p.intent == "bill" and p.credit_days == days and p.amount in (150000, 2000)


def test_invoice_number_date_and_old_typed_format():
    p = one("Verma traders bill no 2231 amount 6400")
    assert (p.amount, p.invoice) == (6400, "2231")
    p = one("Verma Traders 4580 05/10")
    assert (p.intent, p.amount, p.bill_date) == ("bill", 4580, date(2026, 10, 5))
    assert one("Agarwal kirana ka kal ka bill 3200").bill_date == date(2026, 10, 27)


def test_payment_received_future_kind():
    assert one("Verma ko paanch hazaar de diye").intent == "payment"
    assert one("paid 4200 to Balaji Enterprises").supplier["name"] == "Balaji Enterprises"
    v = one("Verma se 2000 aaye")
    assert (v.intent, v.ask) == ("received", "what")   # goods or money back? the owner picks
    assert one("Sharma Traders ne 1000 return kiye").intent == "refund"  # money clearly came back
    assert one("Gupta Agency ne 7500 ka bill diya").intent == "bill"  # 'ne' + bill = the supplier gave a bill
    f = one("kal Gupta ko paanch hazaar dena hai")
    assert (f.intent, f.amount, f.day) == ("future", 5000, date(2026, 10, 29))
    assert one("Gupta ko 900").intent == "kind"  # no verb: the owner picks bill or payment
    assert one("Sharma Traders ka bill bhar diya 9650").intent == "payment"


def test_questions_and_chatter_record_nothing():
    for text in ["Sharma Traders ka kitna baaki hai", "kya maine Balaji ko 5000 diye the?", "aaj 2 bill aaye",
                 "aaj 50 customer aaye, din accha tha", "5000", "Sharma Traders", "mere bete ka 15 tarikh ko birthday hai",
                 "call 9876543210 pe"]:
        assert E.parse(text, SUPS, TODAY, []).kind == "none", text
    assert E.parse("Gupta ko kal yaad dilana 5000 dena hai", SUPS, TODAY, []).kind == "question"


def test_supplier_rules_never_guess():
    assert one("Sharma ko 2000 diye").candidates and not one("Sharma ko 2000 diye").supplier  # two Sharmas: ask
    p = one("Ramesh Steel ko 4000 diye")
    assert p.supplier is None and p.name == "Ramesh Steel"
    m, c = E.strict_match(["balaji"], SUPS)
    assert m["name"] == "Balaji Enterprises"
    m, c = E.strict_match(["shree", "balaji", "medical", "agency"], SUPS)
    assert m is None and [s["name"] for s in c] == ["Balaji Enterprises"]  # offered, never silently used
    m, c = E.strict_match(["sharma", "brothers"], SUPS)
    assert m is None  # 'Sharma Brothers' is not 'Sharma Traders'
    assert E.close_suppliers("Sharma Traders", [sup("Verma Traders")]) == []  # only 'traders' in common
    assert [s["name"] for s in E.close_suppliers("Sharmaa Traders", SUPS)][:2] == ["Sharma Medical", "Sharma Traders"]


def test_multi_parts_verb_shared_and_pronoun():
    u = E.parse("Sharma Traders ko 2000 aur Verma Traders ko 1500 diye", SUPS, TODAY, [])
    assert [(p.intent, p.amount, p.supplier["name"]) for p in u.parts] == [
        ("payment", 2000, "Sharma Traders"), ("payment", 1500, "Verma Traders")]
    u = E.parse("Sharma Traders ka bill aaya 8000 ka aur 3000 unko de bhi diye", SUPS, TODAY, [])
    assert [(p.intent, p.amount, p.supplier["name"]) for p in u.parts] == [
        ("bill", 8000, "Sharma Traders"), ("payment", 3000, "Sharma Traders")]
    u = E.parse("Gupta ko 100 diye, Verma ko 200 diye, Balaji ko 300 diye aur Agarwal ko 400 diye", SUPS, TODAY, [])
    assert u.kind == "too_many"


def test_self_correction_in_a_new_entry_vs_a_fix():
    p = one("Sharma Traders ko teen hazaar... nahi nahi, chaar hazaar diye")
    assert (p.intent, p.amount, p.corrected_from) == ("payment", 4000, 3000)
    recent = [{"type": "payment", "id": "x", "supplier_id": sup("Agarwal Kirana")["id"], "amount": 3000}]
    u = E.parse("Agarwal ko 3000 nahi 3500 diye the", SUPS, TODAY, recent)
    assert u.kind == "fix" and (u.fix.action, u.fix.old_amount, u.fix.new_amount) == ("edit", 3000, 3500)
    u = E.parse("Agarwal ko 3000 nahi 3500 diye the", SUPS, TODAY, [])  # nothing of 3000 saved: a new payment
    assert u.kind == "entries" and u.parts[0].amount == 3500


def test_fixes():
    u = E.parse("8000 nahi 8500", SUPS, TODAY, [])
    assert u.kind == "fix" and (u.fix.old_amount, u.fix.new_amount, u.fix.supplier) == (8000, 8500, None)
    u = E.parse("Verma ka payment 8000 ki jagah 800 karo", SUPS, TODAY, [])
    assert (u.fix.action, u.fix.old_amount, u.fix.new_amount, u.fix.hint) == ("edit", 8000, 800, "payment")
    u = E.parse("galti ho gayi, balaji ka bill 2830 tha", SUPS, TODAY, [])
    assert (u.fix.action, u.fix.new_amount, u.fix.supplier["name"], u.fix.hint) == ("edit", 2830, "Balaji Enterprises", "bill")
    for text in ["last wala galat hai", "Agarwal wala payment hata do", "Balaji ka bill delete karo"]:
        assert E.parse(text, SUPS, TODAY, []).fix.action == "undo", text
    assert E.parse("Sharma ka reminder hatao", SUPS, TODAY, []).kind == "question"  # a reminder, not an entry


# ---------------------------------------------------------------- the AI fallback is checked
def test_from_llm_validates():
    data = {"entries": [
        {"type": "payment", "supplier": "Verma", "amount": 5000},       # said: kept
        {"type": "bill", "supplier": "Mehta", "amount": 900},           # never said: dropped
        {"type": "gift", "supplier": "Verma", "amount": 10},            # unknown type: dropped
        {"type": "payment", "supplier": "Verma", "amount": -5},         # not money: dropped
    ]}
    u = E.from_llm(data, "verma ji ko paanch hajaar ru bheje", SUPS, TODAY)
    assert [(p.intent, p.amount, p.supplier["name"], p.llm_amount) for p in u.parts] == [
        ("payment", 5000, "Verma Traders", False)]
    u = E.from_llm({"entries": [{"type": "payment", "supplier": "Verma", "amount": 7000}]}, "verma ko kuch diye", SUPS, TODAY)
    assert u.parts[0].llm_amount  # an amount our reader cannot find in what was said: flagged on the card
    u = E.from_llm({"entries": [{"type": "payment", "supplier": "Verma", "amount": 5000}]}, "वर्मा को पांच हज़ार", SUPS, TODAY)
    assert u.kind == "entries"  # a Devanagari transcript cannot be checked word by word
    assert E.from_llm(None, "x", SUPS, TODAY).kind == "none" and E.from_llm({"entries": "x"}, "x", SUPS, TODAY).kind == "none"


# ---------------------------------------------------------------- review findings (31 Oct): each one stays fixed
def test_money_that_is_not_money():
    for text in ["Sharma ko 0.4 diye", "Sharma ko 1000 crore diye"]:
        assert E.parse(text, SUPS, TODAY, []).kind == "none", text  # rounds to nothing / over Rs 1 crore
    p = one("Verma ka bill 2000, 400 din ka udhaar")
    assert p.credit_days is None  # over a year: not credit days
    p = one("Verma ka bill 2000, 9999999 din ka udhaar")
    assert p.amount == 2000 and p.credit_days is None
    assert [a.value for a in find_amounts(tokenize("5 k diye"))] == [5000]
    assert [a.value for a in find_amounts(tokenize("2.5 k"))] == [2500]


def test_negation_keeps_the_right_supplier_and_drops_the_negated_part():
    u = E.parse("Sharma Traders ko teen hazaar nahi, Verma ko chaar hazaar diye", SUPS, TODAY, [])
    assert [(p.amount, p.supplier["name"]) for p in u.parts] == [(4000, "Verma Traders")]
    p = one("Sharma Traders ko 3000 nahi Verma ko 4000 diye")
    assert (p.amount, p.supplier["name"]) == (4000, "Verma Traders")


def test_no_part_is_lost_silently():
    u = E.parse("Gupta ko 2000 diye aur 1500 ka bill aaya", SUPS, TODAY, [])
    assert [(p.intent, p.amount, p.supplier["name"]) for p in u.parts] == [
        ("payment", 2000, "Gupta Agency"), ("bill", 1500, "Gupta Agency")]
    u = E.parse("Gupta ko 2000 diye phir 500 aur diye", SUPS, TODAY, [])
    assert [(p.intent, p.amount) for p in u.parts] == [("payment", 2000), ("payment", 500)]
    u = E.parse("Gupta ko 2000 diye, 1500 kuch", SUPS, TODAY, [])
    assert u.kind == "entries"


def test_dates_are_read_or_refused_never_invented():
    assert one("Gupta ko 5000 diye 3 din pehle").bill_date == date(2026, 10, 25)
    assert one("Gupta ka bill 8000, 2 din pehle aaya").bill_date == date(2026, 10, 26)
    assert one("Gupta ka bill 8000 05/11/27").bill_date is None   # a full date in the future: refused
    assert one("Gupta ka bill 8000 05/11/202").bill_date is None  # a 3-digit year: refused
    assert E.parse("Gupta ka bill 8000 29/02", SUPS, date(2028, 1, 15), []).parts[0].bill_date is None  # 2027: no 29 Feb
    from bot import questions as Q
    assert Q.find_day(Q.tokens("29/02 ko yaad dilana"), date(2028, 2, 29), future=True)[0] is None  # no crash
    assert one("Gupta ka bill 8000 ka, 18 oct wala, abhi tak nahi diya").credit_days is None


def test_mixed_fix_and_entry_in_one_message():
    recent = [{"type": "payment", "id": "x", "supplier_id": sup("Verma Traders")["id"], "amount": 8000}]
    u = E.parse("verma ki 8000 wali payment 6000 kar do aur sharma medical ko 2000 diye", SUPS, TODAY, recent)
    assert u.kind == "entries" and (u.fix.old_amount, u.fix.new_amount) == (8000, 6000)
    assert [k for k, _ in u.order] == ["fix", "part"] and u.parts[0].supplier["name"] == "Sharma Medical"
    u = E.parse("balaji wala bill 15150 galat hai, 15,500 karo", SUPS, TODAY, [])
    assert u.kind == "fix" and (u.fix.old_amount, u.fix.new_amount) == (15150, 15500)
    u = E.parse("agarwal ko kal jo 3000 diye the wo actually 3500 the", SUPS, TODAY, [])
    assert u.kind == "fix" and (u.fix.old_amount, u.fix.new_amount) == (3000, 3500)


def test_typed_value_parsers():
    from bot.entry_flow import parse_amount_text, parse_credit_text
    assert parse_amount_text("8,500") == 8500 and parse_amount_text("saade aath hazaar") == 8500
    assert parse_amount_text("Rs 8500 hai") == 8500
    assert parse_amount_text("Sharma ko 2000 diye") is None and parse_amount_text("menu") is None
    assert parse_amount_text("2 crore") is None
    assert parse_credit_text("15") == 15 and parse_credit_text("15 din") == 15 and parse_credit_text("2 mahine") == 60
    assert parse_credit_text("do hafte") == 14 and parse_credit_text("nahi") == 0
    assert parse_credit_text("1000") is None and parse_credit_text("Sharma 15") is None


def test_second_review_no_phantom_cards():
    for text, want in [
        ("Sharma Traders ka bill 8000, 3000 diye, 5000 baaki", [("bill", 8000), ("payment", 3000)]),
        ("Gupta ko 3000 diye, total 8000", [("payment", 3000)]),
        ("Gupta ko 3000 diye, 5000 pending", [("payment", 3000)]),
        ("Gupta ka 8000 ka bill, 2000 discount", [("bill", 8000)]),
        ("Gupta ka bill 8000, gst 1440", [("bill", 8000)]),
        ("Gupta ko 3000 nahi diye, Verma ko 2000 diye", [("payment", 2000)]),
    ]:
        u = E.parse(text, SUPS, TODAY, [])
        assert [(p.intent, p.amount) for p in u.parts] == want, (text, u.parts)
    assert E.parse("Gupta ko 2000 nahi diye", SUPS, TODAY, []).kind == "none"
    assert E.parse("uska aadha de diya", SUPS, TODAY, []).kind == "none"
    assert E.parse("agarwal kirana la 6500 dile aaj", SUPS, TODAY, []).parts[0].intent == "payment"
    assert E.parse("gupta agency ka 4000 baad mein dena, abhi nahi diya", SUPS, TODAY, []).parts[0].intent == "future"


def test_real_speech_patterns_from_the_held_out_voice_notes():
    # 'ne' can mean 'to' (Haryanvi) or 'by': the owner is asked, never a payment card on a guess
    p = one("Sharma Traders ne do hazaar diya")
    assert (p.intent, p.ask) == ("received", "dir")
    assert one("Gupta ne mujhe 500 diye").intent == "refund"   # 'mujhe': the money came to the owner
    assert [a.value for a in find_amounts(tokenize("Thai so diye"))] == [250]   # speech-to-text for 'dhai sau'
    assert E.skeleton("burma") == E.skeleton("verma")
    sups = [{"id": "v", "name": "Verma Medical", "name_norm": "verma medical"}]
    assert [s["name"] for s in E.close_suppliers("Burma Medical", sups)] == ["Verma Medical"]  # offered, not picked
    assert E.strict_match(["burma", "medical"], sups) == (None, [])
    assert one("Verma se maine do hazaar aaya. Theek hai?").intent == "received"  # 'theek hai?' is not a question
    assert one("Sun bhai, Gupta ka bill aaya 8000 ka").bill_date is None          # 'sun' = listen, not Sunday
    p = one("Sharma Traders ne teen hazaar nahi nahi chaar hazaar")
    assert (p.intent, p.amount) == ("kind", 4000)                                 # no verb: the owner picks



def test_money_back_goods_back_and_old_dues():
    assert one("Gupta se 500 wapas aaye").intent == "refund"
    assert one("Verma traders ne zyada le liye the 300 lauta diye").intent == "refund"
    assert one("Balaji se credit note ke 1200 mil gaye cash me").intent == "refund"
    for text in ["Gupta ko 500 ka maal wapas kiya", "Gupta Agency ko 2000 ka saman return kiya",
                 "Balaji ka credit note 1500 ka", "agarwal ko kharab maal wapas bheja 700 ka"]:
        p = one(text)
        assert (p.intent, p.credit_days) == ("return", None), text
    assert one("Gupta ka purana baaki 5000").intent == "udhaar"
    assert one("Sharma Traders ka pichla baaki 12000 hai").intent == "udhaar"
    assert one("Agarwal se 3000 mila").ask == "what"
    assert one("Gupta se 5000 ka maal aaya").intent == "bill"   # the goods are named: a bill, no question
