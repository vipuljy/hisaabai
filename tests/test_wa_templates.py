"""The 3 WhatsApp templates (bot/wa_templates.py): Meta's limits, the same variable counts that bot/jobs.py fills,
the same text as docs/10_templates.md, the submit script, and a tap on the template button. Runs on both stores."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from conftest import OWNER, onboard, photo, tap

from bot import jobs
from bot import wa_templates as W
from bot.check_setup import TEMPLATES
from bot.jobs import IST, run_daily

ROOT = Path(__file__).resolve().parents[1]
WANT_VARS = {"due_reminder": 4, "weekly_summary": 6, "owner_reminder": 2}
TOKEN = "EAAtestTOKENtestTOKENtestTOKEN1234"  # secret-scan: ignore (a fake token for the test)


def test_names_match_jobs_and_setup_check():
    assert set(W.SPECS) == set(WANT_VARS) == set(TEMPLATES)
    assert {jobs.TEMPLATE_REMINDER, jobs.TEMPLATE_SUMMARY, jobs.TEMPLATE_OWNER_REMINDER} == set(W.SPECS)


@pytest.mark.parametrize("name", list(WANT_VARS))
def test_meta_rules(name):
    spec, n = W.SPECS[name], WANT_VARS[name]
    body = spec["body"]
    assert W.variables(body) == list(range(1, n + 1))           # numbered in order, each once
    assert len(spec["example"]) == n and all(spec["example"])    # a sample for every variable
    assert not body.lstrip().startswith("{{") and not body.rstrip().endswith("}}")
    assert len(body) <= 1024 and len(spec["header"]) <= 60 and len(W.FOOTER) <= 60 and len(W.DUES_BUTTON) <= 25
    for fixed in (spec["header"], W.FOOTER, W.DUES_BUTTON):     # header, footer, button: plain text, no variables
        assert fixed.isascii() and "{{" not in fixed and "*" not in fixed and "\n" not in fixed
    assert "STOP" in W.FOOTER                                      # every message says how to stop
    for word in ("offer", "discount", "free", "sale"):           # Utility only: nothing promotional
        assert word not in body.lower()
    assert "vahi" not in body.lower() and "vahi" not in spec["header"].lower()   # a rename needs no new templates


@pytest.mark.parametrize("name", list(WANT_VARS))
def test_docs_show_the_same_text(name):
    doc = (ROOT / "docs" / "10_templates.md").read_text(encoding="utf-8")
    spec = W.SPECS[name]
    assert f"`{name}` ({WANT_VARS[name]} variables)" in doc
    assert spec["body"] in doc and spec["header"] in doc and W.FOOTER in doc and W.DUES_BUTTON in doc


def _sent(world, name):
    return [m for m in world.wa.sent if m["kind"] == "template" and m["name"] == name]


def test_jobs_fill_exactly_the_template_variables(world):
    onboard(world)
    today = __import__("datetime").datetime.now(IST).date()
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580,
                         "due_date": (sunday + timedelta(days=2)).isoformat()}
    world.run(photo())
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("ok:"))))
    shop = world.store.get_shop_by_wa(OWNER)
    world.store.update_shop(shop["id"], experiment_group="treatment")
    world.store.add_reminder(shop["id"], None, "hisaab dekhna", sunday)
    asyncio.run(run_daily(world.store, world.wa, sunday))
    for name, n in WANT_VARS.items():
        sent = _sent(world, name)
        assert sent, name
        assert len(sent[-1]["params"]) == n and all(str(p).strip() for p in sent[-1]["params"])


def test_template_button_tap_shows_all_dues(world):
    onboard(world)
    world.reader.next = {"supplier_name": "Sharma Traders", "total_amount": 4580}
    world.run(photo())
    world.run(tap(next(b for b in world.wa.button_ids() if b.startswith("ok:"))))
    world.run({"id": "wamid.tpl1", "from": OWNER, "type": "button",
               "button": {"text": W.DUES_BUTTON, "payload": W.DUES_BUTTON}})
    assert "Sharma Traders" in world.wa.last["body"] and "₹4,580" in world.wa.last["body"]
    assert W.button_as_text("  sabka   BAAKI dekhein ") == "sabka kitna baaki hai"
    assert W.button_as_text("Something else") == "Something else"


def _meta(existing):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": existing})
        body = json.loads(request.content)
        if body.get("name") == "weekly_summary":
            return httpx.Response(400, json={"error": {"message": f"bad token {TOKEN}",
                                                       "error_user_msg": f"Too many variables {TOKEN}"}})
        return httpx.Response(200, json={"id": "1", "status": "PENDING", "category": "UTILITY"})

    return calls, httpx.Client(transport=httpx.MockTransport(handler))


def test_submit_script_creates_skips_and_redacts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("create_templates", ROOT / "scripts" / "create_templates.py")
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    env = {"WHATSAPP_TOKEN": TOKEN, "WABA_ID": "123"}
    calls, http = _meta([{"id": "9", "name": "owner_reminder", "status": "APPROVED", "language": "en"}])
    lines = []
    code = ct.submit(env, http, "en", out=lines.append)
    out = "\n".join(lines)
    assert code == 1 and TOKEN not in out
    assert "OK    due_reminder: submitted (PENDING)" in out
    assert "FAIL  weekly_summary" in out and "SKIP  owner_reminder" in out
    posted = [json.loads(c.content) for c in calls if c.method == "POST"]
    assert [p["name"] for p in posted] == ["due_reminder", "weekly_summary"]
    assert posted[0]["category"] == "UTILITY" and posted[0]["language"] == "en"
    assert posted[0]["components"][1]["example"]["body_text"] == [W.SPECS["due_reminder"]["example"]]
    # a rejected template is edited in place, not created again
    calls, http = _meta([{"id": "77", "name": "due_reminder", "status": "REJECTED"},
                         {"id": "78", "name": "weekly_summary", "status": "PENDING"},
                         {"id": "79", "name": "owner_reminder", "status": "APPROVED"}])
    lines = []
    assert ct.submit(env, http, "en", out=lines.append) == 0
    assert [c.url.path for c in calls if c.method == "POST"] == ["/v23.0/77"]
    assert "OK    due_reminder: resubmitted" in "\n".join(lines)
    assert ct.submit({"WABA_ID": "1"}, http, "en", out=lines.append) == 1
