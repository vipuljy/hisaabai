"""Answers to the owner's questions: one database query each, never AI.

answer() takes an intent that bot/questions.py (or a menu tap) already understood, runs the
matching store query, and formats it with bot/messages.py. Every answer names its basis
(how many bills / payments, which dates), and an empty result says so with the period
("Is hafte (20–25 Oct) koi payment nahi"), never a bare ₹0.
"""

from __future__ import annotations

from datetime import date

from bot import messages as M
from bot.questions import Period

READ_ONLY = ("dues_all", "dues_supplier", "paid_period", "bills_period", "top_dues", "last_payment", "list_reminders",
             "period_summary")


def answer(store, shop_id, intent: str, today: date, supplier: dict | None = None,
           period: Period | None = None) -> str:
    """Text for a read-only question. The caller has already made sure the needed slots are filled."""
    sid = supplier["id"] if supplier else None
    name = supplier["name"] if supplier else None
    if intent == "dues_all":
        rows = store.dues(shop_id)
        return M.dues_all(rows) if rows else M.NO_DUES
    if intent == "dues_supplier":
        summary = store.supplier_summary(shop_id, sid)
        return M.supplier_dues(summary, today) if summary else M.NO_SUPPLIERS
    if intent == "paid_period":
        rows = store.payments_between(shop_id, period.start, period.end, sid)
        return M.paid_answer(rows, M.period_label(period.key, period.start, period.end), name)
    if intent == "bills_period":
        rows = store.bills_between(shop_id, period.start, period.end, sid)
        return M.bills_answer(rows, M.period_label(period.key, period.start, period.end), name)
    if intent == "period_summary":   # 'aaj ka hisaab': the day's bills and payments, and where the baaki stands
        label = M.period_label(period.key, period.start, period.end)
        return M.period_summary(label, store.bills_between(shop_id, period.start, period.end),
                                store.payments_between(shop_id, period.start, period.end), store.dues(shop_id))
    if intent == "top_dues":
        return M.top_dues(store.dues(shop_id))
    if intent == "last_payment":
        return M.last_payment(store.last_payment(shop_id, sid), name, today)
    if intent == "list_reminders":
        return M.reminders_list(store.list_reminders(shop_id, today))
    raise ValueError(f"not a read-only question: {intent}")
