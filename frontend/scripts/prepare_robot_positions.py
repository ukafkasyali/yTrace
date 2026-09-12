#!/usr/bin/env python3
"""Validate measured KUKA articulation against the recorded Jacobian and export it.

Requires numpy/scipy and tar with zstd support. Does not download source data.
Usage: python3 scripts/prepare_robot_positions.py --archive /path/to/batch-42.tar.zst
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess

import numpy as np
from scipy.io import loadmat
from scipy.spatial.transform import Rotation

RECORDING = "05-28-21-25"
SHA256 = "e98aa696f891d19b38f2c423296113757d55f43214a9abe3ce14c56560e2554a"


def read_member(archive, filename, key):
    data = subprocess.check_output(["tar", "--zstd", "-xOf", str(archive),
                                    f"{RECORDING}/{filename}"])
    return loadmat(io.BytesIO(data))[key]


def jacobian(q, joints):
    """N x 7 angles -> recorded convention: tip XYZ linear, tip ZYX angular."""
    n = len(q)
    rotation = np.broadcast_to(np.eye(3), (n, 3, 3)).copy()
    position = np.zeros((n, 3))
    origins, axes = [], []
    for i, joint in enumerate(joints):
        position = position + np.einsum("nij,j->ni", rotation, joint["originXYZ"])
        origins.append(position.copy())
        axes.append(np.einsum("nij,j->ni", rotation, joint["axis"]))
        rotation = rotation @ Rotation.from_rotvec(q[:, i, None] * joint["axis"]).as_matrix()
    linear = np.stack([np.cross(axis, position-origin) for axis, origin in zip(axes, origins)], axis=2)
    angular = np.stack(axes, axis=2)
    inverse = rotation.transpose(0, 2, 1)
    return np.concatenate([inverse @ linear, (inverse @ angular)[:, ::-1, :]], axis=1)


def summary(errors):
    return {"maxAbsolute": float(np.max(np.abs(errors))), "rms": float(np.sqrt(np.mean(errors**2)))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "public/robot/kuka"
    kinematics = json.loads((output / "kinematics.json").read_text())
    if any(joint["originRPY"] != [0, 0, 0] for joint in kinematics["joints"]):
        raise ValueError("This validator requires zero joint-origin RPY rotations")
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError("Source archive differs from publisher SHA256SUMS")
    position = read_member(args.archive, "JK_PosMsr.mat", "PosMsr")
    recorded_j = read_member(args.archive, "JK_Jcb.mat", "Jcb")
    torque = read_member(args.archive, "JK_MsrExtTrq.mat", "MsrExtTrq")
    assert position.shape == (8, 170001) and recorded_j.shape == (43, 170001)
    assert np.isfinite(position).all() and np.isfinite(recorded_j).all()
    assert np.array_equal(position[0], recorded_j[0]) and np.array_equal(position[0], torque[0])
    assert np.allclose(np.diff(position[0]), .001, atol=1e-9)
    valid = np.any(recorded_j[1:] != 0, axis=0)
    assert np.array_equal(np.flatnonzero(~valid), np.arange(4)), "Unexpected invalid Jacobian samples"
    q = position[1:, valid].T
    actual = recorded_j[1:, valid].T.reshape(-1, 6, 7)
    predicted = jacobian(q, kinematics["joints"])
    errors = predicted - actual
    metrics = summary(errors)
    if metrics["maxAbsolute"] > 1e-5:
        raise ValueError(f"Joint mapping failed Jacobian validation: {metrics}")
    # Negative controls use samples across the complete recording.
    cq, cj = q[::100], actual[::100]
    controls = {
        "treatPositionsAsDegrees": summary(jacobian(np.deg2rad(cq), kinematics["joints"])-cj),
        "reverseJointOrder": summary(jacobian(cq[:, ::-1], kinematics["joints"])-cj),
        "negateAllJointAngles": summary(jacobian(-cq, kinematics["joints"])-cj),
    }
    per_joint = {}
    for i in range(7):
        changed = cq.copy(); changed[:, i] += .25
        per_joint[f"joint_{i+1}"] = summary(jacobian(changed, kinematics["joints"])-cj)
    limits = np.array([j["limitsRadians"] for j in kinematics["joints"]])
    within_limits = bool(np.all((q >= limits[:, 0]) & (q <= limits[:, 1])))
    assert within_limits
    report = {
        "recordingId": RECORDING, "mappingVerified": True,
        "scope": "Recorded joint articulation; global base frame remains uncalibrated",
        "method": "Direct PosMsr radians, sequential joint order and model axes; compare geometric Jacobian in end-effector frame with recorded Jcb",
        "sourceSampleCount": position.shape[1], "validatedSampleCount": len(q),
        "validatedStartSec": float(position[0, valid][0]), "validatedEndSec": float(position[0, -1]),
        "excludedStartupSampleIndices": [0, 1, 2, 3],
        "excludedReason": "Both joint positions and recorded Jacobian are all-zero initialization values",
        "jacobianConvention": "Jcb rows 1..42 reshape C-order to 6x7; first3 rows tip-frame linear XYZ, last3 rows tip-frame angular ZYX",
        "errors": metrics, "linearErrorsMetersPerRadian": summary(errors[:, :3]),
        "angularErrors": summary(errors[:, 3:]), "negativeControls": controls,
        "quarterRadianOffsetControls": per_joint, "allAnglesWithinUrdfLimits": within_limits,
        "timestampsExactlyMatchTorque": True,
        "limits": ["A body Jacobian is invariant to the first joint's angle and global rigid base pose. The first channel uses the same recorded-unit convention as its six verified companions; its absolute zero cannot be independently validated by this test.",
                   "This validates internal kinematic consistency, not experimental camera/world alignment, housing geometry, contact point or sensor calibration.",
                   "No external motion capture or video was used to verify physical pose."],
    }
    indices = np.arange(10, position.shape[1], 10)
    fixture = {
        "recordingId": RECORDING, "angularUnit": "radian", "sampleRateHz": 100,
        "times": np.round(position[0, indices], 3).tolist(),
        "joints": [{"channelId": f"joint_{i+1}", "values": position[i+1, indices].tolist()} for i in range(7)],
        "validation": report,
        "provenance": {"sourceUrl": "https://zenodo.org/records/21927431",
                       "archive": "collision-batch-42.tar.zst", "archiveSha256": digest,
                       "matFile": f"{RECORDING}/JK_PosMsr.mat", "matVariable": "PosMsr",
                       "originalSampleRateHz": 1000, "displayMethod": "Every tenth original sample, no interpolation or angle rounding; first exported sample0.010s",
                       "kinematicsRevision": kinematics["sourceRevision"],
                       "replayRule": "Use latest sample at or before playhead; no pose before first exported sample. Do not read future samples."},
    }
    (output / "positions.json").write_text(json.dumps(fixture, separators=(",", ":"), allow_nan=False)+"\n")
    (output / "position-validation.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"validatedSamples": len(q), "errors": metrics, "negativeControls": controls,
                      "exportedSamples": len(indices), "fixtureBytes": (output/"positions.json").stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
