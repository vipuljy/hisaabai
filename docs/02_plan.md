# Plan (6 weeks, from 2026-10-13)

## Flow
```
DISCOVERY                 BUILD                         PILOT                        PROOF                 OUTPUT
15 shop interviews ─┐                               ┌─> 15-20 shops, 2 weeks   ─┐
100+ bill photos   ─┼─> Wedge ─> WhatsApp bot ──────┤                          ├─> SQL funnel      ─> Case study
Scorecard          ─┘   + PRD    + eval set ────────┴─> A/B: nudges vs none    ─┘   + eval report      + demo video
                                                                                  + business case    + README
```

## Phase 0: Feasibility (13-14 Oct)
- [x] Project folder + structure
- [x] Starter webhook + 6 automated tests
- [x] Verification check: unverified Cloud API reaches 250 unique customers/day, enough for the pilot
- [ ] Meta developer account + app ("Connect with customers through WhatsApp")
- [ ] hello_world template reaches your phone
- [x] ngrok installed (SETUP_ngrok.bat)
- [x] RUN_TESTS.bat shows 6 passed on the laptop (12 Oct)
- [ ] Webhook echoes your "hi" (RUN_STEP0 + RUN_STEP0b)
- [ ] Permanent System User token in .env
- [x] Setup checker (`RUN_check_setup.bat`) tests every key without showing it (13 Oct)
- [ ] **Gate G0**

## Phase 1: Discovery (15-21 Oct)
- [ ] Post the group message + 2 polls (`discovery/outreach_kit.md`)
- [ ] Run `RUN_STEP2_review_mining.bat`; hand-check 200 tagged reviews
- [ ] 8+ conversations: warm network and referrals first (calls fine), walk-ins only as a top-up
- [ ] 100+ bill photos from friends, family and groups, names masked
- [ ] Fill scorecard, pick wedge, 1-page brief + short PRD
- [ ] 5+ owners say yes to a 2-week trial
- [ ] **Gate G1**

## Phase 2: Build (22 Oct-1 Nov)
- [x] Database schema designed and tested on Postgres 16 (`db/schema.sql`)
- [x] Eval script + scoring, tested with a fake model (`src/01_eval_bill_reading.py`)
- [ ] Supabase project created; schema loaded (`RUN_setup_database.bat`, built 13 Oct)
- [x] Cost guards (daily AI caps, photo size), privacy fixes (AI disclosure, 30-day photo deletion, typed bills), sales-bill guard (13 Oct)
- [x] Hosting kit: Dockerfile, /health, private photo bucket, token-protected daily job + GitHub Actions schedule (13 Oct)
- [x] GitHub prep: secret scan + pre-commit hook, LICENSE, `SETUP_github.bat`, CI tests workflow (13 Oct)
- [x] Voice check kit: 25-line script + scorer (`RUN_STEP3_voice_check.bat`) (13 Oct)
- [ ] Pick the host and deploy (`docs/11_setup_and_hosting.md`)
- [x] Bot logic: consent, bill reading + "sahi hai / badlo", edits, typed payments, dues, STOP (all tests: 135 with Postgres, 106 on the laptop, 13 Oct)
- [x] Chat simulator to try the bot without WhatsApp (`RUN_chat_simulator.bat`)
- [x] Free readers (step 6): PDF text + RapidOCR (default) / Tesseract with proof-based trust (rules_v4): TEST printed photos 17/35 totals trusted, 0 trusted-but-wrong (`docs/13_cascade_spec.md` Build log)
- [x] Router (step 07) + cascade (step 08) built and tested on fake cached runs; waiting for the real Claude runs (`RUN_STEP7_router_cascade.bat`)
- [x] Eval on 136 bills (Haiku v2, Sonnet v2) + read policies (step 9): P3 chosen and wired into the bot (`READ_POLICY`)
- [x] Label review (B086, B141) + re-score; policy P5 "money first" is the default (40% cheaper than Sonnet-only, same silent errors) + date one-tap; RapidOCR in the Docker image (28 Oct)
- [x] Question eval (step 10, 62 frozen fake messages, rules only): 89% understood before fixes, 97% after; supplier PDFs on WhatsApp (text PDF free, scanned PDF -> photo flow); free-hosting guide `docs/14_free_hosting.md` (29 Oct)
- [ ] Eval v1 on 100 bills (2 models x 2 prompts)
- [x] Voice notes: payments and dues by voice (Sarvam + fallback parser), tested
- [x] Voice / typed entries (31 Oct): bills with credit days, payments, received / future (never saved as payments), up to 3 entries per message, supplier list (no silent supplier creation, also for photos), fixes / undo for 7 days with an edit history, proof photos; measured on 3 typed sets (2 blind) + 20 held-out real voice notes (`docs/16_voice_typed_entries.md`, `RUN_STEP12_entries_eval.bat`)
- [x] Daily nudges built: due reminders + Sunday summary, treatment only, dry-run mode (`RUN_daily_jobs.bat`)
- [x] Owner questions about the hisaab (25 Oct): 9 fixed question types (kul baaki, ek supplier ka baaki, kitna diya, kitne bill aaye, sabse zyada baaki, last payment, reminders set/list/cancel), understood by Hinglish rules, answered only from the database with the basis stated; WhatsApp list menu ("📋 Hisaab dekhein") when unclear; optional Haiku classifier behind `QA_LLM=1`; owner reminders sent by the daily job; events `question_asked` / `question_answered` measure how many were understood without AI (`bot/questions.py`, `bot/answers.py`)
- [ ] Submit the 3 templates in WhatsApp Manager, including the new `owner_reminder` (`docs/10_templates.md`)
- [ ] Daily job: add the 2 GitHub secrets once hosted (09:45 IST schedule is ready)
- [ ] Spare number ready by 30 Oct (old SIM, a number freed from WhatsApp, or new prepaid SIM)
- [ ] Friendly test with 3-5 owners; switch to production number
- [x] Experiment pre-registered (`docs/09_experiment.md`) with readout script (`src/02_experiment_readout.py`)
- [ ] **Gate G2**

## Phase 3: Pilot (2-15 Nov)
- [ ] Onboard 15-20 shops in person; `python -m bot.admin assign-groups`
- [ ] Daily monitoring; corrections feed the eval
- [ ] 8 Nov kill-rule check (8+ shops active)
- [ ] Exit interviews: time per bill, willingness to pay
- [ ] **Gate G3**

## Phase 4: Analysis + package (16-22 Nov)
- [ ] SQL: funnel, retention, experiment readout, correction rate, share of questions understood without AI
- [ ] Final eval report
- [ ] 2-page business case
- [ ] Case study, 60-second demo video, README update
- [ ] **Gate G4**
