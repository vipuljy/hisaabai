"""Checks for bill reading (bot/extract.py) and the eval (src/). No API calls: a fake model answers."""

import importlib.util
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bot import extract as ex

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- normalisers
@pytest.mark.parametrize("raw, want", [
    ("Rs. 1,234.50/-", 1235), ("₹4,580", 4580), (4580.0, 4580), ("1234.49", 1234),
    ("", None), (None, None), ("abc", None),
])
def test_norm_amount(raw, want):
    assert ex.norm_amount(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("05/10/2026", "2026-10-05"), ("5-10-26", "2026-10-05"), ("05.10.2026", "2026-10-05"),
    ("2026-10-05", "2026-10-05"), ("5 Oct 2026", "2026-10-05"), ("05-Oct-26", "2026-10-05"),
    ("31/02/2026", None), ("", None), ("tomorrow", None),
])
def test_norm_date_is_day_first(raw, want):
    assert ex.norm_date(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("M/s. Sharma & Sons Pvt Ltd", "sharma and sons"), ("SHARMA AND SONS", "sharma and sons"),
    ("Gupta Medical Agency.", "gupta medical agency"), ("  ", None),
])
def test_norm_supplier(raw, want):
    assert ex.norm_supplier(raw) == want


def test_norm_invoice_keeps_leading_zeros():
    assert ex.norm_invoice(" inv - 0231 ") == "INV-0231"


def test_parse_json_inside_fences():
    assert ex.parse_model_json('Here:\n```json\n{"total_amount": 10}\n```') == {"total_amount": 10}


# ---------------------------------------------------------------- fake model
def _png_bytes(w=3000, h=2000):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create with canned replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
                               usage=SimpleNamespace(input_tokens=1500, output_tokens=80))


def test_extract_bill_shrinks_image_and_cleans_fields():
    reply = json.dumps({"is_bill": True, "supplier_name": "Sharma Traders", "bill_date": "05/10/2026",
                        "invoice_number": "inv-2231", "total_amount": "4,580", "due_date": None,
                        "unsure_fields": ["invoice_number"]})
    client = FakeClient([reply])
    out = ex.extract_bill(_png_bytes(), model="m", prompt_version="v2", client=client)
    assert out.error is None
    assert out.fields["total_amount"] == 4580 and out.fields["bill_date"] == "2026-10-05"
    assert out.fields["unsure_fields"] == ["invoice_number"]
    sent = client.calls[0]["messages"][0]["content"][0]["source"]
    img = Image.open(io.BytesIO(__import__("base64").b64decode(sent["data"])))
    assert max(img.size) <= ex.MAX_SIDE and sent["media_type"] == "image/jpeg"


def test_extract_bill_survives_bad_json():
    out = ex.extract_bill(_png_bytes(10, 10), model="m", client=FakeClient(["sorry, cannot read"]))
    assert out.error and set(out.fields["unsure_fields"]) == set(ex.FIELDS)


# ---------------------------------------------------------------- scoring
def test_score_outcomes_and_silent_errors():
    import evalkit
    label = {"supplier_name": "Sharma Traders", "bill_date": "05/10/2026", "invoice_number": "INV-1",
             "total_amount": "4580", "due_date": ""}
    fields = {"supplier_name": "sharma traders", "bill_date": None, "invoice_number": "INV-2",
              "total_amount": 4850, "due_date": "2026-10-20", "unsure_fields": ["invoice_number"]}
    got = {s["field"]: s for s in evalkit.score_bill(label, fields)}
    assert got["supplier_name"]["outcome"] == "correct"
    assert got["bill_date"]["outcome"] == "missed" and not got["bill_date"]["silent_error"]
    assert got["invoice_number"]["outcome"] == "wrong" and not got["invoice_number"]["silent_error"]  # flagged
    assert got["total_amount"]["outcome"] == "wrong" and got["total_amount"]["silent_error"]  # digits swapped
    assert got["due_date"]["outcome"] == "invented" and got["due_date"]["silent_error"]


