import { describe, it, expect } from 'vitest';
import { compareWindows, comparisonReport, rankSimilarIncidents, suggestReference, sampledPeak } from './compare';
import type { DemoData } from '../types';
function fixture(id = 'test'): DemoData {
  const times = Array.from({length: 6000}, (_, i) => i / 1000);
  const channels = Array.from({length: 7}, (_, j) => ({ id: `joint_${j + 1}`, name: `Joint ${j + 1}`, unit: 'Nm', values: times.map((_, i) => (i % 2 ? 1 : -1) * (i >= 4000 ? j + 2 : 1)) }));
  return { recording: {id, name:id, durationSeconds:6, channelCount:7, eventCount:1, sampleRateHz:1000, displaySampleRateHz:100, sourceUrl:'source', archive:'fixture'},
    times: times.filter((_, i) => i % 10 === 0), channels: channels.map(c => ({...c, values:c.values.filter((_, i) => i % 10 === 0)})),
    detail:{startSeconds:0,endSeconds:6,times,channels}, events:[{id:'marker',timeSeconds:4.5,label:'Annotation',kind:'publisher_annotation',source:'publisher'}] };
}
const current = {start:4,end:5.024}, reference = {start:1,end:2.024};
describe('incident comparison', () => {
  it('anchors graph callouts to the signed absolute peak and its original timestamp', () => {
    expect(sampledPeak([4.001, 4.002, 4.003, 4.004], [1, -5, 3, 5])).toEqual({time: 4.002, value: -5});
    expect(sampledPeak([1, 1.001], [0, 0])).toEqual({time: 1, value: 0});
  });
  it('calculates signed differences on equal raw windows and ranks magnitude', () => {
    const r=compareWindows(fixture(),current,fixture(),reference);
    expect(r.sampleRateHz).toBe(1000); expect(r.selected.samplesPerChannel).toBe(1024); expect(r.reference.samplesPerChannel).toBe(1024);
    expect(r.joints[0]).toMatchObject({id:'joint_7', selected:{range:16,variability:8},reference:{range:2,variability:1},rangeDelta:14,variabilityDelta:7});
    expect(r.joints[0].selected.mean).toBeCloseTo(0); expect(r.joints[0].reference.mean).toBeCloseTo(0);
    expect(r.selected.publisherAnnotations).toHaveLength(1); expect(r.reference.publisherAnnotations).toHaveLength(0);
    expect(comparisonReport(r)).not.toHaveProperty('plot');
  });
  it('compares both sides at overview rate when one side lacks raw coverage', () => {
    const b=fixture('other'); b.detail.startSeconds=3;
    const r=compareWindows(fixture(),current,b,reference);
    expect(r.resolution).toBe('display'); expect(r.sampleRateHz).toBe(100); expect(r.selected.samplesPerChannel).toBe(103);
    // Alternating raw spikes alias in this fixture: demonstrates why mixed resolutions are invalid.
    expect(r.joints[0].rangeDelta).toBe(0); expect(r.limitations.join(' ')).toContain('short peaks');
  });
  it('preserves decreases without claiming repair success and permits different recordings', () => {
    const r=compareWindows(fixture('after'),reference,fixture('before'),current);
    expect(r.joints[0].rangeDelta).toBe(-14); expect(r.limitations.join(' ')).toContain('does not prove');
    expect(r.selected.recordingId).toBe('after'); expect(r.reference.recordingId).toBe('before');
  });
  it('rejects overlap, unequal duration, gaps, units and different rates', () => {
    expect(()=>compareWindows(fixture(),current,fixture(),current)).toThrow('overlap');
    expect(()=>compareWindows(fixture(),current,fixture(),{start:1,end:2})).toThrow('equal-duration');
    expect(()=>compareWindows(fixture(),current,fixture(),{start:-1,end:.024})).toThrow();
    const broken=fixture('other'); broken.detail.times.splice(1500,1); broken.detail.channels.forEach(c=>c.values.splice(1500,1));
    expect(()=>compareWindows(fixture(),current,broken,reference)).toThrow('missing samples');
    const units=fixture('other'); units.detail.channels[0].unit='kNm';
    expect(()=>compareWindows(fixture(),current,units,reference)).toThrow('Nm');
    const rate=fixture('other'); rate.detail.startSeconds=3; rate.recording.displaySampleRateHz=50; rate.times=rate.times.filter((_,i)=>i%2===0); rate.channels.forEach(c=>{c.values=c.values.filter((_,i)=>i%2===0);});
    expect(()=>compareWindows(fixture(),current,rate,reference)).toThrow('different sample rates');
    const missing=fixture('other'); missing.detail.channels[0].values[1500]=NaN;
    expect(()=>compareWindows(fixture(),current,missing,reference)).toThrow();
  });
  it('suggests an earlier annotation-free reference, never labels it normal', () => {
    const data=fixture(), w=suggestReference(data,current)!;
    expect(w.end).toBeLessThanOrEqual(current.start-.5); expect(w.end-w.start).toBeCloseTo(1.024);
    expect(data.events.every(e=>e.timeSeconds < w.start-.5 || e.timeSeconds > w.end+.5)).toBe(true);
    expect(suggestReference(data,{start:0,end:1.024})).toBeUndefined();
    expect(suggestReference(data,{start:4,end:4.000001})).toBeUndefined();
  });
  it('keeps the report source windows frozen after selection changes', () => {
    const w={...current}; const r=compareWindows(fixture(),w,fixture(),reference); w.start=0;
    expect(r.selected.interval.start).toBe(4);
  });
  it('ranks raw incident profiles without using publisher labels', () => {
    const selected = fixture('selected');
    const exact = fixture('exact');
    const weaker = fixture('weaker');
    const matches = rankSimilarIncidents(selected, current, [
      { item: { id:'weaker', title:'Different profile', recordingId:'weaker', interval:reference, note:'intentional label is display-only' }, data:weaker },
      { item: { id:'exact', title:'Matching profile', recordingId:'exact', interval:current, note:'free label is display-only' }, data:exact },
    ]);
    expect(matches.map(match => match.caseId)).toEqual(['exact', 'weaker']);
    expect(matches[0]).toMatchObject({ score:1, strongestJoint:'joint_7', samplesPerChannel:1024 });
    expect(matches[1].score).toBeLessThan(matches[0].score);
  });
  it('excludes the selected recording and candidates without complete 1 kHz raw evidence', () => {
    const selected = fixture('selected');
    const unavailable = fixture('unavailable'); unavailable.detail.endSeconds = 1.5;
    const wrongRate = fixture('wrong-rate'); wrongRate.recording.sampleRateHz = 500;
    const matches = rankSimilarIncidents(selected, current, [
      { item: { id:'same-run', title:'Same run', recordingId:'selected', interval:reference, note:'' }, data:selected },
      { item: { id:'unavailable', title:'Unavailable', recordingId:'unavailable', interval:reference, note:'' }, data:unavailable },
      { item: { id:'wrong-rate', title:'Wrong rate', recordingId:'wrong-rate', interval:reference, note:'' }, data:wrongRate },
    ]);
    expect(matches).toEqual([]);
    wrongRate.recording.id = 'selected';
    expect(() => rankSimilarIncidents(wrongRate, current, [])).toThrow('1 kHz');
  });
});
