# HisaabAI: notes for Claude Code

## What this is
A WhatsApp assistant for small Indian shops: bill photo or Hindi voice note in, ledger + supplier dues + weekly summary out. A portfolio project that will be public on GitHub. Plan: `docs/02_plan.md`. Decisions: `docs/04_decisions_log.md`.

## How Vipul works
- Treat him as CEO: before each action, say in one or two lines what you will do and why, and wait for his OK.
- Bullet points, direct verdicts, simple explanations.
- He runs things by double-clicking `RUN_*.bat` files on Windows. Add a new `.bat` for anything he must run.

## Conventions (same as FreshHold)
- Docs are numbered: `docs/0N_name.md`.
- Analysis scripts are numbered and run in order: `src/0N_name.py`.
- Bill reading lives in `bot/extract.py` and is shared by the bot and the eval. Change prompts only by adding a new version (v3...).
- Database: `db/schema.sql` (see `docs/07_database.md`).
- Owner-facing wording lives only in `bot/messages.py`; logic in `bot/conversation.py`. Both stores (`MemoryStore`, `PgStore`) must keep the same methods; tests run on both.
- The experiment is pre-registered in `docs/09_experiment.md`: never change its metric, test or assignment after the pilot starts.
- Template names and variable counts in `bot/jobs.py` must match `docs/10_templates.md`.
- The live bot lives in `bot/`. Run from the project root: `python -m uvicorn bot.app:app --port 8000`.
- Tests: `python -m pytest -q` must pass before any change is called done.
- Photos go through `bot/photos.py` only (local folder or private Supabase bucket), never written directly.
- New `.env` keys go in `.env.example` with a comment, and in `bot/check_setup.py` if they can be tested.
- Before any commit: `python scripts/secret_scan.py` (the pre-commit hook runs it).
- Record every dataset in `docs/05_data_log.md` and every product decision in `docs/04_decisions_log.md`.

## Hard rules
- Never read, print or commit `.env` or any token.
- Never put real shop names, phone numbers or bill images in committed files. Real data goes only in `data/` or `discovery/private/` (both gitignored). See `docs/06_privacy.md`.
- Log only the last 4 digits of phone numbers.
- Every AI call logs an `ai_call` event so the daily caps keep working.
- The bot stays business-specific (bills, dues, summaries). Off-topic messages get a polite decline: WhatsApp bans general-purpose AI chatbots.
- No number goes in the case study or CV unless it was measured and is reproducible from this repo.
