"""18 Nov phone test on the real number: one tap for any supplier's detail from 'kitna baaki hai'.
(The read-date button is in tests/test_policy.py; the photo stored while the AI reads, in tests/test_reply_speed.py.)
Runs on both stores."""

from conftest import onboard, photo, tap, text

from bot import messages as M


def _bill(w, name, amount):
    w.reader.next = {"supplier_name": name, "total_amount": amount}
    w.run(photo())
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("ok:"))))


def test_dues_list_has_a_detail_row_per_supplier(world):
    onboard(world)
    _bill(world, "Sharma Traders", 4580)
    _bill(world, "Gupta Agency", 1200)
    world.run(text("kitna baaki hai"))
    last = world.wa.last
    assert last["kind"] == "list" and last["button"] == M.LIST_BTN_DETAIL
    assert "Sharma Traders: ₹4,580" in last["body"] and "Kul baaki: ₹5,780" in last["body"]
    assert [t for _, t, _ in last["rows"]] == ["Sharma Traders", "Gupta Agency"]   # most baaki first
    assert last["rows"][0][2] == "Baaki ₹4,580"
    world.run({"id": "wamid.dsup1", "from": last["to"], "type": "interactive",
               "interactive": {"type": "list_reply", "list_reply": {"id": last["rows"][1][0], "title": "x"}}})
    body = world.wa.last["body"]
    assert "*Gupta Agency*: *₹1,200* baaki" in body and "1 bill" in body and "Sharma" not in body


def test_detail_tap_for_a_supplier_that_is_gone_shows_the_list_again(world):
    onboard(world)
    _bill(world, "Sharma Traders", 4580)
    world.run({"id": "wamid.dsup2", "from": world.wa.last["to"], "type": "interactive",
               "interactive": {"type": "list_reply", "list_reply": {"id": "dsup:999999", "title": "x"}}})
    assert world.wa.last["kind"] == "list" and "Sharma Traders" in world.wa.last["body"]


def test_no_dues_is_plain_text(world):
    onboard(world)
    world.run(text("kitna baaki hai"))
    assert world.wa.last["kind"] == "text" and world.wa.last["body"] == M.NO_DUES


def test_many_suppliers_show_top_ten_rows(world):
    onboard(world)
    names = ["Sharma Traders", "Gupta Agency", "Verma Stores", "Bansal Foods", "Jain Medical", "Kapoor Hardware",
             "Mehta Paints", "Singh Dairy", "Arora Plastics", "Patel Oils", "Khanna Paper", "Rao Electricals"]
    for i, name in enumerate(names):
        _bill(world, name, 1000 + i * 10)
    world.run(text("kitna baaki hai"))
    last = world.wa.last
    assert len(last["rows"]) == 10 and len(last["body"]) <= 1024 and "Sharma Traders" in last["body"]
    assert last["rows"][0][1] == "Rao Electricals"     # the 10 with the most baaki; all 12 are in the text
