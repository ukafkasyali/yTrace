"""Load the primary checkpoint and exercise the exact seven-channel input contract."""

from __future__ import annotations

import json

import torch
from opentslm import OpenTSLM


def main() -> None:
    model = OpenTSLM.load_pretrained(
        "OpenTSLM/llama-3.2-1b-har-sp",
        device="cuda",
        enable_lora=True,
    )
    sample = {
        "pre_prompt": "Analyze synchronized robot external torque. ",
        "time_series_text": [f"J{joint} external torque at 1000 Hz. " for joint in range(1, 8)],
        "time_series": torch.zeros((7, 1024), dtype=torch.float32),
        "post_prompt": "Did contact occur? Return JSON. ",
        "answer": 'Evidence: no disturbance.\nAnswer: {"contact":false}' + model.get_eos_token(),
    }
    loss = model.compute_loss([sample])
    output = model.generate([sample], max_new_tokens=32, do_sample=False)[0]
    print(
        json.dumps(
            {
                "loss": float(loss.detach().cpu()),
                "output": output,
                "gpu_allocated_gb": torch.cuda.memory_allocated() / 2**30,
                "gpu_peak_gb": torch.cuda.max_memory_allocated() / 2**30,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
