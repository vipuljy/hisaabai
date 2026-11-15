"""HisaabAI webhook: receives WhatsApp messages from Meta and hands each one to the Bot.

Run from the project root:  python -m uvicorn bot.app:app --port 8000

Settings come from .env (see .env.example):
  DATABASE_URL set   -> data in Postgres / Supabase
  DATABASE_URL empty -> data in memory (lost on restart; fine for the test number)
  SUPABASE_URL + SUPABASE_SERVICE_KEY set -> bill photos in a private Supabase bucket, else data/raw/bills/
  PILOT_OPEN=1       -> any number that messages becomes a test shop (test number only)
  JOBS_TOKEN set     -> POST /jobs/daily (with header X-Jobs-Token) runs the daily jobs; a scheduler calls it
  REQUIRE_PERSISTENT_STORAGE=1 (render.yaml) -> /health says "down" (503) unless bills go to Postgres AND photos to
                       Supabase: Render wipes its disk on every restart, so a deploy without them must not go live

Who can do what:
  GET  /, /health    anyone; says only "ok" or "down", never data
  GET  /webhook      Meta, once, proving it knows VERIFY_TOKEN
  POST /webhook      Meta only: every request must carry Meta's signature made with APP_SECRET
  POST /jobs/daily   only a caller holding JOBS_TOKEN
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from functools import partial

from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hisaabai")
IST = timezone(timedelta(hours=5, minutes=30))


def signature_is_valid(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Meta signs every webhook with the app secret. Skipped only if no secret is configured."""
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _env_number(name, default, cast=int):
    try:
        return cast(os.getenv(name, "").strip() or default)
    except ValueError:
        log.warning("%s in .env is not a number; using %s", name, default)
        return default


def build_reader(policy: str | None = None):
    """How bill photos are read (READ_POLICY in .env, default P5_no_ocr, chosen in docs/04_decisions_log.md):
      haiku    one Haiku read (BILL_MODEL), no checks: the old behaviour
      P1/P2/P3 Haiku, free checks on its answer, Sonnet (ESCALATION_MODEL) re-reads when a check fails;
               P3 also flags total / date when the two readers still disagree (the owner checks them).
      P5_no_ocr      (default, live on Render free) = P5 without the OCR checks; the OCR is never run
      P5_trusted_ocr (Oracle option) = P5, but the OCR total counts only when it is proved (words = digits / maths)
      P5       "money first": Sonnet only when a check on the TOTAL fails; date / invoice doubts
               are asked to the owner (date: one tap)
    The free OCR check (RapidOCR) is used when it is installed (requirements-ocr-bot.txt; in the Docker image) and OCR_CHECK is not 0."""
    from bot.extract import extract_bill
    from bot.policy import DEFAULT_POLICY, PolicyReader

    policy = policy or DEFAULT_POLICY
    prompt = os.getenv("BILL_PROMPT", "v2")
    haiku = partial(extract_bill, model=os.getenv("BILL_MODEL", "claude-haiku-5-5"), prompt_version=prompt)
    if policy == "haiku":
        return haiku
    sonnet = partial(extract_bill, model=os.getenv("ESCALATION_MODEL", "claude-sonnet-5-5"), prompt_version=prompt)
    from bot.policy import OCR_RULES, POLICIES
    ocr = None
    if os.getenv("OCR_CHECK", "1").strip() != "0" and POLICIES.get(policy, set()) & OCR_RULES:
        from bot import read_ocr
        if read_ocr.default_engine() == "rapidocr":
            def ocr(image_bytes):
                ex = read_ocr.read_ocr(image_bytes, engine="rapidocr")
                return None if ex.error else {"fields": ex.fields, "raw_text": ex.raw_text,
                                              "total_proof": (ex.extra or {}).get("total_proof")}
        else:
            log.info("RapidOCR not installed: the OCR checks (a, b) are skipped")
    return PolicyReader(policy, haiku=haiku, sonnet=sonnet, ocr=ocr)


def bot_from_env():
    from bot.conversation import Bot, Limits
    from bot.photos import photos_from_env
    from bot.store import MemoryStore, PgStore
    from bot.whatsapp import WhatsAppClient

    db_url = os.getenv("DATABASE_URL", "").strip()
    store = PgStore(db_url) if db_url else MemoryStore()
    if not db_url:
        log.warning("DATABASE_URL not set: using in-memory store (data is lost on restart)")
    wa = WhatsAppClient(os.getenv("WHATSAPP_TOKEN", ""), os.getenv("PHONE_NUMBER_ID", ""),
                        os.getenv("GRAPH_VERSION", "v23.0"))
    extractor = build_reader(os.getenv("READ_POLICY", "").strip() or None)
    transcriber = entry_parser = None
    if os.getenv("SARVAM_API_KEY", "").strip():
        from bot.voice import SarvamTranscriber, llm_entry_parser
        transcriber = SarvamTranscriber(os.getenv("SARVAM_API_KEY").strip())
        if os.getenv("VOICE_LLM", "1").strip() != "0":  # the Haiku fallback for voice notes the rules cannot read
            entry_parser = partial(llm_entry_parser, model=os.getenv("BILL_MODEL", "claude-haiku-5-5"))
    question_llm = None
    if os.getenv("QA_LLM", "").strip() == "1" and os.getenv("ANTHROPIC_API_KEY", "").strip():
        from bot.questions import llm_question_parser
        question_llm = partial(llm_question_parser, model=os.getenv("BILL_MODEL", "claude-haiku-5-5"))
    limits = Limits(per_shop_per_day=_env_number("AI_CALLS_PER_SHOP_PER_DAY", 60),
                    total_per_day=_env_number("AI_CALLS_PER_DAY", 1000),
                    max_image_mb=_env_number("MAX_IMAGE_MB", 8.0, float),
                    max_pdf_mb=_env_number("MAX_PDF_MB", 10.0, float))
    if not os.getenv("APP_SECRET", "").strip():
        log.warning("APP_SECRET not set: webhook signatures are NOT checked. Fine for a first test, not for the pilot.")
    return Bot(store, wa, extractor, pilot_open=os.getenv("PILOT_OPEN", "") == "1",
               transcriber=transcriber, entry_parser=entry_parser, photos=photos_from_env(), limits=limits,
               question_llm=question_llm, timing_events=True)


