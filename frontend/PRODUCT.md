# Robot observability frontend
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
React, TypeScript and Vite implement an isolated frontend module. No hosting target is committed. Python ingestion and inference remain independently owned services.

## Users
Controls and test engineers preparing a handoff after a recorded contact event; Samet builds the hackathon dashboard. Uğur and Atakan own ingestion; Atakan, Uğur and Ece own model training.

## Product Purpose
Turn a recorded contact interval into a reviewable handoff: a trained OpenTSLM prediction, deterministic measurements, linked raw signals, provenance, and an export for the controls lead or vendor. Retrieve similar raw signal profiles across recordings so recurring evidence can be reviewed or labeled as a cohort.

## Operating Context
EHL Zurich Temporal AI hackathon. The first source is Zenodo 21927431: KUKA LWR4+ torque recordings. No live robot control or validated maintenance recommendations.

## Capabilities and Constraints
The local version uses real measured telemetry and deterministic numerical analysis. The private canary-v4 OpenTSLM endpoint is verified; no CNN inference endpoint or checkpoint is connected. Publisher markers are not certified physical onset times. The fixture contains a 100 Hz overview and 1 kHz detail only for [4,9) seconds. Replay is recorded playback, not a live robot feed. Queries retain their original interval and playback cursor.

## Brand Commitments
Foxglove is the user's layout inspiration, with compact synchronized signal strips and an assistant. The upper-left area switches between the recording overview and an optional 3D reference. Its independently rendered schematic uses numerical KUKA joint geometry and recorded joint articulation at 100 Hz, with measured torque and shared channel selection. Joint mapping is numerically validated against the recorded Jacobian; body shape and global base orientation remain schematic. Missing position data uses a clearly labeled fixed pose. y/trace is the frontend title.

## Evidence on Hand
One real recording fixture, annotations and verified reference calculations; integration contracts in ../docs/FRONTEND_INTEGRATION.md. No model accuracy or production customers may be invented.

## Product Principles
Shared time selection. Traceable claims. Independent team modules. Inspect the data before interpreting it. Preserve raw meaning and expose resolution.
