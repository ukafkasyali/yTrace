# Trace interface

Operate-mode recording workbench, inspired by the supplied Foxglove screenshot. The primary interaction is selecting an event and seeing all plots and the assistant share the same interval.

- Charcoal surfaces: canvas #141719, panel #1b1e21, raised #23282b. Thin separators #343b3f. Text #e6ebeb, secondary #a2adb1.
- Mint #99d5bd marks selection and primary actions; amber #d9ad70 denotes publisher annotations. Channel colors remain stable across views.
- IBM Plex Sans UI with system fallback; IBM Plex Mono/tabular figures for times, IDs and measurements. Fonts are requested from Google Fonts and gracefully fall back when offline. Compact 13px baseline, 14px conversation text and 20px recording title.
- A compact top bar and left navigation rail frame the workspace. Desktop: left recording explorer above assistant, right seven synchronized plots, bottom shared transport. Smaller screens stack surfaces with a section switcher.
- Panels are divided by hairlines, not nested cards. Four-pixel control corners, no gradients, ornamental badges or hero metrics.
- Focus rings, named icon buttons, keyboard event selection, explicit failure and disconnected states, reduced-motion support.
- Signal plots and annotation markers use real source measurements, with 100 Hz overview and 1 kHz detail explicitly distinguished. Local analysis is identified as calculated evidence, never model output. Disconnected ingestion and model controls remain unavailable until an API is configured; no progress is fabricated.
- Replay reveals measurements and annotation marks up to a shared playback cursor. Interval selection and answer evidence preserve context; querying future intervals is rejected. A compact Overview / 3D reference control changes only the upper-left context. The reference follows measured joint positions at the shared replay cursor, with real torque readouts and joint colors matching the signal strips. Body shape and global base orientation are schematic; absent measurements show an explicitly illustrative fixed pose. Camera controls sit below the model; the camera never rotates automatically. Position sample-and-hold does not interpolate future data.

## Investigation refinement

Readable plot labels and adaptive 11–14px UI type keep all seven channels visible on desktop. The recording summary uses less vertical space, leaving the conversation and composer room to breathe. Short suggestion labels name numerical operations directly. Answers open at their question context rather than jumping to the bottom. The zoom control explicitly distinguishes an interval selection from a rolling view. Neutral inline notes distinguish disconnected services without turning every explanation into a colored callout. Scrollbars, caret, slider and focus states share the palette.


## Investigation-first refinement, 13 September 2026

The primary action is Analyze interval. Desktop has a stable investigation on the
left and a visual workbench on the right: Robot (default, with two focused torque
traces), All 7 signals, and Publisher markers. The former collapsed robot/overview
stack and navigation rail are removed. Source metadata and exact model receipts
use explicit details controls; they remain accessible without crowding the answer.
Predictions lead with a short class and optional joint/onset fields, followed by
measured torque. Unsupported fields retain the full response instead of a cleaned-up
claim. Mobile switches Replay / Investigation and uses the same direct visual tabs.

The selected investigation interval is independent of moving replay. Playback cannot
rewrite model input or repeatedly shift the chat's validation controls. User-created
turns may scroll the conversation once; generation updates do not reset reading
position. Robot articulation comes from per-recording measured angles, with mapping
validation scope disclosed. Body and world frame remain schematic.

Comparison is a fourth direct view. It leads with the largest measured change and
a selectable seven-joint table, followed by a shared-scale torque overlay. Reference
editing and methodological details are collapsed when a suggestion is available.
The assistant offers comparison both for the current interval and an answer's saved
window. Reference normality is never implied by missing publisher annotations;
next steps ask the engineer to confirm comparable operating conditions.
