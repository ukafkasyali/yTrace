# OpenTSLM research notes

Primary sources: [paper](https://arxiv.org/abs/2510.02410),
[official repository](https://github.com/OpenTSLM/OpenTSLM),
[project site](https://www.opentslm.com/), and
[official checkpoints](https://huggingface.co/OpenTSLM/models).

SoftPrompt encodes each univariate series in non-overlapping four-sample patches using a convolution
and six-layer Transformer, projects the resulting 128-dimensional patch embeddings into the LLM
hidden space, and interleaves them with channel descriptions and question tokens. Seven 1,000-sample
channels become about 1,750 time-series tokens. The LLM is frozen except for LoRA; encoder and
projector are trainable. Training applies autoregressive cross-entropy only to rationale/answer tokens.

Flamingo uses a Perceiver resampler and gated cross-attention, giving more stable memory scaling for
long signals. It is not the critical path because current upstream multichannel packing is defective:
[issue #54](https://github.com/OpenTSLM/OpenTSLM/issues/54) and
[unmerged correction #50](https://github.com/OpenTSLM/OpenTSLM/pull/50). Current multi-GPU training
also has an [unmerged fix](https://github.com/OpenTSLM/OpenTSLM/pull/49). Chronos-2 appears in the
paper, but its [implementation remains unmerged](https://github.com/OpenTSLM/OpenTSLM/pull/41).

The primary checkpoint is
[`OpenTSLM/llama-3.2-1b-har-sp`](https://huggingface.co/OpenTSLM/llama-3.2-1b-har-sp), because HAR is
the closest released multichannel sensor/reasoning stage. This is an alignment initialization, not a
zero-shot robotics model. Upstream also has an open
[reproducibility concern](https://github.com/OpenTSLM/OpenTSLM/issues/33), so checkpoint loading and
one-batch behavior are tested before committing an overnight run.

The plot baseline uses
[`Qwen/Qwen3-VL-4B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct), not an unofficial
quantization. Qwen's official repository documents vLLM 0.11+ support. This produces a stronger and
cleaner comparison: a larger VLM seeing rendered plots versus a smaller TSLM consuming numeric data.
