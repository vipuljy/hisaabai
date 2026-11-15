# 17. Build history: second session (25 Oct – 2 Nov 2026)

This document records everything built, measured and decided in one long working chat between Vipul (the owner of the project) and Claude, from 25 Oct to 2 Nov 2026. Read it after `docs/01`–`docs/13`. It explains the **why** behind the code; the code and the Build log in `docs/13_cascade_spec.md` hold the **what**.

Privacy: bills are named only by their id (B001 … B174). No real shop name, supplier name, phone number, invoice number or key appears here.

---

## 0. How to read this document

### 0.1 Ground rules of the work (they shaped every choice)

| Rule | What it meant in practice |
|---|---|
| Plan first, then wait for "go" | Every step started with a 5-bullet plan. Exceptions: two overnight runs where Vipul pre-approved the steps. |
| Vipul runs every command | Every reply ended with the exact command and its expected output. |
| `.env` is never read or printed | Keys were only ever named, never shown. Tests were later made unable to pick up real keys (see 3, item 30). |
| `data/` is private | Real bills, labels and caches live only in `data/` (gitignored). `data/eval/labels.csv` was changed once, by Vipul's explicit request, with a backup. |
| No deletes | Claude's sandbox had no delete permission in the project folder. Two stray items had to be deleted by hand (see 6). |
| Tests must pass | Every change ended with `python -m pytest -q` passing. Postgres tests ran against a temporary Postgres 16 in Claude's sandbox. |
| Design on DEV, quote TEST | From 26 Oct, every new rule was designed on half the printed bills (DEV) and the other half (TEST) is the number to quote. |

### 0.2 Words used everywhere

