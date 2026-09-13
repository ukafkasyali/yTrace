export type StructuredPrediction = {
  contact: boolean;
  event_type: 'free' | 'intentional' | 'accidental';
  onset_ms: number | null;
  strongest_joint: string | null;
  affected_joints: string[];
  evidence_start_ms: number | null;
  evidence_end_ms: number | null;
};

const predictionKeys = ['contact', 'event_type', 'onset_ms', 'strongest_joint', 'affected_joints', 'evidence_start_ms', 'evidence_end_ms'] as const;

function timing(value: unknown, endpoint = false): number | null | undefined {
  if (value === null) return null;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1024 && (endpoint || value < 1024) ? value : undefined;
}

function answerObject(output: string): { value: unknown; keys: string[] } | undefined {
  const matches = [...output.matchAll(/Answer:\s*/gi)];
  const searchStart = matches.length ? matches.at(-1)!.index + matches.at(-1)![0].length : 0;
  const start = output.indexOf('{', searchStart);
  if (start < 0) return;
  let depth = 0, arrayDepth = 0, quoted = false, escaped = false, stringStart = -1;
  const keys: string[] = [];
  for (let index = start; index < output.length; index += 1) {
    const character = output[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') {
        quoted = false;
        if (depth === 1 && arrayDepth === 0) {
          let next = index + 1;
          while (/\s/.test(output[next] ?? '')) next += 1;
          if (output[next] === ':') keys.push(JSON.parse(output.slice(stringStart, index + 1)) as string);
        }
      }
      continue;
    }
    if (character === '"') { quoted = true; stringStart = index; }
    else if (character === '{') depth += 1;
    else if (character === '[') arrayDepth += 1;
    else if (character === ']') arrayDepth -= 1;
    else if (character === '}' && --depth === 0) return { value: JSON.parse(output.slice(start, index + 1)), keys };
  }
}

/** Keep only the supported prediction payload; generated prose is not telemetry evidence. */
export function structuredPrediction(output?: string): StructuredPrediction | undefined {
  if (!output) return;
  try {
    const answer = answerObject(output);
    if (!answer) return;
    const p = answer?.value as Record<string, unknown> | undefined;
    if (!p || typeof p !== 'object') return;
    if (answer.keys.length !== predictionKeys.length || new Set(answer.keys).size !== predictionKeys.length || predictionKeys.some(key => !(key in p))) return;
    if (typeof p.contact !== 'boolean') return;
    const eventType = typeof p.event_type === 'string' && ['free', 'intentional', 'accidental'].includes(p.event_type)
      ? p.event_type as StructuredPrediction['event_type'] : undefined;
    if (!eventType || p.contact !== (eventType !== 'free')) return;
    const joint = p.strongest_joint === null ? null : typeof p.strongest_joint === 'string' && /^J[1-7]$/.test(p.strongest_joint) ? p.strongest_joint : undefined;
    const affected = Array.isArray(p.affected_joints) && p.affected_joints.every((value: unknown) => typeof value === 'string' && /^J[1-7]$/.test(value))
      && new Set(p.affected_joints).size === p.affected_joints.length ? p.affected_joints as string[] : undefined;
    const onset = timing(p.onset_ms), evidenceStart = timing(p.evidence_start_ms), evidenceEnd = timing(p.evidence_end_ms, true);
    if (joint === undefined || affected === undefined || onset === undefined || evidenceStart === undefined || evidenceEnd === undefined) return;
    if (eventType === 'free' && (joint !== null || affected.length || onset !== null || evidenceStart !== null || evidenceEnd !== null)) return;
    if (eventType !== 'free' && (joint === null || !affected.includes(joint) || onset === null || evidenceStart === null || evidenceEnd === null || evidenceStart >= evidenceEnd)) return;
    return { contact: p.contact, event_type: eventType, onset_ms: onset, strongest_joint: joint,
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
