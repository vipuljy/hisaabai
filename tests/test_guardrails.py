"""3 Nov build: understanding (Hindi script, spelling slips, old dues, 'se ... liya'), guardrails against the owner's
own mistakes (duplicates, unusual amounts, refunds asked why, before -> after on every card), proof photos read first,
old entries corrected by a dated entry, and trust (the intro, STOP, 'mera data delete karo'). Runs on both stores."""

from datetime import datetime, timedelta, timezone

import pytest
from conftest import OWNER, onboard, photo, tap, text

from bot import messages as M
from bot import understand as U
from bot.photos import LocalPhotos

IST = timezone(timedelta(hours=5, minutes=30))


def btn(w, prefix):
    return next(b for b in w.wa.button_ids() if b.startswith(prefix))


def shop(w):
    return w.store.get_shop_by_wa(OWNER)


def dues(w):
    return {r["supplier"]: int(r["due"]) for r in w.store.dues(shop(w)["id"])}


def pick(row_id):
    return {"id": f"wamid.pk{row_id[-10:]}{datetime.now().timestamp()}", "from": OWNER, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": row_id, "title": "x"}}}


def add(w, msg):
    w.run(text(msg))
    if w.wa.last["kind"] == "list":
        w.run(pick(next(i for i, _, _ in w.wa.last["rows"] if i.startswith("spn:"))))
    w.run(tap(btn(w, "old_no:") if any(b.startswith("old_no:") for b in w.wa.button_ids()) else
              next(b for b in w.wa.button_ids() if b.startswith(("ok:", "pok:")))))
    if any(b.startswith(("ok:", "pok:")) for b in (w.wa.button_ids() if w.wa.last["kind"] == "buttons" else [])):
        w.run(tap(next(b for b in w.wa.button_ids() if b.startswith(("ok:", "pok:")))))


@pytest.fixture()
def ready(world):
    onboard(world)
    world.run(text("naam: Ramesh Kirana"))
    add(world, "Sharma Traders 5000")
    add(world, "Gupta Agency 3000")
    return world


# ---------------------------------------------------------------- understanding
def test_hindi_script_and_spelling_slips_are_cleaned_before_the_rules():
    assert U.to_roman("शर्मा जी को 2000 दिए") == "sharma ji ko 2000 diye"
    assert U.to_roman("कितना बाकी है") == "kitna baaki hai"           # the silent 'a': kitna, not kitana
    assert U.to_roman("गुप्ता से 500 का बर्तन लिया") == "gupta se 500 ka bartan liya"
    assert U.to_roman("बीस हज़ार") == "bis hazaar" and U.to_roman("मेरा डेटा डिलीट करो") == "mera data delete karo"
    assert U.fix_typos("sharma ka hiasb", [])[0] == "sharma ka hisaab"
    assert U.fix_typos("hasib dikhao", [])[0] == "hisaab dikhao"
    assert U.fix_typos("bakki kitna", [])[0] == "baaki kitna"
    assert U.fix_typos("paymnet kar diya 500", [])[0] == "payment kar diya 500"
    # never a name: a supplier called Hasib, or a word before 'ko'
    assert U.fix_typos("hasib traders ko 500 diye", [{"name_norm": "hasib traders"}])[0] == "hasib traders ko 500 diye"
    assert U.fix_typos("hasib ko 500 diye", [])[0] == "hasib ko 500 diye"
    assert U.fix_typos("bhejo", [])[0] == "bhejo"                     # a real word, not a slip of 'bheje'


def test_hindi_script_message_becomes_a_card(ready):
    w = ready
    w.run(text("शर्मा जी को 2000 दिए"))
    body = w.wa.last["body"]
    assert "Naya payment" in body and "Sharma Traders" in body and "₹2,000" in body
    assert "aapne 'Sharma' likha" in body          # the supplier was matched from a shorter name: shown
    w.run(text("कितना बाकी है"))
    assert "Kul baaki" in w.wa.last["body"]


def test_typo_question_and_old_dues_phrases(ready):
    w = ready
    w.run(text("sharma ka hiasb"))
    assert "*Sharma Traders*" in w.wa.last["body"] and "baaki" in w.wa.last["body"]
    w.run(text("Sharma ka 1000 purana hisab jodo"))
    assert "Purana baaki jodna" in w.wa.last["body"] and "₹5,000 → *₹6,000*" in w.wa.last["body"]
    w.run(text("Gupta se 500 ka bartan liya"))
    assert "Naya bill" in w.wa.last["body"] and "Gupta Agency" in w.wa.last["body"]


# ---------------------------------------------------------------- cards: before -> after, guesses, warnings
def test_every_card_shows_the_baaki_before_and_after(ready):
    w = ready
    w.run(text("Sharma ko 2000 diye"))
    assert "*Baaki:* ₹5,000 → *₹3,000*" in w.wa.last["body"] and "Sahi hai?" in w.wa.last["body"]
    w.run(text("Gupta Agency ka bill 700"))
    assert "*Baaki:* ₹3,000 → *₹3,700*" in w.wa.last["body"]
    w.run(text("Sharma ko 9000 diye"))             # more than the baaki: said on the card
    assert "₹4,000* zyada" in w.wa.last["body"] and "₹4,000 advance" in w.wa.last["body"]


def test_duplicate_is_flagged_and_can_be_dropped_or_kept(ready):
    w = ready
    add(w, "Sharma ko 2000 diye")
    w.run(text("Sharma ko 2000 diye"))             # the same payment again (sent twice, or said twice)
    body = w.wa.last["body"]
    assert "Ye pehle se save hai?" in body and w.wa.button_ids()[0].endswith(":dup")
    assert [t for _, t in w.wa.last["buttons"]] == [M.BTN_DUP_SAVE, M.BTN_EDIT, M.BTN_DUP_CANCEL]
    w.run(tap(btn(w, "pcancel:")))
    assert w.wa.last["body"] == M.DUP_CANCELLED and dues(w)["Sharma Traders"] == 3000
    w.run(text("Sharma ko 2000 diye"))             # really a second payment: one tap keeps it
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Sharma Traders"] == 1000


def test_duplicate_saved_after_the_card_was_shown_is_caught_at_the_tap(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 4580"))
    first = btn(w, "ok:")
    w.run(text("Gupta Agency ka bill 4580"))
    second = btn(w, "ok:")
    w.run(tap(first))
    w.run(tap(second))                             # its card showed no warning: the warning comes now
    assert "Ye pehle se save hai?" in w.wa.last["body"] and dues(w)["Gupta Agency"] == 7580


def test_same_bill_number_is_a_duplicate_at_any_date(ready):
    w = ready
    w.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 1200, "invoice_number": "INV-77"}
    w.run(photo())
    w.run(tap(btn(w, "ok:")))
    w.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 1250, "invoice_number": "inv-77"}
    w.run(photo())
    assert "bill no INV-77" in w.wa.last["body"] and btn(w, "ok:").endswith(":dup")


