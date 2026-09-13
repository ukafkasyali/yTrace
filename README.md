# Trace

Trace is a replay-only robot observability workbench for investigating recorded contact incidents. It combines real KUKA telemetry, deterministic measurements, and a trained OpenTSLM interpretation in one reviewable handoff.

An agentic data pipeline can search for open telemetry, verify native source evidence, pin an approved revision, acquire selected assets, inspect their structure, and convert compatible records into TimeNet. Trace then lets an engineer replay an event, inspect the responsible signals, compare a reference window, and export the investigation as Markdown.

Built for the Aionic Labs × Agentic Systems Lab Temporal AI Challenge in Zurich.

## What Trace demonstrates

```text
Find and verify open telemetry
        ↓
Approve an immutable source revision
        ↓
Acquire, inspect, and convert it to TimeNet
        ↓
Replay a recorded incident
        ↓
Run measurements + OpenTSLM
        ↓
Inspect linked signal evidence
        ↓
Export a reviewable incident report
```

The product keeps three evidence sources separate:

- **Publisher annotations** mark events supplied with the dataset.
- **Measurements** are deterministic calculations over the selected telemetry.
- **OpenTSLM interpretations** are generated predictions linked to their exact input window.

Trace does not claim live collision prevention, verified root cause, exact contact location, or reconstructed world pose.

## Run the complete local demo

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js, npm, and curl.

From the repository root:

```bash
./scripts/run-local.sh --cached --open
```

This starts the dataset scout, ingestion API and worker, fixture inference bridge, and frontend at `http://127.0.0.1:5173`. Cached mode uses the checked-in KUKA sourcing evidence and consumes no API credits.

For live agentic discovery, copy `data_sourcing/.env.example` to `data_sourcing/.env`, configure Tavily and OpenAI, then run:

```bash
./scripts/run-local.sh --live --open
```

The launcher reuses a healthy inference service already listening on port 8000. Without one, it starts the fixture bridge so the rest of the product remains testable. The trained checkpoint stays on the private Nebius VM and is never committed to Git.

## Two-minute demo path

1. Open **Data source** and show how a brief becomes verified requirements and an approved source revision.
2. Return to the accidental-collision recording and replay into the publisher marker.
3. Analyze the fixed `[5.787, 6.811)` second raw window.
4. Reveal the OpenTSLM event prediction beside deterministic torque measurements.
5. Open the exact seven-channel signal evidence and export the Markdown investigation report.
6. Open **Evaluation** briefly and state the result honestly: dedicated baselines classify better, while OpenTSLM produces a readable evidence-linked investigation object.

The [full demo script](docs/submission/DEMO.md) includes timing, fallback behavior, and jury questions.

## Architecture

| Module | Responsibility |
|---|---|
| [`frontend/`](frontend/README.md) | React replay workbench, recorded articulation, evidence navigation, comparison, and report export |
| [`data_sourcing/`](data_sourcing/README.md) | LangGraph dataset scout with Tavily discovery, native-source verification, approval, and durable manifests |
| [`data_ingestion/`](data_ingestion/README.md) | Safe acquisition, structural inspection, semantic mapping, TimeNet conversion, and validation receipts |
| [`model_training/`](model_training/README.md) | Leakage-safe window preparation, baselines, OpenTSLM training, and evaluation |
| [`inference/`](inference/README.md) | Private checkpoint service, strict 7 × 1,024 input contract, release validation, and smoke tests |
| [`docs/submission/`](docs/submission/README.md) | Submission evidence, demo material, benchmark artifacts, limitations, and checkpoint handoff |

Local ports are fixed so the frontend can proxy every service:

| Port | Service |
|---|---|
| `5173` | Integrated frontend launcher |
| `5174` | Optional manual frontend session |
| `8000` | OpenTSLM inference bridge |
| `8001` | Dataset sourcing API |
| `8002` | Dataset ingestion API |
| `8003` | Optional isolated rationale-v6 inference bridge |

## Data and model contract

