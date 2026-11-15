"""16 Nov phone test (Rounds 11-17): English messages, goods sent back while the shop still owes, partial returns.
Runs on both stores."""

from datetime import date

import pytest
from conftest import OWNER, onboard, tap, text

from bot import entries as E

TODAY = date(2026, 11, 16)
SUPS = [{"id": "g", "name": "Gupta", "name_norm": "gupta"}, {"id": "s", "name": "Sharma Traders",
                                                             "name_norm": "sharma traders"}]


def intents(msg):
    u = E.parse(msg, SUPS, TODAY)
    return [(p.intent, p.amount) for p in u.parts]


@pytest.mark.parametrize("msg,want", [
    ("gupta gave me 500", [("refund", 500)]),            # was a bill for "Gupta Gave"
    ("gupta paid me 500", [("refund", 500)]),            # was a payment BY the shop
    ("got 500 back from gupta", [("refund", 500)]),
    ("gupta returned 500", [("refund", 500)]),
    ("paid gupta 500", [("payment", 500)]),
    ("i paid 2000 to sharma traders", [("payment", 2000)]),
    ("sent 500 to gupta", [("payment", 500)]),
    ("gupta 500 paid", [("payment", 500)]),
    ("received 500 from gupta", [("received", 500)]),    # goods or money? asked
    ("got 500 from gupta", [("received", 500)]),
    ("gupta gave 500", [("received", 500)]),             # which way? asked
    ("gupta paid 500", [("received", 500)]),
    ("bought 500 from gupta", [("bill", 500)]),
    ("returned goods 500 to gupta", [("return", 500)]),  # was a bill
    ("gupta took back the goods 500", [("return", 500)]),
    ("i owe gupta 500", [("future", 500)]),
    ("gupta ne maal wapas liya 300 ka", [("return", 300)]),       # was money back: the dues must go DOWN
    ("gupta ne 300 ka maal wapas le liya", [("return", 300)]),
    ("Gupta se 500 wapas liye", [("refund", 500)]),               # money: unchanged
    ("Sharma ko 500 pay kiya", [("payment", 500)]),               # Hinglish 'pay': unchanged
])
def test_english_and_return_direction(msg, want):
    assert intents(msg) == want


def test_english_words_never_become_a_supplier_name():
    u = E.parse("gupta gave me 500", SUPS, TODAY)
    assert u.parts[0].supplier["name"] == "Gupta" and "Gave" not in (u.parts[0].name or "")
    u = E.parse("gupta ko 500 ka maal wapas kiya, 300 hi kata", SUPS, TODAY)
    assert len(u.parts) == 1 and u.parts[0].amounts == (500, 300)        # 'Kata' is not a supplier


@pytest.mark.parametrize("msg,intent", [
    ("gupta ko 500 ka maal wapas kiya, 300 hi kata", "return"),
    ("gupta ne 500 ke maal ke 300 hi wapas diye", "refund"),
    ("returned goods worth 500 to gupta, he accepted only 300", "return"),
])
def test_partial_return_keeps_both_amounts(msg, intent):
    p = E.parse(msg, SUPS, TODAY).parts[0]
    assert (p.intent, sorted(p.amounts)) == (intent, [300, 500])


def btn(w, prefix, i=0):
    return [b for b in w.wa.button_ids() if b.startswith(prefix)][i]


def dues(w):
    shop = w.store.get_shop_by_wa(OWNER)
    return {r["supplier"]: int(r["due"]) for r in w.store.dues(shop["id"])}


@pytest.fixture()
def gupta_owed(world):
    onboard(world)
    world.run(text("naam: Ramesh Kirana"))
    world.run(text("Gupta se 1000 ka bartan liya"))
    world.run(tap(btn(world, "ok:")))
    return world


def test_english_gave_me_is_never_a_bill(gupta_owed):
    w = gupta_owed
    w.run(text("gupta gave me 500"))
    assert "Kyun?" in w.wa.last["body"] and "Naya bill" not in w.wa.last["body"]


def test_goods_back_while_owing_asks_cash_or_cut_and_cut_lowers_the_baaki(gupta_owed):
    w = gupta_owed
    w.run(text("Gupta se 500 mile vapas"))
    w.run(tap(btn(w, "rfw_g:")))
    body = w.wa.last["body"]
    assert "Paisa haath mein mila, ya baaki se kata?" in body and "₹1,000 → ₹500" in body
    assert w.wa.button_ids()[0].startswith("rcash_cut:")           # the usual answer comes first
    w.run(tap(btn(w, "rcash_cut:")))
    assert "Maal wapas" in w.wa.last["body"] and "₹1,000 → *₹500*" in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Gupta"] == 500


def test_goods_back_with_cash_in_hand_keeps_the_baaki(gupta_owed):
    w = gupta_owed
    w.run(text("Gupta se 500 wapas aaye expiry maal ke"))
    w.run(tap(btn(w, "rcash_got:")))
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Gupta"] == 1000


def test_goods_back_after_paying_needs_no_cash_question(gupta_owed):
    w = gupta_owed
    w.run(text("Gupta ko 1000 diye"))
    w.run(tap(btn(w, "pok:")))
    w.run(text("Gupta se 500 wapas aaye expiry maal ke"))          # nothing owed: the cash came back
    assert "baaki se kata" not in w.wa.last["body"] and btn(w, "pok:")


def test_partial_return_asks_what_the_supplier_accepted(gupta_owed):
    w = gupta_owed
    w.run(text("gupta ko 500 ka maal wapas kiya, 300 hi kata"))
    assert "Gupta ne kitna maana?" in w.wa.last["body"]
    assert [t for _, t in w.wa.last["buttons"]][:2] == ["₹300", "₹500"]
    w.run(tap(btn(w, "rmaan:", 0)))
    body = w.wa.last["body"]
    assert "₹500 ka maal, supplier ne ₹300 maana" in body and "₹1,000 → *₹700*" in body
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Gupta"] == 700


def test_partial_return_with_cash_goes_through_both_questions(gupta_owed):
    w = gupta_owed
    w.run(text("gupta ne 500 ke maal ke 300 hi wapas diye"))
    w.run(tap(btn(w, "rmaan:", 0)))
    assert "*₹500* ka maal lautaya, *₹300* maana" in w.wa.last["body"]
    w.run(tap(btn(w, "rcash_got:")))
    assert "₹500 ka maal, supplier ne ₹300 maana" in w.wa.last["body"]
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Gupta"] == 1000
