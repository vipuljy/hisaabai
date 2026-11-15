# Build spec: cheap-first bill reading (cascade + router)

Paste this into the Claude that builds it. It must fit the existing repo and eval; do not change existing scoring.

## Goal
Read most bills for free and send only the hard ones to Claude, without more silent errors.
Report one table: accuracy, silent-error rate, % of bills sent to Claude, cost per bill, for each reader and for the full cascade.

## What already exists (do not rewrite)
- `bot/extract.py`: `FIELDS` (supplier_name, bill_date, invoice_number, total_amount, due_date), `NORMALISERS`, `extract_bill()` (Claude vision).
- `src/evalkit.py`: `load_labels()` (ignores rows whose notes start with SKIP), `score_bill()`, `summarise()`. Cache format per bill:
  `data/eval/runs/<run_name>/<bill_id>.json` = `{"bill_id","model","prompt","fields":{...,"unsure_fields":[]},"raw_text","error","input_tokens","output_tokens","latency_ms"}`.
- `src/01_eval_bill_reading.py`: runs Claude runs from `src/eval_config.json`, writes `outputs/eval/summary_by_run.csv`.
- Data: `data/eval/images/B###.jpg`, `data/eval/labels.csv` (136 usable bills: 70 printed, 66 handwritten), `data/eval/source_map.csv` (bill_id -> original file, PDF page), originals in `data/raw/bills_inbox/done/`.

**Rule: every new reader writes the same cache format into its own `data/eval/runs/<name>/` folder,** so `evalkit` scores it exactly like Claude. Free readers: tokens 0.

## Readers to build (new files)
1. `bot/read_pdf.py` - `read_pdf_text(pdf_path, page) -> fields`. Text layer via `pypdfium2`; regex for "Total Invoice Value", "GRAND TOTAL", "NET AMOUNT PAYABLE", "Net Payment", "Invoice No", "Date", "Due Date", and supplier = first business line. Returns all-null + `error="no_text"` for scanned PDFs (e.g. the 51-page photo bundle).
2. `bot/read_ocr.py` - `read_ocr(image_bytes) -> fields + ocr_confidence`. Tesseract (`pytesseract`, eng) first; PaddleOCR optional behind a flag. Same rule set as the PDF reader (shared module `bot/field_rules.py`). Auto-rotate (Tesseract OSD) because some photos are sideways.
3. `bot/checks.py` - free validators, each returns pass/fail/na:
   - `words_match_total`: "Rupees ... only" in words == total in digits (write a small Indian number-words parser: lakh, thousand, hundred).
   - `items_sum`: sum of line amounts + tax + round-off within ₹2 of total (only if line amounts were read).
   - `sane_fields`: date parses day-first and is within 2 years; total > 0 and < 50 lakh; invoice number not a GSTIN/phone.
   - `not_balance`: total is not on a line containing "balance", "amount due", "B/F", "C/F".
4. `bot/templates.py` (stretch) - per-supplier memory: after a bill is confirmed, store the label positions/keywords; next bill from the same GSTIN/name uses them. Report separately.

## Router (the trained model)
- `src/06_router.py`. One row per bill, features computed for free:
  blur (variance of Laplacian), brightness, contrast, aspect ratio, is_pdf_with_text, OCR mean confidence, OCR word count, digit ratio, Devanagari char ratio, each check result, printed-vs-handwritten score.
- Target: `free_ok` = the free reader got total_amount AND bill_date correct (from labels).
- Model: logistic regression and a depth-3 decision tree (scikit-learn). Pick by recall of "free_ok = False" (we must not let bad free reads through).
- **Evaluation: 5-fold stratified cross-validation on the 136 bills. Never report accuracy on bills the model trained on.** Save the tree as an image for the case study.
- Threshold chosen for: silent-error rate of cascade <= silent-error rate of Claude-only (+1 point max).

## Cascade
`src/07_cascade_eval.py`: for each bill, PDF text -> if checks pass, done. Else OCR -> router says free_ok and checks pass -> done. Else Haiku -> if `unsure_fields` empty, done. Else Sonnet.
Uses cached results only (no new API calls if Claude runs already cached). Writes run `cascade` to `data/eval/runs/cascade/` with an extra `"route"` field, plus `outputs/eval/cascade_table.csv`:
`reader, n, accuracy_all_fields, total_amount_accuracy, silent_error_rate, pct_sent_to_claude, cost_per_bill_inr`
with rows: pdf_text, ocr_rules, haiku_v2, sonnet_v2, cascade. Plus the same split by printed / handwritten.

