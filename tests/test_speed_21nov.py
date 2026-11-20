"""21 Nov, after the 20 Nov health report: photo replies median 13.5 s (owner: "Time jyada lagta h"), AI reading
6.1 s live vs 2.8 s in the eval, and "Sends failed 1" with no reason.
  1. One shared AI client, warmed at start-up; an effort switch that is off by default (bot/ai_client.py).
  2. photo_timed splits the AI reading into the AI service's time and ours; the health report prints it.
  3. A refused or undelivered WhatsApp message keeps Meta's error code; the health report says what it means.
  4. The speed test (src/14_effort_speed.py) applies the rule written before the run.
Store-dependent tests run on both stores."""

import asyncio
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from conftest import OWNER, _events, onboard, photo
from fastapi.testclient import TestClient

from bot import ai_client, health, jobs
from bot import whatsapp as WAmod
from bot.app import create_app
from bot.extract import extract_bill
from bot.policy import PolicyReader

ROOT = Path(__file__).resolve().parents[1]


def props(w, name):
    return [e["props"] for e in _events(w.store) if e["event"] == name]


# ------------------------------------------------------------------ 1. shared client and effort
def test_effort_values():
    assert ai_client.effort_body(None) == {} and ai_client.effort_body("") == {}
    assert ai_client.effort_body("low") == {"output_config": {"effort": "low"}}
    assert ai_client.effort_body(" Medium ") == {"output_config": {"effort": "medium"}}
    assert ai_client.effort_body("off") == {"thinking": {"type": "disabled"}}
    with pytest.raises(ValueError):
        ai_client.check_effort("fast")


def test_warm_up_needs_a_key_and_never_raises():
    assert ai_client.warm() is False          # tests run without ANTHROPIC_API_KEY (conftest)


class FakeClient:
    def __init__(self):
        self.kwargs = []
        self.messages = self

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        usage = SimpleNamespace(input_tokens=1500, output_tokens=120,
                                output_tokens_details=SimpleNamespace(thinking_tokens=40))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"supplier_name": "Sharma Traders", '
                                                                            '"total_amount": 4580}')],
                               usage=usage, stop_reason="end_turn")


def _jpeg():
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (250, 250, 250)).save(buf, format="JPEG")
    return buf.getvalue()


def test_effort_is_sent_only_when_set_and_recorded():
    c = FakeClient()
    ex = extract_bill(_jpeg(), model="claude-haiku-5-5", prompt_version="v3", client=c)
    assert "extra_body" not in c.kwargs[-1] and ex.effort is None        # default: the request is as before
    ex = extract_bill(_jpeg(), model="claude-haiku-5-5", prompt_version="v3", client=c, effort="low")
    assert c.kwargs[-1]["extra_body"] == {"output_config": {"effort": "low"}} and ex.effort == "low"
    assert ex.fields["total_amount"] == 4580 and ex.thinking_tokens == 40


def test_build_reader_reads_the_effort_settings(monkeypatch):
    from bot.app import build_reader
    monkeypatch.setenv("BILL_EFFORT", "low")
    monkeypatch.setenv("ESCALATION_EFFORT", "")
    r = build_reader("P5_no_ocr")
    assert r.haiku.keywords["effort"] == "low" and r.sonnet.keywords["effort"] is None
    monkeypatch.setenv("BILL_EFFORT", "turbo")
    with pytest.raises(ValueError):
        build_reader("P5_no_ocr")


def test_setup_check_flags_a_bad_effort(tmp_path):
    from bot.check_setup import Checker
    ck = Checker({"BILL_EFFORT": "turbo", "ESCALATION_EFFORT": "medium"}, root=tmp_path)
    got = {r.name: r.status for r in ck.misc()}
    assert got["BILL_EFFORT"] == "FAIL" and got["ESCALATION_EFFORT"] == "OK"


def test_voice_and_question_calls_use_the_shared_client():
    for path in ("bot/voice.py", "bot/questions.py", "bot/extract.py"):
        assert "client = anthropic.Anthropic()" not in (ROOT / path).read_text(encoding="utf-8")


# ------------------------------------------------------------------ 2. where a photo's AI time goes
def test_escalation_adds_up_thinking_tokens():
    from bot.extract import Extraction

    def haiku(_):
        return Extraction(fields={"supplier_name": "Sharma", "total_amount": None, "unsure_fields": ["total_amount"]},
                          raw_text="", model="h", prompt_version="v3", latency_ms=3000, thinking_tokens=400)

    def sonnet(_):
        return Extraction(fields={"supplier_name": "Sharma", "total_amount": 4580, "unsure_fields": []},
                          raw_text="", model="s", prompt_version="v3", latency_ms=5000, thinking_tokens=200)
    ex = PolicyReader("P5_no_ocr", haiku=haiku, sonnet=sonnet).read(b"x")
    assert ex.extra["calls"] == ["h", "s"] and ex.latency_ms == 8000 and ex.thinking_tokens == 600


