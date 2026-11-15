"""The bot's behaviour, message by message. Each test runs on the in-memory store and on Postgres."""

import asyncio

from conftest import OWNER, FakeReader, FakeWA, onboard, photo, tap, text

from bot import messages as M
from bot.conversation import Bot, match_supplier, parse_payment
from bot.store import MemoryStore

SHARMA = {"supplier_name": "Sharma Traders", "total_amount": 4580, "bill_date": "2026-10-05",
          "invoice_number": "INV-2231"}


def send_bill(w, fields):
    w.reader.next = fields
    w.run(photo())
    return w.wa.last


def confirm_last(w):
    ok = next(b for b in w.wa.button_ids() if b.startswith("ok:"))
    w.run(tap(ok))


# ---------------------------------------------------------------- onboarding and access
def test_invite_only_when_pilot_closed(store):
    wa = FakeWA()
    bot = Bot(store, wa, FakeReader(), pilot_open=False)
    asyncio.run(bot.handle(text("hi", "919999999999")))
    assert wa.last["body"] == M.NOT_IN_PILOT
    assert store.get_shop_by_wa("919999999999") is None


def test_nothing_processed_before_consent(world):
    world.run(text("hi"))
    assert world.wa.last["kind"] == "buttons" and "consent_yes" in world.wa.button_ids()
    world.reader.next = SHARMA
    world.run(photo())
    assert world.wa.downloads == []  # photo ignored until consent
    world.run(tap("consent_yes"))
    assert world.wa.last["body"] == M.READY
    assert "opt_in" in world.events()


def test_duplicate_delivery_processed_once(world):
    onboard(world)
    msg = text("kitna baaki hai")
    world.run(msg)
    world.run(msg)
    assert world.events().count("dues_asked") == 1


# ---------------------------------------------------------------- bills
def test_happy_path_bill_confirmed_and_dues(world):
    onboard(world)
    summary = send_bill(world, SHARMA | {"unsure_fields": ["invoice_number"]})
    assert world.wa.sent[-2]["body"] == M.READING
    assert "Sharma Traders" in summary["body"] and "₹4,580" in summary["body"] and "⚠️" in summary["body"]
    confirm_last(world)
    assert "₹4,580" in world.wa.sent[-2]["body"]
    # first saved entry: the shop's name is asked once, by tap (no WhatsApp profile name in this test)
    assert world.wa.last["body"] == M.ASK_SHOP_NAME and world.wa.button_ids() == ["sn_edit", "sn_skip"]
    world.run(text("kitna baaki hai?"))
    assert "Sharma Traders: ₹4,580" in world.wa.last["body"]
    # a supplier the shop never had: the card said "➕ naya supplier", and it is created only on "Sahi hai"
    assert world.events()[-5:] == ["bill_received", "supplier_created", "bill_confirmed", "shop_name_asked",
                                   "dues_asked"]


def test_missing_total_is_asked_and_counts_as_correction(world):
    onboard(world)
    send_bill(world, {"supplier_name": "Gupta Agency", "total_amount": None})
    assert world.wa.last["body"] == M.MISSING_PREFIX + M.ASK_FIELD["total_amount"]
    world.run(text("pata nahi"))
    assert world.wa.last["body"] == M.BAD_VALUE["total_amount"]
    world.run(text("Rs 1,235"))
    assert "₹1,235" in world.wa.last["body"]
    confirm_last(world)
    assert "bill_corrected" in world.events()


def test_owner_edits_total_before_confirming(world):
    onboard(world)
    send_bill(world, SHARMA)
    edit = next(b for b in world.wa.button_ids() if b.startswith("edit:"))
    world.run(tap(edit))
    assert world.wa.last["body"] == M.EDIT_WHAT
    field_btn = next(b for b in world.wa.button_ids() if b.endswith(":total_amount"))
    world.run(tap(field_btn))
    world.run(text("4850"))
    assert "₹4,850" in world.wa.last["body"]
    confirm_last(world)
    assert "₹4,850" in world.wa.sent[-2]["body"]
    assert world.events()[-2:] == ["bill_corrected", "shop_name_asked"]


def test_same_supplier_spelled_differently_is_merged(world):
    onboard(world)
    send_bill(world, SHARMA)
    confirm_last(world)
    send_bill(world, {"supplier_name": "SHARMA TRADERS.", "total_amount": 1000})
    confirm_last(world)
    assert "₹5,580" in world.wa.last["body"]
    assert len(world.store.list_suppliers(world.store.get_shop_by_wa(OWNER)["id"])) == 1


def test_not_a_bill_and_reader_failure(world):
    onboard(world)
    send_bill(world, {"is_bill": False})
    assert world.wa.last["body"] == M.NOT_A_BILL
    world.reader.fail = True
    world.run(photo())
    assert world.wa.last["body"] == M.READ_FAILED
    assert "extract_failed" in world.events()


def test_cannot_confirm_someone_elses_bill(world):
    onboard(world)
    send_bill(world, SHARMA)
    ok = next(b for b in world.wa.button_ids() if b.startswith("ok:"))
    onboard(world, "918888888888")
    world.run(tap(ok, "918888888888"))
    assert world.wa.last["body"] == M.ENTRY_GONE   # says nothing about another shop's entry


