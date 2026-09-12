# Measured demo fixture

`kuka-demo.json` contains real signed external joint torque from recording
`05-28-21-25` in `collision-batch-42.tar.zst`, from
[Raw Torque Data from Collision Experiments on a KUKA Robot, Part I](https://zenodo.org/records/21927431).
The original recording has 170,001 samples at 1 kHz across seven joints and 35
publisher event markers. It is a single inspected development sample, not an
evaluation dataset or a trained-model result.

The overview selects every tenth sample (100 Hz). It is a display preview and
can omit brief extrema; it must not be presented as full-resolution analysis.
The detail section preserves 1 kHz samples for the half-open interval `[4,9)`.
Exported signals are rounded to six decimals. Evidence metrics are calculated
from original-precision samples before rounding, using half-open intervals.
RMS variability is measured about each interval's own median.

`JK_moments` integers are displayed divided by 1,000. The publisher scripts do
not conclusively distinguish millisecond values from MATLAB one-based sample
indices, leaving a possible 1 ms convention difference. More importantly,
these annotations are not independently validated physical collision onsets.
The first visible torque changes may precede a marker. No contact-free labels,
physical cause claims, robot poses, or model predictions are inferred here.

Regenerate from `frontend/` using:

```sh
python3 scripts/prepare_demo_data.py
# Or reuse a local downloaded archive:
python3 scripts/prepare_demo_data.py --archive /path/to/collision-batch-42.tar.zst
```

Requires Python with NumPy and SciPy, curl, and a tar implementation with zstd
support. Only two MAT members are read into memory; nothing is extracted from
the archive to filesystem paths. Source SHA-256 is verified against the
publisher's `SHA256SUMS` and recorded in the JSON.

Keep original attribution. The source's license metadata and README have been
reported as inconsistent; resolve their terms before publishing this derived
fixture or redistributing the raw dataset. This fixture is for the local
hackathon prototype.
