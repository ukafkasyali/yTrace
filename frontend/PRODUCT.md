# Robot observability frontend
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
React, TypeScript and Vite implement an isolated frontend module. No hosting target is committed. Python ingestion and inference remain independently owned services.

## Users
Robot operators inspecting recorded telemetry; Samet builds the hackathon dashboard. Uğur and Atakan own ingestion; Atakan, Uğur and Ece own model training.

## Product Purpose
Select a recording, examine events across synchronized channels, and ask an assistant for answers linked to visible evidence. The assistant will orchestrate TSLM, CNN, statistics and retrieval tools when services are available.

## Operating Context
EHL Zurich Temporal AI hackathon. The first source is Zenodo 21927431: KUKA LWR4+ torque recordings. No live robot control or validated maintenance recommendations.

## Capabilities and Constraints
The local version uses real measured telemetry and deterministic numerical analysis. Optional API clients and service-backed workspaces are implemented, but no real model or ingestion endpoint has been verified. Publisher markers are not certified physical onset times. The fixture contains a 100 Hz overview and 1 kHz detail only for [4,9) seconds. Replay is recorded playback, not a live robot feed. Queries retain their original interval and playback cursor.

## Brand Commitments
Foxglove is the user's layout inspiration, with compact synchronized signal strips and an assistant. The upper-left area is a dataset/recording explorer; 3D pose replay is deferred. Trace is a provisional frontend title, not an approved team name.

## Evidence on Hand
One real recording fixture, annotations and verified reference calculations; integration contracts in ../docs/FRONTEND_INTEGRATION.md. No model accuracy or production customers may be invented.

## Product Principles
Shared time selection. Traceable claims. Independent team modules. Inspect the data before interpreting it. Preserve raw meaning and expose resolution.
