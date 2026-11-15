# Bill-reading eval

**Question:** how often does the bot read a bill right, and how often is it confidently wrong?

## How it works
- The bot and the eval use the same code (`bot/extract.py`), so what we measure is what ships.
- 4 runs: 2 models (Claude Haiku, Claude Sonnet) x 2 prompts (`v1` simple, `v2` with Indian-bill rules). Set in `src/eval_config.json`.
- Every model reply is cached in `data/eval/runs/`: re-running or re-scoring costs nothing.

## Scoring (per field: supplier, bill date, invoice number, total, due date)
| Outcome | Meaning | Risk |
|---|---|---|
| correct | Matches the hand label | None |
| correct_blank | Nothing printed, model said null | None |
| missed | Model said null | Low: the bot asks the owner |
| wrong | Model read a different value | High if not flagged |
| invented | Model made up a value that is not on the bill | High |

**Silent error** = wrong or invented, and the model did not flag it as unsure. This is the number to drive down: it is the confident wrong value an owner might accept.

Matching rules: totals to the rupee; dates are day-first (05/10/26 = 5 Oct 2026); supplier names ignore case, punctuation, "M/s", "&" vs "and", "Pvt Ltd"; invoice numbers ignore spaces and case.

## Steps
1. Copy `src/templates/eval_labels.csv` to `data/eval/labels.csv`, delete the 2 example rows.
2. Put bill photos in `data/eval/images/`, one row per photo with the correct values typed by hand.
3. Add `ANTHROPIC_API_KEY` to `.env`; fill prices in `src/eval_config.json` from the Anthropic pricing page.
4. Double-click `RUN_STEP1_eval.bat`: checks labels (free), then a 5-bill trial, then the full run, asking before each.

## Outputs (`outputs/eval/`)
- `summary_by_run.csv`: per run, share of bills fully right, total right, with a silent error; latency; Rs per bill.
- `summary_by_field.csv`: accuracy, missed and silent-error rate per field, printed vs handwritten.
- `per_field_detail.csv`: every field of every bill, for error analysis.

## Voice check (small, 25 notes)
1. Record the 25 lines in `src/templates/voice_script.csv` as WhatsApp voice notes (2-3 different people if possible; normal shop noise is fine).
2. Save them as `data/voice/V01.ogg` ... `V25.ogg` (never committed).
3. Double-click `RUN_STEP3_voice_check.bat`. It runs each note through the bot's own path (Sarvam, rule parser, Claude fallback, supplier match).
4. Scores: intent, amount, supplier, fully correct (`outputs/voice/voice_summary.csv`). A miss is never silent: every payment needs the owner's "Haan, sahi" tap.