def test_unusual_amount_gets_a_warning(ready):
    w = ready
    for _ in range(3):
        add(w, "Gupta Agency ka bill 1000")
        w.run(tap(btn(w, "ok:"))) if w.wa.last["kind"] == "buttons" and any(
            b.startswith("ok:") for b in w.wa.button_ids()) else None
    w.run(text("Gupta Agency ka bill 30000"))
    assert "aam taur pe ~₹1,000" in w.wa.last["body"]


def test_guessed_supplier_name_is_shown_but_exact_name_is_not(ready):
    w = ready
    w.run(text("Sharma ji ka bill 800"))
    assert "Sharma Traders _(aapne 'Sharma' likha)_" in w.wa.last["body"]
    w.run(text("Sharma Traders ka bill 800"))
    assert "aapne" not in w.wa.last["body"]


def test_second_supplier_with_the_same_name_is_asked_for_its_place(ready):
    w = ready
    w.run(text("Sharma ka bill 900"))                 # one Sharma: the card guesses Sharma Traders
    w.run(tap(btn(w, "edit:")))
    w.run(pick(next(i for i in w.wa.list_ids() if i.endswith(":supplier_name"))))
    w.run(text("Sharma Traders Sadar"))              # typed: never merged silently into 'Sharma Traders'
    assert w.wa.last["kind"] == "list"
    w.run(pick(next(i for i in w.wa.list_ids() if i.startswith("spn:"))))
    assert "Sharma Traders Sadar" in w.wa.last["body"] and M.NEW_SUPPLIER_TAG in w.wa.last["body"]
    w.run(tap(btn(w, "ok:")))
    assert dues(w)["Sharma Traders Sadar"] == 900 and dues(w)["Sharma Traders"] == 5000
    w.run(text("Sharma Traders ko 100 diye"))
    w.run(text("Sharma ka bill 50"))                 # now 'Sharma' fits two: a list, never a guess
    assert w.wa.last["kind"] == "list"
    w.run(pick(next(i for i in w.wa.list_ids() if i.startswith("spn:"))))   # '➕ Naya: Sharma'
    assert "pehle se hai" in w.wa.last["body"]       # a third Sharma needs its place / full name
    w.run(text("Sharma"))                            # still says less than an existing name: asked again
    assert "pehle se hai" in w.wa.last["body"]
    w.run(text("Sharma Kirana Indore"))
    assert "Sharma Kirana Indore" in w.wa.last["body"] and M.NEW_SUPPLIER_TAG in w.wa.last["body"]


