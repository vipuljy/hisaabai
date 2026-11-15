"""Questions about the hisaab: understanding (bot/questions.py), answers (bot/answers.py),
menus and reminders (bot/conversation.py, bot/jobs.py). No network: a fake WhatsApp and a fake Haiku.
Supplier names are invented."""

import asyncio
import itertools
from datetime import date, timedelta

import pytest
from conftest import OWNER, onboard, tap, text

from bot import messages as M
from bot import questions as Q
from bot.jobs import run_daily

THU = date(2026, 10, 22)  # a Thursday
SUPPLIERS = [
    {"id": "s1", "name": "Sharma Traders", "name_norm": "sharma traders"},
    {"id": "s2", "name": "Gupta Agency", "name_norm": "gupta agency"},
    {"id": "s3", "name": "Sharma Brothers", "name_norm": "sharma brothers"},
    {"id": "s4", "name": "Kaveri Pharma", "name_norm": "kaveri pharma"},
]
_ids = itertools.count(1)


def pick(row_id, sender=OWNER):
    return {"id": f"wamid.list{next(_ids)}", "from": sender, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": row_id, "title": "x"}}}


# ---------------------------------------------------------------- 50+ Hinglish examples
D = lambda y, m, d: date(y, m, d)  # noqa: E731
CASES = [
    # text, intent, supplier, period key, remind_on, unclear
    ("kitna baaki hai", "dues_all", None, None, None, []),
    ("kitna baki hai bhai", "dues_all", None, None, None, []),
    ("total bakaya kitna hai", "dues_all", None, None, None, []),
    ("mera udhaar kitna hai", "dues_all", None, None, None, []),
    ("kitna dena hai", "dues_all", None, None, None, []),
    ("Sharma Traders ka kitna baaki", "dues_supplier", "Sharma Traders", None, None, []),
    ("gupta ka baki", "dues_supplier", "Gupta Agency", None, None, []),
    ("Gupta Agency ko kitna dena hai", "dues_supplier", "Gupta Agency", None, None, []),
    ("gupta ka hisaab", "dues_supplier", "Gupta Agency", None, None, []),
    ("guptaa ka bakaya?", "dues_supplier", "Gupta Agency", None, None, []),
    ("Kaveri wale ka baaki batao", "dues_supplier", "Kaveri Pharma", None, None, []),
    ("Verma ka kitna baaki", "dues_supplier", None, None, None, ["supplier_not_found"]),
    ("sharma ka baaki", "dues_supplier", None, None, None, ["supplier_ambiguous"]),
    ("is mahine kitna diya", "paid_period", None, "this_month", None, []),
    ("kitna diya Sharma Traders ko is mahine", "paid_period", "Sharma Traders", "this_month", None, []),
    ("aaj kitna payment kiya", "paid_period", None, "today", None, []),
    ("kal kitna diya", "paid_period", None, "yesterday", None, []),
    ("pichhle hafte kitna diya gupta ko", "paid_period", "Gupta Agency", "last_week", None, []),
    ("pichle mahine kitna payment kiya", "paid_period", None, "last_month", None, []),
    ("is hafte kitne paise diye", "paid_period", None, "this_week", None, []),
    ("iss week total kitna paid", "paid_period", None, "this_week", None, []),
    ("kitna diya", "paid_period", None, None, None, ["period"]),
    ("Monday ko kitna diya", "paid_period", None, "day", None, []),
    ("somvaar ko kitna diya kaveri ko", "paid_period", "Kaveri Pharma", "day", None, []),
    ("ab tak gupta ko kitna diya", "paid_period", "Gupta Agency", "all", None, []),
    ("is hafte kitne bill aaye", "bills_period", None, "this_week", None, []),
    ("aaj kitne bill aaye?", "bills_period", None, "today", None, []),
    ("pichhle mahine kitna maal aaya", "bills_period", None, "last_month", None, []),
    ("gupta ke kitne bill aaye is mahine", "bills_period", "Gupta Agency", "this_month", None, []),
    ("kitne bill aaye", "bills_period", None, None, None, ["period"]),
    ("sabse zyada kiska baaki hai", "top_dues", None, None, None, []),
    ("sabse jyada baki kiska", "top_dues", None, None, None, []),
    ("top baaki", "top_dues", None, None, None, []),
    ("Gupta ko last payment kab kiya", "last_payment", "Gupta Agency", None, None, []),
    ("sharma traders ko aakhri payment kab diya", "last_payment", "Sharma Traders", None, None, []),
    ("last payment kab kiya", "last_payment", None, None, None, []),
    ("Gupta ko Friday yaad dilana", "set_reminder", "Gupta Agency", None, D(2026, 10, 23), []),
    ("kal yaad dilana", "set_reminder", None, None, D(2026, 10, 23), []),
    ("Gupta ko shukravaar ko yaad dila dena", "set_reminder", "Gupta Agency", None, D(2026, 10, 23), []),
    ("remind me on sunday", "set_reminder", None, None, D(2026, 10, 25), []),
    ("28 tarikh ko Gupta ka reminder", "set_reminder", "Gupta Agency", None, D(2026, 10, 28), []),
    ("Kaveri ko 5/11 ko yaad dilana", "set_reminder", "Kaveri Pharma", None, D(2026, 11, 5), []),
    ("Gupta ko yaad dilana", "set_reminder", "Gupta Agency", None, None, ["day"]),
    ("mere reminders dikhao", "list_reminders", None, None, None, []),
    ("reminders", "list_reminders", None, None, None, []),
    ("kaunse reminder hai", "list_reminders", None, None, None, []),
    ("Gupta wala reminder hatao", "cancel_reminder", "Gupta Agency", None, None, []),
    ("reminder cancel karo", "cancel_reminder", None, None, None, []),
]


@pytest.mark.parametrize("msg, intent, supplier, period_key, remind_on, unclear", CASES, ids=[c[0] for c in CASES])
def test_understood_by_rules(msg, intent, supplier, period_key, remind_on, unclear):
    p = Q.parse_question(msg, SUPPLIERS, THU)
    assert p.intent == intent
    assert (p.supplier or {}).get("name") == supplier
    assert (p.period.key if p.period else None) == period_key
    assert p.remind_on == remind_on
    assert p.unclear == unclear


@pytest.mark.parametrize("msg", [
    "Sharma Traders ko 5000 diye",       # a PAYMENT to save, not a question
    "gupta ko 1,200 de diye",
    "Sharma ko 5000 diye kya?",          # amount + paid -> payment flow (it asks before saving)
    "Kaveri Pharma ko ₹750 bheje",
    "Sharma Traders 4580",               # a typed bill
    "aaj cricket match kaun jeetega?",   # off topic
    "hisaab",                            # alone: the menu, handled by the conversation
])
def test_not_a_question(msg):
    assert Q.parse_question(msg, SUPPLIERS, THU).intent is None


def test_ambiguous_name_lists_every_match_and_picks_none():
    p = Q.parse_question("sharma ka baaki", SUPPLIERS, THU)
    assert p.supplier is None and [c["name"] for c in p.candidates] == ["Sharma Brothers", "Sharma Traders"]


def test_reminder_note_when_not_a_supplier():
    p = Q.parse_question("parso yaad dilana GST bharna", SUPPLIERS, THU)
    assert p.intent == "set_reminder" and p.note == "gst bharna" and p.remind_on == date(2026, 10, 24)
    assert p.unclear == []


def test_off_topic_is_not_hisaab_like_but_a_supplier_is():
    assert not Q.parse_question("aaj cricket match kaun jeetega?", SUPPLIERS, THU).hisaab_like
    assert Q.parse_question("gupta ke saath len den", SUPPLIERS, THU).hisaab_like


# ---------------------------------------------------------------- period maths on fixed dates
@pytest.mark.parametrize("key, start, end", [
    ("today", date(2026, 10, 22), date(2026, 10, 22)),
    ("yesterday", date(2026, 10, 21), date(2026, 10, 21)),
    ("this_week", date(2026, 10, 19), date(2026, 10, 22)),   # Monday to today
    ("last_week", date(2026, 10, 12), date(2026, 10, 18)),
    ("this_month", date(2026, 10, 1), date(2026, 10, 22)),
    ("last_month", date(2026, 9, 1), date(2026, 9, 30)),
])
def test_periods(key, start, end):
    p = Q.period(key, THU)
    assert (p.start, p.end) == (start, end)


def test_periods_across_year_and_on_a_monday():
    jan = date(2027, 1, 4)  # a Monday
    assert (Q.period("last_month", jan).start, Q.period("last_month", jan).end) == (date(2026, 12, 1), date(2026, 12, 31))
    assert Q.period("this_week", jan).start == jan
    assert Q.period("last_week", jan).start == date(2026, 12, 28)


@pytest.mark.parametrize("words, future, want", [
    ("kal", False, date(2026, 10, 21)), ("kal", True, date(2026, 10, 23)),       # kal = yesterday / tomorrow
    ("parso", False, date(2026, 10, 20)), ("parso", True, date(2026, 10, 24)),
    ("thursday", False, date(2026, 10, 22)), ("thursday", True, date(2026, 10, 29)),  # same weekday: today / next week
    ("ravivaar", True, date(2026, 10, 25)), ("monday", False, date(2026, 10, 19)),
    ("28 tarikh", False, date(2026, 9, 28)), ("28 tarikh", True, date(2026, 10, 28)),
    ("5/11", True, date(2026, 11, 5)), ("15/10/26", False, date(2026, 10, 15)),
])
def test_day_words(words, future, want):
    day, _ = Q.find_day(Q.tokens(words), THU, future=future)
    assert day == want


def test_31st_skips_a_short_month():
    day, _ = Q.find_day(Q.tokens("31 tarikh"), date(2026, 11, 5), future=True)
    assert day == date(2026, 12, 31)


def test_labels_and_money_formats():
    assert M.period_label("this_week", date(2026, 10, 19), date(2026, 10, 22)) == "Is hafte (19–22 Oct)"
    assert M.period_label("last_month", date(2026, 9, 1), date(2026, 9, 30)) == "Pichhle mahine (1–30 Sep)"
    assert M.span(date(2026, 9, 28), date(2026, 10, 3)) == "28 Sep–3 Oct"
    assert M.inr(101757) == "₹1,01,757" and M.inr(12400) == "₹12,400"


def test_empty_answers_name_the_period_not_zero():
    label = M.period_label("this_week", date(2026, 10, 19), date(2026, 10, 22))
    assert M.paid_answer([], label, None) == "💸 Is hafte (19–22 Oct) koi payment nahi."
    assert M.bills_answer([], label, "Gupta Agency") == "🧾 Is hafte (19–22 Oct) Gupta Agency ka koi bill nahi aaya."


# ---------------------------------------------------------------- the conversation
def add_bill(w, name, amount):
    w.run(text(f"{name} {amount}"))
    if w.wa.last["kind"] == "list":  # a name like an existing supplier's: the owner taps "➕ Naya: <name>"
        w.run(pick(next(i for i in w.wa.list_ids() if i.startswith("spn:"))))
    w.run(tap(w.wa.button_ids()[0]))


def add_payment(w, name, amount):
    w.run(text(f"{name} ko {amount} diye"))
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("pok:"))))


