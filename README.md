# HisaabAI: WhatsApp Bookkeeping for Small Shops

**One line:** Small Indian shop owners lose hours and money on paper supplier bills. HisaabAI lets them send a bill photo or a Hindi voice note on WhatsApp and get back a clean ledger, supplier-due reminders and a weekly cash summary, with the owner confirming every amount. Owners see it as **Vahi** (from *vahi-khata*, the traditional ledger); HisaabAI is the project name.

## Folder map
| Folder | What lives here |
|---|---|
| `docs/` | Problem statement, plan, market facts with sources, decisions log, data log, privacy rules |
| `bot/` | The WhatsApp webhook service (FastAPI) and bill reading (`extract.py`) |
| `db/` | Database schema and ready-made queries (Supabase / Postgres) |
| `src/` | Numbered analysis scripts, run in order (eval, SQL, experiment) |
| `tests/` | Automated checks (bot, database, eval, setup checker, secret scan) |
| `scripts/` | `secret_scan.py`: blocks commits with keys, phone numbers or photos |
| `discovery/` | Interview kit and templates. Filled-in notes go in `discovery/private/` (never committed) |
| `data/` | Events, eval set, pilot data (never committed) |
| `outputs/` | Charts, tables, case-study assets |

## How to run
1. Copy `.env.example` to `.env` and fill it in (see the comments inside).
2. Double-click `RUN_TESTS.bat` (checks the bot works on your machine).
3. Double-click `RUN_check_setup.bat` (tests every key and connection; prints OK / FAIL, never a secret).
4. Once Supabase exists: `RUN_setup_database.bat` (tables + private photo bucket; safe to re-run).
5. Double-click `RUN_STEP0_start_bot.bat` (starts the bot on port 8000).
6. Double-click `RUN_STEP0b_public_link.bat` (gives Meta a public link to the bot, via ngrok).
7. Later: `RUN_STEP13_tricky_bills.bat` (safety test: 14 made-up photos built to fool the reader), `RUN_STEP1_eval.bat` (bill reading), `RUN_STEP2_review_mining.bat` (app reviews), `RUN_STEP3_voice_check.bat` (voice notes).
8. Hosting and the daily job: `docs/11_setup_and_hosting.md`. Putting it on GitHub: `SETUP_github.bat`, then `SETUP_github_push.bat` once; later changes: `RUN_github_update.bat` (Render redeploys from GitHub).

**Try it without WhatsApp:** double-click `RUN_chat_simulator.bat` and chat as a shop owner.

## What the bot does
| Owner sends | Bot does |
|---|---|
| "feedback", "dikkat ...", "shikayat ...", or the menu row | Asks what went wrong (or saves the words at once), typed or spoken; listed first in the evening health report. The live bot also asks for a 3-tap rating after the 5th saved entry and from day 10, and offers [Dikkat batayein] after 2 cancelled cards or words like "kaam nahi kar raha" (once a day) |
| First message | Welcome from **VahiBook** (says an AI reads photos, photos deleted after 30 days) + consent buttons. Nothing is read before "Shuru karein". No questions at signup: the shop's own name is asked once, after the first saved entry (the WhatsApp profile name offered in one tap) |
| Bill photo | Reads supplier, total, date, invoice, due date; asks for anything missing; shows "Sahi hai / Badlo" |
| "Badlo" | Lets the owner fix supplier, total or date; every fix is recorded for the eval |
| "Sharma Traders ka bill aaya 8000 ka, 15 din ka udhaar" | A typed (or spoken) bill: card with supplier, total, date, credit days, due date, and the baaki before → after; a later photo of it is read and offered as its proof (never a second bill) |
| Their own sales bill | Spots that the "supplier" is the shop itself and asks before adding it |
| "Sharma ji ko 5000 diye" | Matches the supplier (a list when two fit; "➕ naya supplier" only on a tap), asks to confirm, saves the payment |
| "8000 nahi 8500", "last wala galat hai" | Fixes or removes an entry from the last 7 days, after a confirm tap; every change is kept in an edit history |
| "Verma se 2000 aaye", "Sharma ne 2000 diya" | Never guessed: asks what came (goods or money back) or which way the money went, then a card |
| "paid sharma 2000", "gupta gave me 500", "returned goods 500 to gupta" | Typed English works too (the same cards and questions as Hinglish); a word it does not know is asked about, never guessed |
| "Gupta ko 500 ka maal wapas kiya, 300 hi kata" | Goods back: the baaki changes by what the supplier accepted (it asks which amount); if the shop still owes, it asks "paisa haath mein mila, ya baaki se kata?" |
| "Gupta ko 5000 dena hai", "Sharma ka 2000 purana baaki hai", "Gupta ko 800 ka maal wapas kiya" | Shows the dues it knows and offers to add or remind; old dues and returns get their own cards and change the dues (not counted as bills) |
| "kitna baaki hai", "aaj ka hisaab", "aaj ka payment" | Dues per supplier and the total; a day's (or week's) bills, payments and the baaki |
| Voice note | Transcribed (Sarvam); then exactly like text (bills, payments, fixes, questions, up to 3 entries per note). Details and measured accuracy: `docs/16_voice_typed_entries.md` |
| STOP / START, "mera data delete karo" | Opts out (the hisaab is kept) / back in; deletes all the shop's data and photos after one confirm tap |
| Anything else | Politely declines (WhatsApp bans general-purpose AI chatbots) |

Running the pilot: `RUN_pilot_health.bat` (a one-page daily report from the events table, shop codes only, with the median reply time),
the onboarding script, check-ins and exit interview in `docs/19_pilot_kit.md`, and the owner's card
`discovery/vahi_card.html`.

**Nudges (the experiment):** treatment shops get a reminder 2 days before a supplier due date and a Sunday summary (`bot/jobs.py`, run daily). Control shops get none. Pre-registered in `docs/09_experiment.md`.

**Safety built in:** Meta signature check on every webhook, token-protected daily-job endpoint, row-level security on every table, private photo bucket, daily AI-call caps per shop and overall, 30-day photo deletion, secret scan before every commit. Details: `docs/11_setup_and_hosting.md`.

Code map: `bot/conversation.py` (logic), `bot/understand.py` (Hindi script and spelling slips, before the rules), `bot/entries.py` + `bot/numbers.py` (what a typed / spoken message wants to record), `bot/entry_flow.py` (cards, guards against duplicates and unusual entries, supplier list, fixes, dated corrections, proof photos), `bot/messages.py` (every word the owner sees), `bot/extract.py` (bill reading), `bot/store.py` (data), `bot/photos.py` (photo storage and deletion), `bot/whatsapp.py` (Meta API), `bot/app.py` (web server, health check, daily-job endpoint), `bot/voice.py` (voice notes), `bot/jobs.py` (daily nudges and photo clean-up), `bot/experiment.py` (A/B assignment and statistics), `bot/admin.py` (pilot admin), `bot/check_setup.py` (setup checker), `bot/setup_db.py` (database setup). Deploy: `Dockerfile`; scheduled job and CI tests: `deploy/github-workflows/` (copied into `.github/workflows/` by `SETUP_github.bat`).

## Status
See `docs/02_plan.md`.

## Privacy
Shop names, phone numbers, bill images and tokens never enter this repository. See `docs/06_privacy.md`.

## License
MIT (`LICENSE`).