# ---------------------------------------------------------------- whole eval, end to end
def _load_script():
    spec = importlib.util.spec_from_file_location("eval01", ROOT / "src" / "01_eval_bill_reading.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_eval_end_to_end_with_cache(tmp_path):
    (tmp_path / "src").mkdir()
    shutil.copy(ROOT / "src" / "eval_config.json", tmp_path / "src" / "eval_config.json")
    cfg = json.loads((tmp_path / "src" / "eval_config.json").read_text())
    cfg["runs"] = cfg["runs"][:1]
    cfg["prices"] = {cfg["runs"][0]["model"]: {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 5.0}}
    cfg["usd_to_inr"] = 85
    (tmp_path / "src" / "eval_config.json").write_text(json.dumps(cfg))
    images = tmp_path / "data" / "eval" / "images"
    images.mkdir(parents=True)
    for b in ("B001", "B002"):
        (images / f"{b}.png").write_bytes(_png_bytes(40, 40))
    (tmp_path / "data" / "eval" / "labels.csv").write_text(
        "bill_id,image_file,bill_type,segment,supplier_name,bill_date,invoice_number,total_amount,due_date,notes\n"
        "B001,B001.png,printed,kirana,Sharma Traders,05/10/2026,INV-1,4580,,\n"
        "B002,B002.png,handwritten,medical,Gupta Agency,07/10/2026,77,1235,,\n", encoding="utf-8")
    right = json.dumps({"supplier_name": "Sharma Traders", "bill_date": "2026-10-05", "invoice_number": "INV-1",
                        "total_amount": 4580, "due_date": None, "unsure_fields": []})
    wrong_total = json.dumps({"supplier_name": "Gupta Agency", "bill_date": "2026-10-07", "invoice_number": "77",
                              "total_amount": 1325, "due_date": None, "unsure_fields": []})
    script = _load_script()
    client = FakeClient([right, wrong_total])
    assert script.main([], client=client, root=tmp_path) == 0
    rows = list(__import__("csv").DictReader(open(tmp_path / "outputs" / "eval" / "summary_by_run.csv")))
    assert rows[0]["all_fields_right"] == "0.5" and rows[0]["bills_with_silent_error"] == "0.5"
    assert float(rows[0]["cost_per_bill_inr"]) == pytest.approx((1500 * 1 + 80 * 5) / 1e6 * 85, abs=1e-3)
    # second run is fully cached: no new model calls
    assert script.main([], client=FakeClient([]), root=tmp_path) == 0


def test_load_labels_ignores_skip_rows(tmp_path):
    from src.evalkit import load_labels
    p = tmp_path / "labels.csv"
    p.write_text(
        "bill_id,image_file,bill_type,segment,supplier_name,bill_date,invoice_number,total_amount,due_date,notes\n"
        "B001,B001.jpg,printed,other,A,05/10/2026,1,100,,\n"
        "B002,B002.jpg,printed,other,,,,,,SKIP continuation of B001\n"
        "B003,B003.jpg,,other,,,,,,skip not a bill\n", encoding="utf-8")
    assert [r["bill_id"] for r in load_labels(p)] == ["B001"]


def test_eval_check_rejects_bad_labels(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    shutil.copy(ROOT / "src" / "eval_config.json", tmp_path / "src" / "eval_config.json")
    (tmp_path / "data" / "eval" / "images").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "labels.csv").write_text(
        "bill_id,image_file,bill_type,segment,supplier_name,bill_date,invoice_number,total_amount,due_date,notes\n"
        "B001,B001.png,typed,kirana,X,32/13/2026,1,abc,,\n", encoding="utf-8")
    assert _load_script().main(["--check"], root=tmp_path) == 1
    msg = capsys.readouterr().out
    assert "bill_type" in msg and "bill_date" in msg and "total_amount" in msg


# ---------------------------------------------------------------- supplier name: same rule for eval scoring
@pytest.mark.parametrize("truth, pred, outcome", [
    ("Navrang Enterprises", "navrang enterprises hisar", "correct"),       # city appended
    ("Saraswati Book Company", "saraswati book company bikaner", "correct"),
    ("Navrang Enterprises, Hisar", "navrang enterprises", "correct"),      # either way round
    ("Sharma Traders", "verma traders", "wrong"),                          # ratio 0.82: a different supplier
    ("Gupta Agency", "gupta agency pvt ltd", "correct"),                   # legal suffix already normalised away
    ("Gupta Medical Agency", "medical agency", "wrong"),                   # words not at the start
    ("Shree Ganesh Traders", "shree ganesh trader", "correct"),            # near-identical spelling (>= 0.9)
])
def test_supplier_scoring_tolerates_city_but_not_other_names(truth, pred, outcome):
    from evalkit import score_field
    assert score_field("supplier_name", truth, ex.norm_supplier(pred), [])["outcome"] == outcome


def test_other_fields_stay_strict():
    from evalkit import score_field
    assert score_field("invoice_number", "INV-2231", "INV-22310", [])["outcome"] == "wrong"
    assert score_field("total_amount", "4580", 4581, [])["outcome"] == "wrong"


# ---------------------------------------------------------------- adaptive thinking, truncation, re-reading errors
GOOD_REPLY = json.dumps({"is_bill": True, "supplier_name": "Sunrise Health Agency", "bill_date": "18/09/2026",
                         "invoice_number": "WH000731", "total_amount": 21390, "due_date": None, "unsure_fields": []})


class ThinkingClient:
    """A reply shaped like Haiku 5.5's: a thinking block (no text) + a text block, thinking tokens in usage."""

    def __init__(self, text=GOOD_REPLY, stop_reason="end_turn", output_tokens=900, thinking_tokens=820):
        self.text, self.stop, self.out, self.think = text, stop_reason, output_tokens, thinking_tokens
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="", signature="x"),
                     SimpleNamespace(type="text", text=self.text)],
            stop_reason=self.stop,
            usage=SimpleNamespace(input_tokens=2829, output_tokens=self.out,
                                  output_tokens_details=SimpleNamespace(thinking_tokens=self.think)))


