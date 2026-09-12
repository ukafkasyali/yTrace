# Measured articulation validation

The position-to-model mapping now has numerical evidence. From the existing
`collision-batch-42.tar.zst` archive, `PosMsr` contains seven position rows and a
timestamp row; `Jcb` contains 42 Jacobian rows and the same timestamps. Both also
match the torque timestamps exactly.

Using `PosMsr` values directly as radians and in sequential joint order, we
computed forward kinematics from `kinematics.json`. The resulting geometric
Jacobian, expressed in the end-effector frame, matches the recorded Jacobian
when its linear rows are XYZ and angular rows are ZYX. The flattened recorded
42-vector is reshaped row-major to 6 by 7.

We checked **all 169,997 noninitialization samples** from 0.004 through 170.000
seconds. Maximum absolute matrix-entry error was **1.8247e-7**; aggregate RMS
error was **1.6817e-8**. The report separately records linear and angular errors
because their units differ. Degree interpretation, reversed ordering and
negated-angle controls produced aggregate RMS errors of roughly 0.37–0.41 on
samples distributed across the entire recording. Every measured angle remains
within the model's joint limits. This evidence supports the recorded radians,
axis directions, order and articulation zero configuration for joints 2–7.

## Limits

A body Jacobian is invariant to the first joint angle and to a global rigid
base pose. Thus this check cannot independently establish joint 1's absolute
zero or the experimental world's orientation. Joint 1 uses the same recorded
unit convention as its six verified companions. A quarter-radian offset control
is included for every joint and demonstrates this invariance for joint 1.

Use the label **measured articulation in a schematic frame**. This is not
camera-aligned motion capture, exact housing geometry, a contact-point estimate
or proof of sensor calibration. The original fixed reference pose remains useful
as a fallback; it should not be called the recorded pose.

## Replay fixture

`positions.json` exports every tenth original sample, starting at 0.010 seconds
and ending at 170.000 seconds: 17,000 samples, seven angle channels, 100 Hz. Values
retain source precision. The first four raw samples contain zero initialization
positions and Jacobians and are excluded from verification; starting at raw
index 10 keeps the display series uniformly sampled without those values.

Use the latest sample whose timestamp is at or before the shared replay cursor.
Do not interpolate, extrapolate or inspect a later sample. Before 0.010 seconds,
no measured pose is available. The independently generated robot schematic may
move with these positions while torque plots remain on their own synchronized
recording timeline.

Regenerate from `frontend/` using:

```sh
python3 scripts/prepare_robot_positions.py --archive /tmp/kuka-collision-batch-42.tar.zst
```

The script verifies the archive's publisher SHA-256, all source dimensions and
timestamps, numerical Jacobian agreement and joint limits before writing output.
`position-validation.json` is its machine-readable report. The original torque
fixture is untouched. Source: [Zenodo 21927431](https://zenodo.org/records/21927431),
with the same attribution and source-license considerations as the torque data.
