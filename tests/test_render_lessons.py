"""16 Nov, first day on Render: a voice note with the amount said twice, 'data delete kro', and a token pasted
together with the next .env line (every reply failed and the error printed the token)."""

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from bot import entries as E
from bot import redact
from bot.app import create_app
from bot.conversation import DELETE_DATA_RE

SUPS = [{"id": "s", "name": "Sharma Traders", "name_norm": "sharma traders"}]
FAKE = "EAAfakefakefakefakefakefake1234567890"  # secret-scan: ignore


@pytest.mark.parametrize("msg", ["Sharma ko 200 diye 200", "sharma ko 200 diye. 200.", "sharma ko do sau diye 200",
                                 "Sharma ko 200 diye. Two hundred."])
def test_an_amount_said_twice_is_one_entry(msg):
    u = E.parse(msg, SUPS, date(2026, 11, 16))
    assert [(p.intent, p.amount) for p in u.parts] == [("payment", 200)]


def test_two_different_amounts_still_ask():
    u = E.parse("Sharma ko 200 diye aur 300", SUPS, date(2026, 11, 16))
    assert len(u.parts) == 2                       # 'aur 300': maybe another entry: unchanged


@pytest.mark.parametrize("msg", ["data delete kro", "mera data delete kro", "mera data delete krdo",
                                 "mera data delete kar do", "mera data delete karna", "data delete kar dijiye"])
def test_delete_my_data_as_owners_type_it(msg):
    assert DELETE_DATA_RE.match(msg)


@pytest.mark.parametrize("msg", ["last entry delete karo", "sharma ka bill delete kro"])
def test_deleting_one_entry_is_not_deleting_everything(msg):
    assert not DELETE_DATA_RE.match(msg)


def test_a_key_pasted_with_the_next_line_is_caught():
    env = {"WHATSAPP_TOKEN": FAKE + "\nWABA_ID=1600", "APP_SECRET": "abcdef123456", "PHONE_NUMBER_ID": "13466"}
    assert redact.pasted_wrong(env) == ["WHATSAPP_TOKEN"]
    assert redact.pasted_wrong({"WHATSAPP_TOKEN": FAKE}) == []


def test_health_fails_with_the_reason(monkeypatch, store):
    monkeypatch.setenv("WHATSAPP_TOKEN", FAKE + "\nWABA_ID=1600")

    class B:
        pass

    b = B()
    b.store, b.photos = store, None
    r = TestClient(create_app(bot=b, verify_token="v", app_secret="s")).get("/health")
    assert r.status_code == 503 and "WHATSAPP_TOKEN" in r.json()["config"] and FAKE not in r.text


def test_no_key_reaches_the_logs(monkeypatch, capsys):
    monkeypatch.setenv("WHATSAPP_TOKEN", FAKE + "\nWABA_ID=1600")
    logger = logging.getLogger("redact-test")
    handler = logging.StreamHandler()
    handler.addFilter(redact.RedactFilter())
    logger.addHandler(handler)
    try:
        try:
            raise ValueError(f"Illegal header value b'Bearer {FAKE}\\nWABA_ID=1600'")
        except ValueError:
            logger.exception("message handling failed (token %s)", FAKE)
    finally:
        logger.removeHandler(handler)
    err = capsys.readouterr().err
    assert "message handling failed" in err and "ValueError" in err and FAKE not in err and "EAAfake" not in err