def test_thinking_is_recorded_and_billed_as_output():
    client = ThinkingClient()
    out = ex.extract_bill(_png_bytes(), model="claude-haiku-5-5", client=client)
    assert out.error is None and out.fields["total_amount"] == 21390
    assert client.kwargs["max_tokens"] == 2000 and "temperature" not in client.kwargs
    assert out.stop_reason == "end_turn" and out.block_types == ["thinking", "text"]
    assert out.thinking_tokens == 820 and out.output_tokens == 900  # output_tokens includes thinking


def test_cut_off_reply_is_an_error_named_truncated():
    """B013: the token budget ran out (stop_reason max_tokens): never trust a half reply."""
    out = ex.extract_bill(_png_bytes(), model="m", client=ThinkingClient(
        text='{"is_bill": true, "supplier_name": "SUNRISE HEALTH AGENCY", "b', stop_reason="max_tokens"))
    assert out.error == "truncated" and out.fields["total_amount"] is None
    assert set(out.fields["unsure_fields"]) == set(ex.FIELDS)


def test_run_one_rereads_a_cached_error_once_and_keeps_good_ones(tmp_path):
    from evalkit import run_one
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "B013.jpg").write_bytes(_png_bytes())
    cache = tmp_path / "runs"
    cache.mkdir()
    (cache / "B013.json").write_text(json.dumps({"bill_id": "B013", "error": "bad_json: no JSON object in reply",
                                                 "fields": {}}), encoding="utf-8")
    label = {"bill_id": "B013", "image_file": "B013.jpg"}
    client = ThinkingClient()
    rec = run_one(label, tmp_path / "img", cache, "claude-haiku-5-5", "v2", client=client)
    assert client.calls == 1 and rec["error"] is None and rec["retried_after"].startswith("bad_json")
    assert rec["thinking_tokens"] == 820 and rec["stop_reason"] == "end_turn"
    again = run_one(label, tmp_path / "img", cache, "claude-haiku-5-5", "v2", client=client)
    assert client.calls == 1 and again["fields"]["total_amount"] == 21390  # good cache reused: no new call
    # a reply that fails again is re-read on the NEXT run only (one try per run)
    bad = ThinkingClient(stop_reason="max_tokens")
    (cache / "B014.json").write_text(json.dumps({"bill_id": "B014", "error": "truncated", "fields": {}}), encoding="utf-8")
    (tmp_path / "img" / "B014.jpg").write_bytes(_png_bytes())
    r1 = run_one({"bill_id": "B014", "image_file": "B014.jpg"}, tmp_path / "img", cache, "m", "v2", client=bad)
    assert bad.calls == 1 and r1["error"] == "truncated"


def test_summary_reports_thinking_and_text_tokens():
    from evalkit import summarise
    recs = [{"run": "haiku_v2", "input_tokens": 2800, "output_tokens": 900, "thinking_tokens": 820, "latency_ms": 3000,
             "error": None},
            {"run": "haiku_v2", "input_tokens": 2800, "output_tokens": 700, "thinking_tokens": 620, "latency_ms": 3000,
             "error": "truncated"}]
    scored = [{"run": "haiku_v2", "bill_id": b, "bill_type": "printed", "field": "total_amount", "outcome": "correct",
               "silent_error": False} for b in ("B1", "B2")]
    _, by_run = summarise(scored, recs, [{"name": "haiku_v2", "model": "claude-haiku-5-5", "prompt": "v2"}],
                          {"claude-haiku-5-5": {"input_usd_per_mtok": 0.10, "output_usd_per_mtok": 0.50}}, 95)
    r = by_run[0]
    assert r["avg_thinking_tokens"] == 720 and r["avg_text_tokens"] == 80 and r["truncated"] == 1
    # cost uses ALL output tokens (thinking included): (2800*0.10 + 800*0.50) / 1e6 * 95 per bill
    assert r["cost_per_bill_inr"] == round((2800 * 0.10 + 800 * 0.50) / 1e6 * 95, 3)
