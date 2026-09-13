export type StructuredPrediction = {
  contact: boolean;
  event_type: 'free' | 'intentional' | 'accidental';
  onset_ms: number | null;
  strongest_joint: string | null;
  affected_joints: string[];
  evidence_start_ms: number | null;
  evidence_end_ms: number | null;
};

function timing(value: unknown): number | null | undefined {
  if (value === null) return null;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value < 1024 ? value : undefined;
}

/** Keep only the supported prediction payload; generated prose is not telemetry evidence. */
export function structuredPrediction(output?: string): StructuredPrediction | undefined {
  if (!output) return;
  try {
    const first = output.indexOf('{'), last = output.lastIndexOf('}');
    const p = JSON.parse(output.slice(first, last + 1));
    if (!p || typeof p !== 'object' || !['free', 'intentional', 'accidental'].includes(p.event_type)) return;
    if (p.contact !== (p.event_type !== 'free')) return;
    const joint = p.strongest_joint === null ? null : typeof p.strongest_joint === 'string' && /^J[1-7]$/.test(p.strongest_joint) ? p.strongest_joint : undefined;
    const affected = Array.isArray(p.affected_joints) && p.affected_joints.every((value: unknown) => typeof value === 'string' && /^J[1-7]$/.test(value))
      ? [...new Set(p.affected_joints as string[])] : undefined;
    const onset = timing(p.onset_ms), evidenceStart = timing(p.evidence_start_ms), evidenceEnd = timing(p.evidence_end_ms);
    if (joint === undefined || affected === undefined || onset === undefined || evidenceStart === undefined || evidenceEnd === undefined) return;
    if (p.event_type === 'free' && (joint !== null || affected.length || onset !== null || evidenceStart !== null || evidenceEnd !== null)) return;
    if (p.event_type !== 'free' && (joint === null || onset === null)) return;
    if ((evidenceStart === null) !== (evidenceEnd === null) || (evidenceStart !== null && evidenceEnd !== null && evidenceEnd < evidenceStart)) return;
    return { contact: p.contact, event_type: p.event_type, onset_ms: onset, strongest_joint: joint,
      affected_joints: affected, evidence_start_ms: evidenceStart, evidence_end_ms: evidenceEnd };
  } catch { return; }
}

/** A compact operator view of the supported generated fields. */
export function predictionBrief(output?: string) {
  const p = structuredPrediction(output);
  if (!p) return;
  return {
    title: p.event_type === 'free' ? 'Free motion predicted' : p.event_type === 'intentional' ? 'Intentional contact predicted' : 'Accidental contact predicted',
    strongest: p.strongest_joint ?? undefined,
    onset: p.onset_ms ?? undefined,
  };
}

/** Convert relative generated onset to recording time, without inventing contact location. */
export function predictionCue(output: string | undefined, interval: { start: number; end: number }) {
  const brief = predictionBrief(output);
  if (!brief || brief.title === 'Free motion predicted' || brief.onset === undefined ||
    !Number.isFinite(interval.start) || !Number.isFinite(interval.end) || interval.start < 0 || Math.abs(interval.end - interval.start - 1.024) > 1e-8) return;
  return { title: brief.title, onsetSeconds: interval.start + brief.onset / 1000,
    channelId: brief.strongest ? `joint_${brief.strongest.slice(1)}` : undefined, interval: { ...interval } };
}
export type PredictionCue = NonNullable<ReturnType<typeof predictionCue>>;