@pytest.fixture()
def ledger(world):
    onboard(world)
    world.run(text("naam: Ramesh Kirana"))  # the shop's own name
    add_bill(world, "Sharma Traders", 4580)
    add_bill(world, "Sharma Traders", 3000)
    add_bill(world, "Gupta Agency", 12400)
    add_payment(world, "Sharma Traders", 1000)
    return world


def today():
    from bot.conversation import IST
    from datetime import datetime
    return datetime.now(IST).date()


def test_supplier_dues_states_its_basis(ledger):
    ledger.run(text("sharma traders ka kitna baki hai?"))
    body = ledger.wa.last["body"]
    assert "*Sharma Traders*: *₹6,580* baaki" in body
    assert "🧾 2 bill: +₹7,580" in body and "💸 1 payment: −₹1,000" in body   # the arithmetic, by kind


def test_dues_all_keeps_old_event_with_question_props(ledger):
    ledger.run(text("kitna baaki hai"))
    assert "Kul baaki: ₹18,980" in ledger.wa.last["body"]
    ev = ledger.store.events[-1] if hasattr(ledger.store, "events") else None
    if ev:
        assert ev["event"] == "dues_asked" and ev["props"]["understood_by"] == "rules"


def test_paid_today_and_empty_last_week(ledger):
    ledger.run(text("aaj kitna diya"))
    assert ledger.wa.last["body"].startswith(f"💸 Aaj ({M.short_date(today())}) kul *₹1,000* diye (1 payment)")
    ledger.run(text("pichhle hafte kitna diya gupta ko"))
    per = Q.period("last_week", today())
    assert ledger.wa.last["body"] == f"💸 Pichhle hafte ({M.span(per.start, per.end)}) Gupta Agency ko koi payment nahi."


