"""Live feedback from owners (bot/feedback_flow.py, 20 Nov): said in the chat, offered by the bot, a 3-tap rating, and
the first section of the health report. Runs on both stores."""

import itertools
from datetime import date, datetime, timedelta, timezone

from conftest import OWNER, _events, onboard, photo, tap, text

from bot import feedback_flow as F
from bot import health
from bot import messages as M

_ids = itertools.count(1)


def pick(row_id):
    return {"id": f"wamid.fbpick{next(_ids)}", "from": OWNER, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": row_id, "title": "x"}}}


def voice():
    return {"id": f"wamid.fbvoice{next(_ids)}", "from": OWNER, "type": "audio", "audio": {"id": "aud1"}}


def heard(words):
    async def t(_audio):
        return words
    return t


def props(w, name):
    return [e["props"] for e in _events(w.store) if e["event"] == name]


def shop(w):
    return w.store.get_shop_by_wa(OWNER)


def bill(w, name="Sharma Traders", amount=4580):
    w.reader.next = {"supplier_name": name, "total_amount": amount}
    w.run(photo())
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("ok:"))))


def cancelled_bill(w):
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    w.run(photo())
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("bcancel:"))))


# ---------------------------------------------------------------- 1. the owner says it
def test_keyword_alone_asks_then_the_next_message_is_saved(world):
    onboard(world)
    world.run(text("feedback"))
    assert world.wa.last["body"] == M.FB_ASK
    world.run(text("photo bahut der se padhta hai"))
    assert world.wa.last["body"] == M.FB_THANKS
    assert props(world, "feedback_given") == [{"text": "photo bahut der se padhta hai", "source": "keyword",
                                               "via": "typed"}]
    assert not (shop(world).get("state") or {}).get("await")


def test_keyword_with_words_is_saved_at_once(world):
    onboard(world)
    world.run(text("Feedback: voice note galat samjha"))
    assert world.wa.last["body"] == M.FB_THANKS
    assert props(world, "feedback_given")[0]["text"] == "Feedback: voice note galat samjha"


def test_wanting_to_give_feedback_asks_what(world):
    onboard(world)
    world.run(text("mujhe feedback dena hai"))
    assert world.wa.last["body"] == M.FB_ASK and not props(world, "feedback_given")


def test_a_problem_said_in_a_sentence_without_numbers(world):
    onboard(world)
    world.run(text("photo bhejne mein dikkat aa rahi hai"))
    assert world.wa.last["body"] == M.FB_THANKS


def test_an_entry_that_mentions_a_problem_is_still_an_entry(world):
    onboard(world)
    bill(world)
    world.run(text("dikkat ho gayi, Sharma ko 2000 diye"))
    assert not props(world, "feedback_given") and world.wa.last["kind"] == "buttons"
    assert "2,000" in world.wa.last["body"]


def test_an_entry_while_feedback_is_awaited_is_handled_as_an_entry(world):
    onboard(world)
    bill(world)
    world.run(text("feedback"))
    world.run(text("Sharma ko 2000 diye"))
    assert not props(world, "feedback_given") and "2,000" in world.wa.last["body"]


def test_cancel_closes_the_feedback_question(world):
    onboard(world)
    world.run(text("shikayat"))
    world.run(text("cancel"))
    assert world.wa.last["body"] == M.KEPT and not props(world, "feedback_given")


def test_the_feedback_question_expires_after_15_minutes(world):
    onboard(world)
    world.run(text("feedback"))
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    world.store.update_shop(shop(world)["id"], state={"await": "feedback", "source": "keyword", "at": old})
    world.run(text("aaj dukaan jaldi band hogi"))
    assert not props(world, "feedback_given")


def test_menu_row_starts_feedback(world):
    onboard(world)
    world.run(text("menu"))
    assert world.wa.list_ids()[-1] == "fb:start" and len(world.wa.last["rows"]) == 10
    world.run(pick("fb:start"))
    assert world.wa.last["body"] == M.FB_ASK
    assert props(world, "feedback_started") == [{"source": "button"}]


def test_a_photo_while_feedback_is_awaited_is_read_as_a_bill(world):
    onboard(world)
    world.run(text("feedback"))
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    world.run(photo())
    assert "Sharma Traders" in world.wa.last["body"] and not props(world, "feedback_given")
    assert not (shop(world).get("state") or {}).get("await") == "feedback"


def test_voice_feedback(world):
    onboard(world)
    world.run(text("feedback"))
    world.bot.transcriber = heard("app achha hai par photo thoda der se padhta hai")
    world.run(voice())
    assert world.wa.last["body"] == M.FB_THANKS
    assert props(world, "feedback_given")[0] == {"text": "app achha hai par photo thoda der se padhta hai",
                                                 "source": "keyword", "via": "voice"}
    world.bot.transcriber = heard("shikayat hai ki reminder nahi aaya")
    world.run(voice())
    assert props(world, "feedback_given")[1]["via"] == "voice"


