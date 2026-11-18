"""Read policies (bot/policy.py): Haiku first, free checks, Sonnet when a check fails. No network: fake readers."""

from datetime import date

import pytest
from conftest import onboard, photo, tap

from bot import messages as M
from bot import policy as P
from bot.extract import FIELDS, Extraction

TODAY = date(2026, 10, 28)
GOOD = {"is_bill": True, "supplier_name": "shiv medical distributors", "bill_date": "2026-10-05",
        "invoice_number": "D5721", "total_amount": 4769, "due_date": None, "unsure_fields": []}
OCR_TEXT = "SHIV MEDICAL\nRs. Four Thousand Seven Hundred and Sixty Nine only GRAND TOTAL 4769.00"


def ocr(total=4769, text=OCR_TEXT):
    return {"fields": {"total_amount": total}, "raw_text": text}


# ---------------------------------------------------------------- the checks
def test_clean_answer_passes_every_check():
    c = P.haiku_checks(GOOD, ocr(), TODAY)
    assert c["ocr_agree"] is True and c["proof"] == "pass" and c["sanity"] == "pass" and not c["unsure"]
    assert P.reasons_to_escalate("P3", c) == []


@pytest.mark.parametrize("fields, ocr_rec, reason", [
    (GOOD | {"total_amount": 4796}, ocr(), "ocr_disagrees"),                       # a) digits swapped
    (GOOD | {"total_amount": 4796}, ocr(total=None), "proof_fails"),               # b) words say 4769
    (GOOD | {"unsure_fields": ["bill_date"]}, ocr(), "haiku_unsure"),              # c)
    (GOOD | {"bill_date": "2027-01-05"}, ocr(), "sanity_fails"),                   # d) date in the future
    (GOOD | {"total_amount": None}, None, "sanity_fails"),                         # d) no total
    (GOOD | {"invoice_number": "07ABCDE1234F1Z2"}, ocr(), "sanity_fails"),         # d) a GSTIN
])
def test_each_check_escalates(fields, ocr_rec, reason):
    assert reason in P.reasons_to_escalate("P2", P.haiku_checks(fields, ocr_rec, TODAY))


def test_p1_does_not_use_the_proof_check():
    c = P.haiku_checks(GOOD | {"total_amount": 4796}, ocr(total=None), TODAY)
    assert P.reasons_to_escalate("P1", c) == [] and P.reasons_to_escalate("P2", c) == ["proof_fails"]


def test_without_ocr_only_unsure_and_sanity_count():
    c = P.haiku_checks(GOOD, None, TODAY)
    assert c["ocr_agree"] is None and c["proof"] == "na" and P.reasons_to_escalate("P3", c) == []


def test_p3_asks_the_owner_when_sonnet_and_haiku_still_disagree():
    fields, asked = P.final_fields("P3", GOOD, GOOD | {"total_amount": 4796, "bill_date": "2026-10-06"})
    assert asked == ["total_amount", "bill_date"] and {"total_amount", "bill_date"} <= set(fields["unsure_fields"])
    fields, asked = P.final_fields("P2", GOOD, GOOD | {"total_amount": 4796})
    assert asked == [] and fields["total_amount"] == 4796  # P2 trusts Sonnet
    same, asked = P.final_fields("P3", GOOD, dict(GOOD))
    assert asked == [] and same["unsure_fields"] == []


# ---------------------------------------------------------------- the live reader
class Reader:
    def __init__(self, model, fields, boom=False):
        self.model, self.fields, self.boom, self.calls = model, fields, boom, 0

    def __call__(self, image_bytes):
        self.calls += 1
        if self.boom:
            raise RuntimeError("API down")
        return Extraction(fields=dict(self.fields), raw_text="{}", model=self.model, prompt_version="v2",
                          input_tokens=2800, output_tokens=400, latency_ms=3000)


def reader(haiku_fields, sonnet_fields=GOOD, ocr_rec=None, policy="P3", sonnet_boom=False):
    h, s = Reader("claude-haiku-5-5", haiku_fields), Reader("claude-sonnet-5-5", sonnet_fields, sonnet_boom)
    return P.PolicyReader(policy, haiku=h, sonnet=s, ocr=(lambda img: ocr_rec), today_fn=lambda: TODAY), h, s


def test_reader_trusts_haiku_when_checks_pass():
    r, h, s = reader(GOOD, ocr_rec=ocr())
    ex = r.read(b"img")
    assert s.calls == 0 and ex.model == "claude-haiku-5-5" and ex.extra["calls"] == ["claude-haiku-5-5"]


