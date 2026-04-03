"""Atomic rrmPolicy.json writer for gNB slice control.

Writes the RRM policy file atomically (temp file + rename) to avoid
partial reads by the gNB, which reloads the file every ~1.28 seconds.
"""

import json
import logging
import os
import tempfile
import time

from config import RRM_POLICY_PATH, SLICES

logger = logging.getLogger(__name__)


def build_rrm_policy(ratios: list[int], dedicated: int = 5) -> dict:
    """Build rrmPolicy.json content from per-slice percentage ratios.

    Args:
        ratios: Percentage allocation per slice (matching SLICES order).
        dedicated: Dedicated ratio for each slice (default 5).

    Returns:
        Dict matching the gNB's expected rrmPolicy.json format.

    The policy includes three entries matching the gNB config:
      - SST=1 (default slice, covers UE1)
      - SST=1, SD=1 (unused but present in gNB config)
      - SST=1, SD=2 (slice 2, covers UE2)
    """
    # Map our 2 logical slices to the 3 policy entries the gNB expects.
    # Entry 0: SST=1, no SD → Slice 1 (our slice index 0)
    # Entry 1: SST=1, SD=1 → Not actively used, give minimal allocation
    # Entry 2: SST=1, SD=2 → Slice 2 (our slice index 1)
    entries = []

    # Slice 1: SST=1, default
    entries.append({
        "sst": 1,
        "dedicated_ratio": dedicated,
        "min_ratio": ratios[0],
        "max_ratio": ratios[0],
    })

    # Slice 1 SD=1: present in gNB config but no UEs assigned
    # Give it a small allocation so it doesn't interfere
    entries.append({
        "sst": 1,
        "sd": 1,
        "dedicated_ratio": dedicated,
        "min_ratio": 1,
        "max_ratio": 5,
    })

    # Slice 2: SST=1, SD=2
    entries.append({
        "sst": 1,
        "sd": 2,
        "dedicated_ratio": dedicated,
        "min_ratio": ratios[1],
        "max_ratio": ratios[1],
    })

    return {"rrmPolicyRatio": entries}


def write_rrm_policy(ratios: list[int], path: str = RRM_POLICY_PATH):
    """Atomically write rrmPolicy.json with the given slice ratios.

    Uses a temporary file in the same directory + os.rename() for atomicity.

    Args:
        ratios: Percentage allocation per slice [slice1_pct, slice2_pct].
        path: Path to rrmPolicy.json.
    """
    policy = build_rrm_policy(ratios)
    policy_json = json.dumps(policy, indent="\t")

    # Write to temp file in the same directory, then atomic rename
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(policy_json + "\n")
        os.rename(tmp_path, path)
        logger.info(
            "[%s] Policy updated: slice1=%d%%, slice2=%d%%",
            time.strftime("%H:%M:%S"),
            ratios[0],
            ratios[1],
        )
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_current_policy(path: str = RRM_POLICY_PATH) -> dict | None:
    """Read and parse the current rrmPolicy.json."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not read policy at %s: %s", path, e)
        return None