def test_bills_today_and_top_and_last_payment(ledger):
    ledger.run(text("aaj kitne bill aaye"))
    assert "*3 bill* aaye, kul *₹19,980*" in ledger.wa.last["body"]
    ledger.run(text("sabse zyada kiska baaki hai"))
    assert "Sabse zyada baaki: *Gupta Agency* — *₹12,400*" in ledger.wa.last["body"]
    ledger.run(text("Sharma ko last payment kab kiya"))
    assert ledger.wa.last["body"].startswith("💸 Sharma Traders ko last payment: *₹1,000*")


def test_payment_still_a_payment(ledger):
    ledger.run(text("Gupta Agency ko 5000 diye"))
    assert ledger.wa.last["kind"] == "buttons" and "₹5,000" in ledger.wa.last["body"]


def test_two_matching_suppliers_show_a_list_never_a_guess(ledger):
    add_bill(ledger, "Sharma Brothers", 900)
    ledger.run(text("sharma ka kitna baaki"))
    last = ledger.wa.last
    assert last["kind"] == "list" and "2 supplier" in last["body"]
    assert sorted(t for _, t, _ in last["rows"]) == ["Sharma Brothers", "Sharma Traders"]
    brothers = next(i for i, t, _ in last["rows"] if t == "Sharma Brothers")
    ledger.run(pick(brothers))
    assert "*Sharma Brothers*: *₹900* baaki" in ledger.wa.last["body"]


