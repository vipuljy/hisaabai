# Discovery kit (Phase 1)

| File | Use |
|---|---|
| `outreach_kit.md` | Remote-first: WhatsApp group post, 2 polls, intro and bill-photo messages, call-interview tips, optional form |
| `playbook.html` | Read Friday night, carry on the day: opening line, objections, bill-photo ask, real vs polite yes, 5-minute version (2 pages) |
| `interview_sheet.html` | Open in a browser, Ctrl+P, print 15 copies (2 pages each: questions, then scoring + consent slip) |
| `templates/shops_tracker.csv` | Copy to `private/shops_tracker.csv`, one row per shop |
| `templates/scorecard.csv` | Copy to `private/scorecard.csv`; fill the average score (1-5) per wedge after all 15 interviews |
| `vahi_card.html` | **Pilot:** the card left at every shop (open in a browser, Ctrl+P, 4 cards per A4 page) |
| `templates/pilot_tracker.csv` | **Pilot:** copy to `private/pilot_tracker.csv`, one row per shop; the visit, check-ins and exit interview (`docs/19_pilot_kit.md`) |

## Rules
- Shop names and phone numbers go **only** in `private/` (never committed). Everywhere else, use the Shop ID (S01, S02...).
- Bill photos go in `data/raw/bills/` (never committed). Mask names before any photo is shown outside.
- Pick shops: 5 kirana/general, 5 medical, 5 hardware/sanitary. Mix one-person shops with 3+ staff. Visit 2-5 pm.
- Do not recruit anyone linked to your employer.

## Four evidence tracks (13 Oct plan)
| Track | Source | Target |
|---|---|---|
| A. Warm network | Family, friends, shops you buy from, group referrals; calls are fine | 8+ conversations |
| B. App reviews | `RUN_STEP2_review_mining.bat` (Khatabook, OkCredit, Vyapar, myBillBook) | Pain ranking |
| C. Polls / form | WhatsApp groups (`outreach_kit.md`) | 20+ answers, reported as a separate segment |
| D. Bill photos | Friends, family, groups | 100+ photos |
| E. Walk-ins | Optional top-up with the 5-minute version | As needed |

## Decision rule
Highest weighted score wins, unless fewer than 5 owners would try that wedge for 2 weeks. Review mining and polls break ties and check the interviews are not a fluke.
