# What Qwen3-VL received

The Notion prompt supplied by the user on 13 September 2026 agrees with
`build_prompt()` in `model_training/src/robot_observability/plot_baseline.py`.
The exact UTF-8 prompt saved in [QWEN_BASELINE_PROMPT.txt](QWEN_BASELINE_PROMPT.txt)
hashes to the archived manifest value:

```text
876c1cb65cc4ec7c8c95a399f024322aa3869b7cbdf85262a6b2b4d3ab65d24c
```

Each request contains one PNG image and that fixed text prompt. The image is a
1600×1200 seven-panel plot of the prepared train-robust-normalized telemetry window,
with synchronized relative time in milliseconds, joint names J1–J7 and common
vertical limits −20 to +20. Each window is seven channels × 1,024 samples at 1 kHz.
No numeric sample array is sent as text. The channel-schema paragraphs describe
channel identity, units and preprocessing; they do not contain example-specific
measurements. Normalized plotted values are not raw Nm amplitudes.

The renderer includes no publisher event marker, target onset, event class,
recording ID or target joint. The fixed prompt requests a short evidence sentence,
then `Answer:` and seven JSON fields, without Markdown fences. It instructs free
motion to use null onset/joint/interval fields and an empty affected-joint list.
It does not explicitly spell out the boolean type or the free-motion `false` value
for `contact`. That is a possible prompt weakness, not an established explanation
for the observed null-contact outputs. A revision needs validation-only testing;
do not silently change or reinterpret the historical benchmark.

Archived run: `Qwen/Qwen3-VL-4B-Instruct`, model revision
`ebb281ec70b05090aa6165b016eac8ec08e71b17`, zero-shot, 256 output tokens,
temperature 0, seed 20260912. The audit joins the same 512 windows as OpenTSLM.
See [manifest](evaluation/source/qwen-manifest.json) and
[comparison](evaluation/report/comparison.md).

Describe this as **fine-tuned OpenTSLM on numeric telemetry versus zero-shot
Qwen3-VL on rendered telemetry plots**. Training, model architecture and input
representation differ together. It is an end-to-end baseline, not an isolation of
architecture or a proof that generic AI cannot solve the task. The transparent
signal-feature baseline is also required to interpret the product's added value.