def test_unknown_supplier_shows_the_supplier_list(ledger):
    ledger.run(text("Verma ka kitna baaki"))
    last = ledger.wa.last
    assert last["kind"] == "list" and "*Verma* naam ka supplier nahi mila" in last["body"]
    assert [t for _, t, _ in last["rows"]] == ["Gupta Agency", "Sharma Traders"]  # most baaki first
    assert last["rows"][0][2] == "Baaki ₹12,400"


def test_menu_walk_through_paid_period(ledger):
    ledger.run(text("hisaab"))
    assert ledger.wa.last["kind"] == "list" and len(ledger.wa.last["rows"]) == 9
    ledger.run(pick("q:paid_period"))
    assert ledger.wa.list_ids()[0] == "qs:all"
    ledger.run(pick("qs:all"))
    assert ledger.wa.last["button"] == M.LIST_BTN_PERIOD
    ledger.run(pick("qp:this_month"))
    assert "kul *₹1,000* diye" in ledger.wa.last["body"]
    names = ledger.events()
    assert "question_asked" in names and names[-1] == "question_answered"


def test_half_understood_question_asks_then_accepts_typed_answer(ledger):
    ledger.run(text("kitna diya"))
    assert ledger.wa.last["kind"] == "list" and ledger.wa.last["body"].startswith("🤔 Samjha: *Kitna payment diya*")
    ledger.run(text("is mahine"))
    assert "kul *₹1,000* diye" in ledger.wa.last["body"]


def test_off_topic_keeps_decline_text_inside_the_list(ledger):
    ledger.run(text("aaj cricket match kaun jeetega?"))
    assert ledger.wa.last["kind"] == "list" and ledger.wa.last["body"] == M.OFF_TOPIC


