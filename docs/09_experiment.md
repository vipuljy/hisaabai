# Experiment pre-registration

**Registered: 13 October 2026, before any pilot data exists.** Nothing below changes after the first shop is onboarded. Analysis code: `src/02_experiment_readout.py` (written the same day).

| Part | Plan |
|---|---|
| Question | Do proactive nudges (Sunday summary + reminders 2 days before a supplier due date) keep shops active? |
| Hypothesis | Treatment shops are more often active in week 2 than control shops |
| Unit | One shop |
| Assignment | `python -m bot.admin assign-groups`: random, 1:1, balanced within segment (kirana, medical, hardware, other), fixed seed 20261102. Done once all pilot shops are registered, before the first nudge. Groups never change |
| Treatment | Weekly summary every Sunday + due reminders (`bot/jobs.py`) |
| Control | The bot works the same but never messages first |
| Primary metric | Share of shops **active in week 2**: 3+ confirmed bills in days 7-13 after onboarding |
| Secondary | Confirmed bills per shop in week 2; payments logged within 3 days of a reminder (treatment only, descriptive) |
| Guardrail | Opt-out (STOP) rate per group. If treatment opt-outs reach 3 more than control, nudges stop and this is reported |
| Test | Fisher's exact test, two-sided, alpha 0.05; difference with Newcombe 95% interval; Wilson 95% interval per group |
| Who counts | Every shop assigned and onboarded, as assigned (intention to treat). A shop that opts out counts as not active. Shops never onboarded are excluded |
| Analysed when | After every assigned shop has finished its week 2 |

## Note added 25 Oct 2026 (before the pilot; metric, test and assignment unchanged)
Owners can now set their own reminders ("Sharma ko Friday yaad dilana"). These are a feature the owner asks for, not a nudge, so **both groups** get them. They are logged separately (`reminder_set`, `owner_reminder_sent`, each with the shop's group); the nudge event `reminder_sent` stays treatment-only. The readout will report how many owner reminders each group set, as a descriptive check.

## Note added 31 Oct 2026 (before the pilot; metric, test and assignment unchanged)
- **Pilot start moves from 2 Nov to about 6-7 Nov**: voice / typed entries were added, and the hosted bot, the approved
  templates and a full phone test must come first. The product is frozen from the first pilot day; no feature changes
  during the pilot.
- **Bills sent by voice or typed count toward the primary metric** ("3+ confirmed bills in days 7-13") exactly like
  photo bills: every one is confirmed by the owner's tap. The readout will also report the split by source
  (`bills.source`: photo / pdf / voice / typed). Bills the owner removed (status `deleted`) do not count.
- **Only real bills count** (clarified 1 Nov, before the pilot): rows with `bills.kind = 'bill'`. Udhaar entries
  (old dues with no bill) and returns (goods sent back) change the dues but are not bills, so they never count toward
  the metric (`v_shop_weeks` and `v_funnel` filter on it; `src/02_experiment_readout.py` reads `v_shop_weeks`).

## Sample size, stated honestly
To detect 40% vs 60% active at alpha 0.05 and 80% power needs about **97 shops per group** (`bot.experiment.min_n_per_group`). The pilot has about 10 per group, so the result is reported as a **directional pilot signal with its interval**, plus the sample a conclusive test would need. It is not presented as proof.