def test_photo_timed_has_the_ai_service_time(world):
    onboard(world)
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    world.run(photo())
    t = props(world, "photo_timed")[-1]
    assert t["api_ms"] == 900 and "think_tok" in t and t["effort"] is None
    line = health.photo_line([t | {"read_ms": 2500, "think_tok": 400},
                              {"download_ms": 900, "read_ms": 9000, "api_ms": 6500, "think_tok": 500,
                               "effort": "low", "escalated": False, "kb": 100}])
    assert "AI reading split (2 photos): AI service" in line and "our side" in line
    assert "thinking 450 tokens a photo" in line and "effort" in line


def test_old_photo_events_without_the_split_still_print():
    line = health.photo_line([{"download_ms": 900, "read_ms": 12000, "escalated": True, "kb": 300}])
    assert line.startswith("- Photos: 1") and "split" not in line


# ------------------------------------------------------------------ 3. why a message failed
def _meta_client(status, body):
    wa = WAmod.WhatsAppClient("t", "123")
    transport = httpx.MockTransport(lambda req: httpx.Response(status, json=body))
    wa._http = lambda: httpx.AsyncClient(transport=transport)   # noqa: SLF001
    return wa


def test_failed_send_keeps_metas_error_code():
    async def go():
        bad = _meta_client(400, {"error": {"code": 131047, "message": "Re-engagement message"}})
        assert await bad.send_text("919800000000", "hi") is None
        first = WAmod.last_error()
        good = _meta_client(200, {"messages": [{"id": "wamid.1"}]})
        assert await good.send_text("919800000000", "hi") == "wamid.1"
        return first, WAmod.last_error()
    first, after = asyncio.run(go())
    assert first == {"code": 131047, "status": 400} and after is None