def test_stale_tap_and_bad_ids_are_safe(ledger):
    ledger.run(pick("qp:this_month"))
    assert ledger.wa.last["body"] == M.QUESTION_EXPIRED
    ledger.run(pick("qr:not-a-real-id"))
    assert ledger.wa.last["body"] == M.REMINDER_GONE
    ledger.run(pick("q:steal_money"))
    assert ledger.wa.last["kind"] == "list"


# ---------------------------------------------------------------- reminders
def test_reminder_set_listed_cancelled(ledger):
    ledger.run(text("Gupta ko kal yaad dilana"))
    tomorrow = today() + timedelta(days=1)
    assert ledger.wa.last["body"].startswith(f"✅ {M.reminder_date(tomorrow)} ko yaad dilaunga: Gupta Agency — baaki ₹12,400")
    ledger.run(text("mere reminders dikhao"))
    assert f"• {M.day_label(tomorrow)}: Gupta Agency" in ledger.wa.last["body"]
    ledger.run(pick("q:cancel_reminder"))
    rid = ledger.wa.list_ids()[0]
    ledger.run(pick(rid))
    assert ledger.wa.last["body"].startswith("✅ Reminder hata diya")
    ledger.run(text("reminders"))
    assert ledger.wa.last["body"] == M.NO_REMINDERS
    assert {"reminder_set", "reminder_cancelled"} <= set(ledger.events())


def test_reminder_by_menu_picks_supplier_and_day(ledger):
    ledger.run(pick("q:set_reminder"))
    assert ledger.wa.list_ids()[0] == "qs:none"
    ledger.run(pick("qs:none"))
    assert ledger.wa.list_ids()[-1] == "qd:more"
    ledger.run(pick(ledger.wa.list_ids()[2]))  # "3 din baad"
    assert f"{M.reminder_date(today() + timedelta(days=3))} ko yaad dilaunga: hisaab dekhna\n" in ledger.wa.last["body"]


def test_reminder_job_sends_once_to_both_groups(ledger):
    shop = ledger.store.get_shop_by_wa(OWNER)
    ledger.store.update_shop(shop["id"], experiment_group="control")
    ledger.run(text("Sharma Traders ko kal yaad dilana"))
    tomorrow = today() + timedelta(days=1)
    asyncio.run(run_daily(ledger.store, ledger.wa, today()))  # not due yet
    assert not [m for m in ledger.wa.sent if m.get("name") == "owner_reminder"]
    c = asyncio.run(run_daily(ledger.store, ledger.wa, tomorrow))
    sent = [m for m in ledger.wa.sent if m.get("name") == "owner_reminder"]
    assert c["owner_reminders"] == 1 and sent[-1]["params"] == ["Sharma Traders", "₹6,580"]
    assert asyncio.run(run_daily(ledger.store, ledger.wa, tomorrow + timedelta(days=1)))["owner_reminders"] == 0
    ev = [e for e in getattr(ledger.store, "events", []) if e["event"] == "owner_reminder_sent"]
    if hasattr(ledger.store, "events"):
        assert len(ev) == 1 and ev[0]["props"]["group"] == "control"
    assert "reminder_sent" not in ledger.events()  # the experiment's nudge event stays treatment-only


# ---------------------------------------------------------------- events and the optional Haiku fallback
def test_question_events_record_who_understood(ledger):
    ledger.run(text("is mahine kitna diya"))
    asked = [e for e in getattr(ledger.store, "events", []) if e["event"] == "question_asked"]
    if asked:
        assert asked[-1]["props"] == {"intent": "paid_period", "source": "text", "understood_by": "rules", "unclear": []}
    assert ledger.events()[-1] == "question_answered"


def test_no_ai_call_without_the_flag(ledger):
    ledger.run(text("gupta ke saath len den"))
    assert ledger.wa.last["kind"] == "list" and "ai_call" not in ledger.events()


