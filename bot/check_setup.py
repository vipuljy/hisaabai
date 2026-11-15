"""Checks every key and connection in .env, one line each. Never prints a secret.

  python -m bot.check_setup        (or double-click RUN_check_setup.bat)

  OK    works
  WARN  works, but needs attention before the pilot
  FAIL  broken: the line says how to fix it
  SKIP  not set up yet (fine until that phase)

The output is safe to paste to Claude: tokens, keys, passwords and phone numbers are never shown
(only the last 4 digits of the bot's own number).
"""

from __future__ import annotations

import io
import re
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = ("WHATSAPP_TOKEN", "APP_SECRET", "VERIFY_TOKEN", "ANTHROPIC_API_KEY", "SARVAM_API_KEY",
               "DATABASE_URL", "SUPABASE_SERVICE_KEY", "JOBS_TOKEN")
PLACEHOLDER_HINTS = ("paste-your", "make-up-any", "your-", "changeme", "xxxx")
TEMPLATES = ("due_reminder", "weekly_summary", "owner_reminder")
TABLES = ("shops", "suppliers", "messages", "bills", "bill_fields", "payments", "events", "reminders", "entry_edits")
NEW_COLUMNS = (("shops", "shop_name"), ("shops", "eval_consent"), ("bills", "photo_deleted_at"))


@dataclass
class Result:
    status: str  # OK, WARN, FAIL, SKIP
    name: str
    detail: str = ""


def is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return not v or any(h in v for h in PLACEHOLDER_HINTS)


def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _meta_error(resp: httpx.Response) -> tuple[int | None, str]:
    try:
        err = resp.json().get("error", {})
        return err.get("code"), str(err.get("message", ""))[:160]
    except ValueError:
        return None, resp.text[:160]


