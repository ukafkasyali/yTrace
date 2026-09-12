export type Interval = { start: number; end: number };
export type Channel = { id: string; name: string; unit: string; values: number[] };
export type Marker = { id: string; timeSeconds: number; kind: 'publisher_annotation'; label: string; source: string };
export type DemoData = {
  recording: { id: string; name: string; durationSeconds: number; sampleRateHz: number; displaySampleRateHz: number; sourceUrl: string; archive: string; channelCount: number; eventCount: number };
  times: number[];
  channels: Channel[];
  events: Marker[];
  detail: { startSeconds: number; endSeconds: number; times: number[]; channels: Channel[] };
};
export type WindowData = { times: number[]; channels: Channel[]; resolution: 'raw' | 'display'; sampleRateHz: number };
export type EvidenceLink = { channelId: string; label: string; interval: Interval };
export type Analysis = { text: string; evidence: EvidenceLink[]; tools: string[]; resolution: 'raw' | 'display' };