The primary open dataset contains accidental-collision and intentional-contact recordings from a KUKA LWR4+ robot. The published [collision recordings](https://zenodo.org/records/21927431) and [intentional-contact recordings](https://zenodo.org/records/21941203) are available under CC BY 4.0. Each model example contains seven synchronized external-joint-torque channels sampled at 1 kHz for 1.024 seconds. Recording folders are split before window generation to prevent leakage across train, validation, and test.

The deployed model is OpenTSLM SoftPrompt with a Llama 3.2 1B backbone and HAR warm start. Every request must contain the seven canonical channels and exactly 1,024 contiguous raw samples. Publisher markers are never passed to the model.

The bundled frontend keeps a 100 Hz overview of the complete demo recording and raw 1 kHz samples only for `[4, 9)` seconds. Additional service-backed examples include raw excerpts for collision, intentional-contact, and annotation-free motion.

See the [dataset card](model_training/docs/dataset.md), [mapping decisions](HUMAN_MAPPING_DECISIONS.md), and [frontend integration contract](docs/FRONTEND_INTEGRATION.md).

## Held-out evaluation

All reported methods use 512 windows from 67 held-out recording groups with seed `20260912`.

| Method | Semantics macro-F1 | Median onset error | Strongest-joint accuracy | Usable complete answer |
|---|---:|---:|---:|---:|
| Signal features | **0.9880** | 21 ms | **0.8413** | 97.07% |
| 1D CNN | 0.9751 | **18 ms** | 0.7565 | Not available |
| OpenTSLM canary-v4 | 0.8845 | 54 ms | 0.7528 | 77.93% |
| Qwen3-VL zero-shot plots | 0.6951 | 42 ms | 0.4244 | 0.78% |

OpenTSLM does not beat the dedicated feature or CNN baselines on fixed classification. Its contribution is a single readable output containing event semantics, timing, affected joints, and evidence text. Its main measured weakness is structured-output reliability, especially on free-motion windows.

The [audited comparison](docs/submission/evaluation/report/comparison.md) includes the exact predictions, targets, checksums, uncertainty estimates, and reproduction command. The Qwen baseline receives a rendered seven-panel plot, while OpenTSLM receives numeric series, so representation and training are both confounded.

Recompute the comparison without a GPU or model download:

```bash
PYTHONPATH=model_training/src python3 -m robot_observability.comparison \
  --source docs/submission/evaluation/source \
  --output /tmp/trace-comparison
```

## Verification

```bash
cd frontend && npm test && npm run build
cd ../data_sourcing && .venv/bin/pytest -q
cd ../data_ingestion && PYTHONPATH=src python -m pytest tests -q
cd .. && python3 -m unittest discover -s inference -p 'test_*.py' -v
```

The TimeNet evidence pack contains fresh build, load, and raw-array validation receipts for one original recording from each published KUKA part. The browser export and live canary path were also smoke-tested. These checks establish the software path, not production safety or model generalization.

## Submission package

- [Submission index](docs/submission/README.md)
- [Two-minute demo](docs/submission/DEMO.md)
- [UI walkthrough](docs/submission/UI_WALKTHROUGH.md)
- [Audited evaluation](docs/submission/evaluation/report/comparison.md)
- [TimeNet verification](docs/submission/timenet/README.md)
- [Checkpoint handoff](docs/submission/CHECKPOINT_HANDOFF.md)
- [Engineer pilot protocol](docs/submission/PILOT.md)
- [Final verification](docs/submission/VERIFICATION.md)

## Current limitations

- The immediate user benefit, faster incident reporting, is still a hypothesis until the prepared pilot is run.
- The dataset covers one robot platform and contains experimental confounds between event classes and interaction tools.
- OpenTSLM produces a usable complete answer on 77.93% of held-out windows.
- The 3D body geometry and world frame are schematic. Recorded joint angles provide articulation context only.
- Similar signal profiles do not prove a shared physical cause.
- The checkpoint remains private and must be delivered through the challenge's approved submission channel.

All claims, metrics, and limitations in this README point to versioned repository artifacts. No API token, model weight, private dataset, or generated success result belongs in Git.
