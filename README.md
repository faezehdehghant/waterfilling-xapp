# Waterfilling xApp for O-RAN 5G Network Slicing

A closed-loop xApp that dynamically allocates Physical Resource Blocks (PRBs) across 5G network slices using a weighted waterfilling algorithm. Built on OpenAirInterface (OAI) with real-time gNB telemetry parsing and atomic RRM policy control.

## Overview

5G network slicing enables multiple virtual networks to share the same physical infrastructure, each with its own resource allocation and QoS guarantees. This xApp implements an O-RAN-style control loop that:

1. **Reads** real-time MAC-layer telemetry from the OAI gNB log (MCS, RSRP, BLER, throughput)
2. **Estimates** per-slice channel quality using 3GPP TS 38.214 lookup tables
3. **Optimizes** PRB allocation via bisection-based weighted waterfilling
4. **Writes** the updated `rrmPolicy.json` atomically to steer the gNB MAC scheduler

The waterfilling approach maximizes weighted proportional fairness: slices with worse channel conditions receive more PRBs to compensate, while per-slice weights allow operators to express priority policies.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Waterfilling xApp                        │
│                                                             │
│  ┌───────────┐   ┌──────────────┐   ┌──────────────┐       │
│  │ Telemetry │──>│   Channel    │──>│ Waterfilling  │       │
│  │  Parser   │   │  Estimator   │   │  Optimizer    │       │
│  │           │   │              │   │              │       │
│  │ tail -f   │   │ MCS → SE →  │   │ max Σ wᵢln  │       │
│  │ gnb.log   │   │ rate/PRB    │   │ (rᵢnᵢ)      │       │
│  └───────────┘   └──────────────┘   └──────┬───────┘       │
│        ▲                                     │              │
│        │                                     ▼              │
│  ┌─────┴─────┐                     ┌──────────────┐        │
│  │   gNB     │                     │   Control    │        │
│  │ MAC Stats │                     │   Writer     │        │
│  │ (log)     │                     │              │        │
│  └───────────┘                     │ rrmPolicy.json│       │
│                                    └──────┬───────┘        │
└───────────────────────────────────────────┼────────────────┘
                                            │
                    ┌───────────────────────┐│
                    │      OAI gNB          │▼
                    │  ┌─────────────────┐  │
                    │  │ MAC Scheduler   │<─┤ reads rrmPolicy.json
                    │  │ (per-slice PRB  │  │ every ~1.28s
                    │  │  allocation)    │  │
                    │  └────────┬────────┘  │
                    │           │           │
                    │     ┌─────┴─────┐     │
                    │     │   Radio   │     │
                    │     │ (RFSim)   │     │
                    │     └─────┬─────┘     │
                    └───────────┼───────────┘
                          ┌─────┴─────┐
                     ┌────┴──┐   ┌───┴────┐
                     │  UE1  │   │  UE2   │
                     │Slice 1│   │Slice 2 │
                     └───────┘   └────────┘
