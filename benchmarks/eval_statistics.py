"""Small dependency-free statistical helpers for evaluation reports."""
from __future__ import annotations

import math
from typing import Any, Iterable


def wilson_interval(
    successes: int,
    trials: int,
    z: float = 1.959963984540054,
) -> dict[str, Any]:
    """Return a two-sided 95% Wilson interval for a binary rate."""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("expected 0 <= successes <= trials")
    if trials == 0:
        return {
            "successes": 0,
            "trials": 0,
            "rate": None,
            "low": None,
            "high": None,
            "method": "wilson_95",
            "unit": "independent_case",
        }
    rate = successes / trials
    denominator = 1.0 + (z * z / trials)
    center = (rate + z * z / (2.0 * trials)) / denominator
    margin = (
        z
        * ((rate * (1.0 - rate) / trials + z * z / (4.0 * trials * trials)) ** 0.5)
        / denominator
    )
    return {
        "successes": successes,
        "trials": trials,
        "rate": rate,
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
        "method": "wilson_95",
        "unit": "independent_case",
    }


def exact_paired_binary_test(
    paired_outcomes: Iterable[tuple[bool, bool]],
    *,
    baseline_label: str = "baseline",
    treatment_label: str = "treatment",
) -> dict[str, Any]:
    """Exact two-sided McNemar test via the binomial distribution.

    Each tuple is ``(baseline_success, treatment_success)``. Only discordant
    pairs contribute to the p-value. This is appropriate for small paired
    evaluation sets and avoids a chi-square approximation.
    """
    pairs = [(bool(baseline), bool(treatment)) for baseline, treatment in paired_outcomes]
    treatment_wins = sum(treatment and not baseline for baseline, treatment in pairs)
    treatment_losses = sum(baseline and not treatment for baseline, treatment in pairs)
    ties = len(pairs) - treatment_wins - treatment_losses
    discordant = treatment_wins + treatment_losses
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, k)
            for k in range(0, min(treatment_wins, treatment_losses) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2.0 * tail)
    return {
        "baseline_label": baseline_label,
        "treatment_label": treatment_label,
        "pair_count": len(pairs),
        "treatment_wins": treatment_wins,
        "treatment_losses": treatment_losses,
        "ties": ties,
        "discordant_pairs": discordant,
        "p_value": p_value,
        "method": "exact_mcnemar_binomial_two_sided",
        "unit": "independent_case",
    }
