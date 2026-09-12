import type { Interval, Marker } from '../types';

export function markerWindow(marker: Marker, durationSeconds: number): Interval {
  const start = Math.round(Math.max(0, Math.min(marker.timeSeconds - .4, durationSeconds - 1.024)) * 1000) / 1000;
  return { start, end: Math.min(durationSeconds, Math.round((start + 1.024) * 1000) / 1000) };
}
