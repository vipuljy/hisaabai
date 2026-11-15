"""Supplier memory: where did this supplier print the total last time?

After a bill is confirmed, learn() records the label next to the confirmed total ("net amount", "bill amount")
and how far down the page it was. On the next bill from the same supplier (same seller GSTIN, else same
normalised name), apply() looks for that label first. A remembered total is NEVER trusted on its own: it must
still pass a words or maths proof (bot/checks.py). Pure functions + a small in-memory index; the eval uses it
leave-one-out (a bill never learns from itself).
"""

from __future__ import annotations

import difflib
import re

from bot import field_rules
from bot.extract import norm_amount, norm_supplier


def _label_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def learn(text: str, total) -> dict | None:
    """{"label": "net amount", "pos": 0.83} from a confirmed bill's text, or None if the total is not on it."""
    target = norm_amount(total)
    lines = (text or "").splitlines()
    if target is None or not lines:
        return None
    for i in range(len(lines) - 1, -1, -1):  # totals sit near the bottom: take the last place it appears
        line = lines[i]
        if field_rules.BALANCE_RX.search(line):
            continue
        for m in field_rules.AMOUNT_RX.finditer(line):
            if norm_amount(field_rules.parse_amount(m.group(1))) != target:
                continue
            label = _label_text(line[: m.start()])
            if len(label.replace(" ", "")) < 3 and i > 0:
                label = _label_text(lines[i - 1])  # amount alone on its line: the label is just above
            label = " ".join(label.split()[-4:])
            if len(label.replace(" ", "")) >= 3:
                return {"label": label, "pos": round(i / len(lines), 3)}
    return None


def apply(text: str, template: dict) -> float | None:
    """The amount next to the remembered label, choosing the matching line closest to the remembered position."""
    lines = (text or "").splitlines()
    best = None
    for i, line in enumerate(lines):
        if field_rules.BALANCE_RX.search(line):
            continue
        words = _label_text(line)
        if not words:
            continue
        lab = template["label"]
        idx = words.find(lab)
        if idx < 0:
            ratio = difflib.SequenceMatcher(None, lab, words[: len(lab) + 4]).ratio()
            if ratio < 0.85:
                continue
        found = None
        m = re.search(r"(?i)" + r"\s*".join(re.escape(c) for c in lab.replace(" ", "")), line)
        start = m.end() if m else 0
        am = field_rules.AMOUNT_RX.search(line[start:])
        if am:
            found = am.group(1)
        else:
            for j in (i + 1, i + 2):
                if j < len(lines) and field_rules._pure_amount(lines[j].strip()):
                    found = field_rules._pure_amount(lines[j].strip())
                    break
        if found:
            dist = abs(i / len(lines) - template["pos"])
            if best is None or dist < best[0]:
                best = (dist, field_rules.parse_amount(found))
    return best[1] if best else None


def keys(gstin: str | None, supplier_name: str | None) -> list[str]:
    out = []
    if gstin:
        out.append(f"gstin:{gstin}")
    name = norm_supplier(supplier_name)
    if name:
        out.append(f"name:{name}")
    return out


class SupplierMemory:
    """key -> [(bill_id, template)]. lookup() can leave one bill out (the eval's leave-one-out rule)."""

    def __init__(self):
        self.by_key: dict[str, list] = {}

    def add(self, bill_keys: list[str], bill_id: str, template: dict | None) -> None:
        if template:
            for k in bill_keys:
                self.by_key.setdefault(k, []).append((bill_id, template))

    def lookup(self, bill_keys: list[str], exclude_bill: str | None = None) -> list[dict]:
        seen, out = set(), []
        names = [k for k in self.by_key if k.startswith("name:")]
        wanted = list(bill_keys)
        for k in bill_keys:  # a slightly misread name still finds the supplier
            if k.startswith("name:"):
                wanted += [n for n in names if difflib.SequenceMatcher(None, k, n).ratio() >= 0.85]
        for k in wanted:
            for bill_id, tpl in self.by_key.get(k, []):
                key = (tpl["label"], tpl["pos"])
                if bill_id != exclude_bill and key not in seen:
                    seen.add(key)
                    out.append(tpl)
        return out
