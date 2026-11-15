"""Consent: full welcome only once, then a short nudge; typed yes counts only as a whole message."""

import pytest
from conftest import OWNER, photo, tap, text

from bot import messages as M


def test_full_welcome_first_then_short_nudge(world):
    world.run(text("hi"))
    assert world.wa.last["body"] == M.WELCOME
    assert world.wa.button_ids() == ["consent_yes", "consent_example", "consent_no"]
    assert world.wa.last["header"] == M.WELCOME_HEADER and world.wa.last["footer"] == M.WELCOME_FOOTER
    assert "STOP" in M.WELCOME_FOOTER and "30 din" in M.WELCOME_FOOTER   # the privacy promise is on the first screen
    world.run(tap("consent_example"))      # an example card, then the same choice again
    assert "Sahi hai" in world.wa.last["body"] and world.wa.button_ids() == ["consent_yes", "consent_no"]
    assert world.store.get_shop_by_wa(OWNER)["consent_at"] is None
    world.run(text("ye kya hai?"))
    assert world.wa.last["body"] == M.CONSENT_NUDGE and world.wa.button_ids() == ["consent_yes", "consent_no"]
    world.run(text("hello"))
    assert world.wa.last["body"] == M.CONSENT_NUDGE
    assert sum(m.get("body") == M.WELCOME for m in world.wa.sent) == 1


def test_photo_before_consent_gets_nudge_and_is_not_read(world):
    world.run(text("hi"))
    world.run(photo())
    assert world.wa.last["body"] == M.CONSENT_NUDGE and world.wa.downloads == []


@pytest.mark.parametrize("msg", ["haan", "Haan!", "ha", "YES", "ok.", "shuru", "haan ji", "han", "haa", "Shuru karo 🙏"])
def test_typed_yes_as_whole_message_is_consent(world, msg):
    world.run(text("hi"))
    world.run(text(msg))
    assert world.wa.last["body"] == M.READY
    shop = world.store.get_shop_by_wa(OWNER)
    assert shop["consent_at"] is not None and shop["status"] == "active"
    if hasattr(world.store, "events"):
        assert world.store.events[-1]["props"] == {"via": "typed"}


@pytest.mark.parametrize("msg", ["aur ha sharma ko yaad dilana", "haan bhai photo bhejta hoon", "ok kal", "nahi",
                                 "Gupta General Store"])
def test_yes_inside_a_longer_message_is_not_consent(world, msg):
    world.run(text("hi"))
    world.run(text(msg))
    assert world.wa.last["body"] == M.CONSENT_NUDGE
    shop = world.store.get_shop_by_wa(OWNER)
    assert shop["consent_at"] is None and shop.get("shop_name") is None


def test_first_message_can_itself_be_yes(world):
    world.run(text("haan"))
    assert world.wa.last["body"] == M.READY


def test_button_still_works_and_decline_still_declines(world):
    world.run(text("hi"))
    world.run(tap("consent_no"))
    assert world.wa.last["body"] == M.CONSENT_DECLINED
    world.run(text("acha"))
    assert world.wa.last["body"] == M.CONSENT_NUDGE
    world.run(tap("consent_yes"))
    assert world.wa.last["body"] == M.READY
    if hasattr(world.store, "events"):
        assert [e["props"] for e in world.store.events if e["event"] == "opt_in"] == [{"via": "button"}]