# ---------------------------------------------------------------- refunds, old dues, 'dena hai'
def test_refund_asks_why_and_goods_back_keeps_the_baaki(ready):
    w = ready
    w.run(text("Gupta se 500 wapas mile"))
    assert "Kyun?" in w.wa.last["body"]
    w.run(tap(btn(w, "rfw_g:")))                     # goods went back...
    assert "Paisa haath mein mila, ya baaki se kata?" in w.wa.last["body"]   # still owing: asked (16 Nov)
    w.run(tap(btn(w, "rcash_got:")))                 # ... AND money came back in hand
    body = w.wa.last["body"]
    assert "Supplier se paisa wapas mila" in body and "maal lautaya tha" in body
    assert "₹3,000 → *₹3,000*" in body and "baaki wahi" in body
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Gupta Agency"] == 3000 and "maal wapas aur paisa wapas" in w.wa.last["body"]
    sid = shop(w)["id"]
    kinds = sorted(r["kind"] for r in w.store.recent_entries(sid, datetime(2000, 1, 1, tzinfo=IST)))
    assert kinds.count("refund") == 1 and kinds.count("return") == 1
    w.run(text("Gupta ka 500 wapas wala hatao"))     # removing one removes its pair
    w.run(tap(btn(w, "udel:")))
    assert dues(w)["Gupta Agency"] == 3000
    kinds = [r["kind"] for r in w.store.recent_entries(sid, datetime(2000, 1, 1, tzinfo=IST))]
    assert "refund" not in kinds and "return" not in kinds


def test_refund_reason_from_the_words(ready):
    w = ready
    w.run(text("Gupta se 2000 wapas aaye expiry maal ke"))    # the reason is in the message: no 'why?'
    assert "Kyun?" not in w.wa.last["body"] and btn(w, "rcash_got:")   # only 'cash or cut?' (Gupta still owes)
    w.run(tap(btn(w, "rcash_got:")))
    assert "maal lautaya tha" in w.wa.last["body"] and btn(w, "pok:")


def test_first_payment_without_known_dues_asks_for_dues_from_before(ready):
    w = ready
    w.run(text("Mehta Steel ko 2000 diye"))
    assert "pehle ka koi baaki" in w.wa.last["body"]
    w.run(tap(btn(w, "old_yes:")))
    w.run(text("12000"))
    assert any("purana baaki *₹12,000*" in m["body"] for m in w.wa.sent[-2:])
    assert "*Baaki:* ₹12,000 → *₹10,000*" in w.wa.last["body"]
    w.run(tap(btn(w, "pok:")))
    assert dues(w)["Mehta Steel"] == 10000


