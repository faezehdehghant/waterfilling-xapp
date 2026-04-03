"""Unit tests for the gNB log telemetry parser."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xapp"))

import time
from telemetry import GnbLogParser, UeSample

# Sample gNB log block (matches real OAI gNB output)
SAMPLE_LOG_BLOCK = """
[0m[NR_MAC]   Frame.Slot 256.0
UE RNTI 6b99 CU-UE-ID 1 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -62 (16 meas)
UE 6b99: UL-RI 1, TPMI 0
UE 6b99: dlsch_rounds 50560/1/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9
UE 6b99: ulsch_rounds 395620/1355/7/0, ulsch_errors 0, ulsch_DTX 0, BLER 0.00000 MCS (0) 28 (Qm 6  dB) NPRB 5  SNR 51.0 dB
UE 6b99: MAC:    TX      221869218 RX       47057323 bytes
UE 6b99: LCID 1: TX            549 RX            266 bytes
UE 6b99: LCID 2: TX              0 RX              0 bytes
UE 6b99: LCID 4: TX      220041600 RX        2070380 bytes
UE RNTI 8b87 CU-UE-ID 2 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -71 (16 meas)
UE 8b87: UL-RI 1, TPMI 0
UE 8b87: dlsch_rounds 45953/0/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9
UE 8b87: ulsch_rounds 377202/1314/0/0, ulsch_errors 0, ulsch_DTX 0, BLER 0.00031 MCS (0) 28 (Qm 6  dB) NPRB 5  SNR 51.0 dB
UE 8b87: MAC:    TX      141469583 RX       44365094 bytes
UE 8b87: LCID 1: TX            555 RX            273 bytes
Active slices for UE 6b99 = [ 1 ]
Active slices for UE 8b87 = [ 2 ]
""".strip()

SAMPLE_LOG_BLOCK_2 = """
[0m[NR_MAC]   Frame.Slot 384.0
UE RNTI 6b99 CU-UE-ID 1 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -68 (16 meas)
UE 6b99: UL-RI 1, TPMI 0
UE 6b99: dlsch_rounds 50565/1/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9
UE 6b99: MAC:    TX      221879218 RX       47067323 bytes
UE RNTI 8b87 CU-UE-ID 2 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -74 (16 meas)
UE 8b87: dlsch_rounds 45960/0/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9
UE 8b87: MAC:    TX      141479583 RX       44375094 bytes
""".strip()


def test_parse_ue_header():
    """Parses RNTI, CU-UE-ID, RSRP from header line."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    latest = parser.get_latest_ue_stats()
    assert "6b99" in latest
    assert "8b87" in latest

    ue1 = latest["6b99"]
    assert ue1 is not None
    assert ue1.rnti == "6b99"
    assert ue1.cu_ue_id == 1
    assert ue1.rsrp == -62


def test_parse_dlsch_stats():
    """Parses MCS, BLER, dlsch_rounds."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    ue1 = parser.get_latest_ue_stats()["6b99"]
    assert ue1.dl_mcs == 9
    assert ue1.dl_bler == 0.0
    assert ue1.dlsch_rounds == (50560, 1, 0, 0)
    assert ue1.dlsch_errors == 0


def test_parse_mac_bytes():
    """Parses TX/RX byte counts."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    ue1 = parser.get_latest_ue_stats()["6b99"]
    assert ue1.tx_bytes == 221869218
    assert ue1.rx_bytes == 47057323


def test_parse_slice_assignment():
    """Parses 'Active slices for UE' lines."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    assert parser.ue_slice_map["6b99"] == 1
    assert parser.ue_slice_map["8b87"] == 2


def test_slice_stats_aggregation():
    """Aggregates per-UE stats into per-slice stats."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    stats = parser.get_slice_stats()
    assert 1 in stats  # Slice 1
    assert 2 in stats  # Slice 2

    s1 = stats[1]
    assert s1.avg_mcs == 9.0
    assert s1.num_ues == 1


def test_throughput_calculation():
    """Throughput is computed from byte deltas between samples."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)

    # Parse first block at t=1000
    parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0)

    # Parse second block at t=1001 (1 second later)
    parser.parse_lines(SAMPLE_LOG_BLOCK_2.split("\n"), timestamp=1001.0)

    stats = parser.get_slice_stats()
    s1 = stats[1]

    # UE 6b99 TX went from 221869218 to 221879218 = 10000 bytes in 1s
    # DL throughput = 10000 * 8 = 80000 bps = 0.08 Mbps
    assert s1.dl_throughput_bps > 0
    expected_tput = 10000 * 8  # 80000 bps
    assert abs(s1.dl_throughput_bps - expected_tput) < 1000  # within 1 kbps


def test_sliding_window():
    """Window size limits the number of stored samples."""
    parser = GnbLogParser(log_path="/dev/null", window_size=3)

    # Parse the same block 5 times with different timestamps
    for i in range(5):
        parser.parse_lines(SAMPLE_LOG_BLOCK.split("\n"), timestamp=1000.0 + i)

    # Should only have 3 samples (window_size)
    assert len(parser._ue_samples["6b99"]) == 3


def test_empty_log():
    """No crash on empty log input."""
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines([], timestamp=1000.0)

    stats = parser.get_slice_stats()
    assert stats == {}


def test_partial_block():
    """Handles partial blocks (missing MAC bytes line)."""
    partial = """
UE RNTI 6b99 CU-UE-ID 1 in-sync PH 0 dB PCMAX 0 dBm, average RSRP -62 (16 meas)
UE 6b99: dlsch_rounds 50560/1/0/0, dlsch_errors 0, pucch0_DTX 0, BLER 0.00000 MCS (0) 9
""".strip()
    parser = GnbLogParser(log_path="/dev/null", window_size=10)
    parser.parse_lines(partial.split("\n"), timestamp=1000.0)

    # Should not crash, and UE should not have a completed sample
    latest = parser.get_latest_ue_stats()
    assert latest.get("6b99") is None


if __name__ == "__main__":
    test_parse_ue_header()
    test_parse_dlsch_stats()
    test_parse_mac_bytes()
    test_parse_slice_assignment()
    test_slice_stats_aggregation()
    test_throughput_calculation()
    test_sliding_window()
    test_empty_log()
    test_partial_block()
    print("All telemetry tests passed!")
