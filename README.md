# y/trace

**Second place out of 11 teams at the Zurich Temporal AI Challenge and selected for the Munich finals.**

y/trace is a replay workbench for investigating recorded robot contact events. It combines real KUKA telemetry, deterministic signal measurements, recorded robot articulation, and a trained OpenTSLM interpretation in one reviewable incident report.

Built by team **y/agent** during the Aionic Labs × Agentic Systems Lab Temporal AI Challenge.

---

## Why this exists

After a robot stops, an engineer may need to inspect several synchronized torque traces, locate the incident, decide whether contact was accidental or intentional, identify the joints that reacted, and hand the evidence to another engineer.

y/trace turns that manual chart review into one traceable workflow:

~~~text
Replay the recording
  -> select an incident window
  -> run deterministic measurements and OpenTSLM
  -> inspect the exact seven-channel evidence
  -> compare another window
  -> export a Markdown handoff
~~~

The product keeps three evidence sources separate:

- **Publisher annotations** are event markers supplied with the dataset.
- **Measurements** are deterministic calculations over the selected signals.
- **OpenTSLM interpretations** are generated predictions tied to an exact input window.

## What works

| Capability | Status | Implementation |
|---|---|---|
| Recorded incident replay | Working | Real KUKA torque recordings with shared time navigation |
| Robot articulation | Working | Recorded PosMsr joint angles rendered with a schematic Three.js kinematic chain |
| Temporal interpretation | Working | OpenTSLM predicts event semantics, onset, and affected joints from seven numeric series |
| Evidence inspection | Working | Every model request is linked to seven channels and 1,024 contiguous samples |
| Deterministic analysis | Working | Torque range, sampled peaks, variability, and cross-window comparisons |
| Investigation export | Working | Human-readable Markdown plus a machine-readable audit export |
| Held-out evaluation | Reproducible | Signal features, 1D CNN, OpenTSLM, and Qwen3-VL compared on the same recording groups |
| Dataset sourcing | Working | LangGraph scout with Tavily discovery, native-source verification, and human approval |
| Dataset ingestion | Working | Revision-pinned acquisition, safe inspection, semantic mapping, and TimeNet validation |

## Architecture

~~~text
Natural-language dataset brief
            |
       LangGraph scout
            |
  Native-source verification
            |
      Human approval
            |
 Safe acquisition and inspection
            |
      TimeNet / TimeF
            |
      Recorded telemetry
            |
  +---------+-----------+
  |                     |
Measurements        OpenTSLM
  |                     |
  +---- Evidence-linked investigation
                         |
                  Markdown handoff
~~~

| Module | Responsibility |
|---|---|
| [frontend](frontend/README.md) | React workbench, replay, recorded articulation, signal evidence, comparison, and exports |
| [data_sourcing](data_sourcing/README.md) | Evidence-complete dataset discovery, verification, ranking, and approval |
| [data_ingestion](data_ingestion/README.md) | Safe acquisition, structural inspection, semantic mapping, and TimeNet conversion |
| [model_training](model_training/README.md) | Leakage-safe data preparation, baselines, OpenTSLM training, and evaluation |
| [inference](inference/README.md) | Strict model input validation, private checkpoint serving, and release checks |

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Three.js |
| APIs | Python, FastAPI, Pydantic, Uvicorn |
| Agentic sourcing | LangGraph, Tavily, OpenAI Structured Outputs |
| Temporal data | TimeNet, TimeF, NumPy, SciPy, PyArrow |
| Models | OpenTSLM SoftPrompt, Llama 3.2 1B, LoRA, PyTorch |
| Baselines | scikit-learn, 1D CNN, Qwen3-VL |
| Persistence | SQLite checkpoints and content-addressed ingestion artifacts |
| Testing | Vitest, pytest, unittest, Ruff |

## Model and data contract

