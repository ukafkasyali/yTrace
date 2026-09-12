# KUKA LWR 4+ kinematic reference

`kinematics.json` records numerical joint origins, axes and limits inspected in
[Jan Kaniuka's ROS 2 model](https://github.com/jkaniuka/kuka_lwr4plus_urdf), revision
`d080ec1f0076d6e76c3fcddcd547b54db81c3df4`. The interface renders its own geometric
links and joints from these facts. No upstream CAD meshes, robot artwork or URDF
source files are distributed here. The schematic is not exact KUKA housing geometry.

## Transform convention

Use a Z-up frame. For joint i, translate by its `originXYZ`, apply its
`originRPY`, then rotate about its declared `axis` by the joint angle. The next
joint is a child of that transformed frame. Connect adjacent joint frames with
independently created cylinders or other simple geometry; give their visual
radii no engineering or collision meaning.

Joint origins along local Z are `[0.11,0.2005,0.20,0.20,0.20,0.19,0.078]` meters.
Joint axes are `[+Z,-Y,+Z,+Y,+Z,-Y,+Z]`; origin RPY values are all zero. Limits
alternate +/-170 and +/-120 degrees. At all-zero angles the frame chain lies
along Z, with the final frame 1.1785 meters above the base. JSON angles are radians.

## Pose and signal meaning

`positions.json` contains recorded articulation at 100 Hz for recording
`05-28-21-25`, from 0.010 to 170.000 seconds. Values retain source precision.
The source `PosMsr` values, interpreted as radians in sequential joint order,
reproduce the independently recorded Jacobian across all 169,997 nonstartup
1 kHz samples; maximum absolute error is 1.82e-7. See
[POSITION_VALIDATION.md](POSITION_VALIDATION.md) and the reproducible exporter
at `frontend/scripts/prepare_robot_positions.py`.

The display uses the latest position sample at or before the shared replay cursor.
Global base orientation and joint 1's absolute zero are not calibrated by this
body-Jacobian check. Describe the result as measured articulation in a schematic
frame, not an exact world-space reconstruction or collision location.

The reference pose `[0,0.55,0,1.1,0,0.55,0]` is only a labeled fallback where
positions are missing, including before the first valid exported sample.
Torque remains separate: it does not establish a joint angle or contact location.

## Why schematic geometry

The linked ROS 2 package declares BSD without a full license; the parent model
package declares GPLv2 while that repository's root has an Unlicense. The bounded
asset review did not establish clear mesh redistribution terms. Candidate meshes
were kept outside the project for inspection and are not part of the frontend.
This numerical reference and our schematic avoid treating those CAD assets as
independently verified BSD artwork.