| Word | Meaning |
|---|---|
| Silent error | A bill where a field is **wrong and not flagged**. The owner would accept a wrong value. The number we drive to zero. |
| Flagged / unsure / ⚠️ | The bot shows the field with ⚠️ and asks the owner to check. Wrong-but-flagged is safe. |
| Missed | A field left blank. Safe: the bot asks for it. |
| Trusted total | A free (non-AI) total that passed a **proof** (amount in words = digits, or the bill's own GST maths). The bot does not ask about it. |
| Trusted but wrong | Must always be 0. |
| DEV / TEST | Fixed halves of the 70 printed bills in `data/eval/split.csv` (seed 20261026), grouped so bills from the same supplier or original file never sit in both halves. |
| Scopes | `all` (136 bills), `printed` (70), `printed_TEST` (35), `handwritten` (66), `text_pdf_only` (9). |
| 95% upper bound | One-sided Clopper–Pearson bound on the silent-error rate. For 0 of n it is shown as "< 3/n" (rule of three). |
| Policy | How a bill photo is read: which model first, which free checks, when to call the bigger model, when to ask the owner. P0–P5 and variants. |

### 0.3 The labelled data behind every number

- `data/eval/labels.csv`: 174 rows; **136 are used** by the evals (the loader skips the rest). 70 printed, 66 handwritten.
- `data/eval/split.csv`: DEV/TEST halves of the printed bills.
- `data/eval/runs/<run>/<bill_id>.json`: cached readings, one file per bill per reader (`pdf_text`, `ocr_rules`, `ocr_rapid`, `ocr_rapid_clean`, `haiku_v2`, `sonnet_v2`, `router_features`, `cascade`). Re-running any eval from cache costs nothing.
- `data/eval/questions.csv`: 62 invented owner messages (question eval), frozen before its first run.
- All result tables are written to `outputs/eval/` (gitignored since 29 Oct, because they quote real bills).

---

## 1. Timeline

Dates are India time (IST). "Tests" = `python -m pytest -q` on the laptop setup (Postgres tests skipped); "+PG" = the same suite against Postgres.

| Date | Vipul asked | Built or ran | Result |
|---|---|---|---|
| 25 Oct | Step 1 of the cascade spec: free text rules, checks, PDF reader, free-readers eval | `bot/field_rules.py`, `bot/checks.py`, `bot/read_pdf.py`, `src/06_free_readers_eval.py`, `tests/test_free_readers.py` | Only 9 of 136 bills have a PDF text layer. On those 9: total right 100%, all fields 89%, silent errors 0%. Tests 146 passed |
| 25 Oct | Fix the flaky reminder test; why did one bill miss its invoice number? | Pinned that one test to a Wednesday | B016 prints two numbers under "Invoice No."; the rule correctly refuses to guess. Not a bug |
| 25 Oct | Step 2: free OCR for photos | `bot/read_ocr.py` (Tesseract), confidence on the total's line, threshold fixed at 60 | First dry run: 58% silent errors on photos, mostly wrong supplier names. Tests 158 |
| 25 Oct | Fixes A–E: weak "Total" label, split columns, supplier always unsure, proof gate, seller GSTIN | Same files | Silent errors 58% → 16%; trusted totals on printed photos 4% (3 bills), 0 wrong. Tests 163 |
| 25 Oct | Fixes F–G, freeze free-OCR rules (`rules_v3`) | Invoice number trusted only if read twice; skip a lone 0.00 on the next line | Photos: 0% silent errors. Tests 165 |
| 25 Oct | Plan the router (Haiku vs Sonnet per photo) and the cascade routing | Agreed design (no code) | Routing table logged in `docs/04_decisions_log.md` later |
| 25 Oct | Owner questions about their hisaab (9 intents, menus, reminders) | `bot/questions.py`, `bot/answers.py`, `reminders` table, list menus, `owner_reminder` template text | Tests 268 (+PG 319) |
| 25 Oct | Consent UX fix + reminder bug from the simulator | Short nudge after the first welcome; typed yes only as a whole message; reminder words win; relative days | Tests 305 (+PG 378) |
| 25 Oct | Research free OCR engines; then RapidOCR first | `engine = tesseract | rapidocr` in `bot/read_ocr.py`, `requirements-ocr-extra.txt` | Printed photos: total right 16% → 53%, trusted 4% → 19% (13 bills), 0 wrong. docTR skipped. Tests 308 |
| 25–26 Oct (overnight) | Raise trusted totals with 0 wrong: DEV/TEST split, maths proofs, supplier memory, photo cleanup, docTR | `data/eval/split.csv`, maths proofs in `bot/checks.py`, `bot/templates.py`, cleanup option, docTR adapter | Printed TEST: **17 of 35 trusted (49%), 0 wrong**. Memory: no gain. Cleanup: not adopted. docTR: could not install. Tests 314 |
| 26 Oct (night) | Build the router (07) and cascade (08) before the Claude runs exist | `bot/router.py`, `src/07_router.py`, `src/08_cascade_eval.py`, fake-cache tests | Plumbing proven on 24 fake bills. Tests 319 |
| 26 Oct | Choose the router threshold nested | Inner grouped CV per outer fold | Both numbers reported. Tests 320 |
| 27 Oct | Explain the terms and what to do next | Glossary + 11-step run list | Found that `src/01_eval_bill_reading.py` does not load `.env` (flagged; Vipul ran the eval anyway) |
| 27 Oct | Supplier scoring rule; why did B013 fail; remove `temperature` | `same_supplier()` in `bot/extract.py`, used by `src/evalkit.py` | B013 = reply cut off by thinking tokens. Tests 328 |
| 27 Oct | Fix B013; never match Sharma to Verma | `max_tokens` 600 → 2000, `stop_reason` + thinking tokens cached, cached errors re-read once; `match_supplier` → `same_supplier` | Tests 337 (+PG 413) |
| 28 Oct | Real eval done: silent-error review + policies P0–P4 + wire the best into the bot | `bot/policy.py`, `src/09_policies_eval.py`, `READ_POLICY` setting | **P3** chosen: 2 silent errors (= Sonnet-only) at ₹0.664 vs ₹0.822 per bill. Tests 357 (+PG 435) |
| 28 Oct | Label review (B086, B141 changed), policy P5 "money first", date one-tap, RapidOCR in Docker | Labels backup `labels_v1.csv`, P5 + date-age rule, date buttons, `requirements-ocr-bot.txt` | **P5** default: 1 silent error (= P3) at ₹0.491, 40% cheaper than Sonnet-only. Tests 376 (+PG 459) |
| 28 Oct | Check e (invoice no. vs OCR), policy P5e | P5e row | Still 1 silent error, so the default stayed P5 |
| 29 Oct | Question eval + PDFs on WhatsApp + P5 without OCR + free-hosting guide | `src/10_questions_eval.py`, `data/eval/questions.csv`, PDF document flow, `docs/14_free_hosting.md`, `keep-alive.yml` | Questions: **89% understood before fixes**, 97% after, 0 wrong. PDFs: 9 of 10 real text PDFs read free. P5 without OCR: same 1 silent error at ₹0.205. Tests 399 (+PG 490) |
| 29 Oct | P5_trusted_ocr with a decision rule fixed before the run | New policy rows + check audit | Rule met → P5_trusted_ocr default (₹0.287). Tests 402 |
| 29 Oct | Host on Render free → live default P5_no_ocr; templates; voice eval; Render readiness; pre-push check | `src/11_voice_eval.py`, `render.yaml`, `docs/15_render_env.md`, `scripts/memory_check.py`, webhook tests, `.gitignore` | Memory 667 → ~400 MB under bursts; 122 files would be pushed; real names/numbers removed from 11 files. Tests 415 (+PG 509) |
| 30 Oct | Voice notes over 30 s live; privacy guard; voice-eval kinds and "multi" | Splitting in `bot/voice.py`, guard in `scripts/secret_scan.py`, `tests/test_voice_split.py` | Tests **425 passed, 96 skipped (+PG 521)**; secret scan clean |
| 30 Oct | Scope change: voice and typed text as a full input channel | **Plan only** (intents, cards, DB changes, ~4 build days) | Waiting for "go" and two decisions (see 6) |
| 2 Nov | This handover document | `docs/17_build_history_second_session.md` | — |

---

## 2. The pieces, one by one

Each piece follows the same pattern: problem → options → how it works → commands → results (with the file each number comes from) → what did not work.

### 2.1 Free text rules and checks (`bot/field_rules.py`, `bot/checks.py`)

**Problem.** Reading a bill with Claude costs money on every bill. Many supplier bills (Marg, KARP, Tally style) carry their numbers as plain text. Rules can read that text for free, but only if we can tell when the rules are right.

**Options considered.**
- Take the *last* "total" label on the page (the spec's first idea). Rejected: one pharma distributor format prints "INVOICE AMOUNT" before "NET AMOUNT PAYABLE", so "last" picks the wrong one on other layouts. Vipul set a priority order instead.
- Trust a total when OCR confidence is high. Rejected later (2.3): confidence scales differ between engines and high confidence does not mean the right line.
- Chosen: rules find candidates; **independent checks prove them**.

**How it works.**
- Totals: labels in priority order NET AMOUNT PAYABLE > GRAND TOTAL > Total Invoice Value > Net Payment > Net Amount > Bill Amount > Total. The amount is looked for on the label's line, then the next 1–2 lines. Lines with balance, B/F or C/F are skipped (a "balance with this bill" line is a trap).
- Tolerant of OCR noise: "T0TAL", "lnvoice N0", "Rs." / "₹", lakh commas (1,01,757), "21,390.35".
- Dates are day-first. Supplier = first line that looks like a business name, dropping a trailing ", CITY".
- `bot/checks.py` returns pass / fail / na for: `words_match_total` (an Indian number-words parser: lakh, thousand, hundred, paisa, "Rupees … Only"), `items_sum`, `sane_fields`, `not_balance`. Later (2.4) maths proofs were added.
- A failed check marks the related field **unsure**, so a wrong value becomes "flagged", not silent.

**Commands.** `python -m pytest -q tests/test_free_readers.py` → all pass (36 at the time).

**Results.** See 2.2 (the rules are scored through the readers).

**What did not work.** B016's invoice number stays blank: the page prints two different numbers after "No.", and the rule refuses to guess (correct behaviour, not changed).

### 2.2 Free PDF reader (`bot/read_pdf.py`)

**Problem.** Some suppliers send PDFs made by billing software; their text layer is exact and free.

**How it works.** `pypdfium2` reads the page's text. If there are fewer than 80 letters/digits (after dropping converter watermarks) the page is "scanned" and comes back blank with error `no_text`, so it goes on to OCR or Claude. Results are cached in the same format as the Claude runs (`data/eval/runs/pdf_text/`, tokens 0), so the same scorer works.

**Commands.** `python src/06_free_readers_eval.py` (or `RUN_STEP6_free_readers.bat`).

**Results** (`outputs/eval/free_readers_summary.csv`):

| Scope | Bills | Total right | All fields right | Silent errors |
|---|---|---|---|---|
| text_pdf_only | 9 | 100% | 89% | 0% |
| all | 136 | 7% | 6% | 0% |

Only 9 of 136 bills have a text layer (the 51-page "photo to PDF" bundle is scanned). So PDF text is the cheapest path but rare in this dataset.

### 2.3 Free OCR for photos (`bot/read_ocr.py`)

**Problem.** 127 of 136 bills are photos. Could a free OCR engine read some totals safely?

**Options considered.**
- Tesseract (installed on Windows separately). Built first.
- PaddleOCR: skipped (heavy, poor fit for Python 3.13).
- Research (25 Oct): RapidOCR (light, Apache-2.0, `rapidocr` 3.10 supports Python 3.13; the older `rapidocr-onnxruntime` does not), docTR (needs PyTorch, ~1 GB), Surya (GPL code + non-commercial model weights). **Vipul: skip Surya (licence + weight); RapidOCR first; docTR only if RapidOCR does not clearly win.**

**How it works.**
1. Fix the phone's rotation flag; Tesseract's orientation check (OSD) turns sideways photos upright (11 of 136 eval photos were turned).
2. Read words with positions and confidence (Tesseract, or RapidOCR segments converted to the same word-box format).
3. Split a line where there is a big horizontal gap (fix B), so seller and buyer headers do not merge.
4. Run the same `field_rules` and `checks` as the PDF reader.
5. Guards (fixes A, C, D, F, G, plus the confidence rule):
   - Page confidence < 60 → every field unsure; total's own line < 60 → total unsure. **Threshold fixed at 60, never tuned** (Vipul: tuning on 136 bills would overfit).
   - A total from the plain "Total" label is never trusted (A).
   - The supplier name from OCR is always unsure (C); the seller GSTIN is recorded instead when its check character is valid (E).
   - **Proof gate (D):** a total is trusted only if a proof passes (words = digits; from 26 Oct also maths proofs).
   - An invoice number is trusted only if the same string appears twice on the page (F).
   - A lone 0.00 on the line after a label is skipped (G).
6. The raw word boxes are cached, so a rule change re-scores in seconds without OCR.

**Commands.**
- `python -m pip install -r requirements-ocr-extra.txt` (RapidOCR)
- `python src/06_free_readers_eval.py` → FREE READERS table; `--fresh` redoes OCR.

**Results** (`outputs/eval/free_readers_summary.csv`, printed photos):

| Stage | Engine | Total right | Total trusted | Trusted but wrong | Silent errors |
|---|---|---|---|---|---|
| First dry run (no fixes) | Tesseract | 14% | – | – | 54% |
| After A–E | Tesseract | 16% | 4% (3 bills) | 0 | 10% |
| After F–G (`rules_v3`) | Tesseract | 16% | 4% | 0 | 0% |
| Engine switch | RapidOCR | 53% | 19% (13 bills) | 0 | 0% |
| After maths proofs (`rules_v4`), **TEST** | RapidOCR | 77% | **49% (17 of 35)** | **0** | 0% |
| Same, DEV | RapidOCR | 43% | 37% (13) | 0 | 0% |

Handwritten totals are never trusted (RapidOCR reads 29% of them right, but none pass a proof). Seconds per photo in Claude's 2-core sandbox: Tesseract 10.9, RapidOCR 12.1.

**What did not work.**
- Plain "Total" was right only 2 times in 14 (item-table rows).
- Seller and buyer headers merged into one line before fix B.
- Tesseract reads almost no handwriting (64 of 66 blank).
- Fix B's side effect: a stray "0.00" next line (fixed by G).
- OCR swaps like I→1 in invoice numbers (fixed by F).

### 2.4 Proofs, DEV/TEST split, supplier memory, photo cleanup, docTR (overnight 25–26 Oct)

**Problem.** RapidOCR read 53% of printed totals right but only 19% were trusted, because the words proof needs an amount-in-words line, which many bills lack.

**What was built.**
- `data/eval/split.csv`: DEV/TEST halves (seed 20261026), grouped by supplier + original file. Written once, never overwritten.
- **Maths proofs** in `bot/checks.py`: the bill's own tax formulas (`base × rate% = tax`, "CGST @ x% on base"), the GST summary (taxable − discount + taxes + round-off = total) and line items. Tolerance = **nearest rupee (≤ ₹0.51)**, not ₹1 (see 3, item 5).
- **Supplier memory** (`bot/templates.py`): remembers which label and position held the total for a supplier; tested leave-one-out. **No gain** on this data, so not wired into the bot.
- **Photo cleanup** (trim, deskew, CLAHE contrast): on DEV it added 2 trusted totals but also 1 silent error (a handwritten date 21/7 read as 21/3 at 82% confidence). Kept as an option (`ocr_rapid_clean`), **not the default**.
- **docTR**: adapter and a "both engines agree" proof were written and tested with fakes, but PyTorch could not be installed in Claude's sandbox (network 403, then the 3-minute command limit). Optional file `requirements-ocr-doctr.txt`. **Never run.**

**Result.** Printed TEST trusted totals 6 → 17 of 35 (49%) with 0 trusted-but-wrong (`outputs/eval/free_readers_summary.csv`).

### 2.5 Owner questions (`bot/questions.py`, `bot/answers.py`)

**Problem.** Owners want to ask "Sharma ka kitna baaki?" in Hinglish. Money answers must never be invented.

**Options considered.** Let an AI answer → rejected (it could produce numbers). Chosen: **AI may only classify; every number comes from a database query.**

**How it works.**
- 9 fixed intents: `dues_all`, `dues_supplier`, `paid_period`, `bills_period`, `top_dues`, `last_payment`, `set_reminder`, `list_reminders`, `cancel_reminder`.
- Rules first: Hinglish keywords, misspellings (baki, bakaya), date words (aaj, kal, is hafte, pichhle mahine, somvaar…ravivaar in their "-vaar" form only, "28 tarikh", "3 din baad", "agle hafte").
- "kal" = yesterday in a question, tomorrow in a reminder.
- Anything unclear (two matching suppliers, missing period) → "Kya aap ye poochh rahe hain?" + a WhatsApp **list menu**. Never a guess.
- Optional Haiku fallback behind `QA_LLM=1`: returns only `{intent, supplier, period}`, checked against the fixed lists; it counts toward the daily AI cap.
- Every answer states its basis, e.g. "(2 bill ₹7,580, 1 payment ₹1,000, 5–25 Oct)". An empty period is named ("Is hafte (19–25 Oct) koi payment nahi").
- Reminders: `reminders` table; `bot/jobs.py` sends each once via the `owner_reminder` template; events carry the shop's experiment group.

**Commands.** `RUN_chat_simulator.bat`, type `hisaab`, then a number to tap. `RUN_setup_database.bat` adds the `reminders` table (safe to re-run).

### 2.6 Consent UX and the reminder bug

- Before consent: full welcome only on the first message, then a short nudge with the same two buttons.
- Typed yes counts only when the **whole message** is a yes-word (haan, ha, yes, ok, shuru, haan ji, han, haa, shuru karo). "aur ha sharma ko yaad dilana" is not consent.
- `opt_in` records `via: typed | button`.
- Reminder bug: "aur ha sharma ko 3 din me yaad dilana vapas krne h" was answered as dues. Root cause: an open supplier list was checked before parsing the new message. Fix: parse first; reminder words win; relative days; leading fillers ignored; unclear day → list (Kal / Parso / 3 din baad / Agle hafte / Doosri date).

### 2.7 The real Claude eval and its fixes (27–28 Oct)

**Problem.** The cascade needs real Haiku and Sonnet readings of all 136 bills.

**Fixes made before and during the run.**
- **Supplier scoring:** a name with the city appended was scored wrong. New `same_supplier()` (exact, one name's words at the start of the other, or difflib ratio ≥ 0.9) used by both the eval and the bot's `match_supplier`, so "Sharma Traders" can never be proposed as "Verma Traders" (ratio 0.82).
- **B013:** Haiku's reply was cut off. Haiku 5.5 / Sonnet 5.5 think adaptively by default and `max_tokens` caps thinking + answer together; 600 was used up by thinking. Fix: `max_tokens` = 2000; cache `stop_reason`, block types and thinking tokens; `stop_reason = max_tokens` → error `truncated` (never trusted); a cached reply with an error is re-read once on the next run.
- `temperature` removed from every call (newer models reject it).

**Commands** (Vipul ran these):
1. `RUN_check_setup.bat` → Anthropic OK line.
2. `python src/01_eval_bill_reading.py --check` → `Labels OK: 136 bills (70 printed, 66 handwritten).`
3. `python src/01_eval_bill_reading.py --runs haiku_v2 sonnet_v2 --limit 5` (trial), then without `--limit`.

**Results** (`outputs/eval/summary_by_run.csv`, after the label fix of 28 Oct):

| Run | All fields right | Total right | Bills with a silent error | ₹/bill | Avg thinking / text tokens |
|---|---|---|---|---|---|
| haiku_v2 | 76% | 96% | 2% | 0.05 | 408 / 91 |
| sonnet_v2 | 86% | 98% | 1% | 0.82 | 205 / 89 |

₹/bill includes thinking tokens (billed as output).

### 2.8 Photo router (`bot/router.py`, `src/07_router.py`)

**Problem.** Send each photo to the cheap model (Haiku) or the strong one (Sonnet) based on free signals.

**How it works.**
- Free features: blur, contrast, ink, Tesseract script check, RapidOCR confidence, word counts, table-like lines, GSTIN, proof results. **Never the printed/handwritten hand label** (the live bot does not have it).
- Target: "Haiku read total and bill date right".
- Logistic regression and a depth-3 tree, 5-fold **grouped** cross-validation (groups = supplier + original file).
- Threshold **nested** (Vipul's fix): chosen inside each training fold, applied only to that fold's held-out bills. The "same predictions" number is shown only to measure optimism.

**Commands.** `RUN_STEP7_router_cascade.bat` or `python src/07_router.py` → CV lines, a `NESTED (honest)` line and a `same predictions (optimistic)` line; saves `outputs/eval/router_tree.png`, `router_oof.csv`, `router_choice.json`.

**Results** (`outputs/eval/router_choice.json`, after the label fix):
- Grouped CV AUC: logistic regression 0.67, tree 0.55. Catches Haiku's wrong bills: logreg 46%, tree 79% (at 50% accuracy).
- Nested: 80% of photos to Sonnet, 1% silent errors, ₹0.67 per bill. Optimistic: 47% to Sonnet, ₹0.42.

**What did not work.** The router is barely better than chance. Photo features do not predict Haiku's mistakes well. This is why the read policies (2.10) replaced it.

### 2.9 The cascade (`src/08_cascade_eval.py`)

PDF text (free) → trusted OCR total + Haiku for the other fields (same total = double-confirmed, different = ask the owner) → router → Haiku or Sonnet. Built and tested on 24 fake bills before real data existed.

Results (`outputs/eval/cascade_table.csv`, all 136 bills): about 1 silent error, ₹0.56 per bill, 65% to Sonnet. Superseded by the policies.

### 2.10 Read policies P0–P5 (`bot/policy.py`, `src/09_policies_eval.py`)

**Problem.** The router could not predict Haiku's errors. Better idea (Vipul): **verify Haiku's answer with free checks, then escalate**.

**How it works.** Haiku reads every non-PDF bill. Free checks on its answer:
- (a) Haiku's total vs the free OCR total
- (b) proofs on Haiku's total using the OCR text (words = digits, GST maths, line items)
- (c) Haiku listed a field as unsure
- (d) sanity: total missing or ≤ 0, date invalid or in the future, invoice number that is a GSTIN or phone number
- (e, P5e only) Haiku's invoice number vs the OCR's invoice number

A failed check means Sonnet re-reads the bill. The same file runs in the eval and in the bot (`PolicyReader`), so what is measured is what ships.

| Policy | Rule |
|---|---|
| P0 | Benchmark: printed → Haiku, handwritten → Sonnet (uses the hand label, so not deployable) |
| P1 | Checks a, c, d → Sonnet on fail |
| P2 | Checks a, b, c, d → Sonnet on fail |
| P3 | P2 + if Sonnet and Haiku still disagree on total or date → ask the owner |
| P4 | Learned escalation (check results + photo features, nested grouped CV) |
| **P5** "money first" | Sonnet **only** when a check on the **total** fails. Date / invoice / due-date doubts → ask the owner (date: one tap). Plus a **date-age rule**: a bill date more than 183 days before the photo arrived is flagged |
| P5_no_date_age | P5 without the date-age rule |
| P5e | P5 + check e |
| **P5_no_ocr** | P5 with checks a and b off; the OCR is never run (for a host without RapidOCR) |
| P5_trusted_ocr | P5, but check a fires only when the OCR total is proved (words or maths); b stays |

**Commands.** `python src/09_policies_eval.py --today 2026-10-28` (cache only, ₹0). Prints the table, a P3 / P4 / P5 / P5e block, the check audit and the decisions; writes `outputs/eval/policy_table.csv`, `policy_routes.csv`, `policy_choice.json` and `silent_errors_review.csv`. `--today` stands for the day the photo was received.

**Results** (`outputs/eval/policy_table.csv`, all 136 bills, after the label fix):

| Policy | Silent errors | 95% upper | To Sonnet | ₹/bill | vs Sonnet-only |
|---|---|---|---|---|---|
| Haiku only | 3 | 5.6% | 0% | 0.051 | 94% cheaper |
| Sonnet only | 1 | 3.4% | 100% | 0.822 | – |
| P0 (benchmark) | 2 | 4.6% | 49% | 0.458 | – |
| P1 | 2 | 4.6% | 61% | 0.595 | – |
| P2 | 2 | 4.6% | 71% | 0.664 | – |
| P3 | 1 | 3.4% | 71% | 0.664 | 19% cheaper |
| P4 | 0 | < 2.2% | 87% | 0.780 | 5% cheaper |
| P5 | 1 | 3.4% | 52% | 0.491 | 40% cheaper |
| P5_no_date_age | 2 | 4.6% | 52% | 0.491 | 40% cheaper |
| P5e | 1 | 3.4% | 52% | 0.491 | 40% cheaper |
| P5_trusted_ocr | 1 | 3.4% | 29% | 0.287 | 65% cheaper |
| **P5_no_ocr (live default)** | **1** | 3.4% | **17%** | **0.205** | **75% cheaper** |

The one remaining silent error for every P5 variant is B145's invoice number (one digit misread). No policy has a silent error on a total.

**Check audit** (photos only; printed in the `09` output and saved in `policy_choice.json`). 4 Haiku totals were wrong, and Haiku had marked all 4 as unsure itself:

| Check | Fires on | Caught a wrong total | Caught that no other check did | False alarms |
|---|---|---|---|---|
| a: any OCR total | 40 | 2 | 0 | 38 |
| a_trusted: proved OCR only | 0 | 0 | 0 | 0 |
| b: proof on OCR text fails | 17 | 1 | 0 | 16 |

So on these bills the OCR checks added cost without catching anything Haiku's own "unsure" flag missed. OCR remains insurance against a *confidently* wrong total, which did not occur in 136 bills.

**"Asked owner" changed meaning on 28 Oct.** It now counts any ⚠️ the owner sees (the models' own unsure fields + policy flags). So P3 shows 40%, not the 18% reported earlier (which counted only disagreement asks).

### 2.11 Date one-tap and other bot changes for P5

- When the bill date has ⚠️ (and the policy is money-first), the bot asks "Bill ki date aaj ki hai?" [Haan, aaj] [Kal] [Doosri date]. Asked once per bill, after any missing total or supplier. A field the owner set loses its ⚠️ (`checked` list in the store).
- OCR runs **in parallel** with the Haiku call (one OCR at a time), so it adds little to reply time.
- Each Sonnet call logs its own `ai_call` event, so the daily caps count it. At the cap, the bot flags total and date instead of calling Sonnet.

### 2.12 Question eval (`src/10_questions_eval.py`)

**Problem.** How often do the rules answer a *different* question than asked?

**How it works.** 62 invented messages in `data/eval/questions.csv` (all 9 intents + 14 tricky ones: payments that look like questions, two Sharmas, "kal" both ways, relative dates, unknown supplier, typo, voice style, off-topic). **Frozen before the first run** (sha256 in `docs/05_data_log.md`; the script warns if it changes). Each message goes through the real bot for a fake shop (6 invented suppliers, 12 bills, 6 payments, 1 reminder) with today fixed at Wed 28 Oct 2026. Outcomes: correct / safe (menu or follow-up) / WRONG.

**Commands.** `python src/10_questions_eval.py` → a before → after table, the WRONG list, and the SAFE list. The first run is saved as `outputs/eval/questions_detail_before.csv` and never overwritten.

**Results** (`outputs/eval/questions_table.csv`):

| | Correct | Safe | WRONG |
|---|---|---|---|
| **Before fixes (headline)** | **89%** | 8% | 3% (2 of 62) |
| After fixes (tuned on the same 62) | 97% | 3% | 0% |

- Both WRONG cases took "sabka" / "sabko" as a supplier name; the bot started a one-supplier question, so no wrong number was sent.
- Fixes: "all suppliers" words are never names; "saare supplier ka hisaab" = everyone's dues; bills/payments with a named period need no question word; a typo matching more words beats a one-word tie.
- Still safe but not understood: a past-tense reminder list question, and a payment whose name the payment parser misses.

### 2.13 PDFs on WhatsApp (`bot/read_pdf.read_whatsapp_pdf`, `Bot._on_document`)

**Problem.** A WhatsApp `document` fell through to "off-topic".

**Options.** Scanned multi-page PDF: last page (A, chosen) vs OCR every page to find the total (B, 6–15 s per page). Images sent as documents: photo flow (chosen).

**How it works.**
- Image sent as a document → the photo flow.
- Text PDF → every page read free; the last page with a total is used; supplier, date and invoice number come from page 1. A proved total → bill saved with **no AI call**.
- Scanned PDF, or no / doubtful total → the page is rendered at 1600 px and goes through the photo flow (current policy).
- Not a PDF, broken or password PDF, > 10 pages or > 10 MB (`MAX_PDF_MB`) → a short polite message.
- The rendered page is stored like a photo (30-day deletion).

**Result.** On the real PDFs in `data/raw/bills_inbox/done/`, 9 of 10 text PDFs (including 5-page and 3-page ones) were read free with the right total on the right page. The 10th is a 51-page "photo to PDF" batch of many bills and gets the "too big" message.

### 2.14 Free hosting (`docs/14_free_hosting.md`, `docs/15_render_env.md`, `render.yaml`)

**Options.**

| | Oracle Always Free (Arm VM) | Render free + keep-alive |
|---|---|---|
| Card | Needed to verify | Not needed |
| Machine | Up to 2 OCPU / 12 GB (allowance halved in 2026) | 0.1 CPU / 512 MB |
| OCR | Yes (`P5_trusted_ocr`) | No (`OCR=0`, `P5_no_ocr`) |
| Sleeps | No (but idle VMs may be reclaimed) | After 15 min without traffic → ping every 10 min |
| HTTPS | Caddy + sslip.io, or Cloudflare Tunnel (needs a domain) | Automatic |

**Decision (Vipul, 29 Oct): Render free.** `render.yaml` describes the service (free plan, Singapore, Docker, `/health`, 26 env vars; secrets with `sync: false`). Render passes env vars to the Docker build as build args, so `OCR=0` builds without RapidOCR.

### 2.15 Render readiness (29 Oct)

| Check | How | Result |
|---|---|---|
| Peak memory | `scripts/memory_check.py` (Linux): the real app with WhatsApp/Anthropic faked | Started 89 MB; one shop, one message at a time ~240 MB; bursts of 25 photos/PDFs/texts from 5 shops at once 667 MB → **~400 MB** after `MALLOC_ARENA_MAX=2` (Dockerfile) + at most 2 image decodes at a time |
| 200 before work | `tests/test_render_ready.py` drives the ASGI app and checks the order | Response sent before the work starts; a crash still answers 200 |
| No double processing | Same file | Retries, a doubled batch and two simultaneous deliveries → 1 bill, 1 reply (memory + Postgres) |
| Nothing on local disk | `REQUIRE_PERSISTENT_STORAGE=1` | `/health` returns 503 unless bills go to Postgres and photos to Supabase |
| Keep-alive | `deploy/github-workflows/keep-alive.yml` | Pings `/health` every 10 min; fails visibly if down; no-op without the secret |

The memory numbers come from Claude's sandbox (Python 3.10), not the real container. Check on Render after deploy.

### 2.16 Voice eval and long voice notes (`src/11_voice_eval.py`, `bot/voice.py`)

**Problem.** Measure how well spoken notes are understood, and handle notes longer than Sarvam's 30-second limit per request.

**How it works.**
- Audio is decoded with PyAV (`av`) to 16 kHz mono WAV.
- Cut into pieces of at most 29.5 s at the quietest 100 ms in the last 5 s before each limit (a pause between words). Each piece is transcribed by Sarvam, then the texts are joined.
- Then the same order as the live bot: question rules → payment rules → Haiku fallback.
- Notes over 2 minutes are not sent; the owner is asked for a shorter note. Each piece counts as one AI call.
- Eval: at most 30 new files per run; transcripts are cached by content hash; Sarvam seconds and a ₹ estimate are printed.
- You fill in `kind`, `true_is_payment`, `true_amount` and `true_supplier`. Scoring covers caught / wrong amount / missed / false payment / multi safe / multi only-one / too long, split by kind, with a label typo check.

**Commands.**
1. Put notes in `data\voice_test\`, then double-click `RUN_STEP11_voice_eval.bat`. Expect one line per note plus "Sarvam: N seconds of audio sent … about Rs …".
2. Fill the four columns in `outputs\eval\voice_results.csv`.
3. Double-click `RUN_STEP11b_voice_score.bat`. Expect the VOICE EVAL and BY KIND tables.

**Fill format:**

| Column | Values |
|---|---|
| `kind` | payment / question / future / received / bill / chatter / multi |
| `true_is_payment` | yes / no / multi |
| `true_amount` | whole rupees, digits only (multi: `5000+2000`) |
| `true_supplier` | the name as said (multi: `Sharma+Verma`) |

**Results.** Not run yet: it needs your recordings and the Sarvam key.

### 2.17 Privacy: pre-push check and guard (`.gitignore`, `scripts/secret_scan.py`)

- `.gitignore` covers `.env`, `data/`, `outputs/`, `*.db`/`*.sqlite`, audio files, stray photos/PDFs (brand icon allowed) and `_to_delete/`.
- A listing with a separate git directory (no repo created in the folder) showed **122 files would be pushed**.
- Matching every real supplier name and invoice number from `labels.csv` against those files found real data in **11 files** (tests, code comments, docs). All of it was replaced with invented values.
- **Guard (30 Oct):** if `data/eval/labels.csv` exists, the secret scan blocks any committed file containing a real supplier name or invoice number. It prints file:line only, never the value, and `secret-scan: ignore` does not silence it.
  - Names are compared normalised, at least 6 characters.
  - Codes with a letter, `-` or `/` are matched anywhere.
  - Plain-number invoice numbers are matched only on lines that mention an invoice or bill number.

**Command.** `python scripts/secret_scan.py --all` → `Secret scan: clean.`

### 2.18 Templates (for Meta)

All Utility, language **English (`en`)**, no buttons. Variables are in the order `bot/jobs.py` sends them; the full texts are in `docs/10_templates.md`.

| Template | Variables |
|---|---|
| `due_reminder` | supplier, bill amount, due date, supplier's current baaki |
| `weekly_summary` | week span, bill count, billed ₹, paid ₹, total baaki, top supplier (₹) |
| `owner_reminder` | reminder text, baaki |

Risk: Meta may reject `weekly_summary` for having many variables in a short body.

---

## 3. Errors and surprises

| # | What happened | How it was found | Fix |
|---|---|---|---|
| 1 | A reminder test failed on Sundays (the weekly summary fires on Sundays) | Full test run on a Sunday | Pinned that one test to a Wednesday |
| 2 | B016 invoice number blank | Field accuracy 89% on text PDFs | Not a bug: two numbers after "No."; the rule rightly refuses to guess |
| 3 | OCR: plain "Total" took item-table rows; seller and buyer headers merged; handwriting unreadable | First OCR dry run (58% silent errors) | Fixes A–C, proof gate D |
| 4 | Column split caused a "0.00" next-line read; OCR I→1 swaps in invoice numbers | Second dry run | Fixes G and F |
| 5 | A ₹1 maths tolerance would accept a last-digit misread | Reasoning on DEV | Nearest-rupee tolerance (≤ ₹0.51) |
| 6 | B120 (DEV): trusted-but-wrong total, because the bill prints the GST amount in words | DEV run in the overnight session | Words phrases labelled "tax" are ignored |
| 7 | Photo cleanup created a silent date error (21/7 → 21/3) | DEV comparison | Cleanup kept optional, not default |
| 8 | PyTorch (docTR) would not install | Install attempts in the sandbox (403, 3-min limit) | docTR left unrun; code tested with fakes |
| 9 | Tesseract's script check fails on 77 photos | Router features run | Gaps filled automatically |
| 10 | Reminder sentence answered as dues | Vipul's simulator test | Parse the new message before answering an open list |
| 11 | "pichhle hafte kitna diya" read as last payment | New question tests | "pichhle hafte" treated as a period |
| 12 | "traders" alone matched "Verma Traders"; 0.82 similarity accepted | Sharma/Verma tests | Generic-word rule; 0.9 cutoff; `same_supplier` |
| 13 | Postgres dated payments with the server's UTC date | Question feature review | Payments dated with the shop's IST date |
| 14 | `src/01_eval_bill_reading.py` does not load `.env` | Explaining next steps | Flagged; Vipul's run worked, so it was not changed |
| 15 | A demo command created an empty `outputs/eval/_fake_demo_tmp` | Noticed after the run | Vipul to delete by hand |
| 16 | B013: reply cut off by thinking tokens | Reading the cache file | `max_tokens` 2000, `truncated` error, re-read cached errors |
| 17 | `temperature` rejected by newer models | Vipul | Removed everywhere |
| 18 | Supplier with the city appended was scored wrong | First real run | `same_supplier` |
| 19 | Two label errors (B086 due date, B141 total) | Silent-error review | Vipul corrected them; backup `labels_v1.csv` |
| 20 | The labels rewrite changed line endings (LF instead of CRLF) | Diff showed every line changed | Rewrote with CRLF; diff = 2 lines |
| 21 | The date-age rule fires on 16 old archive photos | P5 results | Reported separately (P5_no_date_age); live photos are recent |
| 22 | Check e could not catch B145: OCR had the right number but the field rules did not extract it (merged headers) | P5e run | Not fixed; a text-presence check was seen but not built (designed after seeing B145) |
| 23 | RapidOCR peaked at 0.7–0.9 GB RAM, 6–15 s per photo | Measuring on real photos | Parallel with Haiku; at most 1 OCR at a time; later OCR dropped on Render |
| 24 | Docker not available in the sandbox | – | Image size from package installs (+~360 MB with OCR) |
| 25 | Questions: "sabka/sabko" read as names; other gaps | Question eval | 4 rule fixes (2.12) |
| 26 | PDF: a short last page (< 80 characters) was ignored; supplier read from the total page | PDF tests | 15-character minimum for pages of a text PDF; header fields from page 1 |
| 27 | Memory 667 MB in bursts | Memory harness | `MALLOC_ARENA_MAX=2` + decode limit |
| 28 | uvicorn crashed with uvloop in the sandbox | Harness | Sandbox only; the asyncio loop was used for measuring |
| 29 | Tests could make real paid calls (`bot.app` loads `.env` on import) | A test hit a real API error | Autouse fixture removes API keys in every test |
| 30 | Voice and question fallbacks had only 120–150 tokens (thinking shares the budget) | Code review during voice work | 1000 tokens; cut-off answers dropped |
| 31 | Sarvam takes at most 30 s per request; the live bot sent whole notes | Sarvam docs | Splitting at pauses (2.16) |
| 32 | Real supplier names / invoice numbers in tests and docs | Pre-push matching | Replaced; guard added (2.17) |
| 33 | Secret scan took minutes walking `data/` on the synced folder | Test timeouts | Skipped folders are no longer entered |
| 34 | The file-transfer tool reused an old upload for the same staged path | File content did not change | Unique staging names |
| 35 | A test wrote a probe file into `tests/` that could not be deleted | Permission error | Moved to `_to_delete/` (gitignored); the test now uses a temp folder |
| 36 | The OCR trusted check never fired; check b gave 16 of 17 false alarms | Check audit | Led to the P5_no_ocr decision |

---

## 4. Vipul's decisions

| Date | Decision | Reason given |
|---|---|---|
| 25 Oct | Total label priority order; look 1–2 lines below; tolerant of OCR noise | One pharma format prints invoice amount before net payable; OCR reuse |
| 25 Oct | OCR confidence threshold fixed at 60, never tuned | Tuning on 136 bills would overfit |
| 25 Oct | Proof gate: total trusted only with words = digits | Confidence is not proof |
| 25 Oct | Record the seller GSTIN; supplier name stays unsure | Map GSTIN to known suppliers later |
| 25 Oct | Freeze free-OCR rules after F and G | No more tuning on the same bills |
| 25 Oct | PDF-text bills skip Claude; OCR-trusted photos still go to Haiku (OCR total = second read) | – |
| 25 Oct | Question feature design (DB answers only, menus, reminders); defaults: control shops get their own reminders, "is hafte" = Monday–today, bills counted by received date | Accuracy over cleverness |
| 25 Oct | Two matching suppliers → list; empty answers name the period; `owner_reminder` to be submitted to Meta | – |
| 25 Oct | Consent: not strict, but typed yes only as the whole message | – |
| 25 Oct | Skip Surya; RapidOCR first; docTR only if RapidOCR does not clearly win | Licence and weight |
| 25 Oct | Overnight: RapidOCR default; DEV/TEST split; maths proofs; memory; cleanup; docTR; strict safety rules | Raise trusted totals with 0 wrong |
| 26 Oct | Router threshold must be nested | Honest numbers |
| 27 Oct | `same_supplier` for scoring and for the bot; remove `temperature`; fix B013 | Eval = production |
| 28 Oct | Policy pick rule: cheapest with silent errors ≤ Sonnet-only; second Haiku read only with permission | – |
| 28 Oct | Labels: B086 due date and B141 total changed; B134 and B145 kept (model errors) | Checked the photos |
| 28 Oct | P5 replaces P3 if as safe and clearly cheaper → it did | – |
| 28 Oct | P5e default only if 0 silent errors and ≥ 30% cheaper → not met | – |
| 29 Oct | Freeze questions before the first run; "before" score is the headline | Honest eval |
| 29 Oct | Scanned multi-page PDF → last page; images sent as documents → photo flow | – |
| 29 Oct | P5_trusted_ocr default if silent ≤ P5_no_ocr and ≤ ₹0.30/bill (set before the run) → met | – |
| 29 Oct | **Host on Render free; live default = P5_no_ocr**; P5_trusted_ocr stays the Oracle option | No card; the trusted check never fired; check b 16/17 false alarms; same measured safety |
| 29 Oct | Vipul submits the templates in Meta and adds the Sarvam key himself | – |
| 30 Oct | Split long voice notes live; cap at 2 minutes; privacy guard; voice "multi" and "kind" | – |
| 30 Oct | Voice and typed text to become a full input channel (plan only) | – |

---

## 5. Setup steps guided (no values copied)

| Step | Where |
|---|---|
| Install Tesseract for Windows (path `C:\Program Files\Tesseract-OCR\tesseract.exe` or the `TESSERACT_CMD` setting) | Note in `RUN_STEP6_free_readers.bat` |
| Install RapidOCR: `python -m pip install -r requirements-ocr-extra.txt` | Laptop |
| Optional docTR: `requirements-ocr-doctr.txt` (~1 GB, never run) | Laptop |
| Fill `prices` and `usd_to_inr` in `src/eval_config.json` (public Anthropic prices; rate of the day) | Laptop |
| Check the Anthropic key: `RUN_check_setup.bat` → OK line | Laptop |
| Run the Claude eval: `--check`, `--limit 5`, then full | Laptop |
| Add the reminders table: `RUN_setup_database.bat` | Laptop → Supabase |
| Submit templates: WhatsApp Manager > Message templates > Create; category Utility; language English | Meta |
| Sarvam key: `SARVAM_API_KEY` in `.env` (from the Sarvam dashboard) | Laptop |
| Render: New > Blueprint (`render.yaml`); type each secret once (names in `docs/15_render_env.md`) | Render |
| GitHub secrets: `HISAAB_BOT_URL`, `HISAAB_JOBS_TOKEN`, `HISAAB_KEEPALIVE_URL` | GitHub > Settings > Secrets and variables > Actions |
| Meta webhook: Callback URL `https://<host>/webhook`, Verify token, subscribe to messages | Meta app > WhatsApp > Configuration |

Nothing was signed up for by Claude. Postgres tests ran on a temporary Postgres 16 inside Claude's sandbox (not part of the project); on the laptop they run when `TEST_DATABASE_URL` is set.

---

## 6. Open questions and next steps

**Waiting for Vipul**
1. **Voice/typed text as a full input channel**: the plan is ready (bill without photo, payments, questions, supplier picker, proof photo, fix/undo, received/future guards; DB changes: bill source, proof link, edit history; about 4 build days). Needs "go" plus two answers:
   - **Phasing:** Phase 1 before the pilot, everything after, or everything now.
   - **Metric:** whether voice/typed bills count toward the pilot metric "3+ confirmed bills in days 7–13". This should be logged in `docs/09_experiment.md` **before the pilot starts on 2 Nov**.
2. Photo bills with an unknown supplier still create the supplier silently on confirm. The new plan's supplier picker fixes this.
3. Record ~25 voice notes and run the voice eval.
4. Submit the three templates; watch whether `weekly_summary` is rejected.
5. Delete `_to_delete\` and `outputs\eval\_fake_demo_tmp` by hand.

**Known gaps**
- The bot proposes at most one payment per note; a note with two payments loses one. Possible fix: detect two amounts and ask for them one at a time.
- The payment parser keeps filler words in names ("haan bhai woh … the"), so the supplier is not found.
- An invoice-number ⚠️ cannot be fixed from the Badlo menu (WhatsApp allows 3 buttons).
- The privacy guard skips real names shorter than 6 characters and bare numbers outside invoice context.
- `src/01_eval_bill_reading.py` still does not call `load_dotenv()`.
- Supplier memory (`bot/templates.py`) and docTR are built but not used.
- The second Haiku read (about ₹7) was never run.
- Memory was measured in a sandbox, not on Render.
- `outputs/` is no longer committed, so any number quoted publicly must be regenerated with the commands in this document.

**What I would do next**
1. Log the pilot metric decision today (2 Nov).
2. Build Phase 1 of the input-channel plan with a frozen entries eval first (quote the "before" score).
3. Deploy to Render; check `/health`, real memory and one end-to-end photo, PDF and voice note.
4. After a few hundred pilot bills, re-check the OCR question: if owners correct a total Haiku was sure about, move to Oracle + P5_trusted_ocr.
5. Collect 30–50 real owner questions and voice notes as a held-out test set.

---

## Appendix A. Test counts over time

| When | Tests passed (skipped) | With Postgres |
|---|---|---|
| Step 1 (25 Oct) | 146 (29) | – |
| OCR A–G | 158 → 165 | – |
| Questions | 268 (51) | 319 |
| Consent + reminders | 305 (73) | 378 |
| RapidOCR / overnight / router | 308 → 320 | 381 → 393 |
| Eval fixes | 328 → 337 (76) | 413 |
| Policies P3 / P5 | 357 (78) → 376 (83) | 435 → 459 |
| Questions eval + PDFs | 399 (91) | 490 |
| Trusted-OCR / Render readiness | 402 → 415 (94) | 493 → 509 |
| Voice split + privacy guard (30 Oct) | **425 (96)** | **521** |

## Appendix B. Command cheat-sheet (from the HisaabAI folder)

| Command | Cost | Expected |
|---|---|---|
| `python -m pytest -q` | free | `425 passed, 96 skipped` (as of 30 Oct) |
| `python scripts/secret_scan.py --all` | free | `Secret scan: clean.` |
| `python src/06_free_readers_eval.py` | free (cached OCR) | FREE READERS table; RapidOCR printed_TEST trusted 49% (17), trust wrong 0 |
| `python src/01_eval_bill_reading.py --runs haiku_v2 sonnet_v2` | free if cached | SUMMARY: haiku_v2 76% all fields, sonnet_v2 86% |
| `RUN_STEP7_router_cascade.bat` | free | Router CV lines, NESTED line, CASCADE table |
| `python src/09_policies_eval.py --today 2026-10-28` | free | Policy table; `OCR DECISION … default = P5_trusted_ocr` (the rule's verdict; the live default is P5_no_ocr by Vipul's later decision) |
| `python src/10_questions_eval.py` | free | `ALL 62 correct 89% -> 97% … WRONG 3% -> 0%` |
| `RUN_STEP11_voice_eval.bat` / `RUN_STEP11b_voice_score.bat` | Sarvam per second (~₹29/hour) | Transcripts CSV / VOICE EVAL tables |
| `MALLOC_ARENA_MAX=2 python scripts/memory_check.py` (Linux/WSL) | free | `VERDICT: peak ~400 MB … OK` |
