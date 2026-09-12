# Trace interface

Operate-mode recording workbench, inspired by the supplied Foxglove screenshot. The primary interaction is selecting an event and seeing all plots and the assistant share the same interval.

- Charcoal surfaces: canvas #141719, panel #1b1e21, raised #23282b. Thin separators #343b3f. Text #e6ebeb, secondary #a2adb1.
- Mint #99d5bd marks selection and primary actions; amber #d9ad70 denotes publisher annotations. Channel colors remain stable across views.
- IBM Plex Sans UI with system fallback; IBM Plex Mono/tabular figures for times, IDs and measurements. Fonts are requested from Google Fonts and gracefully fall back when offline. Compact 13px baseline, 14px conversation text and 20px recording title.
- A compact top bar and left navigation rail frame the workspace. Desktop: left recording explorer above assistant, right seven synchronized plots, bottom shared transport. Smaller screens stack surfaces with a section switcher.
- Panels are divided by hairlines, not nested cards. Four-pixel control corners, no gradients, ornamental badges or hero metrics.
- Focus rings, named icon buttons, keyboard event selection, explicit failure and disconnected states, reduced-motion support.
- Signal plots and annotation markers use real source measurements, with 100 Hz overview and 1 kHz detail explicitly distinguished. Local analysis is identified as calculated evidence, never model output. Disconnected ingestion and model controls remain unavailable until an API is configured; no progress is fabricated.
- Replay reveals measurements and annotation marks up to a shared playback cursor. Interval selection and answer evidence preserve context; querying future intervals is rejected. Dataset exploration replaces unverified 3D pose animation.