def test_reader_escalates_and_flags_disagreement():
    r, h, s = reader(GOOD | {"total_amount": 4796}, sonnet_fields=GOOD | {"total_amount": 4769}, ocr_rec=ocr())
    ex = r.read(b"img")
    assert s.calls == 1 and ex.extra["calls"] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert ex.fields["total_amount"] == 4769 and ex.extra["asked"] == ["total_amount"]
    assert "total_amount" in ex.fields["unsure_fields"]
    assert ex.input_tokens == 5600  # both calls counted for cost


def test_reader_at_the_ai_cap_flags_instead_of_calling_sonnet():
    r, h, s = reader(GOOD | {"unsure_fields": ["bill_date"]})
    ex = r.read(b"img", allow_escalation=False)
    assert s.calls == 0 and {"total_amount", "bill_date"} <= set(ex.fields["unsure_fields"])
    assert ex.extra["not_escalated"] == "ai_cap"


def test_reader_survives_sonnet_failure_and_ocr_failure():
    def broken_ocr(img):
        raise RuntimeError("onnx")
    h, s = Reader("claude-haiku-5-5", GOOD | {"unsure_fields": ["total_amount"]}), Reader("s", GOOD, boom=True)
    r = P.PolicyReader("P3", haiku=h, sonnet=s, ocr=broken_ocr, today_fn=lambda: TODAY)
    ex = r.read(b"img")
    assert ex.extra["sonnet_error"] == "RuntimeError" and {"total_amount", "bill_date"} <= set(ex.fields["unsure_fields"])


def test_haiku_policy_is_one_read_and_unknown_policy_is_refused():
    r, h, s = reader(GOOD | {"unsure_fields": ["total_amount"]}, policy="haiku")
    r.read(b"img")
    assert s.calls == 0
    with pytest.raises(ValueError):
        P.PolicyReader("P9", haiku=h)


# ---------------------------------------------------------------- the bot with P3
def test_bot_photo_flow_with_p3_logs_both_ai_calls_and_keeps_buttons(world):
    r, h, s = reader(GOOD | {"supplier_name": "Shiv Medical", "total_amount": 4796},
                     sonnet_fields=GOOD | {"supplier_name": "Shiv Medical", "total_amount": 4769}, ocr_rec=ocr())
    world.bot.extractor = r
    onboard(world)
    world.run(photo())
    last = world.wa.last
    assert last["kind"] == "buttons" and M.BTN_OK in [t for _, t in last["buttons"]]
    assert "₹4,769 ⚠️" in last["body"]  # the two readers disagreed: the owner checks the total
    assert world.events().count("ai_call") == 2
    if hasattr(world.store, "events"):
        rec = next(e for e in world.store.events if e["event"] == "bill_received")["props"]
        assert rec["policy"] == "P3" and rec["escalated"] and rec["asked"] == ["total_amount"]
    world.run(tap(world.wa.button_ids()[0]))
    assert "₹4,769" in world.wa.sent[-2]["body"]  # then the one-time shop-name question


def test_bot_does_not_escalate_at_the_daily_cap(world):
    from bot.conversation import Limits
    r, h, s = reader(GOOD | {"supplier_name": "Shiv Medical", "unsure_fields": ["bill_date"]})
    world.bot.extractor = r
    world.bot.limits = Limits(per_shop_per_day=1)
    onboard(world)
    world.run(photo())
    assert s.calls == 0 and world.events().count("ai_call") == 1


# ---------------------------------------------------------------- P5 "money first" (default since 28 Oct 2026)
def test_p5_no_ocr_is_the_default():
    assert P.DEFAULT_POLICY == "P5_no_ocr" and {"P5", "P5_no_ocr", "P5_trusted_ocr"} <= set(P.POLICIES)


def test_date_age_rule():
    assert P.date_is_old("2025-05-09", TODAY)          # B134: the 2025 misread is > 6 months old
    assert P.date_is_old("2026-04-16", TODAY)          # 195 days
    assert not P.date_is_old("2026-10-01", TODAY) and not P.date_is_old(None, TODAY)
    assert not P.date_is_old("not a date", TODAY)


@pytest.mark.parametrize("fields, ocr_rec, reason", [
    (GOOD | {"total_amount": 4796}, ocr(), "ocr_disagrees"),
    (GOOD | {"total_amount": 4796}, ocr(total=None), "proof_fails"),
    (GOOD | {"unsure_fields": ["total_amount"]}, ocr(), "haiku_unsure_total"),
    (GOOD | {"total_amount": None}, None, "total_sanity_fails"),
])
def test_p5_calls_sonnet_only_for_money_doubts(fields, ocr_rec, reason):
    assert reason in P.reasons_to_escalate("P5", P.haiku_checks(fields, ocr_rec, TODAY))


