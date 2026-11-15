# Setup, security and hosting

## 1. Accounts and where each key goes

| Account | What you do | Goes in `.env` | When |
|---|---|---|---|
| Meta (WhatsApp) | Build Plan, Phase 0 steps 1-7 | `PHONE_NUMBER_ID`, `WHATSAPP_TOKEN`, `APP_SECRET`, `VERIFY_TOKEN` (made up), `WABA_ID` (optional: WhatsApp Manager > account ID) | Now |
| Anthropic | console.anthropic.com: add about $5 of credit, **set a monthly spend limit**, create a key | `ANTHROPIC_API_KEY` | Before the eval |
| Supabase | New project (region Mumbai). Connect > **Session pooler** URI. Project settings > API keys > secret (or service_role) key | `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | By 22 Oct |
| Sarvam | sarvam.ai dashboard > API key | `SARVAM_API_KEY` | Before voice check |
| Host | Later (section 4) | Same keys, typed into the host's dashboard + `JOBS_TOKEN` | Before pilot |

Then: `RUN_setup_database.bat` (tables + private photo bucket), then `RUN_check_setup.bat` (every line OK or SKIP).

## 2. Who can do what (authentication)

| Door | Who gets in | How it is checked |
|---|---|---|
| `POST /webhook` (messages) | Meta only | Every request carries Meta's signature, made with `APP_SECRET`; anything else gets 401 |
| `GET /webhook` (one-time setup) | Meta, proving it knows `VERIFY_TOKEN` | Exact match |
| `POST /jobs/daily` | The scheduler | `X-Jobs-Token` header must equal `JOBS_TOKEN` (constant-time compare); off if `JOBS_TOKEN` is empty |
| `GET /health` | Anyone | Says only "ok" or "down", never data |
| Database | Only the bot | Row-level security on every table: the public anon key sees nothing; the bot uses the database password |
| Photos | Only the bot | Private bucket; no public links |
| Pilot shops | Invited numbers only | `PILOT_OPEN` empty: unknown numbers get a polite "pilot only" reply |

## 3. Cost and abuse guards

| Guard | Default | `.env` key |
|---|---|---|
| AI calls per shop per day (photos + voice notes) | 60 | `AI_CALLS_PER_SHOP_PER_DAY` |
| AI calls per day, whole bot | 1,000 | `AI_CALLS_PER_DAY` |
| Largest photo accepted | 8 MB | `MAX_IMAGE_MB` |
| Largest PDF accepted (up to 10 pages) | 10 MB | `MAX_PDF_MB` |
| Hard money cap | Monthly spend limit in the Anthropic console | (console) |

When a limit is hit, the owner is asked to type the bill instead, and the event `limit_hit` is logged.

## 4. Hosting (decide before 1 Nov)

Free options, step by step: `docs/14_free_hosting.md` (Oracle Always Free VM with OCR, or Render free without OCR).

**Memory:** at least 1 GB RAM (2 GB safer). The image includes the free OCR check (RapidOCR), which peaks at about 0.7–0.9 GB while reading a photo. On a 512 MB plan, build with `--build-arg OCR=0`: the bot still works, but read-policy checks a and b are skipped, so more mistakes reach the owner unflagged.

**Needs:** always on (Meta expects a reply within seconds; a host that sleeps when idle makes the first message after a quiet hour slow), HTTPS, environment variables, a Docker or Python runtime. Photos go to the Supabase bucket, so the host's disk does not matter.

**Steps, any host:**
1. Push the repo to GitHub (private or public; `.env` is never committed).
2. Create a web service from the repo using the `Dockerfile`. Health check path: `/health`.
3. Type every `.env` key into the host's environment settings. Set `PILOT_OPEN` empty. Make a new random `JOBS_TOKEN` (24+ characters).
4. In Meta > WhatsApp > Configuration, change the Callback URL to `https://<host>/webhook`.
5. GitHub repo > Settings > Secrets > Actions: add `HISAAB_BOT_URL` and `HISAAB_JOBS_TOKEN`. The `daily-jobs` workflow (`deploy/github-workflows/`, placed in `.github/workflows/` by `SETUP_github.bat`) then calls the bot at about 09:45 IST every day (reminders, Sunday summary, 30-day photo clean-up). Test it with "Run workflow".
6. Send "hi" from your phone; check `https://<host>/health` says ok.

### 4a. Same region for the bot and the database (the main speed fix, 3 Nov)

Render has no India region, so the bot runs in **Singapore**, while the Supabase project is in **Mumbai**. Every
message makes about 13 database round trips (`scripts/query_count.py`), and each one crosses that gap. Put the
database where the bot is. Before the pilot there is no real data to move, so this is a fresh project, not a migration.

1. supabase.com > **New project**, region **Southeast Asia (Singapore)**, any name (e.g. `vahi-sg`). Save the database
   password in your password manager.
2. **Connect > Session pooler** URI → `DATABASE_URL` in `.env`. **Project settings > API** → the URL and the secret
   (service_role) key → `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`. Keep the old lines in a text file until step 7.
3. Double-click `RUN_setup_database.bat`: tables, views and the private photo bucket, with an OK line for each.
4. Double-click `RUN_check_setup.bat`: every line OK.
5. Render > the service > **Environment**: paste the same three values, **Save** (it redeploys).
6. Send *hi* from your phone (a fresh shop: the old one stays in the old project). For the pilot, add shops with
   `python -m bot.admin add-shop` as usual.
7. Measure: Render > **Logs**, the line `handled text in N ms`, for 10 messages before and 10 after. Write both medians
   in `docs/18_guardrails_and_trust.md`. When you are happy, pause or delete the Mumbai project yourself.

The other way round (the bot in India, next to Mumbai) is the Oracle Mumbai VM in `docs/14_free_hosting.md`: more
setup, and it also brings the OCR check back. Either way the rule is the same: bot and database in one region.

## 5. If a key leaks

| Key | Rotate at | Then |
|---|---|---|
| `WHATSAPP_TOKEN` | Business settings > System users > revoke, generate new | Update host + `.env` |
| `APP_SECRET` | App settings > Basic > Reset | Update host + `.env` |
| `ANTHROPIC_API_KEY` | Console > API keys > delete, create new | Update host + `.env` |
| `DATABASE_URL` password | Supabase > Database > Reset password | Update host + `.env` |
| `SUPABASE_SERVICE_KEY` | Supabase > API keys > roll / create new secret key | Update host + `.env` |
| `JOBS_TOKEN` | Make a new one | Host + GitHub secret |

`python scripts/secret_scan.py` runs before every commit (section in README) so keys and phone numbers never reach GitHub.
