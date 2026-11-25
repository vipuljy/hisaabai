"""24 Nov review as a shopkeeper (docs/04_decisions_log.md, handbook 2.12):
  1. Phrasings that were missed: 'purana bacha 1200', 'Gupta ka 1200 baaki hai'.
  2. A 👍 reaction on a card saves it; other reactions get no reply; a sticker gets 🙏; 'ok' while a choice question
     waits points at its buttons.
  3. The first-day tour (photo, voice, a question), then one tip at a time.
  4. The mistakes report: the words the bot did not get (numbers masked) and feature adoption per shop.
  5. The new welcome: modern, no 'udhaar' or 'munshi'.
Runs on both stores."""

from datetime import date, datetime, timezone

from conftest import OWNER, _events, onboard, photo, tap, text

from bot import admin, health
from bot import messages as M
from bot.photos import LocalPhotos


def events(w, name):
    return [e for e in _events(w.store) if e["event"] == name]


def reaction(emoji, sender=OWNER):
    return {"id": f"wamid.re{emoji.encode().hex()}", "from": sender, "type": "reaction",
            "reaction": {"message_id": "wamid.card", "emoji": emoji}}


# ------------------------------------------------------------------ 1. phrasings
def test_old_dues_said_two_more_ways(world):
    onboard(world)
    for said in ("Gupta ka purana bacha 1200", "Gupta ka 1200 baaki hai"):
        world.run(text(said))
        assert "Purana baaki jodna" in world.wa.last["body"], said
        world.run(text("cancel"))


def test_questions_and_remainders_are_not_old_dues(world):
    onboard(world)
    world.run(text("Gupta ka bill 5000"))
    world.run(text("ok"))
    world.run(text("Gupta ka kitna baaki hai"))
    assert "Purana baaki" not in world.wa.last["body"] and "₹5,000" in world.wa.last["body"]
    world.run(text("Sharma ka bill 8000, 3000 diye, 5000 baaki"))
    assert not any("Purana baaki" in (m.get("body") or "") for m in world.wa.sent[-4:])