@pytest.mark.parametrize("fields, flagged", [
    (GOOD | {"unsure_fields": ["bill_date"]}, ["bill_date"]),
    (GOOD | {"bill_date": "2027-01-05"}, ["bill_date"]),                    # future date: sanity
    (GOOD | {"invoice_number": "07ABCDE1234F1Z2"}, ["invoice_number"]),     # a GSTIN
    (GOOD | {"unsure_fields": ["due_date", "invoice_number"]}, ["invoice_number", "due_date"]),
])
def test_p5_asks_the_owner_instead_of_sonnet(fields, flagged):
    c = P.haiku_checks(fields, ocr(), TODAY)
    assert P.reasons_to_escalate("P5", c) == []
    assert P.reasons_to_escalate("P3", c) != []  # P3 would have paid for Sonnet here
    out, asked = P.apply_policy("P5", fields, None, c, TODAY)
    assert asked == flagged and set(flagged) <= set(out["unsure_fields"])
    assert P.apply_policy("P3", fields, None, c, TODAY)[1] == []


def test_p5_flags_an_old_date_that_p3_lets_through():
    fields = GOOD | {"bill_date": "2025-10-05"}  # like B134: a year misread, every check passes
    c = P.haiku_checks(fields, ocr(), TODAY)
    assert P.reasons_to_escalate("P3", c) == [] and P.apply_policy("P3", fields, None, c, TODAY)[1] == []
    assert P.reasons_to_escalate("P5", c) == [] and P.apply_policy("P5", fields, None, c, TODAY)[1] == ["bill_date"]


def test_p5_date_age_also_checks_sonnets_date():
    c = P.haiku_checks(GOOD | {"total_amount": 4796}, ocr(), TODAY)
    out, asked = P.apply_policy("P5", GOOD | {"total_amount": 4796}, GOOD | {"bill_date": "2025-10-05"}, c, TODAY)
    assert out["total_amount"] == 4769 and "bill_date" in asked and "bill_date" in out["unsure_fields"]


def test_p5_reader_date_doubt_is_free_and_marked_for_one_tap():
    r, h, s = reader(GOOD | {"unsure_fields": ["bill_date"]}, ocr_rec=ocr(), policy="P5")
    ex = r.read(b"img")
    assert s.calls == 0 and ex.extra["ask_date"] is True and ex.extra["asked"] == ["bill_date"]
    r, h, s = reader(GOOD | {"unsure_fields": ["total_amount"]}, ocr_rec=ocr(), policy="P5")
    ex = r.read(b"img")
    assert s.calls == 1 and ex.extra["reasons"] == ["haiku_unsure_total"] and not ex.extra["ask_date"]


def _p5_bot(world, haiku_fields):
    r, h, s = reader(GOOD | {"supplier_name": "Shiv Medical"} | haiku_fields, ocr_rec=ocr(), policy="P5")
    world.bot.extractor = r
    world.bot.today_fn = lambda: TODAY
    onboard(world)
    world.run(photo())
    return s


def _date_line(body):
    return next(line for line in body.splitlines() if M.FIELD_LABELS["bill_date"] in line)


@pytest.mark.parametrize("choice, day, saved", [(0, date(2026, 4, 16), "bill_confirmed"), (1, TODAY, "bill_corrected")])
def test_bot_p5_read_date_gets_its_own_button(world, choice, day, saved):
    # 18 Nov phone test: the date it read was shown, but only 'aaj / kal / doosri' could be tapped
    s = _p5_bot(world, {"bill_date": "2026-04-16", "unsure_fields": ["bill_date"]})
    last = world.wa.last
    assert s.calls == 0 and last["kind"] == "buttons" and "*16 Apr 2026*" in last["body"]
    assert "6 mahine se purani" in last["body"]          # why it asks: the date is old, not unreadable
    assert [t for _, t in last["buttons"]] == ["✅ 16 Apr 2026 sahi", M.BTN_DATE_TODAY_ALT, M.BTN_DATE_OTHER]
    assert "date_tap_asked" in world.events()
    world.run(tap(world.wa.button_ids()[choice]))
    summary = world.wa.last
    assert M.BTN_OK in [t for _, t in summary["buttons"]]
    assert M.nice_date(day) in _date_line(summary["body"]) and "⚠️" not in _date_line(summary["body"])
    assert "date_tapped" in world.events()
    world.run(tap(world.wa.button_ids()[0]))  # Sahi hai
    assert saved in world.events()            # keeping the date it read is not a correction


