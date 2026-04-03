#!/usr/bin/env python3
"""Waterfilling xApp — control loop entry point.

Reads gNB telemetry, computes optimal PRB allocation via waterfilling,
and writes rrmPolicy.json to control the gNB's MAC scheduler.

Usage:
    python main.py --mode waterfill --weights 1.0 1.0
    python main.py --mode static --weights 0.7 0.3
    python main.py --mode round-robin --interval 2
    python main.py --experiment
"""

import argparse
import logging
import signal
import sys
import time

from config import (
    TOTAL_PRBS,
    NUM_SLICES,
    CONTROL_INTERVAL_S,
    MIN_PRB_PER_SLICE,
    DEMAND_LOW_THRESHOLD,
)
from telemetry import GnbLogParser
from channel_estimator import estimate_slice_rates, rate_per_prb_mbps
from waterfilling import waterfill, allocations_to_ratios
from control import write_rrm_policy
from baselines import static_equal, static_weighted, round_robin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("xapp")

# Graceful shutdown
_running = True

def _signal_handler(signum, frame):
    global _running
    logger.info("Received signal %d, shutting down...", signum)
    _running = False

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def run_control_loop(mode: str, weights: list[float], interval: float):
    """Run the main control loop.

    Args:
        mode: Allocation mode ('waterfill', 'static', 'round-robin').
        weights: Per-slice weights [w1, w2].
        interval: Seconds between control iterations.
    """
    parser = GnbLogParser()
    parser.start()

    # Allow telemetry to accumulate
    logger.info("Waiting 5s for initial telemetry...")
    time.sleep(5)

    step = 0
    try:
        while _running:
            step += 1
            stats = parser.get_slice_stats()

            if mode == "waterfill":
                alloc = _waterfill_step(stats, weights)
            elif mode == "static":
                alloc = static_weighted(weights, TOTAL_PRBS)
            elif mode == "round-robin":
                alloc = round_robin(step, NUM_SLICES, TOTAL_PRBS)
            else:
                logger.error("Unknown mode: %s", mode)
                break

            ratios = allocations_to_ratios(alloc, TOTAL_PRBS)

            # Log current state
            if stats:
                mcs_list = [f"{stats[sid].avg_mcs:.1f}" for sid in sorted(stats.keys())]
                rsrp_list = [f"{stats[sid].avg_rsrp:.0f}" for sid in sorted(stats.keys())]
                tput_list = [f"{stats[sid].dl_throughput_bps/1e6:.2f}" for sid in sorted(stats.keys())]
                logger.info(
                    "Step %d | MCS=%s RSRP=%s Tput(Mbps)=%s | Alloc=%s Ratio=%s%%",
                    step, mcs_list, rsrp_list, tput_list, alloc, ratios,
                )
            else:
                logger.info(
                    "Step %d | No telemetry yet | Alloc=%s Ratio=%s%%",
                    step, alloc, ratios,
                )

            write_rrm_policy(ratios)
            time.sleep(interval)

    finally:
        parser.stop()
        logger.info("Control loop stopped after %d steps", step)


def _waterfill_step(stats: dict, weights: list[float]) -> list[int]:
    """One iteration of waterfilling allocation."""
    if not stats:
        # No telemetry yet — equal split
        return static_equal(NUM_SLICES, TOTAL_PRBS)

    sids = sorted(stats.keys())

    # Get rate per PRB for each slice
    rates = estimate_slice_rates(stats)
    rate_list = [rates.get(sid, rate_per_prb_mbps(9)) for sid in sids]

    # Pad if we have fewer slices than expected
    while len(rate_list) < NUM_SLICES:
        rate_list.append(rate_per_prb_mbps(9))  # default MCS 9

    # Adjust weights based on demand (optional demand-awareness)
    adjusted_weights = list(weights)
    for i, sid in enumerate(sids):
        if i < len(adjusted_weights):
            s = stats[sid]
            # If slice is using very few resources (low demand), reduce weight
            # to reallocate to slices with actual demand
            if s.dl_throughput_bps < 1e3:  # < 1 kbps → essentially no traffic
                pass  # demand-awareness disabled for experiment
                logger.debug("Slice %d has no traffic, reducing weight", sid)

    min_prbs = [MIN_PRB_PER_SLICE] * NUM_SLICES
    max_prbs = [TOTAL_PRBS - MIN_PRB_PER_SLICE * (NUM_SLICES - 1)] * NUM_SLICES

    return waterfill(
        rates=rate_list[:NUM_SLICES],
        weights=adjusted_weights[:NUM_SLICES],
        total_prbs=TOTAL_PRBS,
        min_prbs=min_prbs,
        max_prbs=max_prbs,
    )


def main():
    ap = argparse.ArgumentParser(description="Waterfilling xApp for ORANSlice")
    ap.add_argument(
        "--mode",
        choices=["waterfill", "static", "round-robin"],
        default="waterfill",
        help="Allocation strategy (default: waterfill)",
    )
    ap.add_argument(
        "--weights",
        type=float,
        nargs="+",
        default=[1.0, 1.0],
        help="Per-slice weights (default: 1.0 1.0)",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=CONTROL_INTERVAL_S,
        help=f"Control loop interval in seconds (default: {CONTROL_INTERVAL_S})",
    )
    ap.add_argument(
        "--experiment",
        action="store_true",
        help="Run full experiment suite (overrides --mode)",
    )
    args = ap.parse_args()

    if args.experiment:
        from experiment import run_all_experiments
        run_all_experiments()
    else:
        if len(args.weights) != NUM_SLICES:
            ap.error(f"Expected {NUM_SLICES} weights, got {len(args.weights)}")
        logger.info(
            "Starting xApp: mode=%s, weights=%s, interval=%.1fs",
            args.mode, args.weights, args.interval,
        )
        run_control_loop(args.mode, args.weights, args.interval)


if __name__ == "__main__":
    main()
