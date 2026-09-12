import { useEffect, useState } from 'react';
import type { Services } from '../services';
import type { DemoData, Interval } from '../types';
import { modelWindowIssue } from './modelWindow';
import { withRawWindow } from './rawWindow';

export function useRawWindow(source: DemoData, datasetId: string, interval: Interval, services: Services) {
  const [attempt, setAttempt] = useState(0);
  const key = `${datasetId}:${source.recording.id}:${interval.start}:${interval.end}:${attempt}`;
  const sourceAlreadyCoversInterval = !modelWindowIssue(source, interval, interval.end);
  const eligible = services.connected && !sourceAlreadyCoversInterval && interval.start >= 0 && interval.end <= source.recording.durationSeconds && Math.abs(interval.end - interval.start - 1.024) < 1e-8;
  const [loaded, setLoaded] = useState<{key: string; data?: DemoData; error?: string}>();
  useEffect(() => {
    if (!eligible) return;
    let active = true;
    const snapshot = { ...interval };
    services.getWindow(source.recording.id, snapshot.start, snapshot.end, Array.from({length:7}, (_,i)=>`joint_${i+1}`), 1024)
      .then(response => { const data = withRawWindow(source, datasetId, snapshot, response); if (active) setLoaded({key, data}); })
      .catch(e => { if (active) setLoaded({key, error: e instanceof Error ? e.message : 'Raw telemetry could not load.'}); });
    return () => { active = false; };
  }, [source, datasetId, interval.start, interval.end, services, eligible, key]);
  const current = eligible && loaded?.key === key ? loaded : undefined;
  return { data: current?.data ?? source, loading: eligible && !current, error: current?.error, retry: () => setAttempt(n => n + 1) };
}