def test_bot_p5_unclear_recent_date_says_so(world):
    _p5_bot(world, {"bill_date": "2026-10-20", "unsure_fields": ["bill_date"]})
    assert "saaf nahi thi" in world.wa.last["body"] and "*20 Oct 2026*" in world.wa.last["body"]


@pytest.mark.parametrize("choice, day", [(0, TODAY), (1, date(2026, 10, 27))])
def test_bot_p5_no_date_read_one_tap_today_or_yesterday(world, choice, day):
    s = _p5_bot(world, {"bill_date": None, "unsure_fields": ["bill_date"]})
    last = world.wa.last
    assert s.calls == 0 and "Bill ki date aaj ki hai?" in last["body"]
    assert [t for _, t in last["buttons"]] == [M.BTN_DATE_TODAY, M.BTN_DATE_YDAY, M.BTN_DATE_OTHER]
    world.run(tap(world.wa.button_ids()[choice]))
    assert M.nice_date(day) in _date_line(world.wa.last["body"])


def test_bot_p5_kal_can_be_typed_for_the_date(world):
    from conftest import text
    _p5_bot(world, {"unsure_fields": ["bill_date"]})
    world.run(tap(world.wa.button_ids()[2]))           # Doosri date
    assert "kal" in world.wa.last["body"]
    world.run(text("kal"))
    assert M.nice_date(date(2026, 10, 27)) in _date_line(world.wa.last["body"])


def test_bot_p5_other_date_is_typed_once(world):
    _p5_bot(world, {"unsure_fields": ["bill_date"]})
    world.run(tap(world.wa.button_ids()[2]))
    assert world.wa.last["body"] == M.ASK_FIELD["bill_date"]
    from conftest import text
    world.run(text("05/10/2026"))
    assert M.nice_date(date(2026, 10, 5)) in _date_line(world.wa.last["body"])
    assert M.BTN_OK in [t for _, t in world.wa.last["buttons"]]
    world.run(tap(world.wa.button_ids()[1]))  # Badlo -> edit menu, not another date tap
    assert world.wa.last["body"] == M.EDIT_WHAT


def test_bot_p5_clean_bill_and_p3_get_no_date_tap(world):
    _p5_bot(world, {})
    assert M.BTN_OK in [t for _, t in world.wa.last["buttons"]]
    r, h, s = reader(GOOD | {"supplier_name": "Shiv Medical", "unsure_fields": ["bill_date"]}, policy="P3")
    world.bot.extractor = r
    world.run(photo())
    assert M.BTN_OK in [t for _, t in world.wa.last["buttons"]] and "date_tap_asked" not in world.events()


def test_bot_p5_missing_total_is_asked_before_the_date_tap(world):
    from conftest import text
    r, h, s = reader(GOOD | {"supplier_name": "Shiv Medical", "total_amount": None, "unsure_fields": ["bill_date"]},
                     sonnet_fields=GOOD | {"supplier_name": "Shiv Medical", "total_amount": None,
                                           "unsure_fields": ["bill_date"]}, policy="P5")
    world.bot.extractor = r
    world.bot.today_fn = lambda: TODAY
    onboard(world)
    world.run(photo())
    assert s.calls == 1  # no total = a money doubt -> Sonnet
    assert M.ASK_FIELD["total_amount"] in world.wa.last["body"]
    world.run(text("4769"))
    assert "Kya ye date sahi hai?" in world.wa.last["body"]   # GOOD has a date: it is shown with its own button


def test_ocr_runs_alongside_haiku_and_is_used_by_the_checks():
    import threading
    seen = {}

    def slow_ocr(img):
        seen["thread"] = threading.current_thread().name
        return ocr()
    h, s = Reader("claude-haiku-5-5", GOOD | {"total_amount": 4796}), Reader("claude-sonnet-5-5", GOOD)
    ex = P.PolicyReader("P5", haiku=h, sonnet=s, ocr=slow_ocr, today_fn=lambda: TODAY).read(b"img")
    assert seen["thread"].startswith("ocr") and ex.extra["reasons"][0] == "ocr_disagrees" and s.calls == 1


# ---------------------------------------------------------------- P5e = P5 + check e (invoice no. vs free OCR)
def ocr_inv(inv, total=4769):
    return {"fields": {"total_amount": total, "invoice_number": inv}, "raw_text": OCR_TEXT}


