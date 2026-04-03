"""Bisection-based weighted waterfilling optimizer.

Solves:
    maximize  Σ w_i * ln(r_i * n_i)
    subject to  Σ n_i = N_total
                n_min_i <= n_i <= n_max_i

where:
    w_i = weight for slice i (fairness priority)
    r_i = rate per PRB for slice i (from channel estimation)
    n_i = PRBs allocated to slice i
    N_total = total available PRBs

KKT optimality gives: n_i* = w_i / (λ * r_i), clamped to [min_i, max_i].
We find λ by bisection so that Σ n_i*(λ) = N_total.
"""

import logging
import math

logger = logging.getLogger(__name__)


def waterfill(
    rates: list[float],
    weights: list[float],
    total_prbs: int,
    min_prbs: list[int] | None = None,
    max_prbs: list[int] | None = None,
) -> list[int]:
    """Compute weighted waterfilling PRB allocation.

    Args:
        rates: Rate per PRB for each slice (Mbps). Must be > 0.
        weights: Utility weight for each slice. Must be > 0.
        total_prbs: Total PRBs to distribute.
        min_prbs: Minimum PRBs per slice (default: 5 each).
        max_prbs: Maximum PRBs per slice (default: total_prbs each).

    Returns:
        Integer PRB allocation for each slice, summing to total_prbs.
    """
    n = len(rates)
    assert n == len(weights), "rates and weights must have same length"
    assert n >= 1, "need at least one slice"
    assert total_prbs > 0, "total_prbs must be positive"

    if min_prbs is None:
        min_prbs = [5] * n
    if max_prbs is None:
        max_prbs = [total_prbs] * n

    # Sanitize inputs
    rates = [max(r, 1e-6) for r in rates]
    weights = [max(w, 1e-6) for w in weights]

    # Check feasibility: sum of mins must be <= total <= sum of maxs
    if sum(min_prbs) > total_prbs:
        logger.warning("Infeasible: sum(min_prbs)=%d > total=%d, using proportional fallback",
                        sum(min_prbs), total_prbs)
        return _proportional_fallback(weights, total_prbs)

    if sum(max_prbs) < total_prbs:
        logger.warning("Infeasible: sum(max_prbs)=%d < total=%d, capping at max",
                        sum(max_prbs), total_prbs)
        return list(max_prbs)

    # ── Bisection on λ (water level) ──────────────────────────────────────
    # n_i*(λ) = clamp(w_i / (λ * r_i), min_i, max_i)
    # We need Σ n_i*(λ) = N_total.
    # As λ increases, each n_i decreases → Σ decreases.
    # So find λ where Σ = N_total.

    def alloc_at_lambda(lam: float) -> list[float]:
        return [
            max(min_prbs[i], min(max_prbs[i], weights[i] / (lam * rates[i])))
            for i in range(n)
        ]

    def total_at_lambda(lam: float) -> float:
        return sum(alloc_at_lambda(lam))

    # Bracket: find λ_lo (over-allocation) and λ_hi (under-allocation)
    lam_lo = 1e-12
    lam_hi = 1e6

    # Ensure bracket is valid
    if total_at_lambda(lam_lo) < total_prbs:
        # Even at tiny λ, can't reach total (constrained by max_prbs)
        return _round_to_int(alloc_at_lambda(lam_lo), total_prbs, min_prbs, max_prbs)
    if total_at_lambda(lam_hi) > total_prbs:
        # Even at huge λ, sum > total (constrained by min_prbs)
        return _round_to_int(alloc_at_lambda(lam_hi), total_prbs, min_prbs, max_prbs)

    # Bisect
    for _ in range(100):
        lam_mid = (lam_lo + lam_hi) / 2.0
        s = total_at_lambda(lam_mid)
        if abs(s - total_prbs) < 0.01:
            break
        if s > total_prbs:
            lam_lo = lam_mid  # increase λ to reduce allocation
        else:
            lam_hi = lam_mid  # decrease λ to increase allocation

    continuous_alloc = alloc_at_lambda((lam_lo + lam_hi) / 2.0)
    return _round_to_int(continuous_alloc, total_prbs, min_prbs, max_prbs)


def _round_to_int(
    alloc: list[float],
    total: int,
    min_prbs: list[int],
    max_prbs: list[int],
) -> list[int]:
    """Round continuous allocation to integers using largest-remainder method.

    Ensures the sum equals total and respects min/max bounds.
    """
    n = len(alloc)
    floored = [max(min_prbs[i], min(max_prbs[i], int(math.floor(alloc[i])))) for i in range(n)]
    remainder = total - sum(floored)

    if remainder > 0:
        # Distribute remaining PRBs to slices with largest fractional parts
        fracs = [(alloc[i] - math.floor(alloc[i]), i) for i in range(n)]
        fracs.sort(reverse=True)
        for _, i in fracs:
            if remainder <= 0:
                break
            if floored[i] < max_prbs[i]:
                floored[i] += 1
                remainder -= 1
    elif remainder < 0:
        # Remove excess PRBs from slices with smallest fractional parts
        fracs = [(alloc[i] - math.floor(alloc[i]), i) for i in range(n)]
        fracs.sort()
        for _, i in fracs:
            if remainder >= 0:
                break
            if floored[i] > min_prbs[i]:
                floored[i] -= 1
                remainder += 1

    return floored


def _proportional_fallback(weights: list[float], total: int) -> list[int]:
    """Fallback: allocate proportionally to weights."""
    w_sum = sum(weights)
    continuous = [w / w_sum * total for w in weights]
    floored = [int(math.floor(c)) for c in continuous]
    remainder = total - sum(floored)
    fracs = [(continuous[i] - floored[i], i) for i in range(len(weights))]
    fracs.sort(reverse=True)
    for _, i in fracs:
        if remainder <= 0:
            break
        floored[i] += 1
        remainder -= 1
    return floored


def allocations_to_ratios(alloc: list[int], total: int) -> list[int]:
    """Convert PRB counts to percentage ratios (0–100).

    Args:
        alloc: PRB count per slice.
        total: Total PRBs.

    Returns:
        Percentage (0–100) per slice, summing to ~100.
    """
    return [max(1, round(a / total * 100)) for a in alloc]
