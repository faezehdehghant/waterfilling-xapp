"""gNB log parser for MAC-layer telemetry.

Tails /tmp/gnb.log and extracts per-UE statistics:
  - RNTI, CU-UE-ID, RSRP
  - DL MCS, BLER, dlsch_rounds
  - Cumulative TX/RX bytes (used to compute throughput deltas)
  - Slice assignment (from "Active slices for UE" lines)
"""

import re
import subprocess
import threading
import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field

from config import (
    GNB_LOG_PATH,
    TELEMETRY_WINDOW,
    DEMAND_SATURATED_THRESHOLD,
    DEMAND_LOW_THRESHOLD,
)

logger = logging.getLogger(__name__)


@dataclass
class UeSample:
    """A single telemetry snapshot for one UE."""
    timestamp: float
    rnti: str
    cu_ue_id: int
    rsrp: int
    dl_mcs: int
    dl_bler: float
    dlsch_rounds: tuple  # (r0, r1, r2, r3)
    dlsch_errors: int
    tx_bytes: int
    rx_bytes: int
    slice_id: int = -1   # filled in from slice assignment tracking


@dataclass
class SliceStats:
    """Aggregated statistics for one slice over the telemetry window."""
    slice_id: int
    avg_mcs: float = 0.0
    avg_rsrp: float = 0.0
    avg_bler: float = 0.0
    dl_throughput_bps: float = 0.0  # computed from TX byte deltas
    ul_throughput_bps: float = 0.0  # computed from RX byte deltas
    num_ues: int = 0
    demand_ratio: float = 1.0       # 1.0 = assumed full demand


# ── Regex patterns for gNB log lines ──────────────────────────────────────────

# "UE RNTI 6b99 CU-UE-ID 1 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -62 (16 meas)"
RE_UE_HEADER = re.compile(
    r'UE RNTI\s+([0-9a-f]+)\s+CU-UE-ID\s+(\d+)\s+\S+\s+'
    r'PH\s+[-\d]+\s+dB\s+PCMAX\s+[-\d]+\s+dBm,\s+average\s+RSRP\s+([-\d]+)'
)

# "UE 6b99: dlsch_rounds 50560/1/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9"
RE_DLSCH = re.compile(
    r'UE\s+([0-9a-f]+):\s+dlsch_rounds\s+(\d+)/(\d+)/(\d+)/(\d+),\s+'
    r'dlsch_errors\s+(\d+),\s+pucch0_DTX\s+\d+,\s+'
    r'BLER\s+([\d.]+)\s+MCS\s+\(\d+\)\s+(\d+)'
)

# "UE 6b99: MAC:    TX      221869218 RX       47057323 bytes"
RE_MAC_BYTES = re.compile(
    r'UE\s+([0-9a-f]+):\s+MAC:\s+TX\s+(\d+)\s+RX\s+(\d+)\s+bytes'
)

# "Active slices for UE 8b87 = [ 2 ]"
RE_ACTIVE_SLICES = re.compile(
    r'Active slices for UE\s+([0-9a-f]+)\s*=\s*\[\s*([\d\s]+)\s*\]'
)

# "Frame.Slot 256.0"  — marks the start of a new stats block
RE_FRAME_SLOT = re.compile(r'Frame\.Slot\s+(\d+)\.(\d+)')