# ------------------------------------------------------------------ 2. reactions, stickers, 'ok' under a question
def test_thumbs_up_reaction_saves_the_card(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(reaction("👍🏻"))
    assert len(events(world, "bill_confirmed")) == 1


def test_other_reactions_get_no_reply_and_never_sorry(world):
    onboard(world)
    n = len(world.wa.sent)
    world.run(reaction("❤️"))
    world.run(reaction("👍"))          # no card open: nothing to save, nothing said
    assert len(world.wa.sent) == n
    world.run({"id": "wamid.st1", "from": OWNER, "type": "sticker", "sticker": {"id": "s1"}})
    assert world.wa.last["body"] == M.THANKS_REPLY
    assert not any("Maaf kijiye" in (m.get("body") or "") for m in world.wa.sent)


def test_ok_while_a_choice_question_waits_points_at_the_buttons(world):
    onboard(world)
    world.run(text("Gupta ka bill 5000"))
    world.run(text("ok"))
    world.run(text("Gupta se 500 wapas mile"))      # "Kyun?" with buttons
    assert "Kyun?" in world.wa.last["body"]
    world.run(text("ok"))
    assert world.wa.last["body"] == M.ANSWER_THE_BUTTONS


# ------------------------------------------------------------------ 3. the first-day tour and tips
def test_first_day_tour_photo_voice_question(world):
    world.bot.coach = True
    onboard(world)
    assert world.wa.last["body"] == M.TOUR_1
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    world.run(photo())
    world.run(text("sahi hai"))
    assert world.wa.last["body"] == M.TOUR_2                    # not the shop-name question yet
    world.run(text("Sharma ko 1000 diye"))                      # Sharma has dues: a plain payment card
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TOUR_3
    world.run(text("kitna baaki hai"))
    assert "Practice khatam" in world.wa.last["body"]           # a real photo in the tour: keep or remove?
    assert "Sharma Traders" in world.wa.last["body"]
    world.run(tap("tk:del"))
    bodies = "\n".join(str(m.get("body")) for m in world.wa.sent[-3:])
    assert M.TOUR_DONE in bodies and M.REAL_START in bodies and "2 entry hata di" in bodies
    assert "khaali" in bodies
    assert events(world, "tour_done") and events(world, "shop_name_asked")
    world.run(text("kitna baaki hai"))
    assert "Sharma" not in world.wa.last["body"]                 # the practice entries are gone


def test_demo_practice_entries_clear_themselves(world):
    world.bot.coach = True
    onboard(world)
    world.run(text("Demo ka bill 500"))
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TOUR_2
    world.run(text("Demo ko 200 diye"))
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TOUR_3
    world.run(text("kitna baaki hai"))
    assert "300" in "\n".join(str(m.get("body")) for m in world.wa.sent[-3:])   # he sees 500 - 200 work
    done = "\n".join(str(m.get("body")) for m in world.wa.sent[-3:])
    assert "Practice khatam" not in done and M.REAL_START in done and "2 entry hata di" in done
    assert events(world, "tour_practice_cleared")[0]["props"]["n"] == 2
    world.run(text("kitna baaki hai"))
    assert "Demo" not in world.wa.last["body"]


def test_keep_means_the_tour_entries_stay(world):
    world.bot.coach = True
    onboard(world)
    for t in ("Gupta ka bill 4580", "ok", "Gupta ko 1000 diye", "ok", "kitna baaki hai"):
        world.run(text(t))
    world.run(tap("tk:keep"))
    end = "\n".join(str(m.get("body")) for m in world.wa.sent[-3:])
    assert M.REAL_START in end and "2 entry aapke hisaab mein rakhi" in end and "khaali" not in end
    world.run(text("kitna baaki hai"))
    assert "3,580" in world.wa.last["body"]


def test_ok_under_the_old_dues_question_does_not_save(world):
    onboard(world)
    world.run(text("Gupta ko 1000 diye"))                       # no dues yet: "pehle ka koi baaki tha?"
    assert "pehle ka koi baaki" in world.wa.last["body"]
    world.run(text("ok"))
    assert world.wa.last["body"] == M.ANSWER_THE_BUTTONS and not events(world, "payment_logged")


def test_no_tour_without_coach_and_tips_after_it(world):
    onboard(world)                                               # coach off: the old READY message
    assert world.wa.last["body"] == M.READY
    world.bot.coach = True
    for amount in (100, 200, 300):
        world.run(text(f"Gupta ka bill {amount}"))
        world.run(text("ok"))
    assert any(M.TIPS[0] in (m.get("body") or "") for m in world.wa.sent)   # the 3rd save carries tip 1
    assert len(events(world, "tip_shown")) == 1


# ------------------------------------------------------------------ 3b. owners who skip or wander off the tour
def test_tour_can_be_skipped_and_tips_start_after_it(world):
    world.bot.coach = True
    onboard(world)
    assert M.TOUR_SKIP_HINT in world.wa.last["body"]
    world.run(text("baad mein"))
    assert world.wa.last["body"].startswith(M.TOUR_SKIPPED) and M.REAL_START in world.wa.last["body"]
    assert events(world, "tour_done")[0]["props"]["how"] == "skipped"
    world.run(text("Gupta ka bill 100"))
    world.run(text("ok"))
    assert M.TOUR_2 not in [m.get("body") for m in world.wa.sent]          # the tour does not come back
    assert events(world, "shop_name_asked")                                 # normal life: the shop's name


def test_hi_during_the_tour_shows_the_step_again(world):
    world.bot.coach = True
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("ok"))
    world.run(text("hi"))
    assert world.wa.last["body"] == M.TOUR_REMIND.format(step=M.TOUR_STEP[2])
    assert events(world, "tour_reminded")


def test_question_first_then_saves_finishes_the_tour(world):
    world.bot.coach = True
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TOUR_2
    world.run(text("kitna baaki hai"))                                      # step 3 before step 2
    world.run(text("Gupta ko 1000 diye"))
    world.run(text("ok"))
    assert events(world, "tour_done")[0]["props"]["how"] == "any_order"     # no step 3: he already asked
    world.run(tap("tk:keep"))
    assert any(M.TOUR_DONE in str(m.get("body")) for m in world.wa.sent[-3:])


