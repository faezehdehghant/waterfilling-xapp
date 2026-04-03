"""Unit tests for the waterfilling optimizer."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xapp"))

from waterfilling import waterfill, allocations_to_ratios, _proportional_fallback


def test_equal_rates_equal_weights():
    """Equal rates + equal weights → equal split."""
    alloc = waterfill(
        rates=[1.0, 1.0],
        weights=[1.0, 1.0],
        total_prbs=106,
        min_prbs=[5, 5],
        max_prbs=[101, 101],
    )
    assert sum(alloc) == 106
    assert alloc[0] == 53
    assert alloc[1] == 53


def test_equal_rates_weighted():
    """Equal rates + 2:1 weights → 2:1 split."""
    alloc = waterfill(
        rates=[1.0, 1.0],
        weights=[2.0, 1.0],
        total_prbs=90,
        min_prbs=[5, 5],
        max_prbs=[85, 85],
    )
    assert sum(alloc) == 90
    # With w1=2, w2=1: n1 = 2/(lambda*1), n2 = 1/(lambda*1) → n1 = 2*n2
    # n1 + n2 = 90, n1=60, n2=30
    assert alloc[0] == 60
    assert alloc[1] == 30


def test_different_rates_equal_weights():
    """Higher rate slice gets fewer PRBs for same utility."""
    alloc = waterfill(
        rates=[2.0, 1.0],  # slice 0 has 2x rate
        weights=[1.0, 1.0],
        total_prbs=90,
        min_prbs=[5, 5],
        max_prbs=[85, 85],
    )
    assert sum(alloc) == 90
    # n_i = w_i/(lambda*r_i) → with equal w, n1/n2 = r2/r1 = 1/2
    # n1 + n2 = 90, n1=30, n2=60
    assert alloc[0] == 30
    assert alloc[1] == 60


def test_min_prb_constraint():
    """Min PRB constraint is respected."""
    alloc = waterfill(
        rates=[10.0, 0.1],  # huge rate difference
        weights=[1.0, 1.0],
        total_prbs=106,
        min_prbs=[10, 10],
        max_prbs=[96, 96],
    )
    assert sum(alloc) == 106
    assert alloc[0] >= 10
    assert alloc[1] >= 10


def test_max_prb_constraint():
    """Max PRB constraint is respected."""
    alloc = waterfill(
        rates=[1.0, 1.0],
        weights=[10.0, 1.0],
        total_prbs=100,
        min_prbs=[5, 5],
        max_prbs=[60, 95],  # cap slice 0 at 60
    )
    assert sum(alloc) == 100
    assert alloc[0] <= 60


def test_single_slice():
    """Single slice gets all PRBs."""
    alloc = waterfill(
        rates=[1.0],
        weights=[1.0],
        total_prbs=106,
        min_prbs=[5],
        max_prbs=[106],
    )
    assert alloc == [106]


def test_three_slices():
    """Works with more than 2 slices."""
    alloc = waterfill(
        rates=[1.0, 1.0, 1.0],
        weights=[1.0, 1.0, 1.0],
        total_prbs=99,
        min_prbs=[5, 5, 5],
        max_prbs=[89, 89, 89],
    )
    assert sum(alloc) == 99
    assert alloc == [33, 33, 33]


def test_allocations_to_ratios():
    """PRB counts → percentages."""
    ratios = allocations_to_ratios([53, 53], 106)
    assert ratios == [50, 50]

    ratios = allocations_to_ratios([80, 26], 106)
    assert ratios[0] == 75
    assert ratios[1] == 25


def test_proportional_fallback():
    """Fallback produces valid allocation."""
    alloc = _proportional_fallback([1.0, 1.0], 106)
    assert sum(alloc) == 106
    assert alloc == [53, 53]


def test_default_min_prbs():
    """Default min_prbs of 5 is applied."""
    alloc = waterfill(
        rates=[100.0, 0.001],  # extreme difference
        weights=[1.0, 1.0],
        total_prbs=106,
    )
    assert sum(alloc) == 106
    assert alloc[0] >= 5
    assert alloc[1] >= 5


if __name__ == "__main__":
    test_equal_rates_equal_weights()
    test_equal_rates_weighted()
    test_different_rates_equal_weights()
    test_min_prb_constraint()
    test_max_prb_constraint()
    test_single_slice()
    test_three_slices()
    test_allocations_to_ratios()
    test_proportional_fallback()
    test_default_min_prbs()
    print("All waterfilling tests passed!")