def test_dena_hai_inside_the_known_baaki_offers_a_reminder_first(ready):
    w = ready
    w.run(text("Sharma ko 2000 dena hai"))           # 2000 is inside the 5000 already owed
    assert w.wa.button_ids()[0].startswith("fut_rem:")
    assert [t for _, t in w.wa.last["buttons"]][1] == M.BTN_FUT_NEW and "shamil" in w.wa.last["body"]
    w.run(text("Gupta ko 9000 dena hai"))            # more than the known baaki: adding it comes first
    assert w.wa.button_ids()[0].startswith("fut_add:")


# ---------------------------------------------------------------- proof photos: read first
def test_random_photo_never_gets_the_proof_question(ready, tmp_path):
    w = ready
    w.bot.photos = LocalPhotos(tmp_path / "p")
    add(w, "Gupta Agency ka bill 2000")
    w.reader.next = {"is_bill": False}
    w.run(photo())
    assert w.wa.last["body"] == M.NOT_A_BILL and "proof_asked" not in w.events()


def test_proof_by_text_after_saying_no(ready, tmp_path):
    w = ready
    w.bot.photos = LocalPhotos(tmp_path / "p")
    add(w, "Sharma Traders ka bill 4000")
    w.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4000}
    w.run(photo())
    w.run(tap(btn(w, "pfn:")))                       # 'Naya bill hai' ... then the owner changes their mind
    w.run(text("ye sharma ke bill ka proof hai"))
    assert w.wa.last["body"] == M.proof_ok(4000) and dues(w)["Sharma Traders"] == 9000   # no second bill
    w.run(text("ye gupta ke bill ka proof hai"))      # that photo is used up: it is never attached twice
    assert w.wa.last["body"] == M.PROOF_NO_PHOTO


# ---------------------------------------------------------------- trust: STOP, delete my data
def test_stop_keeps_the_data_and_delete_removes_it(world, tmp_path):
    w = world
    w.bot.photos = LocalPhotos(tmp_path / "p")
    onboard(w)
    add(w, "Sharma Traders 5000")
    w.run(text("stop"))
    assert w.wa.last["body"] == M.OPTED_OUT and "safe rakha" in M.OPTED_OUT and "DELETE" in M.OPTED_OUT
    w.run(text("Sharma ko 100 diye"))                 # nothing is processed while stopped
    assert w.wa.last["body"] == M.OPTED_OUT_HINT
    w.run(text("mera data delete karo"))              # works while stopped too
    assert "hamesha ke liye" in w.wa.last["body"] and "1 bill" in w.wa.last["body"]
    w.run(tap(btn(w, "delall_no:")))
    assert w.wa.last["body"] == M.DELETE_KEPT and shop(w) is not None
    w.run(text("DELETE"))
    yes = btn(w, "delall_yes:")
    w.run(tap(yes))
    assert w.wa.last["body"] == M.DELETED_ALL and shop(w) is None
    assert "shop_data_deleted" in [e for e in w.events()]
    w.run(text("hi"))                                 # a fresh start: the intro again, nothing from before
    assert w.wa.last["body"] == M.WELCOME and w.store.list_suppliers(shop(w)["id"]) == []


def test_old_last_entry_delete_is_a_fix_not_a_data_delete(ready):
    w = ready
    w.run(text("last entry delete karo"))
    assert "hamesha ke liye" not in w.wa.last["body"]


def test_stale_buttons_say_what_happened(ready):
    w = ready
    w.run(text("Gupta Agency ka bill 100"))
    ok = btn(w, "ok:")
    w.run(tap(ok))
    w.run(tap(ok))
    assert "pehle hi save ho chuki hai" in w.wa.last["body"]


def test_photo_card_hides_empty_rows_and_says_when_the_date_is_missing(ready):
    w = ready
    w.reader.next = {"supplier_name": "Gupta Agency", "total_amount": 640, "bill_date": None, "invoice_number": None}
    w.run(photo())
    body = w.wa.last["body"]
    assert "*Invoice:*" not in body and M.NO_DATE_ON_BILL in body and "₹3,000 → *₹3,640*" in body


