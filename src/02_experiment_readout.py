"""Step 2: the pre-registered experiment readout (run after the pilot, Phase 4).

Do nudges (Sunday summary + due reminders) keep shops active?
Primary metric: share of shops with 3+ confirmed bills in week 2 after onboarding.
Plan fixed in docs/09_experiment.md BEFORE the pilot; this script only computes it.

  python src/02_experiment_readout.py        (needs DATABASE_URL in .env)
Writes outputs/experiment/readout.json and prints the result.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.experiment import readout  # noqa: E402

SQL = """
select sh.experiment_group as grp,
       coalesce(max(w.bills) filter (where w.week_no = 2), 0) >= 3 as active_week2
from shops sh
left join v_shop_weeks w on w.shop_id = sh.id
where sh.experiment_group is not null and sh.onboarded_at is not null
  and sh.onboarded_at <= now() - interval '14 days'      -- only shops whose week 2 is over
group by sh.id, sh.experiment_group
"""


def main() -> int:
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set in .env")
        return 1
    from bot.store import PgStore
    rows = [(r["grp"], r["active_week2"]) for r in PgStore(url)._all(SQL)]
    try:
        r = readout(rows)
    except ValueError as err:
        print(f"Not enough data yet: {err}")
        return 1
    out = ROOT / "outputs" / "experiment" / "readout.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, indent=2), encoding="utf-8")
    t, c = r["treatment"], r["control"]
    lo, hi = r["difference_ci95"]
    print(f"Active in week 2: treatment {t['active']}/{t['shops']} ({t['rate']:.0%}) "
          f"vs control {c['active']}/{c['shops']} ({c['rate']:.0%})")
    print(f"Difference {r['difference']:+.0%} (95% CI {lo:+.0%} to {hi:+.0%}), Fisher p = {r['fisher_p']:.2f}")
    print(f"A conclusive test would need about {r['shops_needed_per_group_for_40_vs_60']} shops per group.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
