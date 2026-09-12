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
