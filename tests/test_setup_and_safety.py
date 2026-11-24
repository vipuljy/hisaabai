"""Setup checker, database setup, cost guards, privacy (photo clean-up), sales-bill guard, typed bills,
hosting endpoints and Supabase photo storage."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from bot import admin
from bot import messages as M
from bot.app import create_app
from bot.check_setup import Checker, redact, report
from bot.conversation import Bot, Limits, looks_like_shop_name, parse_typed_bill
from bot.jobs import purge_old_photos, run_all
from bot.photos import LocalPhotos, SupabasePhotos
from bot.store import MemoryStore
from conftest import FakeReader, FakeWA, onboard, photo, tap, text

TOKEN = "EAAGtesttokenvalue1234567890"
ENV = {"WHATSAPP_TOKEN": TOKEN, "PHONE_NUMBER_ID": "123456789012345", "VERIFY_TOKEN": "hisaab-vipul-7391",
       "APP_SECRET": "0123456789abcdef0123456789abcdef", "GRAPH_VERSION": "v23.0",
       "ANTHROPIC_API_KEY": "sk-ant-test-key-abcdef", "SARVAM_API_KEY": "sarvam-test-key-123",
       "DATABASE_URL": "", "PILOT_OPEN": "1", "WABA_ID": "555"}


# ---------------------------------------------------------------- typed bills
@pytest.mark.parametrize("msg, name, amount", [
    ("Sharma Traders 4580", "Sharma Traders", 4580),
    ("bill: Gupta Agency ₹12,500", "Gupta Agency", 12500),
    ("Mehta & Sons rs 990/-", "Mehta & Sons", 990),
])
def test_typed_bill_parses(msg, name, amount):
    got = parse_typed_bill(msg)
    assert got["supplier_name"] == name and got["total_amount"] == amount


@pytest.mark.parametrize("msg", ["Sharma ko 5000", "hi 23", "kitna baaki hai", "5000", "aaj 3 bill aaye kya?"])
def test_typed_bill_ignores(msg):
    assert parse_typed_bill(msg) is None


def test_typed_bill_flow(world):
    onboard(world)
    world.run(text("Sharma Traders 4580 05/10/2026"))
    assert world.wa.last["kind"] == "buttons" and "Sharma Traders" in world.wa.last["body"]
    world.run(tap(world.wa.button_ids()[0]))
    assert "4,580" in world.wa.sent[-2]["body"]
    assert world.reader.next is None  # no AI call for a typed bill
    assert "ai_call" not in world.events()


# ---------------------------------------------------------------- shop name and sales-bill guard
def _typed_bill(w, line="Sharma Traders 4580 05/10/2026", profile=""):
    w.run(text(line) | {"_profile_name": profile})
    w.run(tap(next(b for b in w.wa.button_ids() if b.startswith("ok:"))) | {"_profile_name": profile})


def _p(msg, profile="Ramesh Kirana"):
    return msg | {"_profile_name": profile}


def _shop(w):
    return w.store.get_shop_by_wa("919876543210")


def test_signup_asks_nothing_and_the_ready_message_teaches_by_example(world):
    onboard(world)
    assert world.wa.last["body"] == M.READY and world.wa.last["kind"] == "text"
    assert (_shop(world).get("state") or {}) == {}       # no question is waiting
    for line in ("Sharma ko 5000 diye", "Gupta ka bill 4580", "kitna baaki hai", "Sahi hai"):
        assert line in M.READY
    for welcome in (M.WELCOME, M.READY, M.MENU, M.NOT_IN_PILOT, M.OPTED_OUT, M.OPTED_OUT_HINT, M.OWN_BILL_SKIPPED):
        assert "HisaabAI" not in welcome          # owners see one brand: VahiBook
    assert M.BRAND in M.WELCOME_HEADER and "30 din" in M.WELCOME_FOOTER and "STOP" in M.WELCOME_FOOTER
    assert "Sahi hai" in M.WELCOME          # the trust promise: nothing is saved without the owner's tap


def test_profile_name_is_never_stored(world):
    world.run(text("hi") | {"_profile_name": "  Ramesh   Kirana Store "})
    world.run(text("haan") | {"_profile_name": "Ramesh Kirana Store"})
    shop = _shop(world)
    assert shop.get("shop_name") is None and not shop.get("profile_name")   # a suggestion only, never saved


def test_profile_name_is_confirmed_in_one_tap_after_the_first_entry(world):
    world.run(text("hi") | {"_profile_name": "Ramesh Kirana"})
    world.run(tap("consent_yes"))
    _typed_bill(world, profile="Ramesh Kirana")
    assert "₹4,580" in world.wa.sent[-2]["body"]
    assert world.wa.last["body"] == M.shop_name_check("Ramesh Kirana")
    assert world.wa.button_ids() == ["sn_yes", "sn_edit", "sn_skip"]
    world.run(_p(tap("sn_yes")))
    assert _shop(world)["shop_name"] == "Ramesh Kirana"
    assert world.wa.last["body"] == M.shop_name_saved("Ramesh Kirana")
    _typed_bill(world, "Gupta Agency 3000 06/10/2026")      # asked once only
    assert "₹3,000" in world.wa.last["body"]
    world.run(_p(tap("sn_yes")))                               # an old tap: nothing changes
    assert world.wa.last["body"] == M.shop_name_known("Ramesh Kirana")


def test_badlo_lets_the_owner_type_the_name(world):
    world.run(text("hi") | {"_profile_name": "Ramesh Kirana"})
    world.run(tap("consent_yes"))
    _typed_bill(world, profile="Ramesh Kirana")
    world.run(tap("sn_edit"))
    assert world.wa.last["body"] == M.ASK_SHOP_NAME_TYPE
    world.run(text("Ramesh General Store"))
    assert _shop(world)["shop_name"] == "Ramesh General Store"
    world.run(text("naam: Gupta Kirana"))                 # it can always be changed
    assert _shop(world)["shop_name"] == "Gupta Kirana"


@pytest.mark.parametrize("profile", ["", "Rahul", "🙂🙂", "Papa ji ka phone", "9876543210 Rahul", "~"])
def test_no_usable_profile_name_asks_by_tap_and_never_swallows_the_next_message(world, profile):
    world.run(text("hi") | {"_profile_name": profile})
    world.run(tap("consent_yes"))
    _typed_bill(world, profile=profile)
    if profile == "Papa ji ka phone":   # reads like a name: offered, but the owner decides
        assert world.wa.button_ids() == ["sn_yes", "sn_edit", "sn_skip"]
        return
    assert world.wa.last["body"] == M.ASK_SHOP_NAME and world.wa.button_ids() == ["sn_edit", "sn_skip"]
    world.run(text("gupta ke saath len den"))   # NOT a shop name: the question waited for a tap
    assert _shop(world).get("shop_name") is None
    world.run(text("Kapoor Agency 900 07/10/2026"))
    assert "Kapoor Agency" in world.wa.last["body"]


def test_skip_is_respected_and_never_asked_again(world):
    world.run(text("hi") | {"_profile_name": "Ramesh Kirana"})
    world.run(tap("consent_yes"))
    _typed_bill(world, profile="Ramesh Kirana")
    world.run(tap("sn_skip"))
    assert world.wa.last["body"] == M.SHOP_NAME_SKIPPED and _shop(world).get("shop_name") is None
    _typed_bill(world, "Gupta Agency 3000 06/10/2026")
    world.run(text("Sharma ko 500 diye"))
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("pok:"))))
    assert sum(m.get("body", "").startswith("Ek chhoti si baat") for m in world.wa.sent) == 1
    assert "shop_name_skipped" in world.events()


def test_skip_word_after_badlo(world):
    world.run(text("hi") | {"_profile_name": "Ramesh Kirana"})
    world.run(tap("consent_yes"))
    _typed_bill(world, profile="Ramesh Kirana")
    world.run(tap("sn_edit"))
    world.run(text("skip"))
    assert world.wa.last["body"] == M.SHOP_NAME_SKIPPED and _shop(world).get("shop_name") is None


def test_name_question_waits_while_other_cards_are_open(world):
    onboard(world)
    world.run(text("Sharma ka bill 500 aaya aur Gupta ka bill 700 aaya"))
    cards = [m for m in world.wa.sent if m["kind"] == "buttons" and any(b.startswith("ok:") for b, _ in m["buttons"])]
    assert len(cards) == 2
    first = next(b for b, _ in cards[0]["buttons"] if b.startswith("ok:"))
    second = next(b for b, _ in cards[1]["buttons"] if b.startswith("ok:"))
    world.run(tap(first))
    assert not world.wa.last["body"].startswith("Ek chhoti si baat")   # the second card is still waiting
    world.run(tap(second))
    assert world.wa.last["body"] == M.ASK_SHOP_NAME


def test_named_shop_is_never_asked(world):
    onboard(world)
    world.run(text("naam: Gupta General Store"))
    _typed_bill(world)
    assert "₹4,580" in world.wa.last["body"]
    assert "shop_name_asked" not in world.events()


def test_shop_name_question_does_not_swallow_commands(world):
    onboard(world)
    _typed_bill(world)
    world.run(tap("sn_edit"))
    world.run(text("kitna baaki hai"))
    assert "Sharma Traders" in world.wa.last["body"]
    assert _shop(world)["shop_name"] is None
    assert not looks_like_shop_name("aaj cricket match kaun jeetega?")


def test_own_sales_bill_is_caught(world):
    onboard(world)
    world.run(text("naam: Gupta General Store"))
    world.reader.next = {"supplier_name": "GUPTA GENERAL STORE", "total_amount": 350}
    world.run(photo())
    assert world.wa.last["body"].startswith("Is bill pe naam")
    yes, no = world.wa.button_ids()
    world.run(tap(yes))
    assert world.wa.last["body"] == M.OWN_BILL_SKIPPED
    assert "own_sales_bill" in world.events()
    world.run(text("kitna baaki hai"))
    assert world.wa.last["body"] == M.NO_DUES  # the sales bill never reached the ledger


def test_own_bill_denied_goes_to_normal_confirm(world):
    onboard(world)
    world.run(text("naam: Gupta General Store"))
    world.reader.next = {"supplier_name": "Gupta General Store", "total_amount": 350}
    world.run(photo())
    world.run(tap(world.wa.button_ids()[1]))
    assert world.wa.last["body"].startswith("Bill padh liya")


def test_real_supplier_not_flagged(world):
    onboard(world)
    world.run(text("naam: Gupta General Store"))
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    world.run(photo())
    assert world.wa.last["body"].startswith("Bill padh liya")


# ---------------------------------------------------------------- cost and abuse guards
def _bot(store, tmp_path, **limits):
    wa, reader = FakeWA(), FakeReader()
    calls = []
    orig = reader.__call__

    def counting(image):
        calls.append(1)
        return orig(image)

    bot = Bot(store, wa, counting, pilot_open=True, photos=LocalPhotos(tmp_path), limits=Limits(**limits))
    return bot, wa, calls


def test_per_shop_daily_limit(tmp_path):
    store = MemoryStore()
    bot, wa, calls = _bot(store, tmp_path, per_shop_per_day=2)
    run = lambda m: asyncio.run(bot.handle(m))  # noqa: E731
    run(text("hi"))
    run(tap("consent_yes"))
    for _ in range(3):
        run(photo())
    assert len(calls) == 2 and wa.last["body"] == M.SHOP_DAILY_LIMIT


def test_whole_bot_daily_limit(tmp_path):
    store = MemoryStore()
    bot, wa, calls = _bot(store, tmp_path, per_shop_per_day=10, total_per_day=1)
    run = lambda m: asyncio.run(bot.handle(m))  # noqa: E731
    for who in ("911111111111", "912222222222"):
        run(text("hi", who))
        run(tap("consent_yes", who))
        run(photo(who))
    assert len(calls) == 1 and wa.last["body"] == M.BUSY


def test_photo_too_big(tmp_path):
    store = MemoryStore()
    bot, wa, calls = _bot(store, tmp_path, max_image_mb=0.000001)
    run = lambda m: asyncio.run(bot.handle(m))  # noqa: E731
    run(text("hi"))
    run(tap("consent_yes"))
    run(photo())
    assert calls == [] and wa.last["body"] == M.PHOTO_TOO_BIG


# ---------------------------------------------------------------- privacy: 30-day photo clean-up
def test_old_photos_deleted_unless_eval_consent(store, tmp_path):
    photos = LocalPhotos(tmp_path)
    keep = store.create_shop("911000000001", shop_code="S01", status="active")
    drop = store.create_shop("911000000002", shop_code="S02", status="active")
    store.update_shop(keep["id"], eval_consent=True)
    paths = {}
    for shop in (keep, drop):
        ref = photos.save(shop["shop_code"], b"jpeg")
        bill = store.create_bill(shop["id"], {"supplier_name": "X", "total_amount": 10}, {"image_path": ref})
        paths[shop["shop_code"]] = (ref, bill["id"])
    assert purge_old_photos(store, photos)["photos_deleted"] == 0  # too new
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert purge_old_photos(store, photos, now=later, dry_run=True)["photos_deleted"] == 1
    assert os.path.exists(paths["S02"][0])  # dry run changed nothing
    assert purge_old_photos(store, photos, now=later)["photos_deleted"] == 1
    assert not os.path.exists(paths["S02"][0]) and os.path.exists(paths["S01"][0])
    assert store.get_bill(paths["S02"][1])["image_path"] is None
    assert store.get_bill(paths["S02"][1])["photo_deleted_at"] is not None
    assert store.get_bill(paths["S02"][1])["total_amount"] == 10  # the numbers stay


def test_admin_delete_shop_removes_photos(tmp_path):
    store, lines, photos = MemoryStore(), [], LocalPhotos(tmp_path)
    run = lambda *a: admin.main(list(a), store=store, out=lines.append, photos=photos)  # noqa: E731
    assert run("add-shop", "S07", "kirana", "9876500007", "Bansal Store") == 0
    assert store.list_shops()[0]["shop_name"] == "Bansal Store"
    assert run("eval-consent", "S07", "yes") == 0 and store.list_shops()[0]["eval_consent"] is True
    assert run("eval-consent", "S07", "maybe") == 1
    ref = photos.save("S07", b"jpeg")
    assert run("delete-shop", "S07") == 0
    assert not os.path.exists(ref) and "photos" in lines[-1]


# ---------------------------------------------------------------- hosting endpoints
def test_health_and_jobs_endpoint(tmp_path):
    store = MemoryStore()
    bot = Bot(store, FakeWA(), FakeReader(), photos=LocalPhotos(tmp_path))
    client = TestClient(create_app(bot=bot, verify_token="v", app_secret="", jobs_token="j" * 30))
    assert client.get("/health").json() == {"status": "ok", "db": "ok"}
    assert client.post("/jobs/daily").status_code == 401
    assert client.post("/jobs/daily", headers={"X-Jobs-Token": "wrong"}).status_code == 401
    r = client.post("/jobs/daily?dry_run=1", headers={"X-Jobs-Token": "j" * 30})
    assert r.status_code == 200 and r.json()["dry_run"] is True and "reminders" in r.json()
    off = TestClient(create_app(bot=bot, verify_token="v", app_secret="", jobs_token=""))
    assert off.post("/jobs/daily", headers={"X-Jobs-Token": ""}).status_code == 404


def test_run_all_combines_jobs(tmp_path):
    counts = asyncio.run(run_all(MemoryStore(), FakeWA(), LocalPhotos(tmp_path), datetime.now().date(), dry_run=True))
    assert {"photos_deleted", "reminders", "summaries"} <= set(counts)


# ---------------------------------------------------------------- Supabase photo storage (faked HTTP)
def _fake_storage():
    files, seen = {}, []

    def handler(request: httpx.Request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/bucket") and request.method == "POST":
            return httpx.Response(200, json={"name": "bill-photos"})
        if "/bucket/" in path:
            return httpx.Response(200, json={"id": "bill-photos", "public": False})
        if "/object/list/" in path:
            prefix = json.loads(request.content)["prefix"]
            return httpx.Response(200, json=[{"name": k.split("/", 1)[1]} for k in files if k.startswith(prefix + "/")])
        if request.method == "DELETE":
            for p in json.loads(request.content)["prefixes"]:
                files.pop(p, None)
            return httpx.Response(200, json=[])
        key = path.split("/object/bill-photos/", 1)[1]
        if request.method == "POST":
            files[key] = request.content
            return httpx.Response(200, json={"Key": key})
        return httpx.Response(200, content=files[key]) if key in files else httpx.Response(404, json={})

    return files, seen, httpx.Client(transport=httpx.MockTransport(handler))


def test_supabase_photos_roundtrip():
    files, seen, client = _fake_storage()
    sb = SupabasePhotos("https://abc.supabase.co", "sb_secret_testkey", client=client)
    assert sb.ensure_bucket() == "created" and sb.bucket_is_private()
    ref = sb.save("S01", b"jpeg-bytes")
    assert ref.startswith("sb:S01/") and sb.get(ref) == b"jpeg-bytes"
    sb.save("S01", b"two")
    sb.delete_shop("S01")
    assert files == {}
    assert all("authorization" not in r.headers for r in seen)  # new-style keys: apikey header only
    legacy = SupabasePhotos("https://abc.supabase.co", "eyJhbGciOi.legacy.jwt", client=client)
    assert legacy.headers["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------- setup checker (faked HTTP)
def _checker(handler, env=None, **kw):
    return Checker(env or ENV, http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def _happy(request: httpx.Request):
    host, path = request.url.host, request.url.path
    if host == "graph.facebook.com" and path.endswith("/debug_token"):
        return httpx.Response(200, json={"data": {"expires_at": 0}})
    if host == "graph.facebook.com" and path.endswith("/message_templates"):
        return httpx.Response(200, json={"data": [{"name": "due_reminder", "status": "APPROVED", "language": "en"},
                                                  {"name": "weekly_summary", "status": "PENDING"}]})
    if host == "graph.facebook.com":
        return httpx.Response(200, json={"display_phone_number": "+1 555 010 4321", "verified_name": "Test Number"})
    if host == "api.anthropic.com":
        return httpx.Response(200, json={"content": [{"type": "text", "text": "H"}]})
    if host == "api.sarvam.ai":
        return httpx.Response(200, json={"transcript": ""})
    raise httpx.ConnectError("no local bot")


def test_checker_happy_path(tmp_path):
    lines = []
    results = _checker(_happy, root=tmp_path).run_all()
    code = report(results, ENV, out=lines.append)
    out = "\n".join(lines)
    by = {r.name: r.status for r in results}
    assert by["WhatsApp"] == "OK" and "ending 4321" in out
    assert by["Token type"] == "OK" and by["Anthropic"] == "OK" and by["Sarvam (voice)"] == "OK"
    assert by["Template due_reminder"] == "OK" and by["Template weekly_summary"] == "WARN"
    assert by["Database"] == "SKIP" and by["Local bot"] == "SKIP" and by["PILOT_OPEN"] == "OK"
    assert by[".env file"] == "FAIL" and code == 1  # tmp_path has no .env
    for key in ("WHATSAPP_TOKEN", "APP_SECRET", "VERIFY_TOKEN", "ANTHROPIC_API_KEY", "SARVAM_API_KEY"):
        assert ENV[key] not in out


def test_checker_explains_failures():
    def broken(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(401, json={"error": {"code": 190, "message": f"bad token {TOKEN}"}})
        if request.url.host == "api.anthropic.com":
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                                                       "message": "Your credit balance is too low"}})
        if request.url.host == "api.sarvam.ai":
            return httpx.Response(403, json={})
        raise httpx.ConnectError("x")

    c = _checker(broken, env=ENV | {"APP_SECRET": "not-hex", "VERIFY_TOKEN": "short", "WABA_ID": ""})
    res = {r.name: r for r in c.whatsapp() + c.webhook_settings() + c.anthropic() + c.sarvam() + c.templates()}
    assert res["WhatsApp"].status == "FAIL" and "Step 6" in res["WhatsApp"].detail
    assert res["Anthropic"].status == "FAIL" and "credits" in res["Anthropic"].detail
    assert res["Sarvam (voice)"].status == "FAIL"
    assert res["APP_SECRET"].status == "FAIL" and res["VERIFY_TOKEN"].status == "FAIL"
    assert res["Templates"].status == "SKIP"


def test_redact_hides_every_secret():
    env = ENV | {"DATABASE_URL": "postgresql://postgres.abc:Sup3rSecretPw@aws-0.pooler.supabase.com:5432/postgres"}  # secret-scan: ignore
    line = f"token {TOKEN} pw Sup3rSecretPw owner 919876543210"
    out = redact(line, env)
    assert TOKEN not in out and "Sup3rSecretPw" not in out and "9876543210" not in out


def test_checker_database_and_setup(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg

    from bot import setup_db
    from bot.store import PgStore
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("drop schema if exists public cascade; create schema public;")
    assert _checker(_happy, env=ENV | {"DATABASE_URL": url}).database()[-1].status == "FAIL"  # no tables yet
    setup_db.load_schema(url)
    setup_db.load_schema(url)  # running twice is safe
    assert setup_db.smoke_test(PgStore(url)) == []
    res = _checker(_happy, env=ENV | {"DATABASE_URL": url}).database()
    assert res[-1].status == "OK" and "0 shop" in res[-1].detail