def storage_problem(b) -> str | None:
    """With REQUIRE_PERSISTENT_STORAGE=1: why data would be lost on a host that wipes its disk (Render), or None."""
    if os.getenv("REQUIRE_PERSISTENT_STORAGE", "").strip() != "1":
        return None
    from bot.photos import SupabasePhotos
    from bot.store import PgStore
    if not isinstance(b.store, PgStore):
        return "DATABASE_URL not set: bills would live in memory and vanish on restart"
    if not isinstance(b.photos, SupabasePhotos):
        return "SUPABASE_URL / SUPABASE_SERVICE_KEY not set: photos would be written to a disk that is wiped"
    return None


def create_app(bot=None, verify_token: str | None = None, app_secret: str | None = None,
               jobs_token: str | None = None) -> FastAPI:
    verify_token = os.getenv("VERIFY_TOKEN", "") if verify_token is None else verify_token
    app_secret = os.getenv("APP_SECRET", "") if app_secret is None else app_secret
    jobs_token = os.getenv("JOBS_TOKEN", "").strip() if jobs_token is None else jobs_token
    app = FastAPI(title="HisaabAI webhook")
    app.state.bot = bot

    def get_bot():
        if app.state.bot is None:
            app.state.bot = bot_from_env()
        return app.state.bot

    @app.get("/")
    def root() -> dict:
        return {"status": "ok", "service": "hisaabai-webhook"}

    @app.get("/health")
    def health():
        """For the host's health check: is the app up and can it reach the database?"""
        try:
            b = get_bot()
            db_ok = bool(b.store.ping())
            problem = storage_problem(b)
        except Exception:
            log.exception("health check: database unreachable")
            db_ok, problem = False, None
        if problem:
            log.error("health check: %s", problem)
            return JSONResponse({"status": "down", "db": "ok" if db_ok else "down", "storage": problem}, status_code=503)
        return JSONResponse({"status": "ok" if db_ok else "down", "db": "ok" if db_ok else "down"},
                            status_code=200 if db_ok else 503)

    @app.post("/jobs/daily")
    async def daily_jobs(request: Request):
        """Called once a day by a scheduler. Needs the X-Jobs-Token header; disabled if JOBS_TOKEN is empty."""
        if not jobs_token:
            raise HTTPException(status_code=404)
        given = request.headers.get("X-Jobs-Token", "")
        if not hmac.compare_digest(given.encode(), jobs_token.encode()):
            raise HTTPException(status_code=401, detail="Bad token")
        from bot.jobs import run_all
        b = get_bot()
        dry_run = request.query_params.get("dry_run") == "1"
        today = datetime.now(IST).date()
        counts = await run_all(b.store, b.wa, b.photos, today, dry_run=dry_run,
                               language=os.getenv("TEMPLATE_LANGUAGE", "en"))
        log.info("daily jobs %s: %s", "dry run" if dry_run else "done", counts)
        return {"date": today.isoformat(), "dry_run": dry_run, **counts}

    @app.get("/webhook")
    def verify(request: Request):
        """Meta calls this once when you save the Callback URL in the dashboard."""
        p = request.query_params
        if verify_token and p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == verify_token:
            return PlainTextResponse(p.get("hub.challenge", ""))
        raise HTTPException(status_code=403, detail="Verification failed")

    @app.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        raw = await request.body()
        if not signature_is_valid(app_secret, raw, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=401, detail="Bad signature")
        data = json.loads(raw or b"{}")
        b = get_bot()
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                names = {c.get("wa_id"): ((c.get("profile") or {}).get("name") or "")
                         for c in value.get("contacts", []) or []}
                for message in value.get("messages", []):
                    # the WhatsApp profile name (often the shop's name): a suggestion only, the owner confirms it
                    message["_profile_name"] = names.get(message.get("from"), "")
                    background.add_task(_safe_handle, b, message)
        return {"received": True}  # answer Meta fast; it retries if we are slow

    return app


async def _safe_handle(b, message):
    try:
        await b.handle(message)
    except Exception:
        log.exception("message handling failed (type=%s)", message.get("type"))


app = create_app()