def test_feedback_text_rules():
    assert F.feedback_text("feedback") == "" and F.feedback_text("Ek shikayat hai") == ""
    assert F.feedback_text("sujhav: hindi mein bhi likho") == "sujhav: hindi mein bhi likho"
    assert F.feedback_text("Sharma ko 2000 diye") is None and F.feedback_text("kitna baaki hai") is None
    assert F.feedback_text("problem with 2000 bill") is not None   # starts with the word: the bot checks for an entry
    assert F.mask_numbers("mera number 9876543210 hai, bill 4580") == "mera number [number] hai, bill 4580"


# ---------------------------------------------------------------- 2. the bot offers it
def test_two_cancels_offer_feedback_once_a_day(world):
    world.bot.feedback_prompts = True
    onboard(world)
    cancelled_bill(world)
    assert world.wa.last["body"] == M.BILL_CANCELLED
    cancelled_bill(world)
    assert world.wa.last["body"] == M.FB_OFFER and world.wa.button_ids() == ["fb:start", "fb:no"]
    world.run(tap("fb:start"))
    assert world.wa.last["body"] == M.FB_ASK
    world.run(text("photo galat padha"))
    assert props(world, "feedback_given")[0]["source"] == "button"
    cancelled_bill(world)
    cancelled_bill(world)
    assert world.wa.last["body"] == M.BILL_CANCELLED          # once a day
    assert props(world, "feedback_offered") == [{"why": "cancels"}]


def test_no_offer_when_something_was_saved(world):
    world.bot.feedback_prompts = True
    onboard(world)
    bill(world)
    cancelled_bill(world)
    cancelled_bill(world)
    assert not props(world, "feedback_offered")


def test_frustrated_words_get_an_offer(world):
    world.bot.feedback_prompts = True
    onboard(world)
    world.run(text("ye kaam nahi kar raha"))
    assert world.wa.last["body"] == M.FB_OFFER
    world.run(tap("fb:no"))
    assert world.wa.last["body"] == M.FB_NO_REPLY and "feedback_declined" in world.events()
    world.run(text("bekar hai"))                               # already offered today: the usual menu
    assert world.wa.last["kind"] == "list"


def test_prompts_are_off_unless_switched_on(world):
    onboard(world)
    world.run(text("ye kaam nahi kar raha"))
    assert world.wa.last["kind"] == "list"
    for i in range(5):
        bill(world, amount=1000 + i)
    assert world.wa.last["body"] != M.RATE_ASK and not props(world, "rating_asked")


# ---------------------------------------------------------------- 3. the rating
def test_rating_after_the_fifth_saved_entry(world):
    world.bot.feedback_prompts = True
    onboard(world)
    for i in range(4):
        bill(world, amount=1000 + i)
    assert not props(world, "rating_asked")
    bill(world, amount=2000)
    assert world.wa.last["body"] == M.RATE_ASK
    assert world.wa.button_ids() == ["rate:good:entries5", "rate:ok:entries5", "rate:bad:entries5"]
    world.run(tap("rate:good:entries5"))
    assert world.wa.last["body"] == M.RATE_THANKS_GOOD
    world.run(tap("rate:good:entries5"))                     # a second tap counts once
    assert props(world, "rating_given") == [{"rating": "good", "trigger": "entries5"}]
    bill(world, amount=3000)
    assert props(world, "rating_asked") == [{"trigger": "entries5"}]


def test_rating_dikkat_asks_for_the_feedback(world):
    world.bot.feedback_prompts = True
    onboard(world)
    for i in range(5):
        bill(world, amount=1000 + i)
    world.run(tap("rate:bad:entries5"))
    assert world.wa.last["body"] == M.FB_ASK
    world.run(text("supplier ka naam galat aata hai"))
    assert props(world, "feedback_given")[0]["source"] == "rating"


def test_rating_from_day_ten(world):
    world.bot.feedback_prompts = True
    onboard(world)
    world.store.update_shop(shop(world)["id"], onboarded_at=datetime.now(timezone.utc) - timedelta(days=11))
    bill(world)                                               # the first save also asks the shop's name: not both
    bill(world, amount=1200)
    assert world.wa.last["body"] == M.RATE_ASK and props(world, "rating_asked") == [{"trigger": "day10"}]


# ---------------------------------------------------------------- the health report
def test_health_report_lists_feedback_first():
    now = datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc)
    ev = lambda name, p: {"shop_id": "1", "event": name, "props": p, "created_at": now}   # noqa: E731
    events = [ev("feedback_given", {"text": "photo der se\naata hai, call 9876543210", "source": "keyword",
                                    "via": "typed"}),
              ev("rating_given", {"rating": "good", "trigger": "entries5"}), ev("rating_asked", {"trigger": "entries5"}),
              ev("feedback_offered", {"why": "cancels"}), ev("feedback_started", {"source": "button"})]
    report = health.build([{"id": "1", "shop_code": "F01", "status": "active"}], events, date(2026, 11, 20))
    first = report.index("## Feedback from owners")
    assert first < report.index("## Shops")
    assert '20 Nov 18:30 · F01 · typed · keyword: "photo der se aata hai, call [number]"' in report
    assert "9876543210" not in report
    assert "- Ratings: 1 achha, 0 theek-thaak, 0 dikkat (asked 1)" in report
    assert "offered feedback 1 times (after cancels 1, frustrated words 0); taken 1" in report
