import {
  BarChart,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Code,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  useCanvasAction,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type MethodKey = "features" | "cnn" | "opentslm" | "direct";

const methodOrder: MethodKey[] = ["features", "cnn", "opentslm", "direct"];

const methods: Record<
  MethodKey,
  {
    label: string;
    role: string;
    input: string;
    implementation: string[];
    learned: string;
    why: string[];
    limitation: string;
  }
> = {
  features: {
    label: "Signal features",
    role: "Strongest overall baseline",
    input: "Seven normalized torque channels, 1,024 samples each",
    implementation: [
      "For each joint, compute five summaries: top-5%-mean absolute residual, peak, RMS, derivative RMS, and impulsiveness. This produces 35 features.",
      "Standardize those features and fit a class-balanced logistic regression for free, intentional, and accidental motion.",
      "Fit a separate train-only contact threshold: the disturbance must remain above threshold for 10 consecutive 1 ms samples.",
      "Estimate onset from the first sustained crossing after 150 ms; derive strongest/affected joints with calibrated train-free thresholds.",
    ],
    learned: "Logistic-regression weights and a scalar contact threshold. The feature definitions, onset rule, and joint-evidence rule are handcrafted.",
    why: [
      "The dataset is highly compatible with amplitude, impulse, and motion statistics, so the three classes are close to linearly separable after feature engineering.",
      "The joint targets are deterministic pseudo-labels built from nearly the same normalized disturbance calculation. This gives the method a strong task-specific advantage.",
      "Its median onset error is good, but threshold crossings sometimes occur far from the manual marker, producing a 188.6 ms P90 tail.",
    ],
    limitation: "This is transparent and useful, but its joint-attribution strength should not be mistaken for independent physical localization evidence.",
  },
  cnn: {
    label: "1D CNN",
    role: "Strongest learned baseline",
    input: "Seven normalized torque channels, 1,024 samples each",
    implementation: [
      "Three Conv1d blocks use 32/64/128 channels with kernels 9/7/5, BatchNorm, GELU, and 2x max pooling. Time is reduced from 1,024 samples to 128 bins.",
      "A shared temporal encoder feeds four heads: event semantics, onset-bin classification, strongest joint, and seven affected-joint logits.",
      "The weighted multi-task loss is 1.0 semantics + 0.5 onset + 0.25 strongest joint + 0.25 affected joints. Auxiliary losses apply only to contact windows.",
      "Training uses class weighting, AdamW, cosine decay, bfloat16, gradient clipping, early stopping, and validation macro-F1 with onset MAE as tie-breaker.",
    ],
    learned: "60,082 trainable parameters; trained on 29,393 windows. Epoch 23 was selected on validation and evaluated once on the locked test subset.",
    why: [
      "It learns the discriminative waveform shapes directly and almost matches the engineered classifier, but the handcrafted features already encode exactly the statistics this dataset rewards.",
      "The temporal onset head gives the best typical localization at 18 ms median error. Its 128 bins correspond to roughly 8 ms resolution.",
      "Onset uses ordinary cross-entropy, so a near miss and a far-away bin are penalized equally. Occasional wrong argmax bins create the 274 ms P90 tail.",
      "Global mean pooling and channel mixing weaken explicit joint identity, helping explain 75.6% strongest-joint accuracy versus 84.1% for the calibrated feature rule.",
    ],
    limitation: "One seed and one compact architecture are reported; there are no session-clustered confidence intervals or ablations yet.",
  },
  opentslm: {
    label: "OpenTSLM",
    role: "Fine-tuned numeric generative model",
    input: "Numeric torque series plus textual per-joint statistics and a natural-language question",
    implementation: [
      "OpenTSLM SoftPrompt patches each univariate series in groups of four samples, encodes the patches, projects them into the language-model hidden space, and interleaves them with text tokens.",
      "The run starts from the Llama 3.2 1B HAR SoftPrompt checkpoint. The time-series encoder and projector are trained; the language model is frozen except for rank-16 LoRA adapters.",
      "Training examples mix full-summary prompts with deterministic atomic questions. Autoregressive loss teaches an evidence sentence followed by compact JSON.",
      "Generation is deterministic and parsed after the final `Answer:` marker. Invalid JSON is recorded rather than repaired.",
    ],
    learned: "Trainable time-series encoder, projector, and LoRA adapters. The reported artifact is a canary adapter, not a broad hyperparameter search.",
    why: [
      "Direct numeric access and domain fine-tuning make it much stronger than the plot-only zero-shot model.",
      "Binary event presence is simpler than exact semantics, onset, joint attribution, natural-language evidence, and strict JSON generation together.",
      "The small frozen LLM must carry a long multichannel soft prompt while satisfying several output tasks. That capacity and alignment burden contributes to lower semantics quality and 77.9% parse validity.",
      "The canary scorer penalized malformed contact values in accuracy but could map them to negative predictions for binary F1. That explains the unusual accuracy/F1 pair, but the scorer does not match the evaluator committed on this branch.",
    ],
    limitation: "This is an approximately 8,192-example, three-epoch canary with 77.9% parse validity. Its manifest, learning curve, raw predictions, checkpoint, and exact scorer are absent from this branch.",
  },
  direct: {
    label: "Direct LLM zero-shot",
    role: "Untrained plot-based control",
    input: "A 1,600×1,200 raster plot plus per-joint mean/std/RMS/max-absolute statistics",
    implementation: [
      "Render seven stacked torque traces with a shared time axis and one panel per joint.",
      "Send the image and a fixed diagnosis prompt to Qwen3-VL 4B Instruct through vLLM at temperature zero.",
      "Request one evidence sentence and exact JSON fields for contact, semantics, onset, and joint evidence. No robotics training or parameter update occurs.",
      "The optional one-shot path prepends one deterministic labeled example from the training split, reused for every test query; that evaluation has not completed.",
    ],
    learned: "Nothing on this dataset. Performance comes from Qwen's pretrained visual-language representations and the supplied summary statistics.",
    why: [
      "The textual statistics and obvious plot morphology preserve enough information for moderate event-type classification, yielding 69.5% macro-F1.",
      "Rasterization is an indirect, lossy representation of exact 1 kHz timing and small cross-joint differences, so onset and joint attribution trail numeric models.",
      "A general-purpose 4B VLM has no task-specific calibration for the dataset's contact definition or pseudo-label rules.",
      "The extreme contact result cannot yet be explained from model behavior because raw zero-shot predictions and exact record IDs are not committed.",
    ],
    limitation: "Treat the contact result as a metrics-provenance warning, not evidence that the model literally inverted contact. One-shot results are still pending.",
  },
};

const results = [
  ["Signal features", "0.9880", "0.9908", "21", "188.6", "0.8413", "1.0000"],
  ["1D CNN", "0.9751", "0.9777", "18", "274.0", "0.7565", "1.0000"],
  ["OpenTSLM", "0.8845", "0.9871†", "54", "140.0", "0.7528", "0.7793"],
  ["Direct LLM zero-shot", "0.6951", "0.0291†", "42", "313.8", "0.4244", "1.0000"],
];

function MethodPanel({ active }: { active: MethodKey }) {
  const method = methods[active];
  return (
    <Card size="lg">
      <CardHeader trailing={<Pill size="sm" active>{method.role}</Pill>}>
        {method.label}
      </CardHeader>
      <CardBody>
        <Stack gap={14}>
          <Grid columns="0.8fr 1.2fr" gap={18}>
            <Stack gap={6}>
              <Text tone="tertiary" size="small">MODEL INPUT</Text>
              <Text weight="semibold">{method.input}</Text>
              <Divider />
              <Text tone="tertiary" size="small">WHAT IS LEARNED</Text>
              <Text>{method.learned}</Text>
            </Stack>
            <Stack gap={7}>
              <Text tone="tertiary" size="small">IMPLEMENTATION PIPELINE</Text>
              {method.implementation.map((item, index) => (
                <Row key={item} gap={9} align="start">
                  <Text tone="secondary" weight="semibold">{index + 1}.</Text>
                  <Text>{item}</Text>
                </Row>
              ))}
            </Stack>
          </Grid>
          <Divider />
          <Grid columns="1.25fr 0.75fr" gap={18}>
            <Stack gap={7}>
              <Text tone="tertiary" size="small">WHY IT SCORED THIS WAY</Text>
              {method.why.map((item) => (
                <Text key={item}>• {item}</Text>
              ))}
            </Stack>
            <Callout tone={active === "features" || active === "cnn" ? "info" : "warning"} title="Interpretation limit">
              {method.limitation}
            </Callout>
          </Grid>
        </Stack>
      </CardBody>
    </Card>
  );
}

function BaselineMethodsExplained() {
  const [active, setActive] = useCanvasState<MethodKey>("baseline-method", "features");
  const dispatch = useCanvasAction();
  const theme = useHostTheme();

  return (
    <Stack gap={22} style={{ padding: 24, maxWidth: 1220, margin: "0 auto" }}>
      <Stack gap={8}>
        <Row gap={8} align="center" wrap>
          <Pill active>Baseline comparison</Pill>
          <Pill>branch baseline-comparison</Pill>
          <Pill>512 held-out windows</Pill>
        </Row>
        <H1>How each telemetry baseline works—and why it scored that way</H1>
        <Text tone="secondary">
          Grounded in the branch implementation, run manifests, committed metrics, and Entire checkpoint <Code>01M2BXT4HVJK8E2ACDPZ22NYFB</Code>.
        </Text>
      </Stack>

      <Callout tone="info" title="Main conclusion">
        The CNN run is valid and strong, but it does not win every metric: against the OpenTSLM canary it wins event semantics, median onset, and output reliability; OpenTSLM wins contact F1 and onset mean/P90, while joint accuracy is tied. OpenTSLM has not yet received a fair, fully traceable full-budget run.
      </Callout>

      <Grid columns={4} gap={16}>
        <Stat value="0.9880" label="Best semantics macro-F1 · features" tone="success" />
        <Stat value="18 ms" label="Best median onset error · CNN" tone="success" />
        <Stat value="77.9%" label="OpenTSLM parse validity" tone="warning" />
        <Stat value="Pending" label="Direct LLM one-shot result" tone="info" />
      </Grid>

      <H2>Comparable headline results</H2>
      <Table
        headers={["Method", "Semantics macro-F1 ↑", "Contact F1 ↑", "Onset median ms ↓", "Onset P90 ms ↓", "Strongest joint acc. ↑", "Parse validity ↑"]}
        rows={results}
        columnAlign={["left", "right", "right", "right", "right", "right", "right"]}
        rowTone={["success", "info", "warning", "danger"]}
        striped
      />
      <Text tone="tertiary" size="small">
        Source: <Code>model_training/runs/baseline-comparison.json</Code>, recorded 2026-09-13. All rows report n=512 and seed 20260912. † Generative rows came from scorer behavior not committed on this branch; see the audit below.
      </Text>

      <Grid columns={2} gap={20} align="start">
        <Stack gap={8}>
          <H3>Event semantics and joint attribution</H3>
          <Text tone="secondary" size="small">X-axis: method · Y-axis: score (%; higher is better) · Legend: metric</Text>
          <BarChart
            categories={["Features", "CNN", "OpenTSLM", "Zero-shot"]}
            series={[
              { name: "Semantics macro-F1", data: [98.80, 97.51, 88.45, 69.51], tone: "success" },
              { name: "Strongest-joint accuracy", data: [84.13, 75.65, 75.28, 42.44], tone: "info" },
            ]}
            yMin={0}
            yMax={100}
            valueSuffix="%"
            showValues
            height={310}
          />
          <Text tone="tertiary" size="small">Source: committed held-out metrics · test subset of 512 windows · 2026-09-13.</Text>
        </Stack>
        <Stack gap={8}>
          <H3>Onset absolute-error distribution</H3>
          <Text tone="secondary" size="small">X-axis: method · Y-axis: absolute error (ms; lower is better) · Legend: distribution statistic</Text>
          <BarChart
            categories={["Features", "CNN", "OpenTSLM", "Zero-shot"]}
            series={[
              { name: "Median absolute error", data: [21, 18, 54, 42], tone: "info" },
              { name: "P90 absolute error", data: [188.6, 274, 140, 313.8], tone: "warning" },
            ]}
            yMin={0}
            yMax={340}
            valueSuffix=" ms"
            showValues
            height={310}
          />
          <Text tone="tertiary" size="small">Source: committed held-out metrics · errors are conditional on a numeric onset prediction.</Text>
        </Stack>
      </Grid>

      <Stack gap={10}>
        <H2>Explore each implementation</H2>
        <Row gap={8} wrap>
          {methodOrder.map((key) => (
            <Pill key={key} active={active === key} onClick={() => setActive(key)}>
              {methods[key].label}
            </Pill>
          ))}
        </Row>
        <MethodPanel active={active} />
      </Stack>

      <Grid columns="1.15fr 0.85fr" gap={20} align="start">
        <Stack gap={10}>
          <H2>Shared evaluation contract</H2>
          <Text><Text weight="semibold">Input:</Text> 7 KUKA external-torque channels sampled at 1 kHz for 1.024 seconds.</Text>
          <Text><Text weight="semibold">Split:</Text> complete recording sessions are stratified 70/15/15 before window generation; normalization and thresholds use training sessions only.</Text>
          <Text><Text weight="semibold">Evaluation:</Text> a fixed 512-row test subset selected without replacement with seed 20260912.</Text>
          <Text><Text weight="semibold">Labels:</Text> event class and manual onset come from source data. Strongest joint, affected joints, and evidence interval are deterministic signal-derived pseudo-labels.</Text>
          <Text><Text weight="semibold">Scope:</Text> retrospective diagnosis on one robot—not causal collision detection, subject-disjoint validation, or device-transfer evidence.</Text>
        </Stack>
        <Card>
          <CardHeader trailing={<Pill size="sm" active>Key bias</Pill>}>Why the feature baseline is so hard to beat</CardHeader>
          <CardBody>
            <Stack gap={9}>
              <Text>The feature baseline's post-onset top-5%-mean disturbance and train-free calibration closely mirror the code that created the joint pseudo-labels.</Text>
              <Divider />
              <Text tone="secondary">That is valuable as a transparent sanity check. It also means the 84.1% joint score is partly agreement with the labeling formula, not independent discovery of physical impact location.</Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Callout tone="warning" title="Comparability audit: OpenTSLM is not yet a fair head-to-head run">
        <Stack gap={7}>
          <Text>The comparison script checks only that each result says n=512; it does not verify identical record IDs.</Text>
          <Text>The reported OpenTSLM artifact is a three-epoch canary of roughly 8,192 examples, versus all 29,393 training examples for the CNN. Its checkpoint was selected by token loss, not downstream macro-F1 or parse validity.</Text>
          <Text>The canary scorer penalized malformed contact values in accuracy but could treat them as negatives for F1, so 0.7676 accuracy and 0.9871 F1 are not mathematically contradictory. They are still not directly comparable to the evaluator currently committed here.</Text>
          <Text>The OpenTSLM manifest, learning curve, raw predictions, checkpoint, and exact scorer revision are missing from this branch. Training also mixed summary and atomic prompts while evaluation used summaries.</Text>
          <Text>No recording-session-clustered confidence intervals are reported, despite multiple windows being correlated within source sessions.</Text>
        </Stack>
      </Callout>

      <H2>What to do next</H2>
      <Grid columns={3} gap={16}>
        <Card>
          <CardHeader trailing={<Pill size="sm" active>First</Pill>}>Run OpenTSLM fairly</CardHeader>
          <CardBody>
            <Text>Train at full budget, select by downstream validation metrics, use the same strict typed scorer and record IDs, and publish the manifest, predictions, checkpoint, and field coverage.</Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing={<Pill size="sm">Second</Pill>}>Finish one-shot</CardHeader>
          <CardBody>
            <Text>Run the queued deterministic one-shot evaluation and compare paired predictions against zero-shot, especially parse validity and schema consistency.</Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing={<Pill size="sm">Third</Pill>}>Add uncertainty</CardHeader>
          <CardBody>
            <Text>Bootstrap by recording session and report paired confidence intervals for semantics, onset, and joint-attribution deltas.</Text>
          </CardBody>
        </Card>
      </Grid>

      <Row gap={8} align="center" justify="space-between" wrap style={{ borderTop: `1px solid ${theme.stroke.tertiary}`, paddingTop: 14 }}>
        <Text tone="tertiary" size="small">Repository sources: data preparation, feature baseline, CNN, OpenTSLM trainer/dataset, plot VLM, metrics, manifests, and comparison report.</Text>
        <Row gap={8} wrap>
          <Button variant="ghost" onClick={() => dispatch({ type: "openFile", path: "model_training/src/robot_observability/baseline.py" })}>Feature code</Button>
          <Button variant="ghost" onClick={() => dispatch({ type: "openFile", path: "model_training/src/robot_observability/cnn_baseline.py" })}>CNN code</Button>
          <Button variant="ghost" onClick={() => dispatch({ type: "openFile", path: "model_training/src/robot_observability/train_opentslm.py" })}>OpenTSLM code</Button>
          <Button variant="ghost" onClick={() => dispatch({ type: "openFile", path: "model_training/src/robot_observability/plot_baseline.py" })}>Direct LLM code</Button>
          <Button variant="secondary" onClick={() => dispatch({ type: "openFile", path: "/Users/Student/Downloads/EHL_Zurich/ysamet/model_training/runs/baseline-comparison.json" })}>Raw metrics</Button>
        </Row>
      </Row>
    </Stack>
  );
}

export default BaselineMethodsExplained;
