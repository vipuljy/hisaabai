"""One shared Anthropic client for every AI call: bill photos, voice fallbacks, the question classifier.

Why (21 Nov 2026, docs/04_decisions_log.md): every call used to build its own client, so every bill opened a new
connection (TLS handshake included), and the first AI call after a restart also loaded the anthropic library
(about 0.8 s on a laptop, several times that on Render's free CPU) while the owner waited. Now the library is loaded
and the client built once, in the background, when the bot starts (`warm`), and connections are kept open between
messages.

Effort (`effort_body`): Haiku 5.5 / Sonnet 5.5 think before answering; the effort setting tells them how much.
Unset = the model's default (Haiku: medium, Sonnet: high). Set per step with BILL_EFFORT / ESCALATION_EFFORT only
after `RUN_eval_speed.bat` shows accuracy holds (the rule is in docs/08_eval.md).
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("hisaabai")

KEEPALIVE_S = 300          # keep an idle connection to the API for 5 minutes (httpx default: 5 seconds)
EFFORTS = ("low", "medium", "high", "off")   # off = no thinking at all (Haiku only)

_client = None
_lock = threading.Lock()


def get():
    """The shared anthropic.Anthropic() (reads ANTHROPIC_API_KEY). Built on first use; thread-safe."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                import anthropic
                import httpx
                _client = anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10,
                                        keepalive_expiry=KEEPALIVE_S)))
    return _client


def warm() -> bool:
    """Load the library and build the client before an owner waits for it. Never raises; False if not possible
    (no ANTHROPIC_API_KEY, e.g. in tests or the simulator)."""
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return False
    try:
        get()
        return True
    except Exception as err:  # noqa: BLE001  a warm-up must never stop the bot
        log.warning("AI client warm-up failed: %s", type(err).__name__)
        return False


def warm_in_background() -> None:
    threading.Thread(target=warm, name="ai-warm", daemon=True).start()


def check_effort(value: str | None) -> str | None:
    """'' or None -> None (model default); a valid level -> itself; anything else -> ValueError."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if v not in EFFORTS:
        raise ValueError(f"effort must be one of {', '.join(EFFORTS)} (or empty), got {value!r}")
    return v


def effort_body(effort: str | None) -> dict:
    """Extra request fields for an effort level. Sent as extra_body, so it works with any SDK version."""
    effort = check_effort(effort)
    if effort is None:
        return {}
    if effort == "off":
        return {"thinking": {"type": "disabled"}}
    return {"output_config": {"effort": effort}}
