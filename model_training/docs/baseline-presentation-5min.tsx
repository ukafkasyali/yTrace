// Repository mirror of the live five-minute presentation canvas.
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
  Pill,
  Row,
  Stack,
  Stat,
  Text,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

const slideNames = ["Problem", "Methods", "Results", "OpenTSLM", "Decision"];
const slideTimes = ["0:00–0:35", "0:35–1:15", "1:15–2:25", "2:25–3:50", "3:50–5:00"];

function SpeakerCue({ text }: { text: string }) {
  return <Callout tone="info" title="Speaker cue">{text}</Callout>;
}

function Presentation() {
  const [active, setActive] = useCanvasState<number>("five-minute-slide", 0);
  const theme = useHostTheme();
  const previous = () => setActive(index => Math.max(0, index - 1));
  const next = () => setActive(index => Math.min(slideNames.length - 1, index + 1));

  return (
    <Stack gap={18} style={{ padding: 24, maxWidth: 1120, minHeight: 720, margin: "0 auto" }}>
      <Row justify="space-between" align="center" wrap>
        <Row gap={8} align="center" wrap>
          <Pill active>5-minute presentation</Pill>
          <Pill>{active + 1} / {slideNames.length}</Pill>
          <Pill>{slideTimes[active]}</Pill>
        </Row>
        <Text tone="tertiary" size="small">Use the controls below; one claim per scene.</Text>
      </Row>

      <Row gap={7} wrap>
        {slideNames.map((name, index) => (
          <span key={name}>
            <Pill active={active === index} onClick={() => setActive(index)}>{name}</Pill>
          </span>
        ))}
      </Row>

      <Divider />

      {active === 0 && (
        <Stack gap={22} style={{ flex: 1, justifyContent: "center" }}>
          <Stack gap={8}>
            <Text tone="tertiary" size="small">KUKA LWR4+ · EXTERNAL-JOINT TORQUE</Text>
            <H1>From seven torque traces to reviewable incident evidence</H1>
            <Text tone="secondary">We compare direct signal models and language models on the same 1.024-second robot windows.</Text>
          </Stack>
          <Grid columns={4} gap={14}>
            <Stat value="7" label="Synchronized joints" tone="info" />
            <Stat value="1 kHz" label="Sampling rate" tone="info" />
            <Stat value="512" label="Locked test windows" tone="success" />
            <Stat value="4" label="Prediction targets" tone="info" />
          </Grid>
          <SpeakerCue text="Our goal is not merely to detect contact. We need its type, timing, responsible joint, and evidence in a form an engineer can review." />
        </Stack>
      )}

      {active === 1 && (
        <Stack gap={20} style={{ flex: 1, justifyContent: "center" }}>
          <Stack gap={7}>
            <Text tone="tertiary" size="small">ONE LOCKED SPLIT · FOUR MODEL FAMILIES</Text>
            <H1>We tested increasing levels of model complexity</H1>
          </Stack>
          <Grid columns={2} gap={18}>
            <Stack gap={12}>
              <Row gap={12} align="start"><Code>01</Code><Text><Text weight="semibold">Signal features</Text> · 35 engineered statistics and logistic regression.</Text></Row>
              <Row gap={12} align="start"><Code>02</Code><Text><Text weight="semibold">1D CNN</Text> · Raw 7 × 1,024 waveform into four supervised heads.</Text></Row>
            </Stack>
            <Stack gap={12}>
              <Row gap={12} align="start"><Code>03</Code><Text><Text weight="semibold">OpenTSLM</Text> · Numeric tokens through Llama + LoRA into rationale and JSON.</Text></Row>
              <Row gap={12} align="start"><Code>04</Code><Text><Text weight="semibold">Direct Qwen</Text> · Telemetry plots evaluated zero-shot and one-shot.</Text></Row>
            </Stack>
          </Grid>
          <Callout tone="success" title="Fairness boundary">Recording sessions were split before window generation; normalization and thresholds use training sessions only.</Callout>
          <SpeakerCue text="This order matters: each method adds flexibility, but also adds an optimization or generation burden." />
        </Stack>
      )}

      {active === 2 && (
        <Stack gap={18} style={{ flex: 1, justifyContent: "center" }}>
          <Stack gap={7}>
            <Text tone="tertiary" size="small">LOCKED TEST RESULTS · N=512 · SEED 20260912</Text>
            <H1>Simple models win the fixed prediction benchmark</H1>
          </Stack>
          <Grid columns="1.25fr 0.75fr" gap={20} align="start">
            <Stack gap={7}>
              <H2>Event-semantics macro-F1 by method</H2>
              <Text tone="secondary" size="small">X-axis: model · Y-axis: macro-F1 (%; higher is better)</Text>
              <BarChart
                categories={["Features", "1D CNN", "OpenTSLM", "Qwen zero-shot"]}
                series={[{ name: "Semantics macro-F1", data: [98.8, 97.5, 88.5, 69.5], tone: "success" }]}
                yMin={0}
                yMax={100}
                valueSuffix="%"
                showValues
                height={260}
              />
              <Text tone="tertiary" size="small">Source: locked 512-window test comparison · 2026-09-13.</Text>
            </Stack>
            <Stack gap={12}>
              <Stat value="18 ms" label="Best learned median onset · CNN" tone="success" />
              <Stat value="77.9%" label="OpenTSLM canary usable summaries" tone="warning" />
              <Callout tone="warning" title="One-shot anchoring">One intentional example was copied into 467 of 512 outputs; semantics macro-F1 fell to 0.120.</Callout>
            </Stack>
          </Grid>
          <SpeakerCue text="The CNN is the strongest learned fixed predictor, but engineered features remain best because they closely match how several targets were constructed." />
        </Stack>
      )}

      {active === 3 && (
        <Stack gap={18} style={{ flex: 1, justifyContent: "center" }}>
          <Stack gap={7}>
            <Text tone="tertiary" size="small">NEW RATIONALE RUN · VALIDATION DIAGNOSTICS</Text>
            <H1>OpenTSLM trades fixed-task efficiency for a broader interface</H1>
          </Stack>
          <Grid columns={4} gap={14}>
            <Stat value="3,600" label="Selected validation step" tone="info" />
            <Stat value="98.8%" label="JSON validity · n=84" tone="success" />
            <Stat value="0.958" label="Validation semantics F1 · n=84" tone="success" />
            <Stat value="72.6%" label="Rationale presence · n=84" tone="warning" />
          </Grid>
          <Grid columns="0.9fr 1.1fr" gap={20} align="start">
            <Callout tone="success" title="Signal sensitivity">On a 12-session probe, base onset MAE was 25.4 ms and every onset prediction changed after a 64 ms time shift.</Callout>
            <Callout tone="warning" title="Grounding gap">Expected channel-swap accuracy was only 58.3%. Unseen paraphrases and the matched answer-only training run remain incomplete.</Callout>
          </Grid>
          <Text tone="tertiary" size="small">These are validation diagnostics with different sample panels—not replacement scores for the locked test table.</Text>
          <SpeakerCue text="Reasoning is valuable only when it is grounded. The model reacts to time shifts and removed signals, but its channel-level equivariance still needs work." />
        </Stack>
      )}

      {active === 4 && (
        <Stack gap={20} style={{ flex: 1, justifyContent: "center" }}>
          <Stack gap={7}>
            <Text tone="tertiary" size="small">RECOMMENDATION</Text>
            <H1>Use the best model for each responsibility</H1>
          </Stack>
          <Grid columns="1fr auto 1fr" gap={18} align="center">
            <Card size="lg">
              <CardHeader>Typed prediction</CardHeader>
              <CardBody><Stack gap={7}><H2>Signal features or 1D CNN</H2><Text tone="secondary">Class, contact, onset and affected joints.</Text></Stack></CardBody>
            </Card>
            <Text weight="bold" style={{ color: theme.accent.primary }}>+</Text>
            <Card size="lg">
              <CardHeader>Human-facing explanation</CardHeader>
              <CardBody><Stack gap={7}><H2>Grounded OpenTSLM</H2><Text tone="secondary">Explain typed results and answer follow-up questions.</Text></Stack></CardBody>
            </Card>
          </Grid>
          <Callout tone="success" title="Takeaway">Compact signal models currently provide the reliable decision layer. OpenTSLM is the promising explanation layer, pending a matched ablation and stronger grounding tests.</Callout>
          <SpeakerCue text="We are not choosing CNN versus language. We are separating reliable prediction from flexible explanation, then testing whether the explanation remains faithful." />
        </Stack>
      )}

      <Divider />
      <Row justify="space-between" align="center">
        <Button variant="secondary" disabled={active === 0} onClick={previous}>Previous</Button>
        <Text tone="tertiary" size="small">{slideNames[active]} · {slideTimes[active]}</Text>
        <Button variant="primary" disabled={active === slideNames.length - 1} onClick={next}>Next</Button>
      </Row>
    </Stack>
  );
}

export default Presentation;