def test_check_e_compares_the_invoice_number_with_ocr():
    assert P.invoice_agrees("d 5721", "D5721") is True
    assert P.invoice_agrees("4471", "4417") is False
    assert P.invoice_agrees(None, "4417") is None and P.invoice_agrees("4471", None) is None
    assert P.ocr_invoice(ocr_inv("9000000001"), TODAY) is None  # a phone number is not an invoice no.  # secret-scan: ignore
    assert P.ocr_invoice(ocr_inv("07ABCDE1234F1Z2"), TODAY) is None  # nor a GSTIN
    assert P.ocr_invoice(ocr_inv(" d5721 "), TODAY) == "D5721"


def test_p5e_flags_a_different_invoice_number_without_sonnet():
    fields = GOOD | {"invoice_number": "D4833"}
    c = P.haiku_checks(fields, ocr_inv("D5721"), TODAY)
    assert P.reasons_to_escalate("P5e", c) == []
    out, asked = P.apply_policy("P5e", fields, None, c, TODAY)
    assert asked == ["invoice_number"] and "invoice_number" in out["unsure_fields"]
    assert P.apply_policy("P5", fields, None, c, TODAY)[1] == []  # plain P5 does not look
    same = P.haiku_checks(GOOD, ocr_inv("D5721"), TODAY)
    assert P.apply_policy("P5e", GOOD, None, same, TODAY)[1] == []


def test_p5e_checks_the_invoice_number_actually_shown_after_sonnet():
    haiku = GOOD | {"total_amount": 4796, "invoice_number": "D4833"}
    c = P.haiku_checks(haiku, ocr_inv("D5721"), TODAY)
    assert "ocr_disagrees" in P.reasons_to_escalate("P5e", c)
    out, asked = P.apply_policy("P5e", haiku, GOOD, c, TODAY)  # Sonnet read D5721 = OCR: nothing to ask
    assert "invoice_number" not in asked and out["invoice_number"] == "D5721"
    out, asked = P.apply_policy("P5e", haiku, GOOD | {"invoice_number": "D4388"}, c, TODAY)
    assert "invoice_number" in asked


def test_p5e_reader_keeps_the_date_tap():
    r, h, s = reader(GOOD | {"unsure_fields": ["bill_date"]}, ocr_rec=ocr_inv("D5721"), policy="P5e")
    ex = r.read(b"img")
    assert s.calls == 0 and ex.extra["ask_date"] is True


# ---------------------------------------------------------------- P5_trusted_ocr / P5_no_ocr (29 Oct 2026)
def test_trusted_ocr_check_needs_a_proved_ocr_total():
    haiku = GOOD | {"total_amount": 4796}
    proved = {"fields": {"total_amount": 4769}, "raw_text": "", "total_proof": "words"}
    unproved = {"fields": {"total_amount": 4769}, "raw_text": "", "total_proof": None}
    flagged = {"fields": {"total_amount": 4769, "unsure_fields": ["total_amount"]}, "raw_text": "", "total_proof": "maths"}
    assert "trusted_ocr_disagrees" in P.reasons_to_escalate("P5_trusted_ocr", P.haiku_checks(haiku, proved, TODAY))
    for rec in (unproved, flagged):
        c = P.haiku_checks(haiku, rec, TODAY)
        assert P.reasons_to_escalate("P5_trusted_ocr", c) == [] and P.reasons_to_escalate("P5", c) == ["ocr_disagrees"]
    agree = P.haiku_checks(GOOD, proved, TODAY)
    assert agree["ocr_trusted"] and P.reasons_to_escalate("P5_trusted_ocr", agree) == []


def test_trusted_ocr_keeps_check_b_and_no_ocr_drops_both():
    c = P.haiku_checks(GOOD | {"total_amount": 4796}, ocr(total=None), TODAY)  # words say 4769: proof fails
    assert P.reasons_to_escalate("P5_trusted_ocr", c) == ["proof_fails"]
    assert P.reasons_to_escalate("P5_no_ocr", c) == []
    assert P.reasons_to_escalate("P5_no_ocr", P.haiku_checks(GOOD | {"unsure_fields": ["total_amount"]}, None, TODAY)) \
        == ["haiku_unsure_total"]


def test_no_ocr_policy_never_runs_the_ocr():
    calls = []

    def counting_ocr(img):
        calls.append(1)
        return ocr()
    h, s = Reader("claude-haiku-5-5", GOOD), Reader("claude-sonnet-5-5", GOOD)
    P.PolicyReader("P5_no_ocr", haiku=h, sonnet=s, ocr=counting_ocr, today_fn=lambda: TODAY).read(b"img")
    assert calls == []
    P.PolicyReader("P5_trusted_ocr", haiku=h, sonnet=s, ocr=counting_ocr, today_fn=lambda: TODAY).read(b"img")
    assert calls == [1]