# ---------------------------------------------------------------- payments
def test_payment_confirmed_reduces_dues(world):
    onboard(world)
    send_bill(world, SHARMA)
    confirm_last(world)
    world.run(text("Sharma ji ko 2,000 diye"))
    assert world.wa.last["kind"] == "buttons" and "₹2,000" in world.wa.last["body"]
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("pok:"))))
    assert "₹2,580" in world.wa.last["body"]
    assert "payment_logged" in world.events()


def test_payment_cancel_and_unknown_supplier(world):
    onboard(world)
    send_bill(world, SHARMA)
    confirm_last(world)
    world.run(text("sharma ko 500 diye"))
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("pcancel:"))))
    assert world.wa.last["body"] == M.PAY_CANCELLED
    world.run(text("Verma ko 300 diye"))  # a supplier the shop does not have: dues from before the bot?
    assert world.wa.button_ids()[:2] == [f"old_yes:{world.wa.button_ids()[0][8:]}", f"old_no:{world.wa.button_ids()[0][8:]}"]
    world.run(tap(world.wa.button_ids()[1]))   # 'Nahi, advance hai': the card says the supplier would be NEW
    assert world.wa.last["kind"] == "buttons" and M.NEW_SUPPLIER_TAG in world.wa.last["body"]
    world.run(text("kitna baaki"))  # nothing was saved for Verma (no tap)
    assert "₹4,580" in world.wa.last["body"] and "Verma" not in world.wa.last["body"]


# ---------------------------------------------------------------- guardrails
def test_off_topic_is_declined(world):
    onboard(world)
    world.run(text("aaj cricket match kaun jeetega?"))
    assert world.wa.last["body"] == M.OFF_TOPIC
    assert world.events()[-1] == "off_topic"


def test_stop_and_start(world):
    onboard(world)
    world.run(text("STOP"))
    assert world.wa.last["body"] == M.OPTED_OUT
    world.run(photo())
    assert world.wa.last["body"] == M.OPTED_OUT_HINT and world.wa.downloads == []
    world.run(text("start"))
    assert world.wa.last["body"] == M.WELCOME_BACK


# ---------------------------------------------------------------- small helpers
def test_parse_payment():
    assert parse_payment("Sharma ji ko 5,000 diye") == (5000, "sharma")
    assert parse_payment("maine Gupta Agency ko ₹1200 bhej diye") == (1200, "gupta agency")
    assert parse_payment("5000 ka bill aaya") is None
    assert parse_payment("payment kar diya") is None


def test_match_supplier_refuses_ambiguous():
    sups = [{"id": "1", "name_norm": "sharma traders"}, {"id": "2", "name_norm": "sharma medicals"}]
    assert match_supplier("sharma", sups) is None
    assert match_supplier("sharma traders", sups)["id"] == "1"
    assert match_supplier("sharma tradrs", sups)["id"] == "1"


def test_indian_rupee_format():
    assert M.inr(180000) == "₹1,80,000" and M.inr(4580) == "₹4,580" and M.inr(-200) == "-₹200"


def test_memory_store_is_default_safe():
    assert MemoryStore().dues("nope") == []


# ---------------------------------------------------------------- supplier matching: same rule as the eval
def test_sharma_is_never_matched_to_verma():
    sups = [{"id": "v", "name": "Verma Traders", "name_norm": "verma traders"}]
    assert match_supplier("Sharma Traders", sups) is None
    assert match_supplier("sharma", sups) is None


def test_city_appended_is_the_same_supplier():
    sups = [{"id": "k", "name": "Navrang Enterprises", "name_norm": "navrang enterprises"}]
    assert match_supplier("NAVRANG ENTERPRISES, HISAR", sups)["id"] == "k"
    assert match_supplier("Navrang Enterprises Hisar", sups)["id"] == "k"


def test_payment_to_sharma_is_not_proposed_as_verma(world):
    onboard(world)
    send_bill(world, SHARMA | {"supplier_name": "Verma Traders"})
    confirm_last(world)
    world.run(text("Sharma Traders ko 5000 diye"))
    assert "Verma" not in world.wa.last["body"]    # the 'dues from before?' question is about Sharma Traders
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("old_no:"))))
    body = world.wa.last["body"]  # never a card for Verma Traders: a NEW supplier Sharma Traders, if tapped
    assert "Sharma Traders" in body and M.NEW_SUPPLIER_TAG in body and "Verma" not in body
    world.run(text("Verma Traders ko 1000 diye"))
    assert world.wa.last["kind"] == "buttons" and "Verma Traders" in world.wa.last["body"]  # confirm button kept


def test_sharma_bill_is_not_added_to_verma(world):
    onboard(world)
    send_bill(world, SHARMA | {"supplier_name": "Verma Traders", "total_amount": 1000})
    confirm_last(world)
    send_bill(world, SHARMA | {"total_amount": 4580})
    confirm_last(world)
    world.run(text("kitna baaki hai"))
    assert "Verma Traders: ₹1,000" in world.wa.last["body"] and "Sharma Traders: ₹4,580" in world.wa.last["body"]


def test_question_about_sharma_does_not_answer_with_verma(world):
    onboard(world)
    send_bill(world, SHARMA | {"supplier_name": "Verma Traders"})
    confirm_last(world)
    world.run(text("Sharma Traders ka kitna baaki"))
    assert world.wa.last["kind"] == "list" and "nahi mila" in world.wa.last["body"]
