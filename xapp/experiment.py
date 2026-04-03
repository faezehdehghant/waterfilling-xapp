#!/usr/bin/env python3
"""Experiment harness for waterfilling xApp evaluation.

Orchestrates iperf3 traffic, runs different allocation strategies,
and collects throughput + allocation time series for comparison.
"""

import csv
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    TOTAL_PRBS,
    NUM_SLICES,
    CONTROL_INTERVAL_S,
    MIN_PRB_PER_SLICE,
    EXPERIMENT_DURATION_S,
    EXPERIMENT_REPETITIONS,
    IPERF_SERVER_IP,
    UE1_NAMESPACE,
    UE2_NAMESPACE,
)
from telemetry import GnbLogParser
from channel_estimator import estimate_slice_rates, rate_per_prb_mbps
from waterfilling import waterfill, allocations_to_ratios
from control import write_rrm_policy
from baselines import static_equal, static_weighted, round_robin, max_cqi

logger = logging.getLogger("experiment")

RESULTS_DIR = Path("/home/faezeh/ORANSlice/xapp/results")


@dataclass
class DataPoint:
    """Single measurement at one time step."""
    timestamp: float
    step: int
    scenario: str
    repetition: int
    slice1_alloc_prbs: int
    slice2_alloc_prbs: int
    slice1_ratio_pct: int
    slice2_ratio_pct: int
    slice1_mcs: float
    slice2_mcs: float
    slice1_dl_tput_mbps: float
    slice2_dl_tput_mbps: float
    total_dl_tput_mbps: float


