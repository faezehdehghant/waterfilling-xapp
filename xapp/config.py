"""Waterfilling xApp configuration: constants, paths, and 3GPP lookup tables."""

# ── System Parameters ──────────────────────────────────────────────────────────
TOTAL_PRBS = 106          # gNB resource grid width (band 78, 20 MHz)
NUM_SLICES = 2            # Active slices (Slice 1 = default, Slice 2 = SD=2)
SCS_KHZ = 30              # Sub-carrier spacing
SLOT_DURATION_MS = 0.5    # 1 ms / 2 slots per subframe at 30 kHz SCS
SUBCARRIERS_PER_PRB = 12
SYMBOLS_PER_SLOT = 14
OVERHEAD_FACTOR = 0.1     # ~10% overhead (DMRS, control, etc.)

# ── Paths ──────────────────────────────────────────────────────────────────────
GNB_LOG_PATH = "/tmp/gnb.log"
RRM_POLICY_PATH = "/home/faezeh/ORANSlice/oai_ran/rrmPolicy.json"

# ── Control Loop ───────────────────────────────────────────────────────────────
CONTROL_INTERVAL_S = 2.0   # Must be > 1.28s gNB policy reload period
TELEMETRY_WINDOW = 10      # Number of stat samples to average over
MIN_PRB_PER_SLICE = 5      # Minimum PRBs any slice can receive
DEMAND_SATURATED_THRESHOLD = 0.90  # Slice uses >90% allocated → demand-limited
DEMAND_LOW_THRESHOLD = 0.50        # Slice uses <50% allocated → low demand

# ── Slice Definitions ─────────────────────────────────────────────────────────
# These match the gNB's NSSAI configuration.
# Slice 0 (index) = SST=1, SD=None  → NSSAI 1.ffffff  (UE1, RNTI 6b99)
# Slice 1 (index) = SST=1, SD=2     → NSSAI 1.000002  (UE2, RNTI 8b87)
SLICES = [
    {"sst": 1, "sd": None},   # Slice 1: default
    {"sst": 1, "sd": 2},      # Slice 2: SD=0x000002
]

# ── 3GPP TS 38.214 Table 5.2.2.1-2: CQI → MCS Index (4-bit CQI) ──────────
# CQI index → approximate MCS index for 256QAM table
CQI_TO_MCS = {
    0:  0,   # out of range
    1:  0,
    2:  0,
    3:  2,
    4:  4,
    5:  6,
    6:  8,
    7:  11,
    8:  13,
    9:  15,
    10: 18,
    11: 20,
    12: 22,
    13: 24,
    14: 26,
    15: 27,
}

# ── 3GPP TS 38.214 Table 5.1.3.1-2: MCS → Spectral Efficiency (256QAM) ───
# MCS index → modulation order (Qm), target code rate (R x 1024), SE (bits/RE)
# SE = Qm * R / 1024
MCS_TABLE_256QAM = {
    0:  (2, 120,  0.2344),
    1:  (2, 193,  0.3770),
    2:  (2, 308,  0.6016),
    3:  (2, 449,  0.8770),
    4:  (2, 602,  1.1758),
    5:  (4, 378,  1.4766),
    6:  (4, 434,  1.6953),
    7:  (4, 490,  1.9141),
    8:  (4, 553,  2.1602),
    9:  (4, 616,  2.4063),
    10: (4, 658,  2.5703),
    11: (6, 466,  2.7305),
    12: (6, 517,  3.0293),
    13: (6, 567,  3.3223),
    14: (6, 616,  3.6094),
    15: (6, 666,  3.9023),
    16: (6, 719,  4.2129),
    17: (6, 772,  4.5234),
    18: (6, 822,  4.8164),
    19: (6, 873,  5.1152),
    20: (8, 682.5, 5.3320),
    21: (8, 711,  5.5547),
    22: (8, 754,  5.8906),
    23: (8, 797,  6.2266),
    24: (8, 841,  6.5703),
    25: (8, 885,  6.9141),
    26: (8, 916.5, 7.1602),
    27: (8, 948,  7.4063),
}

# ── Experiment Defaults ────────────────────────────────────────────────────────
EXPERIMENT_DURATION_S = 60
EXPERIMENT_REPETITIONS = 3
IPERF_SERVER_IP = "192.168.70.135"  # ext-dn container
UE1_NAMESPACE = "ue1ns"
UE2_NAMESPACE = "ue2ns"