class Checker:
    def __init__(self, env: dict, http: httpx.Client | None = None, pg_connect=None, root: Path = ROOT):
        self.env = {k: (v or "").strip() for k, v in env.items()}
        self.http = http or httpx.Client(timeout=20)
        self.pg_connect = pg_connect
        self.root = root
        self.graph = f"https://graph.facebook.com/{self.get('GRAPH_VERSION') or 'v23.0'}"

    def get(self, key: str) -> str:
        return self.env.get(key, "")

    # ------------------------------------------------------------ WhatsApp / Meta
    def whatsapp(self) -> list[Result]:
        token, phone_id = self.get("WHATSAPP_TOKEN"), self.get("PHONE_NUMBER_ID")
        if is_placeholder(token) or is_placeholder(phone_id):
            return [Result("FAIL", "WhatsApp", "WHATSAPP_TOKEN or PHONE_NUMBER_ID is empty. Meta Steps 3-4.")]
        if not phone_id.isdigit():
            return [Result("FAIL", "WhatsApp", "PHONE_NUMBER_ID should be digits only: the 'Phone number ID' "
                                               "on API Setup, not the phone number.")]
        auth = {"Authorization": f"Bearer {token}"}
        try:
            resp = self.http.get(f"{self.graph}/{phone_id}", headers=auth,
                                 params={"fields": "display_phone_number,verified_name,quality_rating"})
        except httpx.HTTPError as err:
            return [Result("FAIL", "WhatsApp", f"cannot reach Meta ({type(err).__name__}). Check internet.")]
        if resp.status_code != 200:
            code, msg = _meta_error(resp)
            if code == 190:
                fix = "token expired or wrong. Generate a new one on API Setup, or do Step 6 (permanent token)."
            elif code == 100:
                fix = "PHONE_NUMBER_ID not found for this token. Copy 'Phone number ID' again from API Setup."
            else:
                fix = f"Meta said: {msg}"
            return [Result("FAIL", "WhatsApp", fix)]
        data = resp.json()
        digits = re.sub(r"\D", "", data.get("display_phone_number", ""))
        results = [Result("OK", "WhatsApp", f"token works; bot number ending {digits[-4:] or '????'}"
                                            f" ({data.get('verified_name', 'no name')})")]
        results.append(self._token_expiry(token))
        return results

    def _token_expiry(self, token: str) -> Result:
        try:
            resp = self.http.get(f"{self.graph}/debug_token", params={"input_token": token, "access_token": token})
            info = resp.json().get("data", {}) if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            info = {}
        if "expires_at" not in info:
            return Result("SKIP", "Token type", "could not read expiry (fine)")
        if info["expires_at"] == 0:
            return Result("OK", "Token type", "permanent (never expires)")
        import time
        hours = max(0, (info["expires_at"] - time.time()) / 3600)
        return Result("WARN", "Token type", f"temporary, expires in about {hours:.0f} h. Fine for testing; "
                                            "do Step 6 (System User token) before the pilot.")

    def webhook_settings(self) -> list[Result]:
        out = []
        vt = self.get("VERIFY_TOKEN")
        if is_placeholder(vt) or len(vt) < 8:
            out.append(Result("FAIL", "VERIFY_TOKEN", "make up a word of 8+ characters (e.g. hisaab-vipul-7391), "
                                                      "and type the same in Meta > Configuration."))
        else:
            out.append(Result("OK", "VERIFY_TOKEN", f"set ({len(vt)} characters)"))
        secret = self.get("APP_SECRET")
        if not secret:
            out.append(Result("WARN", "APP_SECRET", "empty: anyone could post fake messages to the webhook. "
                                                    "Copy it from App settings > Basic."))
        elif not re.fullmatch(r"[0-9a-f]{32}", secret):
            out.append(Result("FAIL", "APP_SECRET", "should be 32 letters/digits (0-9, a-f). Copy it again "
                                                    "from App settings > Basic > App secret > Show."))
        else:
            out.append(Result("OK", "APP_SECRET", "set; webhook signatures will be checked"))
        return out

    def templates(self) -> list[Result]:
        waba, token = self.get("WABA_ID"), self.get("WHATSAPP_TOKEN")
        if is_placeholder(waba):
            return [Result("SKIP", "Templates", "WABA_ID not set (optional: lets this check see approval status)")]
        try:
            resp = self.http.get(f"{self.graph}/{waba}/message_templates", headers={"Authorization": f"Bearer {token}"},
                                 params={"fields": "name,status,language", "limit": 200})
        except httpx.HTTPError as err:
            return [Result("FAIL", "Templates", f"cannot reach Meta ({type(err).__name__})")]
        if resp.status_code != 200:
            return [Result("FAIL", "Templates", f"Meta said: {_meta_error(resp)[1]}")]
        found = {t.get("name"): t for t in resp.json().get("data", [])}
        out = []
        for name in TEMPLATES:
            t = found.get(name)
            if t is None:
                out.append(Result("WARN", f"Template {name}", "not submitted yet (docs/10_templates.md)"))
            elif t.get("status") == "APPROVED":
                out.append(Result("OK", f"Template {name}", f"approved ({t.get('language', '?')})"))
            elif t.get("status") == "REJECTED":
                out.append(Result("FAIL", f"Template {name}", "rejected. Tell Claude; we reword and resubmit."))
            else:
                out.append(Result("WARN", f"Template {name}", f"status {t.get('status', '?')} (waiting for Meta)"))
        return out

    # ------------------------------------------------------------ AI services
    def anthropic(self) -> list[Result]:
        key, model = self.get("ANTHROPIC_API_KEY"), self.get("BILL_MODEL") or "claude-haiku-5-5"
        if is_placeholder(key):
            return [Result("SKIP", "Anthropic", "ANTHROPIC_API_KEY not set (needed for bill reading, Phase 2)")]
        try:
            # 1-token request: proves key, model name and credits in one call (costs a tiny fraction of a rupee)
            resp = self.http.post("https://api.anthropic.com/v1/messages",
                                  headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                  json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]})
        except httpx.HTTPError as err:
            return [Result("FAIL", "Anthropic", f"cannot reach the API ({type(err).__name__})")]
        if resp.status_code == 200:
            return [Result("OK", "Anthropic", f"key works, model {model} answers, credits available")]
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        kind, msg = err.get("type", ""), str(err.get("message", ""))
        if resp.status_code == 401 or kind == "authentication_error":
            fix = "key wrong or deleted. Create a new key in the Anthropic console > API keys."
        elif "credit" in msg.lower():
            fix = "no credits. Add about $5 in the console (Billing), and set a monthly spend limit."
        elif resp.status_code == 404 or kind == "not_found_error":
            fix = f"model '{model}' not found. Check BILL_MODEL in .env."
        elif resp.status_code == 429:
            return [Result("WARN", "Anthropic", "key works but rate-limited right now; try again in a minute")]
        else:
            fix = f"API said ({resp.status_code}): {msg[:120]}"
        return [Result("FAIL", "Anthropic", fix)]

    def sarvam(self) -> list[Result]:
        key = self.get("SARVAM_API_KEY")
        if is_placeholder(key):
            return [Result("SKIP", "Sarvam (voice)", "SARVAM_API_KEY not set; voice notes get a 'please type' reply")]
        try:
            resp = self.http.post("https://api.sarvam.ai/speech-to-text", headers={"api-subscription-key": key},
                                  data={"model": "saaras:v3", "language_code": "hi-IN", "mode": "translit"},
                                  files={"file": ("check.wav", silent_wav(), "audio/wav")})
        except httpx.HTTPError as err:
            return [Result("FAIL", "Sarvam (voice)", f"cannot reach Sarvam ({type(err).__name__})")]
        if resp.status_code in (401, 403):
            return [Result("FAIL", "Sarvam (voice)", "key rejected. Copy it again from the Sarvam dashboard.")]
        if resp.status_code == 429:
            return [Result("WARN", "Sarvam (voice)", "key works but rate-limited or out of credits")]
        if resp.status_code >= 500:
            return [Result("WARN", "Sarvam (voice)", f"Sarvam is having trouble ({resp.status_code}); try later")]
        return [Result("OK", "Sarvam (voice)", "key accepted")]

    # ------------------------------------------------------------ database and photos
    def database(self) -> list[Result]:
        url = self.get("DATABASE_URL")
        if is_placeholder(url):
            return [Result("SKIP", "Database", "DATABASE_URL not set: the bot keeps data in memory (test number only)")]
        out = []
        host = urlparse(url).hostname or ""
        if host.startswith("db.") and host.endswith("supabase.co"):
            out.append(Result("WARN", "Database URL", "this is the direct connection, which often fails on home "
                                                      "internet. Use Supabase > Connect > Session pooler URI."))
        try:
            connect = self.pg_connect
            if connect is None:
                import psycopg
                connect = lambda u: psycopg.connect(u, connect_timeout=10, prepare_threshold=None)  # noqa: E731
            with connect(url) as conn:
                tables = {r[0] for r in conn.execute(
                    "select table_name from information_schema.tables where table_schema = 'public'").fetchall()}
                cols = {(r[0], r[1]) for r in conn.execute(
                    "select table_name, column_name from information_schema.columns "
                    "where table_schema = 'public'").fetchall()}
                n_shops = conn.execute("select count(*) from shops").fetchone()[0] if "shops" in tables else 0
        except Exception as err:
            text = str(err).lower()
            if "password" in text:
                fix = "wrong database password. Supabase > Project settings > Database > reset it, then update .env."
            elif "could not translate host" in text or "name or service" in text:
                fix = "host not found. Copy the Session pooler URI again."
            else:
                fix = f"cannot connect ({type(err).__name__})."
            return out + [Result("FAIL", "Database", fix)]
        missing = [t for t in TABLES if t not in tables]
        if missing:
            return out + [Result("FAIL", "Database", f"connected, but tables missing ({', '.join(missing)}). "
                                                     "Run RUN_setup_database.bat")]
        if any(c not in cols for c in NEW_COLUMNS):
            return out + [Result("FAIL", "Database", "connected, but the schema is older than the code. "
                                                     "Run RUN_setup_database.bat (safe: keeps data).")]
        return out + [Result("OK", "Database", f"connected, schema up to date, {n_shops} shop(s)")]

    def photo_storage(self) -> list[Result]:
        url, key = self.get("SUPABASE_URL"), self.get("SUPABASE_SERVICE_KEY")
        if is_placeholder(url) or is_placeholder(key):
            return [Result("SKIP", "Photo storage", "Supabase keys not set: photos stay in data/raw/bills/ "
                                                    "(fine on the laptop, needed before hosting)")]
        from bot.photos import SupabasePhotos
        store = SupabasePhotos(url, key, self.get("PHOTO_BUCKET") or "bill-photos", client=self.http)
        try:
            private = store.bucket_is_private()
        except Exception as err:
            text = str(err)
            if " 404" in text or "not found" in text.lower():
                return [Result("FAIL", "Photo storage", "bucket missing. Run RUN_setup_database.bat")]
            if " 401" in text or " 403" in text:
                return [Result("FAIL", "Photo storage", "SUPABASE_SERVICE_KEY rejected. Use the secret / "
                                                        "service_role key from Project settings > API keys.")]
            return [Result("FAIL", "Photo storage", f"cannot reach Supabase Storage ({type(err).__name__})")]
        if not private:
            return [Result("FAIL", "Photo storage", "bucket is PUBLIC: anyone with a link could see bills. "
                                                    "Supabase > Storage > bucket > Edit > turn Public off.")]
        return [Result("OK", "Photo storage", "private bucket ready")]

    # ------------------------------------------------------------ the rest
    def misc(self) -> list[Result]:
        out = []
        jt = self.get("JOBS_TOKEN")
        if not jt:
            out.append(Result("SKIP", "JOBS_TOKEN", "not set (needed only on the host, for the 10 am job)"))
        elif len(jt) < 24:
            out.append(Result("WARN", "JOBS_TOKEN", "too short; use 24+ random characters"))
        else:
            out.append(Result("OK", "JOBS_TOKEN", "set"))
        if self.get("PILOT_OPEN") == "1":
            out.append(Result("WARN", "PILOT_OPEN", "=1: anyone who messages becomes a shop. Fine on the test "
                                                    "number; set it empty for the pilot."))
        if not (self.root / ".env").exists():
            out.append(Result("FAIL", ".env file", "missing. Copy .env.example to .env and fill it in."))
        return out

    def local_bot(self) -> list[Result]:
        vt = self.get("VERIFY_TOKEN")
        try:
            self.http.get("http://localhost:8000/", timeout=3)
        except httpx.HTTPError:
            return [Result("SKIP", "Local bot", "not running (start RUN_STEP0_start_bot.bat to test it)")]
        try:
            resp = self.http.get("http://localhost:8000/webhook", timeout=5, params={
                "hub.mode": "subscribe", "hub.verify_token": vt, "hub.challenge": "4242"})
        except httpx.HTTPError as err:
            return [Result("FAIL", "Local bot", f"running but not answering ({type(err).__name__})")]
        if resp.status_code == 200 and resp.text == "4242":
            return [Result("OK", "Local bot", "running; it accepts your VERIFY_TOKEN (Meta's check will pass)")]
        return [Result("FAIL", "Local bot", "running but rejects VERIFY_TOKEN. Restart it after editing .env.")]

    def run_all(self) -> list[Result]:
        results = []
        for step in (self.misc, self.whatsapp, self.webhook_settings, self.templates, self.anthropic, self.sarvam,
                     self.database, self.photo_storage, self.local_bot):
            try:
                results += step()
            except Exception as err:  # a check must never crash the others
                results.append(Result("FAIL", step.__name__, f"check crashed ({type(err).__name__})"))
        return results


def redact(text: str, env: dict) -> str:
    """Last line of defence: no secret value can appear in printed output."""
    for key in SECRET_KEYS:
        value = (env.get(key) or "").strip()
        if len(value) >= 6:
            text = text.replace(value, "***")
    parsed = urlparse(env.get("DATABASE_URL") or "")
    if parsed.password and len(parsed.password) >= 4:
        text = text.replace(parsed.password, "***")
    return re.sub(r"\b91\d{10}\b", "91**********", text)


def report(results: list[Result], env: dict, out=print) -> int:
    for r in results:
        out(redact(f"{r.status:<5} {r.name:<22} {r.detail}", env))
    counts = {s: sum(r.status == s for r in results) for s in ("OK", "WARN", "FAIL", "SKIP")}
    out("")
    out(f"{counts['OK']} OK, {counts['WARN']} WARN, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP. "
        "These lines contain no secrets: paste them to Claude if anything is FAIL.")
    return 1 if counts["FAIL"] else 0


def main() -> int:
    from dotenv import dotenv_values

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    env_file = ROOT / ".env"
    env = {k: v for k, v in dotenv_values(env_file).items()} if env_file.exists() else {}
    return report(Checker(env).run_all(), env)


if __name__ == "__main__":
    sys.exit(main())