def _run_cmd(cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _start_iperf_servers():
    """Start iperf3 servers on the ext-dn container (two instances on different ports)."""
    logger.info("Starting iperf3 servers on ext-dn...")
    # Kill any existing iperf3 servers
    _run_cmd("sudo docker exec oai-ext-dn pkill -f iperf3 2>/dev/null")
    time.sleep(0.5)
    # Start server on port 5201 (for UE1) and 5202 (for UE2)
    _run_cmd("sudo docker exec -d oai-ext-dn iperf3 -s -p 5201")
    _run_cmd("sudo docker exec -d oai-ext-dn iperf3 -s -p 5202")
    time.sleep(1)


def _stop_iperf():
    """Stop all iperf3 processes."""
    _run_cmd("sudo docker exec oai-ext-dn pkill -f iperf3 2>/dev/null")
    _run_cmd("sudo pkill -f iperf3 2>/dev/null")
    time.sleep(0.5)


def _start_iperf_client_ue1(duration: int, output_file: str) -> subprocess.Popen:
    """Start iperf3 DL client for UE1 (inside ue1ns namespace)."""
    cmd = (
        f"sudo ip netns exec {UE1_NAMESPACE} iperf3 -c {IPERF_SERVER_IP} -p 5201 "
        f"-t {duration} -R -J"  # -R for downlink, -J for JSON
    )
    logger.info("Starting UE1 iperf3: %s", cmd)
    f = open(output_file, "w")
    return subprocess.Popen(cmd, shell=True, stdout=f, stderr=subprocess.DEVNULL)


def _start_iperf_client_ue2(duration: int, output_file: str) -> subprocess.Popen:
    """Start iperf3 DL client for UE2 (inside ue2ns namespace)."""
    cmd = (
        f"sudo ip netns exec {UE2_NAMESPACE} iperf3 -c {IPERF_SERVER_IP} -p 5202 "
        f"-t {duration} -R -J"
    )
    logger.info("Starting UE2 iperf3: %s", cmd)
    f = open(output_file, "w")
    return subprocess.Popen(cmd, shell=True, stdout=f, stderr=subprocess.DEVNULL)


def _parse_iperf_json(filepath: str) -> list[dict]:
    """Parse iperf3 JSON output to get per-interval throughput."""
    try:
        with open(filepath) as f:
            data = json.load(f)
        intervals = data.get("intervals", [])
        return [
            {
                "start": iv["sum"]["start"],
                "end": iv["sum"]["end"],
                "bits_per_second": iv["sum"]["bits_per_second"],
                "mbps": iv["sum"]["bits_per_second"] / 1e6,
            }
            for iv in intervals
        ]
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse iperf output %s: %s", filepath, e)
        return []


def _jains_fairness(throughputs: list[float]) -> float:
    """Compute Jain's fairness index for a list of throughput values."""
    if not throughputs or all(t == 0 for t in throughputs):
        return 0.0
    n = len(throughputs)
    s = sum(throughputs)
    ss = sum(t ** 2 for t in throughputs)
    if ss == 0:
        return 0.0
    return (s ** 2) / (n * ss)


class Experiment:
    """Manages a single experiment scenario run."""

    def __init__(self, scenario: str, duration: int, repetition: int):
        self.scenario = scenario
        self.duration = duration
        self.repetition = repetition
        self.data_points: list[DataPoint] = []
        self.parser = GnbLogParser()

    def run_static_equal(self):
        """Run with static 50/50 allocation."""
        alloc = static_equal(NUM_SLICES, TOTAL_PRBS)
        self._run_fixed_allocation(alloc)

    def run_static_weighted(self, weights: list[float]):
        """Run with static weighted allocation."""
        alloc = static_weighted(weights, TOTAL_PRBS)
        self._run_fixed_allocation(alloc)

    def run_waterfill(self, weights: list[float]):
        """Run with dynamic waterfilling allocation."""
        self._run_dynamic_allocation("waterfill", weights)

    def run_max_cqi(self):
        """Run with max-CQI (throughput-maximizing) allocation."""
        self._run_dynamic_allocation("max_cqi", [1.0, 1.0])

    def _run_fixed_allocation(self, alloc: list[int]):
        """Execute experiment with a fixed PRB allocation."""
        ratios = allocations_to_ratios(alloc, TOTAL_PRBS)
        write_rrm_policy(ratios)

        self.parser.start()
        time.sleep(3)  # let telemetry warm up

        # Start traffic
        out_dir = RESULTS_DIR / self.scenario / f"rep{self.repetition}"
        out_dir.mkdir(parents=True, exist_ok=True)
        _start_iperf_servers()

        p1 = _start_iperf_client_ue1(self.duration, str(out_dir / "ue1_iperf.json"))
        p2 = _start_iperf_client_ue2(self.duration, str(out_dir / "ue2_iperf.json"))

        # Collect telemetry during experiment
        start_time = time.time()
        step = 0
        while time.time() - start_time < self.duration:
            step += 1
            stats = self.parser.get_slice_stats()
            self._record_data_point(step, alloc, ratios, stats)
            time.sleep(CONTROL_INTERVAL_S)

        p1.wait(timeout=10)
        p2.wait(timeout=10)
        self.parser.stop()

    def _run_dynamic_allocation(self, strategy: str, weights: list[float]):
        """Execute experiment with dynamic PRB allocation."""
        self.parser.start()
        time.sleep(3)

        out_dir = RESULTS_DIR / self.scenario / f"rep{self.repetition}"
        out_dir.mkdir(parents=True, exist_ok=True)
        _start_iperf_servers()

        p1 = _start_iperf_client_ue1(self.duration, str(out_dir / "ue1_iperf.json"))
        p2 = _start_iperf_client_ue2(self.duration, str(out_dir / "ue2_iperf.json"))

        start_time = time.time()
        step = 0
        while time.time() - start_time < self.duration:
            step += 1
            stats = self.parser.get_slice_stats()

            if strategy == "waterfill" and stats:
                rates = estimate_slice_rates(stats)
                sids = sorted(stats.keys())
                rate_list = [rates.get(sid, rate_per_prb_mbps(9)) for sid in sids]
                while len(rate_list) < NUM_SLICES:
                    rate_list.append(rate_per_prb_mbps(9))

                alloc = waterfill(
                    rates=rate_list[:NUM_SLICES],
                    weights=weights[:NUM_SLICES],
                    total_prbs=TOTAL_PRBS,
                    min_prbs=[MIN_PRB_PER_SLICE] * NUM_SLICES,
                    max_prbs=[TOTAL_PRBS - MIN_PRB_PER_SLICE * (NUM_SLICES - 1)] * NUM_SLICES,
                )
            elif strategy == "max_cqi" and stats:
                alloc = max_cqi(stats, TOTAL_PRBS)
            else:
                alloc = static_equal(NUM_SLICES, TOTAL_PRBS)

            ratios = allocations_to_ratios(alloc, TOTAL_PRBS)
            write_rrm_policy(ratios)
            self._record_data_point(step, alloc, ratios, stats)
            time.sleep(CONTROL_INTERVAL_S)

        p1.wait(timeout=10)
        p2.wait(timeout=10)
        self.parser.stop()

    def _record_data_point(self, step: int, alloc: list[int], ratios: list[int], stats: dict):
        """Record one measurement data point."""
        sids = sorted(stats.keys()) if stats else []
        s1 = stats.get(1, None) if stats else None
        s2 = stats.get(2, None) if stats else None

        dp = DataPoint(
            timestamp=time.time(),
            step=step,
            scenario=self.scenario,
            repetition=self.repetition,
            slice1_alloc_prbs=alloc[0] if len(alloc) > 0 else 0,
            slice2_alloc_prbs=alloc[1] if len(alloc) > 1 else 0,
            slice1_ratio_pct=ratios[0] if len(ratios) > 0 else 0,
            slice2_ratio_pct=ratios[1] if len(ratios) > 1 else 0,
            slice1_mcs=s1.avg_mcs if s1 else 0.0,
            slice2_mcs=s2.avg_mcs if s2 else 0.0,
            slice1_dl_tput_mbps=s1.dl_throughput_bps / 1e6 if s1 else 0.0,
            slice2_dl_tput_mbps=s2.dl_throughput_bps / 1e6 if s2 else 0.0,
            total_dl_tput_mbps=(
                (s1.dl_throughput_bps if s1 else 0) +
                (s2.dl_throughput_bps if s2 else 0)
            ) / 1e6,
        )
        self.data_points.append(dp)

    def save_results(self):
        """Save collected data points to CSV."""
        out_dir = RESULTS_DIR / self.scenario / f"rep{self.repetition}"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "timeseries.csv"

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "step", "scenario", "repetition",
                "slice1_prbs", "slice2_prbs", "slice1_pct", "slice2_pct",
                "slice1_mcs", "slice2_mcs",
                "slice1_tput_mbps", "slice2_tput_mbps", "total_tput_mbps",
            ])
            for dp in self.data_points:
                writer.writerow([
                    f"{dp.timestamp:.3f}", dp.step, dp.scenario, dp.repetition,
                    dp.slice1_alloc_prbs, dp.slice2_alloc_prbs,
                    dp.slice1_ratio_pct, dp.slice2_ratio_pct,
                    f"{dp.slice1_mcs:.1f}", f"{dp.slice2_mcs:.1f}",
                    f"{dp.slice1_dl_tput_mbps:.3f}", f"{dp.slice2_dl_tput_mbps:.3f}",
                    f"{dp.total_dl_tput_mbps:.3f}",
                ])
        logger.info("Saved time series to %s", csv_path)


