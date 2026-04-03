"""Channel estimation: MCS/CQI → spectral efficiency → rate per PRB.

Uses 3GPP TS 38.214 lookup tables from config.py to convert reported
MCS indices into achievable data rates per PRB.
"""

import logging

from config import (
    MCS_TABLE_256QAM,
    CQI_TO_MCS,
    SUBCARRIERS_PER_PRB,
    SYMBOLS_PER_SLOT,
    OVERHEAD_FACTOR,
    SLOT_DURATION_MS,
)

logger = logging.getLogger(__name__)


def mcs_to_spectral_efficiency(mcs_index: int) -> float:
    """Look up spectral efficiency (bits/RE) for a given MCS index.

    Args:
        mcs_index: MCS index (0–27 for 256QAM table).

    Returns:
        Spectral efficiency in bits per resource element.
    """
    mcs_index = max(0, min(mcs_index, 27))
    _, _, se = MCS_TABLE_256QAM[mcs_index]
    return se


def cqi_to_mcs(cqi: int) -> int:
    """Map 4-bit CQI index to approximate MCS index."""
    cqi = max(0, min(cqi, 15))
    return CQI_TO_MCS[cqi]


def rate_per_prb_mbps(mcs_index: int) -> float:
    """Compute achievable DL data rate per PRB in Mbps.

    Formula:
        rate = SE * 12_subcarriers * 14_symbols * (1 - overhead) / slot_duration
        where slot_duration is in seconds.

    Args:
        mcs_index: Reported DL MCS index.

    Returns:
        Data rate in Mbps achievable per PRB.
    """
    se = mcs_to_spectral_efficiency(mcs_index)

    # Resource elements per PRB per slot
    res_per_prb = SUBCARRIERS_PER_PRB * SYMBOLS_PER_SLOT

    # Bits per PRB per slot (after overhead)
    bits_per_prb_per_slot = se * res_per_prb * (1 - OVERHEAD_FACTOR)

    # Convert to Mbps: bits_per_slot / slot_duration_seconds
    slot_duration_s = SLOT_DURATION_MS / 1000.0
    rate_bps = bits_per_prb_per_slot / slot_duration_s
    rate_mbps = rate_bps / 1e6

    return rate_mbps


def estimate_slice_rates(slice_stats: dict) -> dict[int, float]:
    """Estimate rate-per-PRB for each slice based on its average MCS.

    Args:
        slice_stats: Dict of slice_id → SliceStats (from telemetry).

    Returns:
        Dict of slice_id → rate_per_prb in Mbps.
    """
    rates = {}
    for sid, stats in slice_stats.items():
        mcs = int(round(stats.avg_mcs))
        r = rate_per_prb_mbps(mcs)
        rates[sid] = r
        logger.debug("Slice %d: MCS=%d, rate_per_prb=%.3f Mbps", sid, mcs, r)
    return rates