The primary dataset contains accidental-collision and intentional-contact experiments recorded on a KUKA LWR4+ robot. The published [collision data](https://zenodo.org/records/21927431) and [intentional-contact data](https://zenodo.org/records/21941203) are available under CC BY 4.0.

Each model example contains:

- seven external-joint-torque channels
- 1,024 contiguous samples per channel
- 1 kHz sampling
- a 1.024 second half-open interval
- normalization statistics calculated from training recordings only

Recording folders are split before window generation to prevent the same recording from appearing in both training and evaluation.

The deployed model is OpenTSLM SoftPrompt with a Llama 3.2 1B backbone, HAR warm start, and LoRA adapters. Publisher markers are never passed to the model.

## Held-out evaluation

Every method below uses the same 512 windows from 67 held-out recording groups.

| Method | Semantics macro-F1 | Contact F1 | Median onset error | Strongest-joint accuracy |
|---|---:|---:|---:|---:|
| Signal features | **0.9880** | **0.9908** | 21 ms | **0.8413** |
| 1D CNN | 0.9751 | 0.9777 | **18 ms** | 0.7565 |
| OpenTSLM canary-v4 | 0.8845 | 0.9871 | 54 ms | 0.7528 |
| Qwen3-VL zero-shot plots | 0.6951 | 0.0291 | 42 ms | 0.4244 |

The dedicated feature and CNN baselines are better fixed-task classifiers. OpenTSLM contributes a readable investigation object that combines event semantics, timing, joint attribution, and evidence text. Canary-v4 produced a usable complete answer on 77.93% of the held-out windows, so output reliability remains its clearest limitation.

The [audited comparison](docs/submission/evaluation/report/comparison.md) includes the original predictions, targets, split identities, checksums, and uncertainty estimates.

Reproduce the report without a GPU or model download:

~~~bash
PYTHONPATH=model_training/src python3 -m robot_observability.comparison \
  --source docs/submission/evaluation/source \
  --output /tmp/ytrace-comparison
~~~

## Run locally

Requirements:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- curl

~~~bash
git clone https://github.com/ukafkasyali/ysamet.git
cd ysamet
./scripts/run-local.sh --cached --open
~~~

The cached path starts the sourcing API, ingestion API and worker, fixture inference bridge, and frontend at [http://127.0.0.1:5173](http://127.0.0.1:5173). It uses checked-in KUKA sourcing evidence and consumes no API credits.

For live dataset discovery:

~~~bash
cp data_sourcing/.env.example data_sourcing/.env
# Add Tavily and OpenAI credentials
./scripts/run-local.sh --live --open
~~~

If a healthy OpenTSLM service is already listening on port 8000, the launcher uses it. Otherwise, it starts a fixture bridge so the replay, sourcing, ingestion, and evidence workflow remain testable without model weights.

## Verification

~~~bash
cd frontend && npm test && npm run build
cd ../data_sourcing && .venv/bin/pytest -q
cd ../data_ingestion && PYTHONPATH=src python -m pytest tests -q
cd .. && python3 -m unittest discover -s inference -p 'test_*.py' -v
~~~

The repository also retains the [TimeNet validation receipts](docs/submission/timenet/README.md), [dataset card](model_training/docs/dataset.md), [Qwen baseline contract](docs/submission/QWEN_BASELINE.md), and [checkpoint release procedure](inference/README.md).

## Boundaries

y/trace is retrospective observability software. It does not claim:

- live collision prevention or robot control
- a verified physical root cause
- an exact contact location
- production safety certification
- world-pose reconstruction from torque

The 3D view uses separately recorded joint angles. Its body geometry and global frame are schematic. Similar signal patterns help organize evidence, but they do not prove a shared physical cause.

## Team

Built by **Samet Degirmenci, Baris Can, Atakan Topaloglu, Ugur Kafkasyali, and Ece Akdeniz**.

Team y/agent placed second in the Zurich qualifier and advanced to the Munich finals.
