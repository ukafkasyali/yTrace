"""Project-wide constants and pinned upstream revisions."""

from __future__ import annotations

JOINT_NAMES = tuple(f"J{i}" for i in range(1, 8))
N_JOINTS = len(JOINT_NAMES)

TIMENET_COMMIT = "c39ca32b64ad0c89ea54093dbcb285c1a93eb006"
OPENTSLM_COMMIT = "2968f4b891baab4307f7e9d0043e87677b593a30"

ZENODO_RECORDS = {
    "accidental": 21927431,
    "intentional": 21941203,
}