class RefusingWA:
    """FakeWA whose texts Meta refuses with 131026."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def _refuse(self, *args, **kwargs):
        WAmod._LAST_ERROR.set({"code": 131026, "status": 400})   # noqa: SLF001  what _send does on a refusal
        return None

    send_text = send_buttons = send_list = _refuse


def test_bot_logs_the_code_of_a_refused_reply(world):
    onboard(world)
    world.bot.wa = RefusingWA(world.wa)
    world.run({"id": "wamid.r1", "from": OWNER, "type": "text", "text": {"body": "cricket score batao"}})
    p = props(world, "send_failed")
    assert p and p[-1]["code"] == 131026 and p[-1]["kind"] in ("text", "buttons", "list")


def test_template_failure_keeps_the_code(world):
    onboard(world)
    shop = world.store.get_shop_by_wa(OWNER)
    today = date.today()
    world.store.add_reminder(shop["id"], None, "hisaab dekhna", today)

    async def refuse(to, name, params, language="en"):
        WAmod._LAST_ERROR.set({"code": 131042, "status": 400})   # noqa: SLF001
        return None
    world.wa.send_template = refuse
    asyncio.run(jobs.send_owner_reminders(world.store, world.wa, world.store.get_shop_by_wa(OWNER), today))
    p = props(world, "send_failed")[-1]
    assert p["code"] == 131042 and p["kind"] == "template" and p["template"] == jobs.TEMPLATE_OWNER_REMINDER


def test_undelivered_status_is_logged_without_the_number(world):
    onboard(world)
    c = TestClient(create_app(bot=world.bot, verify_token="v", app_secret=""))
    payload = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.x", "status": "failed", "recipient_id": OWNER, "errors": [{"code": 131042, "title": "x"}]},
        {"id": "wamid.y", "status": "delivered", "recipient_id": OWNER},
        {"id": "wamid.z", "status": "failed", "recipient_id": "919999999999", "errors": [{"code": 131026}]}]}}]}]}
    assert c.post("/webhook", content=json.dumps(payload)).status_code == 200
    got = props(world, "delivery_failed")
    assert got == [{"code": 131042}]               # unknown numbers and other statuses are ignored
    assert OWNER not in json.dumps(got)


def test_health_explains_the_codes():
    now = datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc)
    ev = lambda name, p: {"shop_id": "1", "event": name, "props": p, "created_at": now}   # noqa: E731
    events = [ev("send_failed", {"kind": "text", "code": 131047}), ev("send_failed", {}),
              ev("delivery_failed", {"code": 131042})]
    report = health.build([{"id": "1", "shop_code": "F01", "status": "active"}], events, date(2026, 11, 21))
    assert "- WhatsApp refused 2 (" in report and "131047: 24-hour window closed" in report
    assert "no code (logged before 21 Nov" in report
    assert "accepted but not delivered 1 (131042: payment problem on the WhatsApp account x1)" in report


# ------------------------------------------------------------------ 4. the speed test and its rule
def _speed():
    spec = importlib.util.spec_from_file_location("speed14", ROOT / "src" / "14_effort_speed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(name, allf, silent, ms, errors=0, effort="default", think=400):
    return {"run": name, "all_fields_right": allf, "bills_with_silent_error": silent, "median_latency_ms": ms,
            "json_errors": errors, "effort": effort, "avg_thinking_tokens": think}


def test_speed_rule():
    s = _speed()
    base = _row("haiku_v3", 0.82, 0.037, 2800)
    assert s.verdict(base, _row("a", 0.80, 0.037, 1500))[0]                 # 2 points down: allowed
    assert not s.verdict(base, _row("b", 0.79, 0.037, 1500))[0]             # 3 points down
    assert not s.verdict(base, _row("c", 0.85, 0.044, 1500))[0]             # one more silent error
    assert not s.verdict(base, _row("d", 0.82, 0.037, 1500, errors=1))[0]   # a failed reply
    rows = {"haiku_v3": base, "haiku_v3_low": _row("haiku_v3_low", 0.81, 0.03, 1700, effort="low", think=90),
            "haiku_v3_off": _row("haiku_v3_off", 0.70, 0.06, 1200, effort="off", think=0)}
    lines = []
    assert s.report(rows, "haiku_v3", ["haiku_v3_low", "haiku_v3_off"], "BILL_EFFORT", out=lines.append) == "low"
    assert "set BILL_EFFORT=low on Render" in lines[-1] and any("FAIL" in x for x in lines)
    rows["haiku_v3_low"]["all_fields_right"] = 0.70
    assert s.report(rows, "haiku_v3", ["haiku_v3_low", "haiku_v3_off"], "BILL_EFFORT", out=lines.append) is None


def test_speed_runs_are_in_the_eval_config_and_the_bat():
    cfg = json.loads((ROOT / "src" / "eval_config.json").read_text(encoding="utf-8"))
    runs = {r["name"]: r for r in cfg["runs"]}
    s = _speed()
    for base, cands, _ in s.SETS.values():
        assert runs[base].get("effort") is None
        assert all(runs[c]["prompt"] == runs[base]["prompt"] == "v3" and runs[c]["effort"] for c in cands)
    bat = (ROOT / "RUN_eval_speed.bat").read_text(encoding="utf-8")
    assert "14_effort_speed.py --model haiku" in bat and "SPEED TEST" in bat


def test_eval_passes_the_run_effort(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    import evalkit
    seen = {}

    def fake_extract(image_bytes, model, prompt_version, client=None, effort=None):
        seen["effort"] = effort
        from bot.extract import Extraction
        return Extraction(fields={}, raw_text="{}", model=model, prompt_version=prompt_version)
    monkeypatch.setattr(evalkit, "extract_bill", fake_extract)
    (tmp_path / "b.jpg").write_bytes(b"x")
    rec = evalkit.run_one({"bill_id": "B1", "image_file": "b.jpg"}, tmp_path, tmp_path / "c", "m", "v3", effort="low")
    assert seen["effort"] == "low" and rec["effort"] == "low"


# ------------------------------------------------------------------ 5. the free-replies counter (21 Nov: no payment method yet)
def test_replies_this_month_are_counted(world):
    onboard(world)
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    n = world.store.replies_sent_since(since)
    assert n > 0
    shop = world.store.get_shop_by_wa(OWNER)
    world.store.record_message(shop["id"], "out", "wamid.tpl-x", "template", "weekly_summary")
    world.store.record_message(shop["id"], "out", None, "text", "failed send")
    assert world.store.replies_sent_since(since) == n          # templates and failed sends are not counted
    assert world.store.replies_sent_since(datetime(2999, 1, 1, tzinfo=timezone.utc)) == 0


def test_health_warns_near_the_free_limit():
    assert "412 of about 1,000 free" in health.month_line(412) and "WARNING" not in health.month_line(412)
    assert "WARNING" in health.month_line(850)
    report = health.build([], [], date(2026, 11, 21), month_replies=850)
    assert "Replies this month: 850" in report
    assert "Replies this month" not in health.build([], [], date(2026, 11, 21))
