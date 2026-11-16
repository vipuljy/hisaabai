"""Keys must never reach a log, and a key pasted with a line break must be caught before it breaks every reply.

16 Nov: WHATSAPP_TOKEN on Render was pasted together with the next .env line ("...\\nWABA_ID=..."). Every reply
failed, and the error printed the whole token into Render's logs. Now:
- `pasted_wrong()`: names of keys with a space or line break in them (shown by /health and RUN_check_setup.bat),
- `RedactFilter`: a logging filter that replaces every key value (and anything after "Bearer ") with ***.
"""

import logging
import os
import re
import traceback
from urllib.parse import urlparse

SECRET_ENV = ("WHATSAPP_TOKEN", "APP_SECRET", "VERIFY_TOKEN", "ANTHROPIC_API_KEY", "SARVAM_API_KEY", "DATABASE_URL",
              "SUPABASE_SERVICE_KEY", "JOBS_TOKEN")
NO_SPACES = SECRET_ENV + ("PHONE_NUMBER_ID", "WABA_ID", "SUPABASE_URL")
_BEARER = re.compile(r"(Bearer\s+)[^\s'\"]+")
_META_TOKEN = re.compile(r"\bEAA[A-Za-z0-9]{20,}")


def pasted_wrong(env=None) -> list[str]:
    """Keys whose value holds a space or a line break: almost always two .env lines pasted into one box."""
    env = os.environ if env is None else env
    return [k for k in NO_SPACES if any(c.isspace() for c in (env.get(k) or ""))]


def _secrets(env) -> list[str]:
    values = []
    for key in SECRET_ENV:
        raw = env.get(key) or ""
        values += [v.strip() for v in [raw] + raw.splitlines()]
    pw = urlparse((env.get("DATABASE_URL") or "").strip()).password
    values.append(pw or "")
    return sorted({v for v in values if len(v) >= 6}, key=len, reverse=True)


def redact(text: str, env=None) -> str:
    env = os.environ if env is None else env
    for value in _secrets(env):
        text = text.replace(value, "***")
    text = _BEARER.sub(r"\1***", text)
    return _META_TOKEN.sub("EAA***", text)


class RedactFilter(logging.Filter):
    """On a log handler: the message and any traceback are cleaned before they are written."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg, record.args = redact(record.getMessage()), ()
            if record.exc_info:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info))).rstrip("\n")
                record.exc_info = None
        except Exception:  # noqa: BLE001  logging must never fail
            pass
        return True


def install():
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())