def test_only_saves_never_leave_the_tour_hanging(world):
    world.bot.coach = True
    onboard(world)
    for amount in (100, 200, 300):
        world.run(text(f"Gupta ka bill {amount}"))
        world.run(text("ok"))
    assert events(world, "tour_done")[0]["props"]["how"] == "saved_instead"
    world.run(tap("tk:del"))
    assert any(M.TOUR_DONE_NO_QUESTION in str(m.get("body")) for m in world.wa.sent[-3:])
    assert events(world, "shop_name_asked")


def test_chhodo_cancels_a_card_not_the_tour(world):
    world.bot.coach = True
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("chhodo"))                                               # a card is open: cancel the card
    assert not events(world, "tour_done")


def test_sikhao_shows_every_feature_any_time(world):
    onboard(world)
    world.run(text("Sikhao"))
    assert world.wa.last["body"] == M.GUIDE and events(world, "guide_viewed")
    world.run(text("kaise use kare?"))
    assert world.wa.last["body"] == M.GUIDE


def test_health_report_lists_tour_outcomes():
    now = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
    ev = lambda sid, name, p=None: {"shop_id": sid, "event": name, "props": p or {}, "created_at": now}   # noqa: E731
    win = [ev(s, "tour_started", {"v": 1}) for s in "123"] + [ev("1", "bill_received", {"source": "photo"}),
           ev("1", "tour_done", {"v": 1, "how": "finished"}), ev("2", "tour_done", {"v": 1, "how": "skipped", "step": 1})]
    out = "\n".join(health.adoption_lines(win, {"1": "S01", "2": "S02", "3": "S03"}))
    assert "3 started, 1 finished, 1 skipped (at step 1)" in out and "nudge them yourself: S03" in out


# ------------------------------------------------------------------ 4. mistakes report and adoption
def test_missed_words_are_kept_masked_and_reported(world, tmp_path):
    onboard(world)
    world.run(text("gupta ka hisaab 9876543210 pe bhejo"))     # secret-scan: ignore (a fake number)
    world.run(text("bhai ye kya scene hai"))
    missed = [e["props"]["text"] for e in events(world, "off_topic") if e["props"].get("text")]
    assert missed and all("9876543210" not in t for t in missed)  # secret-scan: ignore
    today = datetime.now(health.IST).date()
    report = health.build(world.store.list_shops(), world.store.events_since(health.window_start(today, 7)), today)
    assert "## What owners sent that the bot did not get" in report and "bhai ye kya scene hai" in report
    assert "## Feature adoption" in report or "Feature adoption" not in report   # only with shop activity rows
    lines = []
    admin.main(["mistakes", "7"], store=world.store, out=lines.append, photos=LocalPhotos(tmp_path))
    out = "\n".join(lines)
    assert "bhai ye kya scene hai" in out and "Mistakes and feature adoption" in out


def test_adoption_table_counts_features_per_shop():
    now = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
    ev = lambda sid, name, p=None: {"shop_id": sid, "event": name, "props": p or {}, "created_at": now}   # noqa: E731
    win = [ev("1", "bill_received", {"source": "photo"}), ev("1", "voice_received"), ev("2", "question_answered"),
           ev("2", "bill_confirmed", {"kind": "udhaar"}), ev("1", "tour_done")]
    out = "\n".join(health.adoption_lines(win, {"1": "S01", "2": "S02"}))
    assert "| S01 | ✓ |  | ✓ |" in out and "| **Shops** | 1 | 0 | 1 | 0 | 1 |" in out


def test_first_reply_after_a_sleep_is_its_own_line():
    line = health.speed_line([{"ms": 12000, "type": "text", "first": True}, {"ms": 800, "type": "text"}])
    assert "First reply after a restart or a sleep: median 12.0 s over 1" in line


# ------------------------------------------------------------------ 5. welcome
def test_welcome_is_modern():
    w = (M.WELCOME_HEADER + M.WELCOME).lower()
    assert "udhaar" not in w and "munshi" not in w and "smart hisaab" in w
    assert len(M.WELCOME) <= 1024 and "photo" in w and "bol dijiye" in w and "likh dijiye" in w