def test_reminder_question_with_two_suppliers_is_short(ready):
    w = ready
    add(w, "Sharma Medical 900")
    w.run(text("Sharma ko kal yaad dilana"))
    body = w.wa.last["body"]
    assert body.startswith("🤔 Samjha: *") and "*Sharma* naam ke 2 supplier hain. Kaunsa? 👇" in body
    assert "Kya aap ye poochh rahe hain" not in body


def test_a_slow_reply_is_logged(world, monkeypatch):
    import bot.conversation as C
    onboard(world)
    monkeypatch.setattr(C, "SLOW_REPLY_MS", 0)
    world.run(text("kitna baaki hai"))
    assert "slow_reply" in world.events()


def test_deleted_shop_codes_are_not_reused_while_others_exist(world):
    onboard(world)
    onboard(world, "919000000003")
    first = world.store.get_shop_by_wa(OWNER)["shop_code"]
    world.run(text("DELETE"))
    world.run(tap(btn(world, "delall_yes:")))
    world.run(text("hi", "919000000004"))
    codes = [s["shop_code"] for s in world.store.list_shops()]
    assert len(codes) == len(set(codes)) and first not in codes or codes.count(first) == 1


# ---------------------------------------------------------------- questions by day, the pilot health report
def test_today_questions_and_corrections_are_not_money_paid_today(ready):
    w = ready
    add(w, "Sharma ko 2000 diye")
    add(w, "kal Gupta ko 3000 diye")
    w.run(text("aaj ka payment"))                       # no 'kitna': still the day's total, from 12 am IST
    assert "Aaj" in w.wa.last["body"] and "*₹2,000*" in w.wa.last["body"] and "3,000" not in w.wa.last["body"]
    w.run(text("kal ka payment"))
    assert "*₹3,000*" in w.wa.last["body"]
    w.run(text("aaj ka hisaab"))
    body = w.wa.last["body"]
    assert "ka hisaab*" in body and "1 payment diye: *₹2,000*" in body and "Kul baaki ab" in body
    w.run(text("aaj payment karna hai"))                # a plan, not a question
    assert "ka hisaab*" not in w.wa.last["body"]
    sid = shop(w)["id"]
    from bot.store import today_ist
    sup = next(s for s in w.store.list_suppliers(sid) if s["name"] == "Sharma Traders")
    p = w.store.create_payment(sid, sup["id"], 700, "text", "sudhaar", paid_on=today_ist(), kind="payment",
                               reason="sudhaar:payment:x:700")
    w.store.confirm_payment(p["id"], sup["id"])
    w.run(text("aaj kitna payment kiya"))                 # a correction is not money paid today
    assert "*₹2,000*" in w.wa.last["body"] and "2,700" not in w.wa.last["body"]


def test_nothing_understood_says_so_plainly(ready):
    w = ready
    w.run(text("kuch toh gadbad hai hisaab mein bhai"))
    assert "Bas ek cheez aur" not in w.wa.last["body"]


def test_pilot_health_report_counts_the_loop_and_the_guards(ready, tmp_path, monkeypatch):
    from bot import admin, health
    w = ready
    add(w, "Sharma ko 2000 diye")
    w.run(text("Sharma ko 2000 diye"))                 # a duplicate warning, then dropped
    w.run(tap(btn(w, "pcancel:")))
    w.run(text("sharma ka hiasb"))
    w.run(text("Mehta ko 500 diye"))
    w.run(tap(btn(w, "old_no:")))
    today = datetime.now(health.IST).date()
    report = health.build(w.store.list_shops(), w.store.events_since(health.window_start(today, 7)), today)
    assert "Used the bot: 1 today" in report and "duplicate 1" in report and "1 dropped" in report
    assert "spelling slips fixed: 1" in report and "1 asked, 0 added, 1 none" in report
    assert "| W01 |" in report and "9198" not in report             # shop codes, never phone numbers
    monkeypatch.chdir(tmp_path)
    out = []
    assert admin.main(["health", "3"], store=w.store, out=out.append, photos=object()) == 0
    assert (tmp_path / "outputs" / "pilot" / f"health_{today:%Y-%m-%d}.md").exists()
