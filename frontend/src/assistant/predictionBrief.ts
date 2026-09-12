/** A compact view of supported generated fields; the full generation stays available. */
export function predictionBrief(output?: string) {
  if (!output) return;
  try {
    const first = output.indexOf('{'), last = output.lastIndexOf('}');
    const p = JSON.parse(output.slice(first, last + 1));
    if (!p || typeof p !== 'object' || !['free', 'intentional', 'accidental'].includes(p.event_type)) return;
    if (p.contact !== (p.event_type !== 'free')) return;
    return {
      title: p.event_type === 'free' ? 'Free motion predicted' : p.event_type === 'intentional' ? 'Intentional contact predicted' : 'Accidental contact predicted',
      strongest: typeof p.strongest_joint === 'string' && /^J[1-7]$/.test(p.strongest_joint) ? p.strongest_joint : undefined,
      onset: typeof p.onset_ms === 'number' && Number.isFinite(p.onset_ms) && p.onset_ms >= 0 && p.onset_ms < 1024 ? p.onset_ms : undefined,
    };
  } catch { return; }
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
