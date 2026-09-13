"""OpenTSLM-SP inference. Imports/downloads happen only when load() is called."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import statistics
import threading
import time

DEFAULT_CONFIG = Path(__file__).with_name("smoke.config.json")

SUMMARY_KEYS = [
    "contact",
    "event_type",
    "onset_ms",
    "strongest_joint",
    "affected_joints",
    "evidence_start_ms",
    "evidence_end_ms",
]


def question_contract(question: str) -> tuple[str, list[str], str]:
    """Route operator wording onto the question families used during training."""
    text = question.casefold()
    if any(word in text for word in ("summar", "diagnos", "analy", "main change", "what do you notice")):
        return "Diagnose this robot telemetry window.", SUMMARY_KEYS, "summary"
    if "strongest" in text and "joint" in text:
        return "Which joint has the strongest normalized disturbance evidence?", ["strongest_joint"], "strongest_joint"
    if ("affected" in text or "materially" in text) and "joint" in text:
        return "Which joints are materially affected, ranked by disturbance?", ["affected_joints"], "affected_joints"
    if "evidence" in text and any(word in text for word in ("where", "interval", "sustained")):
        return "Where is the strongest temporal evidence?", ["evidence_start_ms", "evidence_end_ms"], "evidence_interval"
    if any(word in text for word in ("intentional", "accidental", "semantics", "classify", "free motion")):
        return "Was the motion free, an intentional contact, or an accidental collision?", ["event_type"], "semantics"
    if any(word in text for word in ("when", "onset", "begin", "timing")):
        return "When did external contact begin?", ["onset_ms"], "onset"
    if "contact" in text:
        return "Did external contact occur?", ["contact"], "contact"
    return "Diagnose this robot telemetry window.", SUMMARY_KEYS, "summary"


def load_robust_normalization(path: str | Path) -> tuple[list[float], list[float], float]:
    """Load immutable training-split statistics, never statistics from a query."""
    payload = json.loads(Path(path).read_text())
    center = payload.get("center_nm")
    scale = payload.get("scale_nm")
    clip = payload.get("clip")
    if (not isinstance(center, list) or not isinstance(scale, list) or len(center) != 7 or len(scale) != 7
            or not isinstance(clip, (int, float)) or clip <= 0
            or any(not isinstance(x, (int, float)) or x <= 0 for x in scale)):
        raise ValueError("Invalid robust normalization metadata")
    return [float(x) for x in center], [float(x) for x in scale], float(clip)


def prepare_sample(
    request: dict,
    series: list[dict],
    normalization: str,
    normalization_path: str | None = None,
    output_format: str = "answer_then_evidence",
) -> dict:
    """Preserve selected samples and apply the declared training-compatible encoding.

    ``train_robust`` uses immutable train-split median/MAD statistics rather
    than statistics from the queried window. No annotation, event label,
    answer, or future sample is accepted here.
    """
    if normalization not in ("zscore_sample", "train_robust", "none"):
        raise ValueError("Unknown normalization")
    if output_format not in ("answer_only", "answer_then_evidence", "rationale_then_answer"):
        raise ValueError("Unknown output format")
    robust = load_robust_normalization(normalization_path) if normalization == "train_robust" and normalization_path else None
    if normalization == "train_robust" and robust is None:
        raise ValueError("train_robust requires normalization_path")
    window = request["window"]
    if not (0 <= window["startSec"] < window["endSec"] <= request["playheadSec"]):
        raise ValueError("Invalid historical interval")
    if [s["channelId"] for s in series] != window["channelIds"]:
        raise ValueError("Channel order does not match query")
    canonical_ids = [f"joint_{i}" for i in range(1, 8)]
    if window["channelIds"] != canonical_ids or len(series) != 7:
        raise ValueError("OpenTSLM requires all seven joints in canonical order")
    if abs(window["endSec"] - window["startSec"] - 1.024) > 1e-8:
        raise ValueError("OpenTSLM requires a 1.024-second window")
    canonical_question, schema_keys, intent = question_contract(request["question"])
    descriptions, values = [], []
    reference_times = series[0]["timeSec"]
    for channel in series:
        times, raw = channel["timeSec"], channel["values"]
        if len(raw) != 1024 or len(raw) != len(times) or times != reference_times:
            raise ValueError("Input channels must be aligned and contain exactly 1024 raw samples")
        if any(not math.isfinite(v) for v in raw):
            raise ValueError("Input contains missing/nonfinite values")
        if any(not math.isfinite(t) or not window["startSec"] <= t < window["endSec"]
               or (i and t <= times[i - 1]) for i, t in enumerate(times)):
            raise ValueError("Input extends outside selected half-open interval")
        if any(abs(t - (window["startSec"] + i / 1000)) > 1e-8 for i, t in enumerate(times)):
            raise ValueError("Input must contain contiguous raw 1 kHz samples aligned to the window")
        mean, std = statistics.mean(raw), statistics.stdev(raw)
        if normalization == "zscore_sample":
            encoded = [(v - mean) / (std + 1e-8) for v in raw]
        elif normalization == "train_robust":
            center, scale, clip = robust
            try:
                joint_index = int(channel["channelId"].removeprefix("joint_")) - 1
                joint_center, joint_scale = center[joint_index], scale[joint_index]
            except (ValueError, IndexError):
                raise ValueError(f"Unknown joint identity: {channel['channelId']}") from None
            encoded = [max(-clip, min(clip, (v - joint_center) / joint_scale)) for v in raw]
        else:
            encoded = list(raw)
        values.append(encoded)
        joint_name = f"J{int(channel['channelId'].removeprefix('joint_'))}"
        descriptions.append(
            f"{joint_name} external joint torque in Nm, sampled at 1000 Hz over 1.024 seconds. "
            "The numeric values are normalized with train-only robust statistics."
        )
    schema = json.dumps(schema_keys, separators=(",", ":"))
    if output_format == "rationale_then_answer":
        response_contract = (
            "First write `Rationale:` as one natural paragraph grounded in temporal and joint "
            "patterns. Do not use headings inside it or name the interaction class before the "
            f"final line. End with `Answer:` and one closed compact JSON object containing only {schema}. "
            "Use JSON types exactly: booleans are true/false, numbers are unquoted, arrays are "
            "arrays, and missing values are null. Never quote a boolean, number, or null."
        )
    else:
        response_contract = (
            f"Respond with `Answer:` and valid compact JSON containing only {schema}."
            + (" Then write one short `Evidence:` sentence." if output_format == "answer_then_evidence" else "")
        )
    return {
        "pre_prompt": (
            "You are analyzing synchronized KUKA LWR4+ external-joint-torque telemetry. "
            "Use the numeric time series as primary evidence. "
        ),
        "time_series_text": descriptions,
        "time_series": values,
        "post_prompt": (
            f"\nQuestion: {canonical_question}\n"
            f"{response_contract}"
        ),
        # Flamingo's training collator expects this field; never put targets here.
        "answer": "",
        "intent": intent,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Runtime:
    def __init__(self, config_path: str | Path | None = None):
        path = Path(config_path or os.environ.get("TRACE_MODEL_CONFIG", DEFAULT_CONFIG))
        self.config = json.loads(path.read_text())
        self.ready = False
        self.error = ""
        self.model_id = self.config.get("checkpoint_repo") or self.config.get("model_id", "team-opentslm")
        self.revision = "not-loaded"
        self.model = None
        self.last_trace = None

    def load(self):
        try:
            self._load()
            self.ready, self.error = True, ""
        except Exception:
            logging.exception("OpenTSLM model initialization failed")
            self.ready = False
            self.error = "Model initialization failed; inspect the inference server log"

    def _load(self):
        import torch
        from huggingface_hub import hf_hub_download
        from opentslm.model.llm.OpenTSLMSP import OpenTSLMSP

        cfg = self.config
        if cfg.get("architecture") != "sp":
            raise ValueError("This initial runtime supports SP only. Flamingo needs a checkpoint-specific loader review.")
        if cfg.get("normalization") not in ("zscore_sample", "train_robust", "none"):
            raise ValueError("Set normalization explicitly to match training")
        device = os.environ.get("TRACE_DEVICE", "cuda")
        if device not in ("cpu", "cuda"):
            raise ValueError("Use CUDA or CPU; upstream pretrained checkpoints do not support MPS")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; check nvidia-smi and CUDA-enabled PyTorch")
        checkpoint_path = cfg.get("checkpoint_path")
        if checkpoint_path:
            path = Path(checkpoint_path).expanduser().resolve(strict=True)
        else:
            path = Path(hf_hub_download(
                repo_id=cfg["checkpoint_repo"],
                filename=cfg.get("checkpoint_filename", "model_checkpoint.pt"),
                revision=cfg.get("checkpoint_revision", "main"),
            ))
        digest = file_sha256(path)
        expected = cfg.get("checkpoint_sha256")
        if expected and expected != digest:
            raise ValueError("Checkpoint checksum mismatch")
        # Read tensor state only; do not use upstream's unrestricted pickle loader.
        state = torch.load(path, map_location="cpu", weights_only=True)
        if "encoder_state" not in state or "projector_state" not in state:
            raise ValueError("Not an OpenTSLM-SP encoder/projector checkpoint")
        model = OpenTSLMSP(llm_id=cfg["base_model"], device=device)
        lora = cfg.get("lora")
        if bool(state.get("lora_enabled", False)) != bool(lora):
            raise ValueError("LoRA configuration must exactly match the checkpoint")
        if lora:
            model.enable_lora(**lora)
        model.encoder.load_state_dict(state["encoder_state"], strict=True)
        model.projector.load_state_dict(state["projector_state"], strict=True)
        model.load_lora_state_from_checkpoint(state, allow_missing=False)
        model.eval()
        config_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
        backbone_revision = getattr(model.llm.config, "_commit_hash", None) or "unknown"
        self.revision = f"checkpoint-sha256:{digest}; config:{config_hash[:12]}; backbone:{backbone_revision}"
        self.model = model
        logging.info("Loaded %s %s", self.model_id, self.revision)

    def generate(self, request: dict, series: list[dict], cancelled: threading.Event) -> str:
        if not self.ready or self.model is None:
            raise RuntimeError("Model unavailable")
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList
        from opentslm.time_series_datasets.util import extend_time_series_to_match_patch_size_and_aggregate

        class CancelOrTimeout(StoppingCriteria):
            def __init__(self, deadline):
                self.deadline = deadline
                self.expired = False

            def __call__(self, input_ids, scores, **kwargs):
                self.expired = time.monotonic() >= self.deadline
                return cancelled.is_set() or self.expired

        if cancelled.is_set():
            raise InterruptedError("Query cancelled")
        sample = prepare_sample(
            request,
            series,
            self.config["normalization"],
            self.config.get("normalization_path"),
            self.config.get("output_format", "answer_then_evidence"),
        )
        trace = {
            "model": self.model_id, "revision": self.revision,
            "window": request["window"], "playheadSec": request["playheadSec"],
            "samplesPerChannel": len(series[0]["values"]),
            "inputSha256": hashlib.sha256(json.dumps(series, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "normalization": self.config["normalization"],
            "padding": "zero to multiple of 4 after normalization; no resampling",
        }
        batch = extend_time_series_to_match_patch_size_and_aggregate([sample], patch_size=4)
        stop = CancelOrTimeout(time.monotonic() + 120)
        began = time.monotonic()
        with torch.inference_mode():
            outputs = self.model.generate(
                batch, max_new_tokens=min(256, max(1, int(self.config.get("max_new_tokens", 128)))),
                do_sample=False, stopping_criteria=StoppingCriteriaList([stop]),
            )
        if cancelled.is_set():
            raise InterruptedError("Query cancelled")
        if stop.expired:
            raise TimeoutError("Generation exceeded 120 seconds; partial answer discarded")
        if len(outputs) != 1 or not isinstance(outputs[0], str) or not outputs[0].strip():
            raise RuntimeError("Model returned an empty or invalid answer")
        trace["latencyMs"] = round((time.monotonic() - began) * 1000)
        self.last_trace = trace
        logging.info("Inference trace %s", json.dumps(trace, sort_keys=True))
        return outputs[0].strip()
