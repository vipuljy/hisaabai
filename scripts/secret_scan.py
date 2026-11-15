"""Blocks a commit if it would put a secret, a real phone number or private shop data on GitHub.

  python scripts/secret_scan.py          files staged for the next commit (the pre-commit hook runs this)
  python scripts/secret_scan.py --all    every file in the project

Prints file:line and what kind of secret it looks like (never the secret itself). Exit 1 = blocked.
A line that is a deliberate fake can end with:  # secret-scan: ignore

Privacy guard: if data/eval/labels.csv exists (only on your laptop; it is never committed), every real supplier
name and invoice number in it is looked for in the files that would be committed. Names are compared normalised
(lower case, punctuation ignored); invoice numbers with a letter or a - / (e.g. INV-2231) anywhere, plain-number
invoice numbers (e.g. 2231) only on a line that talks about an invoice / bill number (a bare 2231 is too often just
an amount). Only file:line is printed, never the value, and "secret-scan: ignore" does not silence these.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
PRIVATE_PATHS = ("data/", "discovery/private/", "outputs/")  # outputs/: eval results quote real bills
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".ogg", ".opus", ".mp3", ".m4a", ".wav"}
GENERATED_CHARTS = {"outputs/eval/router_tree.png"}  # charts our scripts draw (exact paths only), never photos
PLACEHOLDERS = ("paste", "your", "make-up", "changeme", "example", "xxxx", "<", "[")

PATTERNS = [
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("Meta access token", re.compile(r"\bEAA[A-Za-z0-9]{30,}")),
    ("Supabase secret key", re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{10,}")),
    ("JWT (e.g. Supabase service_role key)", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("Database URL with password", re.compile(r"postgres(?:ql)?://[^:/\s]+:(?P<pw>[^@\s]{6,})@")),
    (".env-style secret", re.compile(r"^\s*(?:WHATSAPP_TOKEN|APP_SECRET|ANTHROPIC_API_KEY|SARVAM_API_KEY|"
                                     r"SUPABASE_SERVICE_KEY|JOBS_TOKEN)\s*=\s*(?P<val>\S{12,})", re.M)),
    ("Indian mobile number", re.compile(r"(?<![\d.])(?:\+?91[\s\-]?)?(?P<num>[6-9]\d{4}[\s\-]?\d{5})(?![\d.])")),
]


LABELS = ROOT / "data" / "eval" / "labels.csv"
INVOICE_CONTEXT = re.compile(r"invoice|\binv\b|bill\s*no|bill_no|serial", re.I)
_private = {}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def private_values(path: Path = LABELS) -> dict | None:
    """Compiled matchers for the real supplier names / invoice numbers in labels.csv, or None if it is absent."""
    import csv
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    names = {_norm(r.get("supplier_name") or "") for r in rows}
    names = sorted((n for n in names if len(n) >= 6), key=len, reverse=True)  # 'b k', 'ssp' are too short to test
    codes = {(r.get("invoice_number") or "").strip() for r in rows}
    alnum = sorted((c for c in codes if len(c) >= 4 and not c.isdigit()), key=len, reverse=True)
    digits = sorted((c for c in codes if c.isdigit() and len(c) >= 3), key=len, reverse=True)
    rx = lambda items, flags=0: re.compile(  # noqa: E731
        r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, items)) + r")(?![A-Za-z0-9])", flags) if items else None
    return {"names": rx(names), "codes": rx(alnum, re.I),
            "digits": re.compile(r"(?<!\d)(?:" + "|".join(digits) + r")(?!\d)") if digits else None}


def private_findings(text: str, values: dict | None) -> list[tuple[int, str]]:
    if not values:
        return []
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if values["names"] and values["names"].search(_norm(line)):
            found.append((lineno, "real supplier name from data/eval/labels.csv"))
        if values["codes"] and values["codes"].search(line):
            found.append((lineno, "real invoice number from data/eval/labels.csv"))
        elif values["digits"] and INVOICE_CONTEXT.search(line) and values["digits"].search(line):
            found.append((lineno, "real invoice number from data/eval/labels.csv"))
    return found


def _labels() -> dict | None:
    if "v" not in _private:
        _private["v"] = private_values()
    return _private["v"]


def is_fake_number(num: str) -> bool:
    digits = re.sub(r"\D", "", num)[-10:]
    return digits.startswith("98765") or len(set(digits)) <= 3  # 9876543210 family, 9111111111, 9000000001


def findings_in(path: str, text: str) -> list[tuple[int, str]]:
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "secret-scan: ignore" in line:
            continue
        for kind, rx in PATTERNS:
            for m in rx.finditer(line):
                groups = m.groupdict()
                value = groups.get("pw") or groups.get("val") or ""
                if value and any(p in value.lower() for p in PLACEHOLDERS):
                    continue
                if kind == "Database URL with password" and value in ("postgres", "password"):
                    continue
                if kind == "Indian mobile number" and is_fake_number(groups["num"]):
                    continue
                found.append((lineno, kind))
    return found


def check_file(rel: str) -> list[str]:
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return [f"{rel}: secrets file, must never be committed"]
    if rel.startswith(PRIVATE_PATHS):
        return [f"{rel}: private shop data folder, must never be committed"]
    if (Path(rel).suffix.lower() in IMAGE_EXT and not rel.startswith("deploy/brand/")  # brand assets are ours
            and rel not in GENERATED_CHARTS):
        return [f"{rel}: photo or audio file (could be a real bill or voice note)"]
    path = ROOT / rel
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    if rel == "scripts/secret_scan.py":
        return []
    found = findings_in(rel, text) + private_findings(text, _labels())
    return [f"{rel}:{line}: looks like a {kind}" for line, kind in sorted(found)]


def files(all_files: bool) -> list[str]:
    try:
        cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"] if all_files else \
              ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [f for f in out.splitlines() if f]
    except (OSError, subprocess.CalledProcessError):
        # not a git repo yet: walk the folder, skipping what .gitignore would skip. Skipped folders are never
        # entered (data/ holds thousands of photos; on a synced Documents folder each look-up can be slow)
        result = []
        for here, dirs, names in os.walk(ROOT):
            rel_dir = Path(here).relative_to(ROOT).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir + "/"
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (rel_dir + d + "/").startswith(PRIVATE_PATHS)]
            result += [rel_dir + n for n in names if n != ".env" and not (rel_dir + n).startswith(PRIVATE_PATHS)]
        return sorted(result)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    problems = [p for f in files("--all" in argv) for p in check_file(f)]
    if _labels() is None:
        print("Privacy guard: data/eval/labels.csv not found here, real names / invoice numbers not checked.")
    for p in problems:
        print("BLOCKED  " + p)
    if problems:
        print("\nRemove these (or add the path to .gitignore) before committing. "
              "If one is a deliberate fake, end that line with  # secret-scan: ignore")
        return 1
    print("Secret scan: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
