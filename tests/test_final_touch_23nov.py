"""23 Nov review before the pilot (docs/04_decisions_log.md): what a shopkeeper types that the bot missed.
  1. 'ok' / 'haan' typed under ONE open card saves it like the button; with two cards it asks to tap; a duplicate is
     never saved on a word.
  2. Thanks and greetings get a short, warm reply, not "maaf kijiye" and the menu.
  3. The welcome promises only what every shop gets; test shops stay out of the experiment's split.
Runs on both stores."""

from pathlib import Path

from conftest import OWNER, _events, onboard, photo, text

from bot import admin
from bot import messages as M
from bot.conversation import CARD_YES_WORDS, small_words
from bot.photos import LocalPhotos

ROOT = Path(__file__).resolve().parents[1]


def events(w, name):
    return [e for e in _events(w.store) if e["event"] == name]


def test_typed_ok_saves_the_one_open_card(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    assert "Sahi hai?" in world.wa.last["body"]
    world.run(text("Ok 👍🏻!!"))
    assert len(events(world, "bill_confirmed")) == 1
    assert "Save ho gaya" in world.wa.sent[-1]["body"] or any("Save ho gaya" in m.get("body", "")
                                                               for m in world.wa.sent[-3:])
    assert events(world, "typed_yes")[-1]["props"]["result"] == "confirm"


def test_typed_haan_saves_a_payment_card(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("haan"))
    world.run(text("Gupta ko 2000 diye"))
    world.run(text("theek hai"))
    assert len(events(world, "payment_logged")) == 1
    assert any("₹2,580" in m.get("body", "") for m in world.wa.sent[-3:])


def test_typed_yes_on_a_photo_card(world):
    onboard(world)
    world.reader.next = {"supplier_name": "Verma Traders", "total_amount": 1200, "bill_date": None}
    world.run(photo())
    world.run(text("sahi hai"))
    assert len(events(world, "bill_confirmed")) == 1


def test_two_open_cards_ask_to_tap(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("Sharma ka bill 1200"))
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TAP_THE_BUTTON
    assert not events(world, "bill_confirmed")


def test_a_duplicate_is_never_saved_on_a_word(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("ok"))
    world.run(text("Gupta ka bill 4580"))       # the same bill again: the card warns
    assert "pehle se save" in world.wa.last["body"]
    world.run(text("ok"))
    assert len(events(world, "bill_confirmed")) == 1   # shown again with the warning, not saved


def test_ok_with_no_card_is_a_thank_you(world):
    onboard(world)
    for word, reply in (("ok", M.THANKS_REPLY), ("Shukriya", M.THANKS_REPLY), ("thank you ji", M.THANKS_REPLY),
                        ("Good Morning!!", M.GREET_REPLY), ("kya haal hai", M.GREET_REPLY), ("ram ram", M.GREET_REPLY)):
        world.run(text(word))
        assert world.wa.last["body"] == reply, word
    assert not any("Maaf kijiye" in m.get("body", "") for m in world.wa.sent)
    assert len(events(world, "small_talk")) == 6


def test_menu_words_still_show_the_menu(world):
    onboard(world)
    world.run(text("menu"))
    assert world.wa.last["kind"] == "list"
    world.run(text("cricket score batao"))
    assert "Maaf kijiye" in world.wa.last["body"]       # real off-topic still gets the polite decline


def test_small_words_normalises_what_owners_type():
    assert small_words("ok 👍🏻!!") == "ok" and small_words("👍🏻") == "👍" and small_words("🙏🙏") == "🙏"
    assert small_words("  haan   ji. ") == "haan ji"
    assert "👍" in CARD_YES_WORDS and "sahi hai" in CARD_YES_WORDS


def test_welcome_promises_what_every_shop_gets():
    assert "Payment ki date pe" not in M.WELCOME          # due reminders go to the treatment group only
    assert "yaad bhi dilaunga" in M.WELCOME
    assert (M.WELCOME + M.WELCOME_FOOTER).count("🔒") == 1


def test_test_shops_stay_out_of_the_split(store, tmp_path):
    lines = []
    run = lambda *a: admin.main(list(a), store=store, out=lines.append, photos=LocalPhotos(tmp_path))  # noqa: E731
    store.create_shop("919800000001")  # joined while PILOT_OPEN=1: gets a T code  # secret-scan: ignore
    run("add-shop", "S01", "kirana", "9876543210")
    run("add-shop", "S02", "medical", "9876543211")
    run("assign-groups")
    by_code = {s["shop_code"]: s.get("experiment_group") for s in store.list_shops()}
    assert by_code["T01"] is None and {by_code["S01"], by_code["S02"]} == {"treatment", "control"}


def test_the_new_bat_files_exist():
    for name, needle in (("RUN_add_shop.bat", "bot.admin add-shop"), ("RUN_list_shops.bat", "bot.admin list-shops"),
                         ("RUN_assign_groups.bat", "bot.admin assign-groups")):
        assert needle in (ROOT / name).read_text(encoding="utf-8"), name
    assert "YES" in (ROOT / "RUN_assign_groups.bat").read_text(encoding="utf-8")