def _print_summary(scenario: str, data_points: list[DataPoint]):
    """Print summary statistics for a scenario."""
    if not data_points:
        return

    s1_tputs = [dp.slice1_dl_tput_mbps for dp in data_points if dp.slice1_dl_tput_mbps > 0]
    s2_tputs = [dp.slice2_dl_tput_mbps for dp in data_points if dp.slice2_dl_tput_mbps > 0]
    total_tputs = [dp.total_dl_tput_mbps for dp in data_points if dp.total_dl_tput_mbps > 0]

    # Jain's fairness: use per-step pairs
    fairness_values = []
    for dp in data_points:
        if dp.slice1_dl_tput_mbps > 0 or dp.slice2_dl_tput_mbps > 0:
            fairness_values.append(
                _jains_fairness([dp.slice1_dl_tput_mbps, dp.slice2_dl_tput_mbps])
            )

    avg = lambda lst: sum(lst) / len(lst) if lst else 0.0

    logger.info("=" * 60)
    logger.info("SCENARIO: %s", scenario)
    logger.info("  Slice 1 avg throughput: %.2f Mbps", avg(s1_tputs))
    logger.info("  Slice 2 avg throughput: %.2f Mbps", avg(s2_tputs))
    logger.info("  Total avg throughput:   %.2f Mbps", avg(total_tputs))
    logger.info("  Jain's fairness index:  %.4f", avg(fairness_values))
    logger.info("=" * 60)


def run_all_experiments():
    """Run the complete experiment suite."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    scenarios = [
        ("static_equal", lambda exp: exp.run_static_equal()),
        ("static_70_30", lambda exp: exp.run_static_weighted([0.7, 0.3])),
        ("waterfill_equal", lambda exp: exp.run_waterfill([1.0, 1.0])),
        ("waterfill_weighted", lambda exp: exp.run_waterfill([2.0, 1.0])),
        ("max_cqi", lambda exp: exp.run_max_cqi()),
    ]

    all_summaries = {}

    for scenario_name, run_fn in scenarios:
        logger.info("Starting scenario: %s", scenario_name)
        all_data = []

        for rep in range(1, EXPERIMENT_REPETITIONS + 1):
            logger.info("  Repetition %d/%d", rep, EXPERIMENT_REPETITIONS)
            exp = Experiment(scenario_name, EXPERIMENT_DURATION_S, rep)
            run_fn(exp)
            exp.save_results()
            all_data.extend(exp.data_points)

            # Brief pause between repetitions
            time.sleep(5)

        _print_summary(scenario_name, all_data)
        all_summaries[scenario_name] = all_data

        # Pause between scenarios
        time.sleep(10)

    # Save overall summary
    summary_path = RESULTS_DIR / "summary.csv"
    with open(summary_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "scenario", "avg_slice1_mbps", "avg_slice2_mbps",
            "avg_total_mbps", "jains_fairness",
        ])
        for name, data in all_summaries.items():
            s1 = [dp.slice1_dl_tput_mbps for dp in data if dp.slice1_dl_tput_mbps > 0]
            s2 = [dp.slice2_dl_tput_mbps for dp in data if dp.slice2_dl_tput_mbps > 0]
            tot = [dp.total_dl_tput_mbps for dp in data if dp.total_dl_tput_mbps > 0]
            fair = [
                _jains_fairness([dp.slice1_dl_tput_mbps, dp.slice2_dl_tput_mbps])
                for dp in data
                if dp.slice1_dl_tput_mbps > 0 or dp.slice2_dl_tput_mbps > 0
            ]
            avg = lambda lst: sum(lst) / len(lst) if lst else 0.0
            writer.writerow([
                name,
                f"{avg(s1):.3f}", f"{avg(s2):.3f}",
                f"{avg(tot):.3f}", f"{avg(fair):.4f}",
            ])

    logger.info("All experiments complete. Summary saved to %s", summary_path)
