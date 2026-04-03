"""Baseline resource allocation strategies for comparison.

Each function returns a list of integer PRB allocations per slice.
"""

import math


def static_equal(n_slices: int, total: int) -> list[int]:
    """Equal split across all slices."""
    base = total // n_slices
    alloc = [base] * n_slices
    remainder = total - sum(alloc)
    for i in range(remainder):
        alloc[i] += 1
    return alloc


def static_weighted(weights: list[float], total: int) -> list[int]:
    """Proportional allocation based on fixed weights."""
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


def round_robin(step: int, n_slices: int, total: int, swing: int = 20) -> list[int]:
    """Alternating allocation: each step favors a different slice.

    Args:
        step: Current time step (integer).
        n_slices: Number of slices.
        total: Total PRBs.
        swing: PRBs to shift toward the favored slice.

    Returns:
        PRB allocation list.
    """
    base = total // n_slices
    alloc = [base] * n_slices
    remainder = total - sum(alloc)
    alloc[0] += remainder  # give remainder to first slice initially

    favored = step % n_slices
    # Move 'swing' PRBs toward the favored slice
    per_other = swing // (n_slices - 1) if n_slices > 1 else 0
    for i in range(n_slices):
        if i == favored:
            alloc[i] += swing - per_other * (n_slices - 1)
        else:
            alloc[i] -= per_other

    # Clamp to valid range
    alloc = [max(5, a) for a in alloc]
    # Re-normalize to total
    diff = total - sum(alloc)
    alloc[favored] += diff

    return alloc


def max_cqi(slice_stats: dict, total: int, min_prbs: int = 5) -> list[int]:
    """Throughput-maximizing: allocate proportionally to channel quality.

    Gives more PRBs to slices with higher MCS (better channel).
    Maximizes total throughput but ignores fairness.

    Args:
        slice_stats: Dict of slice_id → SliceStats with avg_mcs field.
        total: Total PRBs.
        min_prbs: Minimum PRBs per slice.

    Returns:
        PRB allocation list (ordered by slice_id).
    """
    sids = sorted(slice_stats.keys())
    n = len(sids)
    if n == 0:
        return []

    mcs_values = [max(1, slice_stats[sid].avg_mcs) for sid in sids]
    mcs_sum = sum(mcs_values)

    # Proportional to MCS, with minimum guarantee
    alloc = [max(min_prbs, int(round(m / mcs_sum * total))) for m in mcs_values]

    # Adjust to exactly sum to total
    diff = total - sum(alloc)
    best_idx = mcs_values.index(max(mcs_values))
    alloc[best_idx] += diff

    return alloc