## Tests (pytest, no network, no API)
Number-words parser (e.g. "Rupees One Lakh One Thousand Seven Hundred Fifty Seven Only" -> 101757), each check, regex rules on 5 sample text snippets (Marg ERP "GRAND TOTAL", KARP "Total Invoice Value", a pharma format's "NET AMOUNT PAYABLE", balance-line trap), router CV runs on a tiny synthetic table, cascade routing logic with fake cached records.

## Constraints
- Python 3.13, add deps to `requirements.txt` (`pytesseract`, `opencv-python-headless`, `scikit-learn`); Tesseract binary install note for Windows in a `RUN_*.bat` comment.
- Nothing from `data/` committed. No API keys in code. Run `python scripts/secret_scan.py` before commit.
- Keep each file short, with a docstring saying what it does in plain words.

## Order
1. checks + field_rules + PDF reader (+ tests)  2. OCR reader  3. Claude eval runs (needs API key)  4. router + cascade table.

## Build log
- 2026-10-25, step 1 built: `bot/field_rules.py` (regex rules, priority order for totals, label/amount on next line, OCR-tolerant labels), `bot/checks.py` (4 free checks + Indian number-words parser), `bot/read_pdf.py` (pypdfium2 text layer, cache in `data/eval/runs/pdf_text/`), `src/06_free_readers_eval.py`, `tests/test_free_readers.py` (36 tests, no network).
- A failed check puts the field in `unsure_fields`, so a doubtful free read is flagged (asked), not silent. Totals use priority NET AMOUNT PAYABLE > GRAND TOTAL > Total Invoice Value > Net Payment > Net Amount > Bill Amount > Total.
- Coverage: only 9 of 136 labelled bills have a PDF text layer; the 51-page photo bundle is scanned (`no_text`) and 104 bills are photos (`not_pdf`), all counted as missed, never silent.
- Results (pdf_text, Rs 0/bill): text-PDF bills (n=9) 89% all fields right, 100% total right, 0% silent errors; all 136 bills 6% all fields right, 0% silent errors.
- Only miss: one invoice number whose label and value sit on separate lines with another number in between (left blank, safe). Numbering: router = `src/07`, cascade = `src/08`.
- 2026-10-25, step 2 built: `bot/read_ocr.py` (Tesseract eng, psm 4, 2x upscale, OSD auto-rotate, lines split at column gaps; raw word boxes cached in `data/eval/runs/ocr_rules/` so rules re-score without OCR), `tests/test_read_ocr.py`; `src/06_free_readers_eval.py` now scores both readers; `RUN_STEP6_free_readers.bat`; `pytesseract` in requirements.
- OCR guards (fixed, not tuned): page or TOTAL-line confidence < 60 -> unsure; total trusted only if "Rupees ... Only" words match the digits; plain "Total" label never trusted; supplier name always unsure (seller GSTIN recorded instead, only if its check character is valid); invoice no. trusted only if the same 4+ character number appears twice.
- Results on 136 photos (Rs 0/bill): OCR silent errors 83% with rules only -> 0% with guards; printed bills: total trusted 4% (3 of 70), trusted-but-wrong 0; handwritten: 0% trusted (Tesseract cannot read handwriting). Free-OCR rules FROZEN at `rules_v3`: no more tuning on these 136 bills.
- (1) Free OCR on photos is unsafe without guards: 83% of bills carry a confident wrong field (71% of printed, 95% of handwritten).
- (2) With guards it is safe but covers only ~4% of printed photos (3 of 70 trusted totals, 0 wrong).
- (3) The PDF text reader is the real free path: on text PDFs, 100% total right, 89% all fields right, 0 silent errors, Rs 0.
- 2026-10-25, second free engine: `bot/read_ocr.py` now takes `engine = tesseract | rapidocr` (RapidOCR 3.x = PP-OCR models on onnxruntime, ~80 MB, Apache-2.0, Python 3.13 OK; optional install `requirements-ocr-extra.txt`). Both engines produce the same word boxes, so rules_v3 and the checks are unchanged; rotation uses Tesseract's orientation check for both. New eval run `ocr_rapid`, plus columns "proof gate" (words = digits, the same test for every engine) and seconds per photo.
- Results, printed photos (n=70), Tesseract -> RapidOCR: total right 16% -> 53%; trusted totals 3 -> 13 (19%); proof-gate totals 4% -> 23%; trusted-but-wrong 0 -> 0; silent errors 0% for both with guards (rules only: 71% -> 59%). Speed about the same here (10.9 vs 12.1 s/photo, 2-core sandbox, Python 3.10). Handwritten: RapidOCR reads 29% of totals right but trusts none (Tesseract 0%).
- Decision: RapidOCR clearly beats Tesseract on printed photos, so docTR (~1 GB with PyTorch) was NOT installed or tested; Surya skipped (GPL code, non-commercial weights). Confidence threshold kept at 60 for both engines (RapidOCR's scale runs higher; not tuned).

### Overnight run, 25–26 Oct 2026 (unattended; every decision logged here)
- **Step 0, done:** RapidOCR is the default free photo engine (`read_ocr.default_engine()`), with Tesseract as the fallback. `RUN_STEP6_free_readers.bat` now installs `requirements-ocr-extra.txt`. Logged in `docs/04_decisions_log.md`.
- **Step 1, done:** `data/eval/split.csv` (seed 20261026) is written once by `src/06_free_readers_eval.py` and never overwritten. Bills sharing a supplier or an original file are one group, and no group appears in both halves.
  - Printed: 35 DEV / 35 TEST.
  - Handwritten: 50 / 16. The 51-page handwritten bundle is one supplier, so it can't be split.
  - Every new rule below was designed by looking at DEV bills only. The earlier `rules_v3` extraction was designed on all 136 bills before this split, so TEST is not fully clean for extraction; it is clean for the new trust rules.
- **Step 2, done (`rules_v4`):** a total is trusted only with a **proof** plus the sanity and balance checks. This is the same for every engine; OCR confidence and the plain-"Total" rule no longer decide it.
  - The three proofs:
    - **words:** the amount in words equals the total in digits.
    - **maths, rate formulas:** Marg/Sarvottam `base*rate%=tax` and KARP `CGST @ x% on base`.
    - **maths, GST summary:** taxable / sub total (− discounts) + CGST + SGST / IGST (+ round-off).
  - **Line items:** a sum of item rows + taxes is implemented, but it never fired on DEV because OCR columns come out scrambled.
  - **Recovery:** when the rules found no total, 0, or only the weak "Total" label, a printed amount that equals the words or maths result is taken instead, and still needs its proof.
  - Decisions:
    - **Tolerance is the nearest rupee (≤ ₹0.50), not ₹1.** With ₹1, a last-digit misread (31272 for 31271) would pass.
    - **DEV caught a trusted-but-wrong (B120):** the bill printed the GST amount in words too ("Tax Amount (in words): …"). Words labelled "tax" are now ignored, with a test added. It's back to 0.
- **Step 3, done (`bot/templates.py`):** supplier memory learns the label next to a confirmed total and its position on the page, keyed by seller GSTIN, else by name. Evaluated leave-one-out within the same half.
  - **Result:** no gain for RapidOCR on DEV or TEST (13 → 13, 17 → 17). Tesseract gained +1 on DEV (4 → 5) and 0 on TEST.
  - It is not wired into the bot yet. That needs a table for the templates and the cascade (step 08).
- **Step 4, tried and NOT adopted:** photo cleanup (border trim + deskew + CLAHE), engine `rapidocr_clean`.
  - **DEV printed:** trusted totals 13 → 15, totals right 43% → 46%.
  - **DEV handwritten:** it added 1 silent error. On B018, the date 21/7/26 was read as 21/3/26 with 82% confidence; without cleanup it was unreadable, which is safe.
  - **Rule kept on DEV (B122, Tally layout):** a bill date read from a bare "Dated" label, or a page showing 2+ different labelled dates, is now marked unsure.
  - **Decision (on DEV):** a gain that adds a silent error is not a clear help, so cleanup stays available as an option and is not the default.
  - **TEST:** 16 vs 17 trusted with cleanup, consistent with that decision.
- **Step 5, adapter built but NOT run:** `engine=doctr` and the "both engines agree" proof (`ocr_agree`) are written and tested with fakes. Install `requirements-ocr-doctr.txt` to run them.
  - PyTorch could not be installed in the overnight sandbox. Try 1: the CPU index (download.pytorch.org) is blocked by the sandbox network (403). Try 2: the ~3 GB Linux PyPI build did not finish within the sandbox's 3-minute command limit.
  - This is a sandbox limit, not a Python 3.13 problem: on Windows, PyPI's torch is the small CPU build.
  - docTR is in its own `requirements-ocr-doctr.txt`, not in `-extra`, so `RUN_STEP6` stays a light (~80 MB) install.
- **Run notes:**
  - All OCR was done in a scratch copy on a 2-core sandbox (Python 3.10), so seconds per photo are slower than a laptop.
  - The caches were copied into `data/eval/runs/` (new folders, nothing overwritten), so your run takes seconds. Use `--fresh` to re-OCR on your machine.
  - `labels.csv` and the photos were not touched. 387 tests pass with Postgres (314 + 73 skipped without it); secret scan clean.

#### Final results — total amount on PRINTED photos (TEST is the number to quote)
| Engine (rules_v4) | Half | Total right | Total trusted | Trusted-but-wrong | Trusted by words / maths / memory / agree | Recovered | Silent errors | s/photo |
|---|---|---|---|---|---|---|---|---|
| Tesseract | DEV | 14% | 11% (4) | 0 | 4 / 0 / 0 / – | 3 | 0% | 12.2 |
| Tesseract | **TEST** | 34% | **14% (5)** | **0** | 5 / 0 / 0 / – | 3 | 0% | 9.6 |
| Tesseract + memory | DEV / TEST | 17% / 34% | 14% (5) / 14% (5) | 0 / 0 | 2/0/3 · 5/0/0 | 1 / 3 | 0% | – |
| **RapidOCR (default)** | DEV | 43% | 37% (13) | 0 | 8 / 5 / 0 / – | 2 | 0% | 12.6 |
| **RapidOCR (default)** | **TEST** | 77% | **49% (17)** | **0** | 11 / 6 / 0 / – | 3 | 0% | 11.6 |
| RapidOCR + memory | DEV / TEST | 43% / 77% | 37% (13) / 49% (17) | 0 / 0 | same as above | 2 / 3 | 0% | – |
| RapidOCR + cleanup | DEV | 46% | 43% (15) | 0 | 10 / 5 / 0 / – | 5 | 0% (1 handwritten) | 10.8 |
| RapidOCR + cleanup | TEST | 71% | 46% (16) | 0 | 11 / 5 / 0 / – | 4 | 0% | 10.7 |
| docTR | – | not run (PyTorch blocked in the sandbox) | | | | | | |
| RapidOCR + docTR agree | – | not run (needs docTR) | | | | | | |

Before tonight (rules_v3, same cached OCR): RapidOCR trusted DEV 7 / TEST 6; Tesseract DEV 1 / TEST 2.
**TEST trusted totals with RapidOCR: 6 → 17 of 35 (17% → 49%), 0 trusted-but-wrong.** All 70 printed bills: 13 → 30 (19% → 43%).
Trusted-but-wrong, every engine and both halves: **none**. B120 was caught on DEV during design and fixed before the final run.
Handwritten (66): no engine trusts any handwritten total (0%); RapidOCR reads 29% of them right but cannot prove them.

#### Summary
- **What helped:** proofs instead of confidence. The maths proofs (Marg/Sarvottam rate formulas, KARP "@ x% on", GST summary) gave 6 of the 17 TEST trusted totals, and recovering a total from the words or maths gave 3. Ignoring "tax amount in words" prevented the only trusted-but-wrong.
- **What didn't help:** supplier memory (0 gain with RapidOCR; with this little data its labels repeat what the rules already find), photo cleanup (+2 DEV, −1 TEST, plus a silent date error), and the line-items proof (OCR scrambles the item columns). docTR is untested.
- **Recommended setup for the bot:**
  - PDF text first (exact, free).
  - Then RapidOCR + `rules_v4` on photos. The total is trusted only with a words or maths proof (about half of printed photos).
  - Then Haiku for all other fields and every untrusted total, with the OCR total as a free second read (agree = double-confirmed).
  - Tesseract only as the fallback engine.
  - Handwritten photos go straight to the Claude route; free OCR can't prove them.
- **Next:** run docTR on your laptop (commands below). If "both engines agree" adds trusted totals on TEST with 0 wrong, it is a cheap extra proof. Then build the router (07) and cascade (08) on these cached runs.
- **Morning commands (from the HisaabAI folder):**
  1. `python -m pip install -r requirements-ocr-extra.txt` → "Successfully installed …" or "Requirement already satisfied".
  2. `python -m pytest -q` → `314 passed, 73 skipped`.
  3. `python src/06_free_readers_eval.py` → the table above in seconds (OCR caches reused; docTR "not available: skipping").
  4. Optional docTR (~1 GB): `python -m pip install -r requirements-ocr-doctr.txt`, then `python src/06_free_readers_eval.py --readers ocr_rapid ocr_doctr` (about 10–20 s per photo) → adds `ocr_doctr` and `ocr_agree` rows.
  5. Optional, to re-OCR on your own machine instead of using the sandbox's caches: add `--fresh`.

### Night of 26 Oct: steps 07 + 08 built, waiting for the real Claude runs
**Ready (tested end to end on FAKE cached runs in a temp folder, never in `data/`):**
- `bot/router.py`: free features per photo.
  - Image: blur, brightness, contrast, ink, aspect, and Tesseract's script/orientation check when it can read the page (it couldn't on 77 of 136 real photos; those gaps are filled with the median).
  - RapidOCR reading: confidence, low-confidence share, words, digits, table-like lines, GSTIN, total found / trusted, words or maths proof.
  - The hand label (printed/handwritten) is never a feature.
  - On the 136 real photos the features clearly separate printed from handwritten without it: contrast 48 vs 69, ink 0.20 vs 0.32, low-confidence words 2% vs 13%.
- `src/07_router.py`: target = Haiku read total AND bill date right (from `haiku_v2`).
  - Models: logistic regression and a depth-3 tree, scored with 5-fold grouped CV. Groups are supplier + original file from `split.csv` (built from `source_map.csv` plus the supplier), stricter than the file alone.
  - It picks the model that catches more of Haiku's wrong bills, then the cheapest threshold with silent errors ≤ Sonnet-only + 1 point.
  - Writes `router_cv.csv`, `router_thresholds.csv`, `router_oof.csv`, `router_choice.json`, `router_tree.png` and `router_model.pkl` in `outputs/eval/`.
- `src/08_cascade_eval.py`: per bill, PDF text (free) → RapidOCR trusted total + Haiku for the other fields (agree = double-confirmed, disagree = ask the owner) → router → Haiku or Sonnet.
  - Variant `cascade_escalate`: a Haiku reading with any unsure field is re-read by Sonnet.
  - Writes `outputs/eval/cascade_table.csv` with reader × scope (all / printed / printed_TEST / handwritten) rows and these columns: all fields right, total right, silent errors, % free / Haiku / Sonnet, ₹ per bill, Haiku-vs-OCR agree. Also writes `cascade_routes.csv` and `data/eval/runs/cascade/`.
- **Without the Claude runs,** both scripts print "Run RUN_STEP1_eval.bat first" and stop (exit 1, no crash). Checked on the real folder tonight.
- **Fake-run check:** Haiku is silently wrong on 8 of 24 bills, Sonnet on none. The cascade sent every hard bill to Sonnet: 0% silent errors at ₹0.28 per bill vs Sonnet-only ₹0.53.
- **Honesty notes:**
  - The threshold is picked on the same out-of-fold predictions it is scored on, a small optimism; the CV itself is clean.
  - The cascade's free and OCR steps use `rules_v4`, which was designed on DEV. Quote `printed_TEST`.
- **Housekeeping:**
  - Added `scikit-learn`, `matplotlib`, `opencv-python-headless` to `requirements.txt`, and a new `RUN_STEP7_router_cascade.bat`.
  - Decisions logged in `docs/04_decisions_log.md`; data in `docs/05_data_log.md`.
  - 319 tests pass + 73 skipped (392 with Postgres); secret scan clean.
  - **One slip:** my demo command created an empty folder `outputs/eval/_fake_demo_tmp`. I can't delete files, so please delete it.

**Commands after the Claude eval (from the HisaabAI folder):**
1. Fill the prices and `usd_to_inr` in `src/eval_config.json` (Anthropic pricing page), or the cost column stays empty.
2. `python src/01_eval_bill_reading.py --runs haiku_v2 sonnet_v2` (or `RUN_STEP1_eval.bat` for all 4 runs) → `data/eval/runs/haiku_v2/` and `sonnet_v2/`, 136 files each.
3. `python -m pip install -r requirements-ocr-extra.txt` → installs scikit-learn and matplotlib too.
4. `python src/07_router.py` → "CROSS-VALIDATED (grouped, out-of-fold)" with two lines (logreg, tree): "catches Haiku's wrong bills" %, accuracy, AUC, then the chosen threshold. Look at `outputs/eval/router_tree.png`. First run about 1 minute (reads every photo once).
5. `python src/08_cascade_eval.py` → the CASCADE table. The rows to quote are `cascade` and `sonnet_v2` at scope `printed_TEST`, plus `all`.
6. Or steps 4 + 5 in one go: double-click `RUN_STEP7_router_cascade.bat`.
7. `python -m pytest -q` → `319 passed, 73 skipped`.
- **26 Oct, fix: the router threshold is now chosen NESTED.**
  - **How it works:** in each of the 5 outer folds, an inner grouped CV on the training bills only gives out-of-fold predictions for those bills. The threshold is picked on those predictions, then applied to that outer fold's held-out bills. No bill is routed by a threshold that saw it; a test checks this.
  - **Both numbers are reported.** `src/07_router.py` prints "NESTED (honest)" and "same predictions (optimistic)" (the old way). `router_choice.json` stores both, with the 5 per-fold thresholds. `router_oof.csv` has per-bill `sonnet_nested` and `sonnet_same_predictions`.
  - **In the cascade table,** `src/08_cascade_eval.py` uses the nested decision for `cascade` and `cascade_escalate` (quote these) and adds a `cascade_same_pred` row for comparison.
  - **What stays global:** the saved model for the live bot still uses one threshold picked on all bills, because the bot needs a single number. The choice between logreg and the tree is still made on the outer out-of-fold predictions (2 candidates; noted, not nested).
  - **Fake-run check (24 bills):** nested picked 0.05 in all 5 folds, giving 33% to Sonnet, 0% silent errors and ₹0.29 per bill. The same-predictions method gave identical numbers (0.05), as expected on perfectly separable fake data.
  - **Real numbers:** still to come. Run `python src/07_router.py` after the Claude eval and paste both lines here: nested = the number to quote, same-predictions = how optimistic the old method was.

### 28 Oct: real Claude runs + "verify, then escalate" policies (step 9, cached readings only, Rs 0)
- **Claude runs (136 bills):** Haiku-only got all fields right on 76% of bills, 5 silent errors (3.7%), Rs 0.05/bill (about 408 thinking + 91 answer tokens per bill). Sonnet-only: 85%, 2 silent (1.5%), Rs 0.82/bill (205 thinking + 89 answer). No truncated replies; B013 was re-read fine after the max_tokens fix.
- **Step-08 cascade (photo-feature router):** nested folds picked thresholds 1.01 / 0.05 / 0.15 / 0.25 / 1.01, which sent 68% of bills to Sonnet, with 3 silent errors and Rs 0.58/bill. The "same predictions" threshold looked better (Rs 0.21/bill, 0.10), which is the optimism the nested check was built to expose. The photo-feature router barely beats chance (grouped CV AUC: logreg 0.65, tree 0.60).
- **Step 1, review:** `outputs/eval/silent_errors_review.csv` lists 10 rows across 5 bills (B053, B086, B134, B141, B145). Several look like **label** questions: B086's due date was read identically by both models, B141 has its total cut off (so the label is blank), and B134 / B145 differ only in the year (2025 vs 2026). Vipul is checking the photos.
- **Step 2:** `bot/policy.py` (shared by the eval and the bot) and `src/09_policies_eval.py`. Haiku reads every non-PDF bill. Escalation to Sonnet happens on:
  - (a) OCR total ≠ Haiku total
  - (b) a computable proof (words / GST maths / line items) where none passes
  - (c) any field in Haiku's unsure_fields
  - (d) sanity: total missing or ≤ 0, date invalid or in the future, invoice number = GSTIN / phone
  - P3 also flags total / date for the owner when Sonnet and Haiku still disagree.
  - P4 learns the escalation (check results + photo features, nested grouped CV). The step-07 photo-feature router is not used here.

| Policy (all 136 bills) | Total right | All fields | Silent errors | 95% upper | Asked owner | Free / Haiku / Sonnet | Rs/bill |
|---|---|---|---|---|---|---|---|
| Haiku-only | 96% | 76% | 5 (3.7%) | 7.6% | 0% | 0 / 100 / 0% | 0.051 |
| Sonnet-only | 99% | 85% | 2 (1.5%) | 4.6% | 0% | 0 / 0 / 100% | 0.822 |
| P0 rule router (hand label) | 99% | 84% | 4 (2.9%) | 6.6% | 0% | 7 / 45 / 49% | 0.458 |
| P1 (a, c, d) | 99% | 85% | 3 (2.2%) | 5.6% | 0% | 7 / 93 / 61% | 0.595 |
| P2 (a, b, c, d) | 99% | 85% | 3 (2.2%) | 5.6% | 0% | 7 / 93 / 71% | 0.664 |
| **P3 = P2 + ask owner** | 99% | 85% | **2 (1.5%)** | 4.6% | 18% | 7 / 93 / 71% | **0.664** |
| P4 learned (nested) | 99% | 85% | 1 (0.7%) | 3.4% | 18% | 7 / 93 / 89% | 0.794 |
| Step-08 cascade | 99% | 83% | 3 (2.2%) | 5.6% | 0% | 7 / 26 / 67% | 0.583 |

  - **printed_TEST (35):** P3 has 1 silent error (2.9%) at Rs 0.401 vs Sonnet-only's 2 (5.7%) at Rs 0.776. **Handwritten (66):** P3 has 0 silent, but Haiku + Sonnet costs Rs 0.924 vs Sonnet-only Rs 0.898. Haiku is unsure of the date on 42 of 66 handwritten bills, and OCR disagrees on 34, so nearly all escalate.
  - **Recommended (rule fixed before the run: cheapest of P1–P4 with silent errors ≤ Sonnet-only): P3.** It has 2 silent errors, the same as Sonnet-only, at Rs 0.664 vs Rs 0.822 per bill, **19% cheaper** (48% cheaper on printed_TEST).
  - **Caveats:** with 136 bills, 1–2 bills of difference is within noise (the 95% upper bounds overlap). P3 asks the owner to check total / date on 18% of bills (27% of handwritten). P3's 2 remaining silent errors are B086 and B134, both on the label-review list.
  - P0 needs the printed/handwritten label, which the bot doesn't have, so it is a benchmark only. P4 has fewer silent errors (1) but costs nearly as much as Sonnet-only.
- **Step 3 (a second Haiku read, about Rs 7):** not run. It waits for Vipul's OK. P3 still has 2 silent errors, but both look like label questions, so the labels should be checked first.
- **Step 4:** the live bot reads photos with `READ_POLICY` (default **P3**; also `haiku`, `P1`, `P2`) through `bot/policy.PolicyReader`, built in `bot/app.py` (`ESCALATION_MODEL`, `OCR_CHECK` in `.env.example`).
  - Confirm buttons are unchanged; disagreements show ⚠️.
  - Each Sonnet call logs its own `ai_call`, so the daily caps count it. At the cap, the bot flags the total and date instead of calling Sonnet.
  - `bill_received` records policy, escalated, reasons and asked.
  - The free OCR check needs RapidOCR, which is not in `requirements-bot.txt` (the Docker image). Without it, checks a and b are skipped; adding it is a hosting decision.
  - P4 is not wired (it needs a trained model); PDF documents are not handled by the bot yet.
  - The secret scan now allows exactly `outputs/eval/router_tree.png`, a chart drawn by step 07; all other images are still blocked.
  - 357 tests pass + 78 skipped (435 with Postgres); scan clean.

### 28 Oct (later): label review, re-score, policy P5 "money first" (cached readings only, Rs 0)
- **Labels (Vipul checked the photos; backup `data/eval/labels_v1.csv`, logged in `docs/05_data_log.md`):** B086 due date set to 16/11/2026 (bill says "pay within 30 days"; v2 prompt rule = bill date + days); B141 total set to 9156 (cut off; from the bill's own maths). B134 and B145 labels were right: those are model errors.
- **Re-scored from cache:** Haiku-only now 3 silent errors (was 5), Sonnet-only 1 (was 2). Step-07 nested router: 80% to Sonnet, Rs 0.67/bill (AUC logreg 0.67, tree 0.55). `outputs/eval/silent_errors_review.csv` is now written by `src/09_policies_eval.py`.
- **P5 "money first":** same checks as P3, but Sonnet is called ONLY when a check on the total fails (OCR disagrees, proof fails, Haiku unsure of the total, total sanity). A doubt about the date / invoice no. / due date never calls Sonnet; the owner is asked (date: one tap). New rule: a bill date more than 183 days before the photo arrived is flagged (`--today 2026-10-28` stands for the day the photo arrived).
- **"Asked owner" is now counted from what the owner sees:** any field with ⚠️ (the readers' own unsure fields + policy flags), split into total / date only / other. This is why P3 shows 40% here vs 18% before (18% counted only the disagreement asks).

| All 136 bills | Silent errors | 95% upper | Asked owner (total / date only / other) | To Sonnet | Rs/bill | vs Sonnet-only |
|---|---|---|---|---|---|---|
| Haiku-only | 3 (2.2%) | 5.6% | 52% (16 / 26 / 10) | 0% | 0.051 | 94% cheaper |
| Sonnet-only | 1 (0.7%) | 3.4% | 40% (6 / 21 / 13) | 100% | 0.822 | – |
| P3 | 1 (0.7%) | 3.4% | 40% (7 / 23 / 10) | 71% | 0.664 | 19% cheaper |
| P4 learned (nested) | 0 | < 2.2% | 43% (7 / 25 / 10) | 87% | 0.780 | 5% cheaper |
| **P5 money first** | **1 (0.7%)** | 3.4% | 54% (5 / 38 / 10) | **52%** | **0.491** | **40% cheaper** |
| P5 without the date-age rule | 2 (1.5%) | 4.6% | 45% (5 / 29 / 11) | 52% | 0.491 | 40% cheaper |

| P3 → P5 by scope | Silent | Asked: total / date only | To Sonnet | Rs/bill | vs Sonnet-only |
|---|---|---|---|---|---|
| printed (70) | 1 → 1 | 7% / 3% → 6% / 21% | 47% → 36% | 0.419 → 0.316 | 44% → 58% cheaper |
| printed_TEST (35) | **0 → 1** | 9% / 6% → 6% / 26% | 40% → 23% | 0.401 → 0.250 | 48% → 68% cheaper |
| handwritten (66) | 0 → 0 | 6% / 44% → 5% / 56% | 97% → 70% | 0.924 → 0.677 | -3% → 25% cheaper |

- **Which bills:** P3's silent error is B134 (Haiku read the year 2025; every check passed). P5's date-age rule catches it. P5's silent error is B145's invoice number (Haiku misread one digit). P3 only caught it by luck: Haiku was unsure of the date, so Sonnet re-read the whole bill. Neither policy has a silent error on a total.
- **Decision (rule set before the run: P5 replaces P3 if silent errors ≤ P3 on all bills AND ≥ 10% cheaper): P5 is the default** (`DEFAULT_POLICY`, `READ_POLICY=P5`). Same silent count (1 vs 1), 26% cheaper than P3, 40% cheaper than Sonnet-only.
  - Caveat: on printed_TEST alone P5 has 1 silent vs P3's 0. With 35 bills this is noise (upper bounds 12.9% vs 8.6%), but it's reported.
  - Cost of the saving: more date questions. 38% of bills get a date question vs 23%; on handwritten it's 56%.
  - Of those date questions, 9 points come from the date-age rule firing on old archive photos (16 labelled bills are dated more than 6 months before 28 Oct). Live photos usually arrive the week the bill was made, so this should be lower in the pilot.
  - All-fields-right before the owner answers drops from 85% to 82%, because the date is left to the owner.
- **Bot, date one-tap (P5 only):** when the bill date has a ⚠️ and the owner hasn't set it yet, the bot asks "Bill ki date aaj ki hai?" with three buttons: [Haan, aaj] [Kal] [Doosri date].
  - Aaj / Kal set the IST date. Doosri date asks for the date as text. Then the usual summary with Sahi hai / Badlo.
  - Missing required fields are asked first. The tap is asked once per bill (`checked` = fields the owner set; the ⚠️ disappears for them).
  - Events: `date_tap_asked`, `date_tapped` {choice, read}.
  - Gap: an invoice-number ⚠️ can't be fixed from the Badlo menu (only supplier / total / date; WhatsApp allows 3 buttons).
- **Hosting:** the Docker image now installs RapidOCR + Tesseract (orientation only) through `requirements-ocr-bot.txt`, pinned to the eval's versions (rapidocr 3.10.0, onnxruntime 1.23.2), so checks a and b run in production.
  - **Size:** about +360 MB uncompressed (Python packages 119 → 449 MB, measured with `pip install --target` for Python 3.12 / manylinux; Tesseract about +28 MB). sympy is removed (onnxruntime never needs it to run a model; -79 MB). Docker isn't available in this sandbox, so `docker images hisaabai` gives the real number.
  - **Memory / time (2-CPU VM, real eval photos):** RapidOCR peaks at about 0.7–0.9 GB RAM and takes 6–15 s per photo.
    - So: a host with ≥ 1 GB RAM (2 GB safer). OCR now runs while Haiku reads, one OCR at a time.
    - On a 512 MB host: `docker build --build-arg OCR=0` (checks a and b are then off; not what was measured).
- 376 tests pass + 83 skipped (459 with Postgres, 0 skipped); secret scan clean.

### 28 Oct (later): check e, invoice no. vs free OCR (P5e, cached readings, Rs 0)
- **P5e = P5 + check e:** the invoice no. shown to the owner is compared with the free OCR's invoice no. If they differ, the invoice no. gets a ⚠️ (the owner checks it; Sonnet is never called).
  - The check only runs when OCR read a sane invoice no. (a phone number or GSTIN is ignored).
  - After a Sonnet re-read, it checks Sonnet's value, because that's the one shown.
  - `READ_POLICY=P5e` works in the bot; the date one-tap is unchanged.
- **Result: still 1 silent error (B145), so the default stays P5.** The free OCR had the right number in its text (on a line where two column headers merged), but the field rules didn't extract it (two column headers merged), so check e had nothing to compare. OCR read an invoice no. on 56 of 136 bills.
  - Check e added 2 warnings, both false alarms where Haiku was right: B034 and B047 (handwritten; OCR read only the first one or two digits of a 3-digit number). Two OCR "invoice nos." were phone numbers (B144, B151) and were ignored.

| All 136 bills | Silent | 95% upper | Asked owner (total / date only / other) | To Sonnet | Rs/bill | vs Sonnet-only |
|---|---|---|---|---|---|---|
| P4 learned (nested) | **0** | < 2.2% | 43% (7 / 25 / 10) | 87% | 0.780 | 5% cheaper |
| P5 | 1 | 3.4% | 54% (5 / 38 / 10) | 52% | 0.491 | 40% cheaper |
| P5e | 1 | 3.4% | 54% (5 / 38 / 12) | 52% | 0.491 | 40% cheaper |

- **Rule (0 silent AND ≥ 30% cheaper than Sonnet-only) not met:** P4 vs P5e goes to Vipul.
- **Seen, not built:** a check "is the invoice no. shown anywhere in the OCR text?" would catch B145 with 3 extra warnings (2 false). But it was designed after looking at B145, so on these bills it's not an honest test.
- 380 tests pass + 83 skipped (463 with Postgres); secret scan clean.

### 29 Oct: P5 without OCR, question eval (step 10), PDFs on WhatsApp
- **`P5_no_ocr` (cached, Rs 0): P5 as if RapidOCR is not installed** (checks a and b off, e.g. a 512 MB free host).

| All 136 bills | Silent | 95% upper | Asked owner (total / date) | To Sonnet | Rs/bill | vs Sonnet-only |
|---|---|---|---|---|---|---|
| P5 (with OCR) | 1 | 3.4% | 54% (5 / 38) | 52% | 0.491 | 40% cheaper |
| **P5_no_ocr** | **1** (same bill, B145) | 3.4% | 57% (4 / 43) | **17%** | **0.205** | **75% cheaper** |
| printed: P5 → no OCR | 1 → 1 | | | 36% → 9% | 0.316 → 0.126 | |
| handwritten: P5 → no OCR | 0 → 0 | | | 70% → 26% | 0.677 → 0.289 | |

  - Why: all 4 bills where Haiku's total was wrong (B042, B055, B114, B155) were already marked unsure by Haiku itself, so check c sent them to Sonnet anyway.
  - The OCR checks added Sonnet calls on 35% of bills without catching any extra error on this set. OCR is insurance against a *confidently* wrong total: 0 seen in 136 (the true rate can still be up to about 2%).
  - Not changed: the default stays P5 with OCR where the host has the RAM; a host without OCR loses no measured safety. Vipul decides.
- **Question eval (`src/10_questions_eval.py`, rules only, `QA_LLM=0`):**
  - **Setup:** 62 invented owner messages in `data/eval/questions.csv`, frozen (sha256 logged) before the first run. A fake shop with 6 suppliers (two Sharmas), 12 bills, 6 payments and 1 reminder; today fixed at Wed 28 Oct 2026. Each message goes through the real bot.
  - **Scoring:** correct / safe (menu or a follow-up) / WRONG (answered or started a different question, wrong supplier, period or day, or a payment treated as a question).
  - **Headline, before any fix: 89% correct, 8% safe, 3% WRONG (2 of 62).** Tricky 87% / 13% / 0%.
  - **Both WRONG** were "sabka kitna baaki hai" and "mujhe kul kitna dena hai sabko": "sabka" / "sabko" were taken as a supplier's name. The bot started "one supplier's dues" and showed a "supplier not found" list, so no wrong number was sent, but it misread the question.
  - **Fixed (clear rule bugs only):**
    - "all suppliers" words (sab, sabka, sabko, saare...) are never a name
    - "saare supplier ka hisaab" = everyone's dues
    - bills / payments with a named period need no question word ("shiv medical ke bill is mahine")
    - a typo matching more of a supplier's words beats a one-word tie ("shrma tradrs" = Sharma Traders, not Sharma Medical)
  - **After the fixes: 97% correct, 3% safe, 0% WRONG.** The "after" number is tuned on the same 62 messages, so quote the "before" number.
  - **Still not understood (safe):**
    - "maine kya kya yaad dilane ko bola tha" (a past-tense list of reminders: fixing it risks reading it as a new reminder)
    - "balaji ko kitna dena hai 3000 de diya" (a payment whose name the payment parser misses)
- **PDFs on WhatsApp:** a WhatsApp `document` used to fall through to "off-topic". Now:
  - **Image sent as a document:** goes to the photo flow.
  - **Text PDF:** each page is read free. The last page with a total is used; supplier, date and invoice no. come from page 1. If the total is proved (same rules as `pdf_text`), the bill is made with no AI call.
  - **Scanned PDF, or no / doubtful total:** the page is rendered (1600 px) and goes through the photo flow (P5). For a multi-page scan that's the last page.
  - **Other files:** a non-PDF, a broken / password PDF, more than 10 pages or more than 10 MB (`MAX_PDF_MB`) each get a short polite message.
  - The page image is stored like a photo (30-day deletion). The confirm buttons, own-bill check and date tap are unchanged.
  - Events: `bill_received` source `pdf_text` / `pdf_scan` + pages / page_used / pdf_route, and `pdf_rejected` with a reason. `pypdfium2` added to `requirements-bot.txt`.
  - **On the real PDFs** in `data/raw/bills_inbox/done`:
    - 9 of 10 text PDFs (including a 5-page and a 3-page one) were read free with the right total on the right page.
    - The 10th is a 51-page "Photo to PDF" batch of many bills. It gets the "too big" message; a multi-bill PDF isn't handled.
- **Free hosting:** `docs/14_free_hosting.md` covers Oracle Always Free (Arm VM, Docker, Caddy + sslip.io HTTPS or Cloudflare Tunnel) and Render free (`OCR=0`, `deploy/github-workflows/keep-alive.yml` pinging every 10 min).
- 399 tests pass + 91 skipped (490 with Postgres); secret scan clean.

### 29 Oct (later): P5_trusted_ocr, and the OCR decision (cached, Rs 0)
- **P5_trusted_ocr** = P5, but check a fires only when the OCR total is **trusted**: read_ocr proved it by words = digits or a maths proof (`total_proof` = words / maths; 30 of 136 OCR totals). Check b stays.
- **P5_no_ocr** is now a real policy: no checks a / b, and the OCR is not even run (the live reader skips it too). Same numbers as before.
- **Decision rule (Vipul, set before the run):** P5_trusted_ocr becomes the default if its silent errors are ≤ P5_no_ocr's AND it costs ≤ Rs 0.30/bill; otherwise P5_no_ocr.

| All 136 bills | Silent | 95% upper | To Sonnet | Rs/bill | vs Sonnet-only |
|---|---|---|---|---|---|
| P5 | 1 | 3.4% | 52% | 0.491 | 40% cheaper |
| P5_no_ocr | 1 | 3.4% | 17% | 0.205 | 75% cheaper |
| **P5_trusted_ocr** | **1** (B145 invoice no., same as the others) | 3.4% | **29%** | **0.287** | **65% cheaper** |
| printed: P5 / no OCR / trusted | 1 / 1 / 1 | | 36 / 9 / 31% | 0.316 / 0.126 / 0.285 | |
| printed_TEST | 1 / 1 / 1 | | 23 / 17 / 23% | 0.250 / 0.213 / 0.250 | |
| handwritten | 0 / 0 / 0 | | 70 / 26 / 26% | 0.677 / 0.289 / 0.289 | |

- **Each check on Haiku's total** (photos only; 4 Haiku totals wrong in the set, all already marked unsure by Haiku):

| Check | Fires on | Caught a wrong total | Caught that no other free check did | False alarms |
|---|---|---|---|---|
| a: any OCR total (P5) | 40 | 2 | 0 | 38 |
| a_trusted: proved OCR total only | **0** | 0 | 0 | 0 |
| b: proof on the OCR text fails | 17 | 1 | 0 | 16 |

- **Result: the rule picks P5_trusted_ocr** (1 ≤ 1 silent, Rs 0.287 ≤ 0.30).
  - It's now `DEFAULT_POLICY`, the `.env.example` value, and the recommended Oracle setup in `docs/14_free_hosting.md`. Render uses `P5_no_ocr`.
  - Read this honestly: the trusted check never fired on these bills (Haiku and every proved OCR total agreed).
  - The whole Rs 0.08/bill difference from P5_no_ocr comes from check b: 17 Sonnet calls, 16 of them false alarms, and its 1 catch was already flagged by Haiku.
  - Its value is only insurance for bills not yet seen. Revisit with pilot data (decisions log).
- Live: the bot's OCR helper now also passes read_ocr's `total_proof`, so the trusted check works in production.
- 402 tests pass + 91 skipped (493 with Postgres); secret scan clean.

### 29 Oct (later): live on Render free, templates, voice eval, Render readiness, push check
- **Decision (Vipul): live default = `P5_no_ocr`, hosted on Render free** (no card). `P5_trusted_ocr` stays the Oracle option.
  - Why: the trusted check never fired; check b gave 16 false alarms in 17; same measured safety at Rs 0.205 vs 0.287 per bill.
  - Changed: `DEFAULT_POLICY`, `.env.example`, `render.yaml`, decisions log. The bot no longer even loads the OCR module when the policy doesn't use it.
- **Templates:** `bot/check_setup.py` now also checks `owner_reminder` (it only checked the other two).
- **Bug fixed, voice and question fallbacks:** the Haiku fallback for voice payments (`max_tokens=150`) and the question classifier (`120`) shared their budget with adaptive thinking, so the JSON could be cut off; the same B013 problem as bill reading.
  - Now `SMALL_CALL_MAX_TOKENS = 1000`, and a cut-off answer (`stop_reason=max_tokens`) is dropped.
- **Voice eval (`src/11_voice_eval.py`, `RUN_STEP11_voice_eval.bat` / `RUN_STEP11b_voice_score.bat`):** real notes from `data/voice_test/` go to Sarvam (`SARVAM_API_KEY`), then the bot's exact voice order: question rules → payment rules → Haiku fallback.
  - **Audio:** decoded with PyAV (`av` in `requirements.txt`; no ffmpeg install) to 16 kHz mono WAV.
  - **Cost guard:** max 30 new files per run; transcripts are cached by content hash; Sarvam seconds and an Rs estimate are printed.
  - **Scoring:** caught / wrong amount / missed / false payment.
  - **Finding (not fixed):** Sarvam's REST API takes at most 30 s per request. The eval cuts longer notes into 29 s pieces, but **the live bot sends the whole note, so a note over 30 s fails there.**
- **Render readiness:**
  - **Memory** (`scripts/memory_check.py`, Linux; real app + bill-image prep + PDF reading, network faked; Python 3.10 in a VM, not the Docker image):
    - started: 89 MB
    - one shop, one message at a time: peak 234–241 MB
    - bursts of 25 photos / PDFs / texts from 5 shops at once: **667 MB before** → **398–409 MB after** two fixes:
      1. `MALLOC_ARENA_MAX=2` in the Dockerfile (glibc kept per-thread memory pools)
      2. at most 2 image decodes / PDF renders at a time (`IMAGE_DECODE_SLOTS`; same pixels)
    - Verdict: fits 512 MB, with ~100 MB of margin only in an extreme burst.
  - **Webhook:** Meta gets HTTP 200 before any work starts (test drives the ASGI app and checks the order); a crash while handling still answers 200.
  - **De-duplication:** by WhatsApp message id: retries, a doubled batch and two simultaneous deliveries each give one bill and one reply (tests, memory + Postgres).
  - **Storage:** bills only in Postgres (`DATABASE_URL`), photos only via `bot/photos.py` (Supabase). With `REQUIRE_PERSISTENT_STORAGE=1` (`render.yaml`), `/health` returns 503 if either would land on Render's wiped disk, so such a deploy never goes live.
  - **`render.yaml`:** free plan, Singapore, Docker, `/health`, 26 env vars; secrets with `sync: false`. Names in `docs/15_render_env.md`.
  - **`keep-alive.yml`:** now pings `/health` every 10 min and fails visibly if it's down.
- **Before a GitHub push:**
  - `.gitignore` now also covers `outputs/` (eval results quote real bills), `*.db` / `*.sqlite`, audio files and stray photos / PDFs (brand icon allowed).
  - The secret scan treats `outputs/` as private, and its folder walk no longer enters `data/` (it took minutes on the synced folder).
  - **Privacy fix:** real supplier names (4), invoice numbers (5) and an e-way bill number copied from labelled bills into tests, comments and docs were replaced with invented ones.
  - 122 files would be pushed.
- Tests: 415 passed + 94 skipped (run in parts; this VM had become slow). Postgres: 496 passed outside the two slow files. Secret scan clean.

### 30 Oct: long voice notes live, privacy guard, voice-eval labels
- **Long voice notes in the live bot** (`bot/voice.py`, shared with the step-11 eval).
  - **How:** the note is decoded with PyAV to 16 kHz mono and cut into pieces of at most 29.5 s (Sarvam's limit is 30 s). Each cut goes at the quietest 100 ms in the last 5 s before the limit, i.e. a pause between words; with no pause, as late as possible. Each piece is transcribed, the texts are joined, then the normal parser runs.
  - **Cost and limits:** each piece logs an `ai_call`, so the daily cap counts it. Notes over 2 minutes are not sent: the owner gets `VOICE_TOO_LONG` and the event `voice_too_long` is logged. Audio that cannot be decoded is sent once as it is.
  - **Cost of the change:** `av` is now in `requirements-bot.txt`, adding about 127 MB to the image (installed size, Python 3.12 Linux wheel) and about 22 MB of RAM once a voice note arrives. A 2-minute note decodes and splits in about 0.3 s.
  - **Sarvam:** the transcriber now names WAV pieces as WAV.
  - **Tests:** `tests/test_voice_split.py`, fake audio, no network.
- **Privacy guard in `scripts/secret_scan.py`.** If `data/eval/labels.csv` exists (laptop only), every real supplier name and invoice number in it is searched for in the files that would be committed.
  - Names are compared normalised, at least 6 characters.
  - Invoice numbers with a letter, `-` or `/` are matched anywhere; plain-number ones only on a line that mentions an invoice / bill number.
  - It prints file:line only, never the value, and "secret-scan: ignore" doesn't silence it.
  - It found one more real number in this Build log (two handwritten bill serials), now reworded.
- **Voice eval labels** (`src/11_voice_eval.py`):
  - New `kind` column: payment / question / future / received / bill / chatter / multi.
  - `true_is_payment` accepts `multi`. Correct = the bot doesn't propose just one of the payments; today the bot proposes at most one payment per note, so a single proposal counts as wrong.
  - Notes over 2 minutes score as "too long (asked to resend)".
  - The score is split by kind, and a label check lists typos instead of silently dropping rows.
- `_to_delete/` (gitignored) holds one probe file a test wrote before it was fixed; delete the folder by hand.
- Tests: 425 passed + 96 skipped (Postgres: 521 passed); secret scan clean.

### 31 Oct: voice and typed entries (built in a separate Claude session; full write-up `docs/16_voice_typed_entries.md`)
- **New:** `bot/numbers.py` (amounts as said and written), `bot/entries.py` (the text engine), `bot/entry_flow.py`
  (cards, supplier list, kind / received / future questions, fixes with history, proof photos), `src/12_entries_eval.py`
  + `RUN_STEP12_entries_eval.bat`, `scripts/entry_demo.py`, `src/templates/voice_script_v2.csv`.
- **Changed:** `bot/conversation.py` (text and voice go through the engine; photo bills get the supplier step and a
  Cancel button; a waiting question is left when the owner clearly moves on), `bot/store.py` (pending payments,
  recent entries, edits, removal, proof photos; both stores), `db/schema.sql` (migration, `entry_edits`, dues view
  counts confirmed payments only), `bot/voice.py` (`llm_entry_parser`), `bot/app.py` (`VOICE_LLM`), `bot/jobs.py`
  (proof photos deleted after 30 days too), `bot/messages.py`, `bot/questions.py` ("kis kis ka paisa dena hai";
  29 Feb), `src/10_questions_eval.py` (reads pending payment rows instead of the old chat state: same scoring),
  `src/11_voice_eval.py` (each note goes through the live bot; labels are now true_intent / true_amount /
  true_supplier).
- **Honesty:** the main set was frozen before any engine code; two blind sets came from separate agents that never saw
  the code; each set's first run is kept (`*_detail_before.csv`). The voice notes were transcribed earlier but not read
  until the engine was done. Results table: `docs/16_voice_typed_entries.md`.
- **Reviewed twice by a separate Claude** (14 findings, all fixed with regression tests), among them: proof buttons
  usable twice (a bill counted twice), a typed value swallowed as a new message, "0.4" / "1000 crore" / "400 din"
  crashing Postgres, a negated part becoming a card, "5000 baaki" becoming a phantom payment card.
- Also found while building: a photo bill from "Sharma Brothers" would have been added silently to "Sharma Traders".
- Tests: 622 passed with Postgres (506 + 116 skipped without); 320,000 random messages parsed, 0 crashes, nothing out
  of range; secret scan + privacy guard clean; question eval and bill-reading eval unchanged.
- **2026-11-01, after Vipul's phone test** ("Gupta se 500 aaye" not understood; "Gupta ko 3000 dena hai" missing from
  "kitna baaki hai"; signup felt like a form):
  - Five entry kinds: `bills.kind` (bill / udhaar / return) and `payments.kind` (payment / refund) in `db/schema.sql`;
    dues = bills + udhaar − returns − (payments − refunds); `v_shop_weeks`, `v_funnel`, "kitne bill aaye" and the
    weekly summary count `kind='bill'` only. Both stores, `bot/entries.py` (classify order: old dues, return, future,
    refund, "ne" with took / gave, bill, received), `bot/entry_flow.py` (received asks *what* or *direction*; future
    shows the known dues and offers "Baaki mein jodo"), `bot/messages.py`, `bot/voice.py` (prompt types).
  - Signup: welcome → one tap → ready message; no shop-name question. After the first saved entry, once: the WhatsApp
    profile name offered in one tap (`bot/app.py` attaches it to each message; never stored), or [Naam likhein]
    [Skip]. A typed name is awaited only after a tap (the old open question had saved "gupta ke saath len den" as a
    shop name). Waits while other cards from the same message are open. Brand on owner-facing text: Vahi.
  - Paying more than owed now says "₹200 zyada diye hain (advance)" instead of "-₹200".
  - Also fixed: on Windows two entries could get the same timestamp ("last wala" picked the older one); `_now()` in
    `bot/store.py` is now strictly increasing.
  - Results: typed sets main 78/79, blind 68/70, blind2 76/80 correct, all 0 WRONG; voice 13 correct, 6 partial,
    1 safe, 0 WRONG; question eval 97% correct, 0 WRONG; 652 tests on both stores (522 + 131 skipped without
    Postgres); secret scan clean.

### 3 Nov: guardrails, understanding, trust (after the second phone test; full write-up `docs/18_guardrails_and_trust.md`)
- **Understanding** (`bot/understand.py`, new): Hindi script → Roman letters (silent-'a' rule, loan words), spelling slips
  of meaning words (hiasb / hasib → hisaab); old dues said any common way; "se ___ liya" = a bill.
- **Guardrails**: before → after baaki and the guessed name on every card; duplicate guard (7 days / same bill number);
  10× usual amount, payment above baaki, date ahead; refunds ask why (goods back = return + refund pair); first payment
  without known dues asks for dues from before.
- **Photos read first**, proof asked only on a match; "ye X ke bill ka proof hai"; a photo is never attached twice.
- **Old entries**: dated correcting entries ("sudhaar") instead of "not found"; stale buttons say what happened.
- **Trust**: new intro (header, example, footer), STOP keeps data, "mera data delete karo" with one confirm tap.
- **Speed**: per-message cache, lazy recent entries, reply timing; typed bill 15 → 13 queries with the new checks.
- Checks: 705 tests on both stores (with the day questions and the health report); entries evals 78 / 68 / 77 correct, 0 WRONG; questions 97%, 0 WRONG; voice unchanged.


### 3 Nov (later): day questions, the pilot health report, the pilot kit
- "aaj ka payment", "kal ke bill" (no "kitna") are questions; "aaj ka hisaab" answers the day's bills, payments and
  the baaki; corrections never count as money paid in a period.
- `bot/health.py` + `python -m bot.admin health` (`RUN_pilot_health.bat`): the daily one-page report; `card_warned` events.
- `docs/11_setup_and_hosting.md` 4a: moving the database next to the bot (Singapore). `docs/19_pilot_kit.md`,
  `discovery/vahi_card.html`, `discovery/templates/pilot_tracker.csv`.
- Checks: 705 tests on both stores (549 + 157 skipped on a laptop); evals unchanged (78 / 68 / 77, 0 WRONG; questions 97%).
