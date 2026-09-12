export const COLORS = ['#8cbfff', '#e9b47c', '#d3ce8a', '#8bd2b6', '#b6a1e3', '#e4a5bd', '#92cbd2'];
export function timecode(seconds: number, decimals = false) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
  return `${minutes}:${(seconds % 60).toFixed(decimals ? 3 : 1).padStart(decimals ? 6 : 4, '0')}`;
}
export function intervalLabel(interval: { start: number; end: number }) { return `${interval.start.toFixed(3)}–${interval.end.toFixed(3)} s`; }