def test_llm_fallback_classifies_but_numbers_come_from_the_database(ledger):
    seen = {}

    def fake_haiku(text_, names, today_):
        seen["names"] = names
        return {"intent": "dues_supplier", "supplier": "Gupta Agency", "amount": 99999}  # amount is ignored
    ledger.bot.question_llm = fake_haiku
    ledger.run(text("gupta ke saath len den"))
    assert "*Gupta Agency*: *₹12,400* baaki" in ledger.wa.last["body"] and "99,999" not in ledger.wa.last["body"]
    assert "Gupta Agency" in seen["names"] and "ai_call" in ledger.events()


@pytest.mark.parametrize("reply", [{"intent": "transfer_money"}, None, {"intent": "dues_supplier", "supplier": "Nobody"}])
def test_llm_unsure_or_invalid_shows_the_menu(ledger, reply):
    ledger.bot.question_llm = lambda *a: reply
    ledger.run(text("gupta ke saath len den"))
    assert ledger.wa.last["kind"] == "list" and ledger.wa.last["body"].startswith("🤔")


def test_llm_crash_shows_the_menu(ledger):
    def boom(*a):
        raise RuntimeError("down")
    ledger.bot.question_llm = boom
    ledger.run(text("gupta ke saath len den"))
    assert ledger.wa.last["kind"] == "list"


def test_from_llm_validates_everything():
    p = Q.from_llm({"intent": "paid_period", "supplier": "sharma", "period": "forever"}, SUPPLIERS, THU)
    assert p.intent == "paid_period" and p.period is None and len(p.candidates) == 2
    far = Q.from_llm({"intent": "set_reminder", "remind_on": "2030-01-01"}, SUPPLIERS, THU)
    assert far.remind_on is None


def test_voice_question_uses_the_same_path(ledger):
    async def heard(_audio):
        return "Gupta Agency ka kitna baaki hai"
    ledger.bot.transcriber = heard
    ledger.run({"id": "wamid.voiceq1", "from": OWNER, "type": "audio", "audio": {"id": "a1"}})
    assert "*Gupta Agency*: *₹12,400* baaki" in ledger.wa.last["body"]


# ---------------------------------------------------------------- reminder wording (simulator bug, 25 Oct)
ONE_SHARMA = [s for s in SUPPLIERS if s["id"] != "s3"]
REMINDER_CASES = [
    # text, supplier, remind_on (today = Thu 22 Oct 2026), note
    ("aur ha sharma ko 3 din me yaad dilana vapas krne h", "Sharma Traders", date(2026, 10, 25), "vapas krne"),
    ("Gupta ko teen din baad yaad dilana", "Gupta Agency", date(2026, 10, 25), None),
    ("gupta ko 5 dino me remind karna", "Gupta Agency", date(2026, 10, 27), None),
    ("parso yaad dilana", None, date(2026, 10, 24), None),
    ("agle hafte gupta ko yaad dilana", "Gupta Agency", date(2026, 10, 26), None),        # next Monday
    ("agle mahine yaad dilana rent dena", None, date(2026, 11, 1), "rent"),               # 1st of next month
    ("28 tarikh ko Gupta ko yaad dila dena", "Gupta Agency", date(2026, 10, 28), None),
    ("achha ok gupta ko kal yaad dilana", "Gupta Agency", date(2026, 10, 23), None),      # fillers at the start
    ("Gupta ka baaki kal bata dena", "Gupta Agency", date(2026, 10, 23), None),           # 'bata dena' + a day
    ("bhai ek hafte baad kaveri ko yaad dilana", "Kaveri Pharma", date(2026, 10, 29), None),
    ("sharma traders ka reminder lagao paanch din me", "Sharma Traders", date(2026, 10, 27), None),
    ("ji haan Kaveri ko do din me yaad dilana payment karna hai", "Kaveri Pharma", date(2026, 10, 24), "payment"),
    ("Gupta ko yaad dilana sabse zyada baaki hai", "Gupta Agency", None, "sabse zyada"),   # reminder beats top_dues
]


