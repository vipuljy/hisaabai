"""24 Nov, before the pilot: each shop sees only its own hisaab. Two shops buy from a supplier with the SAME name;
every question, fix, photo-proof match, reminder list and DELETE stays inside the shop that sent the message.
Runs on both stores. Numbers are invented."""

from conftest import OWNER, _events, onboard, photo, tap, text

from bot import messages as M

OTHER = "919000000002"   # secret-scan: ignore (a fake number)


def bodies(w, since):
    return "\n".join(str(m.get("body") or "") for m in w.wa.sent[since:])


def test_two_shops_never_see_each_other(world):
    onboard(world)
    onboard(world, OTHER)
    world.run(text("Gupta Traders ka bill 4580"))
    world.run(text("ok"))
    world.run(text("Gupta Traders ko 1000 diye"))
    world.run(text("ok"))
    world.run(text("Gupta Traders ko Friday yaad dilana"))
    n = len(world.wa.sent)
    for asked in ("kitna baaki hai", "Gupta Traders ka kitna baaki", "aaj ka hisaab", "is hafte kitna diya",
                  "mere reminders", "last wala galat hai", "1000 nahi 1200"):
        world.run(text(asked, OTHER))
    seen = bodies(world, n)
    for leak in ("4,580", "4580", "3,580", "Gupta Traders"):   # (B's own '1000 nahi 1200' may be echoed back to B)
        assert leak not in seen, leak
    # the owner's own view is untouched
    world.run(text("kitna baaki hai"))
    assert "3,580" in world.wa.last["body"]


def test_a_photo_from_one_shop_is_never_proof_for_another(world):
    onboard(world)
    onboard(world, OTHER)
    world.run(text("Gupta Traders ka bill 4580"))
    world.run(text("ok"))
    world.reader.next = {"supplier_name": "Gupta Traders", "total_amount": 4580}
    n = len(world.wa.sent)
    world.run(photo(OTHER))
    assert "proof" not in bodies(world, n).lower()


def test_delete_in_one_shop_leaves_the_other_whole(world):
    onboard(world)
    onboard(world, OTHER)
    world.run(text("Gupta Traders ka bill 4580"))
    world.run(text("ok"))
    world.run(text("Mehta ka bill 700", OTHER))
    world.run(text("ok", OTHER))
    world.run(text("DELETE", OTHER))
    btn = next(b for b, _ in world.wa.last["buttons"] if "del" in b)
    world.run(tap(btn, OTHER))
    assert any(e["event"] == "shop_data_deleted" for e in _events(world.store))
    world.run(text("kitna baaki hai"))
    assert "4,580" in world.wa.last["body"]
    assert len([s for s in world.store.list_shops()]) == 1


def test_logs_keep_only_the_last_4_digits(world):
    onboard(world)
    world.run(text("Gupta Traders ka bill 4580"))
    world.run(text("ok"))
    for e in _events(world.store):
        assert OWNER not in str(e["props"]), e["event"]


def test_no_reply_ever_shows_a_phone_number(world):
    onboard(world)
    world.run(text("Gupta Traders ka bill 4580"))
    world.run(text("ok"))
    for asked in ("kitna baaki hai", "menu", "sikhao", "feedback"):
        world.run(text(asked))
    assert OWNER not in bodies(world, 0) and OWNER[-10:] not in bodies(world, 0)
    assert M.BRAND == "VahiBook"