# ------------------------------------------------------------------ 6. family and friends are counted apart
def test_family_shops_stay_out_of_the_split_and_the_headline(store, tmp_path):
    lines = []
    run = lambda *a: admin.main(list(a), store=store, out=lines.append, photos=LocalPhotos(tmp_path))  # noqa: E731
    run("add-shop", "S01", "kirana", "9876543210")              # secret-scan: ignore (fake numbers)
    run("add-shop", "S02", "medical", "9876543211")             # secret-scan: ignore
    run("add-shop", "F01", "kirana", "9876543212")              # secret-scan: ignore
    run("assign-groups")
    by_code = {s["shop_code"]: s.get("experiment_group") for s in store.list_shops()}
    assert by_code["F01"] is None and {by_code["S01"], by_code["S02"]} == {"treatment", "control"}
    line = health.cohort_line(store.list_shops(), set())
    assert "Real pilot shops (S codes): 2" in line and "friendly users (F family / T test): 1" in line


# ------------------------------------------------------------------ 7. open sign-up (25 Nov)
def test_anyone_who_says_hi_can_start_with_a_w_code(world):
    stranger = "919000000077"   # secret-scan: ignore (a fake number)
    world.run(text("hi", stranger))
    shop = world.store.get_shop_by_wa(stranger)
    assert shop["shop_code"] == "W01" and world.wa.last["body"] != M.NOT_IN_PILOT
    assert any(e["event"] == "self_signup" for e in events(world, "self_signup"))
    world.run(text("hello", "919000000078"))                                  # secret-scan: ignore
    assert world.store.get_shop_by_wa("919000000078")["shop_code"] == "W02"  # secret-scan: ignore
    line = health.cohort_line(world.store.list_shops(), set())
    assert "self-signups (W, joined by saying hi): 2" in line


def test_closed_pilot_still_turns_strangers_away(store):
    from conftest import FakeWA
    from bot.conversation import Bot
    import asyncio
    wa = FakeWA()
    bot = Bot(store, wa, None, pilot_open=False)
    asyncio.run(bot.handle(text("hi", "919000000079")))                       # secret-scan: ignore
    assert wa.last["body"] == M.NOT_IN_PILOT and store.get_shop_by_wa("919000000079") is None  # secret-scan: ignore


def test_demo_and_a_real_entry_together(world):
    world.bot.coach = True
    onboard(world)
    for t in ("Demo ka bill 500", "ok", "Gupta se 440 ka maal aaya", "ok", "kitna baaki hai"):
        world.run(text(t))
    ask = world.wa.last["body"]
    assert "Demo* wali 1 entry hata di" in ask and "Gupta: ₹440" in ask and "Demo:" not in ask
    world.run(tap("tk:keep"))
    end = "\n".join(str(m.get("body")) for m in world.wa.sent[-3:])
    assert "Practice ki 1 entry hata di" in end and "1 entry aapke hisaab mein rakhi" in end and "khaali" not in end
    world.run(text("kitna baaki hai"))
    assert "440" in world.wa.last["body"] and "Demo" not in world.wa.last["body"]


# ------------------------------------------------------------------ 8. cancel (25 Nov)
def test_typed_cancel_closes_the_open_card(world):
    onboard(world)
    world.run(text("Gupta ka bill 4580"))
    world.run(text("cancel"))
    assert world.wa.last["body"] == M.BILL_CANCELLED                       # not "no such entry in 7 days"
    assert events(world, "bill_cancelled")
    world.run(text("kitna baaki hai"))
    assert "4,580" not in world.wa.last["body"]


def test_cancel_in_the_tour_shows_the_step_again_and_never_quits_it(world):
    world.bot.coach = True
    onboard(world)
    world.run(text("Demo ka bill 500"))
    world.run(text("cancel"))
    assert world.wa.last["body"] == M.TOUR_AFTER_CANCEL.format(step=M.TOUR_STEP[1])
    world.run(text("cancel"))                                                # nothing open: still in the tour
    assert not events(world, "tour_done")
    assert world.wa.last["body"] == M.TOUR_REMIND.format(step=M.TOUR_STEP[1])
    world.run(text("Demo ka bill 500"))
    world.run(text("ok"))
    assert world.wa.last["body"] == M.TOUR_2
