"""The 3 WhatsApp message templates, exactly as submitted to Meta (docs/10_templates.md).

One place for the wording: scripts/create_templates.py submits these, bot/jobs.py fills the variables, and
tests/test_wa_templates.py checks that the two agree (names, variable counts, Meta's limits).

The text is Hinglish in Latin script, like the rest of the bot (bot/messages.py). It carries no brand name, so
renaming the WhatsApp display name never needs new templates.

Each template has one quick-reply button. A tap arrives as a message of type "button" with the button's text;
bot/conversation.py turns it into the words in BUTTON_AS_TEXT, so a tap works exactly like typing them.
"""

from __future__ import annotations

import re

CATEGORY = "UTILITY"          # transactional only: no offers, no promotion (Marketing costs about 7x more)
FOOTER = "Band karne ke liye STOP likhein"
DUES_BUTTON = "Sabka baaki dekhein"

# What a tap on a template button means, as if the owner had typed it ('sabka kitna baaki' = every supplier's dues)
BUTTON_AS_TEXT = {DUES_BUTTON.lower(): "sabka kitna baaki hai"}

SPECS = {
    "due_reminder": {
        "header": "Payment reminder",
        "body": ("Namaste 🙏\n\n"
                 "*{{1}}* ka *{{2}}* ka bill *{{3}}* ko due hai.\n"
                 "Is supplier ka kul baaki: *{{4}}*\n\n"
                 "Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega."),
        "example": ["Sharma Traders", "₹4,580", "20 Nov 2026", "₹6,200"],
    },
    "weekly_summary": {
        "header": "Is hafte ka hisaab",
        "body": ("Namaste 🙏 Aapka hisaab, {{1}}:\n\n"
                 "🧾 Bills: {{2}} (kul {{3}})\n"
                 "💸 Payments: {{4}}\n"
                 "📒 Abhi kul baaki: *{{5}}*\n"
                 "📌 Sabse zyada baaki: {{6}}\n\n"
                 "Kisi ek supplier ka hisaab chahiye toh uska naam likh ke poochhiye."),
        "example": ["16 Nov 2026 - 22 Nov 2026", "7", "₹18,450", "₹9,000", "₹21,300", "Sharma Traders (₹8,200)"],
    },
    "owner_reminder": {
        "header": "Aapka reminder",
        "body": ("Namaste 🙏 Aapne aaj ke liye yaad dilane ko kaha tha:\n"
                 "*{{1}}*\n\n"
                 "Abhi baaki: *{{2}}*\n\n"
                 "Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega."),
        "example": ["Sharma Traders", "₹12,400"],
    },
}

VAR_RE = re.compile(r"\{\{(\d+)\}\}")


def variables(body: str) -> list[int]:
    return [int(n) for n in VAR_RE.findall(body)]


def components(name: str) -> list[dict]:
    """The template in the shape Meta's message_templates API takes."""
    spec = SPECS[name]
    return [
        {"type": "HEADER", "format": "TEXT", "text": spec["header"]},
        {"type": "BODY", "text": spec["body"], "example": {"body_text": [spec["example"]]}},
        {"type": "FOOTER", "text": FOOTER},
        {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": DUES_BUTTON}]},
    ]


def payload(name: str, language: str = "en") -> dict:
    return {"name": name, "language": language, "category": CATEGORY, "components": components(name)}


def preview(name: str) -> str:
    """The message as an owner would see it, with the sample values filled in."""
    spec = SPECS[name]
    body = VAR_RE.sub(lambda m: spec["example"][int(m.group(1)) - 1], spec["body"])
    return f"*{spec['header']}*\n{body}\n_{FOOTER}_\n[ {DUES_BUTTON} ]"


def button_as_text(label: str) -> str:
    """A template quick-reply tap -> the words it stands for (unknown labels pass through unchanged)."""
    label = re.sub(r"\s+", " ", label or "").strip()
    return BUTTON_AS_TEXT.get(label.lower(), label)
