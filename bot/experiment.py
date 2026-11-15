"""The nudge experiment: random assignment and the pre-registered readout (see docs/09_experiment.md).

assign_groups: shuffles shops within each segment (kirana, medical, hardware...) and alternates
treatment / control, so both groups get a similar mix. A fixed seed makes it reproducible.

readout: primary metric = share of shops active in week 2 (3+ confirmed bills in days 7-13).
Fisher's exact test (right for small samples) + 95% intervals. With ~10 shops per group this is a
pilot signal, not proof; min_n_per_group says how many shops a real test would need.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict


def assign_groups(shops: list[dict], seed: int = 20261102) -> dict[str, str]:
    """shops: [{'id', 'segment'}] without a group. Returns {shop_id: 'treatment' | 'control'}."""
    rng = random.Random(seed)
    by_segment = defaultdict(list)
    for s in sorted(shops, key=lambda s: str(s["id"])):
        by_segment[s.get("segment") or "other"].append(s)
    out, next_first = {}, "treatment"
    for segment in sorted(by_segment):
        members = by_segment[segment]
        rng.shuffle(members)
        order = [next_first, "control" if next_first == "treatment" else "treatment"]
        for i, s in enumerate(members):
            out[str(s["id"])] = order[i % 2]
        if len(members) % 2:  # odd segment: start the next one with the other group to stay balanced
            next_first = order[1]
    return out


def _log_comb(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2x2 table [[a, b], [c, d]]. Sums all tables at least as unlikely as the observed one."""
    n1, n2, k = a + b, c + d, a + c
    lo, hi = max(0, k - n2), min(k, n1)

    def p(x):
        return math.exp(_log_comb(n1, x) + _log_comb(n2, k - x) - _log_comb(n1 + n2, k))

    p_obs = p(a)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= p_obs * (1 + 1e-9)))


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def diff_interval(s1, n1, s2, n2, z=1.96) -> tuple[float, float]:
    """Newcombe's interval for p1 - p2 (works well with small samples)."""
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson(s1, n1, z)
    l2, u2 = wilson(s2, n2, z)
    d = p1 - p2
    return (d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def min_n_per_group(p1: float = 0.40, p2: float = 0.60, alpha_z: float = 1.96, power_z: float = 0.8416) -> int:
    pbar = (p1 + p2) / 2
    num = (alpha_z * math.sqrt(2 * pbar * (1 - pbar)) + power_z * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p2 - p1) ** 2)


def readout(rows: list[tuple[str, bool]]) -> dict:
    """rows: (group, active_in_week_2) per shop."""
    t = [a for g, a in rows if g == "treatment"]
    c = [a for g, a in rows if g == "control"]
    st, nt, sc, nc = sum(t), len(t), sum(c), len(c)
    if not nt or not nc:
        raise ValueError("need shops in both groups")
    return {
        "treatment": {"shops": nt, "active": st, "rate": st / nt, "ci95": wilson(st, nt)},
        "control": {"shops": nc, "active": sc, "rate": sc / nc, "ci95": wilson(sc, nc)},
        "difference": st / nt - sc / nc,
        "difference_ci95": diff_interval(st, nt, sc, nc),
        "fisher_p": fisher_exact_two_sided(st, nt - st, sc, nc - sc),
        "shops_needed_per_group_for_40_vs_60": min_n_per_group(),
    }