class GnbLogParser:
    """Parses gNB MAC stats from the log file using tail -f."""

    def __init__(self, log_path: str = GNB_LOG_PATH, window_size: int = TELEMETRY_WINDOW):
        self.log_path = log_path
        self.window_size = window_size

        # UE RNTI → slice ID mapping (learned from log)
        self.ue_slice_map: dict[str, int] = {}

        # Per-UE sliding window of samples
        self._ue_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))

        # Temporary per-block accumulator (building up a UeSample line by line)
        self._pending: dict[str, dict] = {}

        # Previous TX/RX bytes for throughput delta calculation
        self._prev_bytes: dict[str, tuple[int, int]] = {}

        # Background thread
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        """Start background log tailing thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._tail_loop, daemon=True)
        self._thread.start()
        logger.info("Telemetry parser started on %s", self.log_path)

    def stop(self):
        """Stop background log tailing thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Telemetry parser stopped")

    def _tail_loop(self):
        """Background: tail -f the gNB log and parse lines."""
        proc = subprocess.Popen(
            ["tail", "-f", "-n", "2000", self.log_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            while not self._stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.01)
                    continue
                self._parse_line(line.strip(), time.time())
        finally:
            proc.terminate()
            proc.wait()

    def _parse_line(self, line: str, ts: float):
        """Parse a single log line and update internal state."""
        # Check for slice assignment
        m = RE_ACTIVE_SLICES.search(line)
        if m:
            rnti = m.group(1)
            slices = [int(s) for s in m.group(2).split()]
            if slices:
                with self._lock:
                    self.ue_slice_map[rnti] = slices[0]
            return

        # UE header line (RNTI, CU-UE-ID, RSRP)
        m = RE_UE_HEADER.search(line)
        if m:
            rnti = m.group(1)
            cu_ue_id = int(m.group(2))
            self._pending[rnti] = {
                "timestamp": ts,
                "rnti": rnti,
                "cu_ue_id": cu_ue_id,
                "rsrp": int(m.group(3)),
            }
            # Fallback slice assignment from CU-UE-ID if not yet mapped
            # CU-UE-ID 1 → Slice 1 (SST=1), CU-UE-ID 2 → Slice 2 (SST=1/SD=2)
            with self._lock:
                if rnti not in self.ue_slice_map:
                    self.ue_slice_map[rnti] = cu_ue_id
                    logger.info("Auto-mapped UE %s (CU-UE-ID %d) → Slice %d",
                                rnti, cu_ue_id, cu_ue_id)
            return

        # DL stats line (dlsch_rounds, BLER, MCS)
        m = RE_DLSCH.search(line)
        if m:
            rnti = m.group(1)
            if rnti in self._pending:
                self._pending[rnti].update({
                    "dlsch_rounds": (int(m.group(2)), int(m.group(3)),
                                     int(m.group(4)), int(m.group(5))),
                    "dlsch_errors": int(m.group(6)),
                    "dl_bler": float(m.group(7)),
                    "dl_mcs": int(m.group(8)),
                })
            return

        # MAC bytes line (TX/RX) — this completes a UE sample
        m = RE_MAC_BYTES.search(line)
        if m:
            rnti = m.group(1)
            if rnti in self._pending and "dl_mcs" in self._pending[rnti]:
                self._pending[rnti]["tx_bytes"] = int(m.group(2))
                self._pending[rnti]["rx_bytes"] = int(m.group(3))
                self._finalize_sample(rnti)
            return

    def _finalize_sample(self, rnti: str):
        """Convert pending data into a UeSample and add to the sliding window."""
        data = self._pending.pop(rnti, None)
        if data is None:
            return

        with self._lock:
            slice_id = self.ue_slice_map.get(rnti, -1)

        sample = UeSample(
            timestamp=data["timestamp"],
            rnti=data["rnti"],
            cu_ue_id=data["cu_ue_id"],
            rsrp=data["rsrp"],
            dl_mcs=data["dl_mcs"],
            dl_bler=data["dl_bler"],
            dlsch_rounds=data["dlsch_rounds"],
            dlsch_errors=data["dlsch_errors"],
            tx_bytes=data["tx_bytes"],
            rx_bytes=data["rx_bytes"],
            slice_id=slice_id,
        )

        with self._lock:
            self._ue_samples[rnti].append(sample)

    def get_slice_stats(self) -> dict[int, SliceStats]:
        """Aggregate per-UE samples into per-slice statistics.

        Returns a dict mapping slice_id → SliceStats.
        Throughput is estimated from cumulative TX/RX byte deltas.
        """
        with self._lock:
            ue_map = dict(self.ue_slice_map)
            all_samples = {rnti: list(dq) for rnti, dq in self._ue_samples.items()}

        # Group UEs by slice
        slice_ues: dict[int, list[str]] = defaultdict(list)
        for rnti, sid in ue_map.items():
            if sid >= 0:
                slice_ues[sid].append(rnti)

        results = {}
        for sid in sorted(slice_ues.keys()):
            rntis = slice_ues[sid]
            mcs_vals, rsrp_vals, bler_vals = [], [], []
            total_dl_tput, total_ul_tput = 0.0, 0.0

            for rnti in rntis:
                samples = all_samples.get(rnti, [])
                if not samples:
                    continue

                # Average MCS, RSRP, BLER over window
                for s in samples:
                    mcs_vals.append(s.dl_mcs)
                    rsrp_vals.append(s.rsrp)
                    bler_vals.append(s.dl_bler)

                # Throughput from byte deltas (first vs last sample in window)
                if len(samples) >= 2:
                    dt = samples[-1].timestamp - samples[0].timestamp
                    if dt > 0:
                        dl_delta = samples[-1].tx_bytes - samples[0].tx_bytes
                        ul_delta = samples[-1].rx_bytes - samples[0].rx_bytes
                        total_dl_tput += (dl_delta * 8) / dt  # bits per second
                        total_ul_tput += (ul_delta * 8) / dt

            stats = SliceStats(
                slice_id=sid,
                avg_mcs=sum(mcs_vals) / len(mcs_vals) if mcs_vals else 0.0,
                avg_rsrp=sum(rsrp_vals) / len(rsrp_vals) if rsrp_vals else 0.0,
                avg_bler=sum(bler_vals) / len(bler_vals) if bler_vals else 0.0,
                dl_throughput_bps=total_dl_tput,
                ul_throughput_bps=total_ul_tput,
                num_ues=len(rntis),
                demand_ratio=1.0,  # default: assume full demand
            )
            results[sid] = stats

        return results

    def get_latest_ue_stats(self) -> dict[str, UeSample | None]:
        """Return the most recent sample for each known UE."""
        with self._lock:
            return {
                rnti: (list(dq)[-1] if dq else None)
                for rnti, dq in self._ue_samples.items()
            }

    def parse_lines(self, lines: list[str], timestamp: float | None = None):
        """Parse a batch of lines (useful for testing without tail -f)."""
        ts = timestamp or time.time()
        for line in lines:
            self._parse_line(line.strip(), ts)