@pytest.mark.parametrize("msg, supplier, remind_on, note", REMINDER_CASES, ids=[c[0] for c in REMINDER_CASES])
def test_reminder_words_win_and_days_are_understood(msg, supplier, remind_on, note):
    p = Q.parse_question(msg, ONE_SHARMA, THU)
    assert p.intent == "set_reminder"
    assert (p.supplier or {}).get("name") == supplier
    assert p.remind_on == remind_on
    assert p.note == note


def test_bata_dena_without_a_day_stays_a_question():
    assert Q.parse_question("kitna baaki hai bata dena", ONE_SHARMA, THU).intent == "dues_all"


def test_n_din_pehle_in_a_question():
    p = Q.parse_question("3 din pehle kitna diya", ONE_SHARMA, THU)
    assert p.intent == "paid_period" and p.period.start == date(2026, 10, 19)


def test_new_reminder_is_not_swallowed_by_a_pending_supplier_question(ledger):
    """The simulator bug: a pending 'which supplier?' list made the bot answer dues instead."""
    ledger.run(pick("q:dues_supplier"))
    assert ledger.wa.last["button"] == M.LIST_BTN_SUPPLIER
    ledger.run(text("aur ha sharma ko 3 din me yaad dilana vapas krne h"))
    d = today() + timedelta(days=3)
    assert ledger.wa.last["body"].startswith(
        f"✅ {M.reminder_date(d)} ko yaad dilaunga: Sharma Traders: vapas krne — baaki ₹6,580")
    rows = ledger.store.list_reminders(ledger.store.get_shop_by_wa(OWNER)["id"], today())
    assert rows[0]["text"] == "Sharma Traders: vapas krne"


def test_unclear_day_asks_with_a_list_never_guesses(ledger):
    ledger.run(text("Gupta ko yaad dilana"))
    titles = [t for _, t, _ in ledger.wa.last["rows"]]
    assert titles[:3] == ["Kal", "Parso", "3 din baad"] and titles[-1] == M.DAY_MORE_ROW
    assert "reminder_set" not in ledger.events()
    ledger.run(pick("qd:more"))
    assert len(ledger.wa.last["rows"]) == 10
    ledger.run(pick(ledger.wa.list_ids()[6]))  # 7 days ahead
    assert f"{M.reminder_date(today() + timedelta(days=7))} ko yaad dilaunga: Gupta Agency" in ledger.wa.last["body"]


def test_typed_date_answers_the_day_list(ledger):
    ledger.run(text("Gupta ko yaad dilana"))
    ledger.run(text("parso"))
    assert ledger.wa.last["body"].startswith(f"✅ {M.reminder_date(today() + timedelta(days=2))} ko yaad dilaunga")


# ---------------------------------------------------------------- fixes from the step-10 question eval
@pytest.mark.parametrize("msg", ["sabka kitna baaki hai", "mujhe kul kitna dena hai sabko",
                                 "bhai saare supplier ka hisab batao"])
def test_all_supplier_words_are_never_a_supplier_name(msg):
    p = Q.parse_question(msg, SUPPLIERS, THU)
    assert p.intent == "dues_all" and p.supplier_text is None and not p.unclear


def test_bills_with_a_named_period_need_no_question_word():
    p = Q.parse_question("sharma traders ke bill is mahine", SUPPLIERS, THU)
    assert p.intent == "bills_period" and p.supplier["name"] == "Sharma Traders" and p.period.key == "this_month"
    p = Q.parse_question("pichhle mahine ka payment", SUPPLIERS, THU)
    assert p.intent == "paid_period" and p.period.key == "last_month"
    assert Q.parse_question("sharma traders ka bill", SUPPLIERS, THU).intent is None  # no period: still the menu


def test_a_typo_matching_more_words_beats_a_one_word_tie():
    p = Q.parse_question("shrma tradrs ka udhar", SUPPLIERS, THU)
    assert p.supplier["name"] == "Sharma Traders" and not p.candidates
    p = Q.parse_question("sharma ka udhar", SUPPLIERS, THU)  # one word, two Sharmas: still asks
    assert p.supplier is None and {c["name"] for c in p.candidates} == {"Sharma Traders", "Sharma Brothers"}