```

## Prerequisites

- **Python 3.10+** (standard library only, no external packages)
- **OpenAirInterface 5G Core** (OAI CN5G) with network slicing support
- **OpenAirInterface gNB** compiled with slicing and `rrmPolicy.json` support
- **2 OAI nrUE instances** in separate network namespaces (RFSim mode)
- **iperf3** for traffic generation during experiments

### Tested Environment

- Ubuntu 22.04 (ARM64) virtual machine
- OAI CN5G v2.1.0 (Docker Compose deployment)
- OAI gNB (develop branch, n78 band, 20 MHz BW, 30 kHz SCS, 106 PRBs)
- 2 UEs via RFSim in network namespaces (`ue1ns`, `ue2ns`)
- 2 network slices: SST=1 (default) and SST=1/SD=2

## Installation

```bash
git clone https://github.com/faezehdehghan/waterfilling-xapp.git
cd waterfilling-xapp
```

No `pip install` needed — the xApp uses only the Python standard library.

## Usage

All commands should be run from the host machine where the gNB is running, with the gNB log redirected to `/tmp/gnb.log`.

### Waterfilling (Default)

Equal-weight waterfilling — adapts allocation to channel conditions:

```bash
python xapp/main.py --mode waterfill --weights 1.0 1.0
```

Prioritize Slice 1 with 2:1 weight ratio:

```bash
python xapp/main.py --mode waterfill --weights 2.0 1.0
```

### Static Allocation

Fixed 50/50 split (baseline):

```bash
python xapp/main.py --mode static --weights 1.0 1.0
```

Fixed 70/30 split:

```bash
python xapp/main.py --mode static --weights 0.7 0.3
```

### Round-Robin

Alternating allocation that cycles which slice gets the majority of PRBs:

```bash
python xapp/main.py --mode round-robin
```

### Custom Control Interval

```bash
python xapp/main.py --mode waterfill --weights 1.0 1.0 --interval 3.0
```

### Run Full Experiment Suite

Runs all scenarios (static, round-robin, waterfilling variants) with iperf3 traffic and saves results:

```bash
python xapp/main.py --experiment
```

## Experiment Results

Results from 20-second iperf3 downlink tests per scenario (OAI RFSim, 2 UEs, 106 PRBs):

| Scenario | Mode | Weights | UE1 (Mbps) | UE2 (Mbps) | Total (Mbps) | Split (S1/S2) | Jain's Fairness |
|---|---|---|---:|---:|---:|---|---:|
| Static 50/50 | static | 1:1 | 25.1 | 13.7 | 38.8 | 50/50 | 0.921 |
| Static 70/30 | static | 7:3 | 24.5 | 10.6 | 35.1 | 70/30 | 0.864 |
| Static 30/70 | static | 3:7 | 15.8 | 21.1 | 36.9 | 30/70 | 0.980 |
| Round-Robin | round-robin | n/a | 22.6 | 16.4 | 39.0 | varies | 0.975 |
| **WF Equal** | **waterfill** | **1:1** | **25.2** | **13.7** | **38.9** | **35/65** | **0.921** |
| WF w=2:1 | waterfill | 2:1 | 21.0 | 11.1 | 32.1 | 67/33 | 0.919 |
| WF w=1:2 | waterfill | 1:2 | 17.7 | 17.0 | 34.7 | 33/67 | **1.000** |

### Key Findings

- **Waterfilling with equal weights** matches the best static allocation (38.9 vs 38.8 Mbps) without requiring manual tuning — the optimizer automatically discovers a 35/65 split based on channel conditions.
- **Waterfilling with 1:2 weights** achieves **perfect fairness** (Jain's index = 1.000) by giving more resources to the weaker-channel UE2.
- **Static 70/30** over-allocates to Slice 1, degrading both total throughput and fairness.
- **Round-robin** provides good fairness (0.975) through temporal diversity but at the cost of per-slice throughput stability.

## File Descriptions

| File | Description |
|---|---|
| `xapp/main.py` | Entry point: CLI argument parsing and control loop |
| `xapp/waterfilling.py` | Bisection-based weighted waterfilling optimizer |
| `xapp/telemetry.py` | gNB log parser (tail -f) for MAC-layer statistics |
| `xapp/channel_estimator.py` | MCS/CQI to spectral efficiency and rate-per-PRB conversion |
| `xapp/control.py` | Atomic `rrmPolicy.json` writer |
| `xapp/baselines.py` | Baseline strategies: static, round-robin, max-CQI |
| `xapp/config.py` | Constants, paths, 3GPP lookup tables |
| `xapp/experiment.py` | Experiment harness with iperf3 orchestration |
| `tests/test_waterfilling.py` | Unit tests for the waterfilling optimizer |
| `tests/test_telemetry.py` | Unit tests for the gNB log parser |
| `results/final_comparison.csv` | Experiment results (all scenarios) |

## Running Tests

```bash
python -m pytest tests/ -v
```

Or run directly:

```bash
python tests/test_waterfilling.py
python tests/test_telemetry.py
```

## License

MIT License. See [LICENSE](LICENSE) for details.
