import { AlertTriangle, ArrowUpRight, Check, CircleX, FileText } from 'lucide-react';
import type { CandidateAssessment, DatasetCandidate, DatasetProfile, SourcingManifest, SourcingRun } from '../services';

type Props = {
  run: SourcingRun;
  report: string;
  busy: string;
  onReview: (decision: 'APPROVE' | 'REJECT') => void;
  onLoadReport: () => void;
  onUseSource: (url: string) => void;
};

function CandidateSummary({ candidate, profile, assessment }: {
  candidate: DatasetCandidate;
  profile?: DatasetProfile;
  assessment: CandidateAssessment;
}) {
  return <article className="scout-candidate">
    <div className="scout-candidate-heading">
      <div><p className="eyebrow">Recommended candidate</p><h3>{candidate.name}</h3></div>
      <span className={`score-badge tier-${assessment.tier.toLowerCase()}`}>{assessment.totalScore}/100 · {assessment.tier.toLowerCase()}</span>
    </div>
    <a href={candidate.canonicalUrl} target="_blank" rel="noopener noreferrer">Open canonical source <ArrowUpRight size={13} aria-hidden="true" /></a>
    <dl className="scout-facts">
      <div><dt>Licence</dt><dd>{profile?.licenseId ?? 'Not verified'}</dd></div>
      <div><dt>Sampling</dt><dd>{profile?.sampleRateHz ? `${profile.sampleRateHz.toLocaleString('en-US')} Hz` : 'Not documented'}</dd></div>
      <div><dt>Evidence confidence</dt><dd>{assessment.evidenceConfidence.toLowerCase()}</dd></div>
      <div><dt>Recommendation confidence</dt><dd>{assessment.recommendationConfidence.toLowerCase()}</dd></div>
    </dl>
    <div className="gate-list" aria-label="Hard gate results">{assessment.gates.map(gate => <div key={gate.gate} className={gate.passed ? 'gate-pass' : 'gate-fail'}>{gate.passed ? <Check size={13} /> : <CircleX size={13} />}<span><strong>{gate.gate.replaceAll('_', ' ')}</strong><small>{gate.reason}</small></span></div>)}</div>
    {assessment.conflicts.length > 0 && <div className="scout-warning"><AlertTriangle size={15} aria-hidden="true" /><div><strong>Conflicting evidence retained</strong>{assessment.conflicts.map(item => <p key={item}>{item}</p>)}</div></div>}
  </article>;
}

function Manifest({ manifest, onUseSource }: { manifest: SourcingManifest; onUseSource: (url: string) => void }) {
  return <div className="scout-manifest">
    <div><p className="eyebrow">Approved manifest</p><h3>{manifest.name}</h3></div>
    <dl className="scout-facts"><div><dt>Revision</dt><dd>{manifest.revision ?? 'Source default'}</dd></div><div><dt>Licence</dt><dd>{manifest.licenseId}</dd></div><div><dt>Evidence records</dt><dd>{manifest.evidenceIds.length}</dd></div><div><dt>Approved</dt><dd>{new Date(manifest.approvedAt).toLocaleString()}</dd></div></dl>
    {manifest.limitations.length > 0 && <div><strong>Known limitations</strong><ul>{manifest.limitations.map(item => <li key={item}>{item}</li>)}</ul></div>}
    <button className="btn btn-primary" onClick={() => onUseSource(manifest.canonicalUrl)}>Use source for ingestion</button>
  </div>;
}

export default function ScoutReview({ run, report, busy, onReview, onLoadReport, onUseSource }: Props) {
  const candidate = run.candidates.find(item => item.id === run.recommendedCandidateId);
  const profile = run.profiles.find(item => item.candidateId === run.recommendedCandidateId);
  const assessment = run.assessments.find(item => item.candidateId === run.recommendedCandidateId);
  const mandatoryGaps = run.requirements.filter(item => item.priority === 'MUST' && item.status !== 'VERIFIED');

  return <div className="scout-review" aria-live="polite">
    <div className="scout-run-line"><span className={`run-status status-${run.status.toLowerCase()}`}>{run.status.replaceAll('_', ' ')}</span><span>mode: {run.executionMode.toLowerCase()} · {run.tavilyCreditsUsed}/12 Tavily credits · {run.gapQueriesUsed}/2 gap searches</span><span className="mono">{run.runId}</span></div>
    {run.errors.length > 0 && <details className="scout-errors" open={run.status === 'FAILED'}><summary>Retrieval notes ({run.errors.length})</summary><ul>{run.errors.map(error => <li role={run.status === 'FAILED' ? 'alert' : undefined} key={error}>{error}</li>)}</ul></details>}
    {mandatoryGaps.length > 0 && <div className="scout-warning"><AlertTriangle size={15} aria-hidden="true" /><div><strong>{run.status === 'NEEDS_INPUT' ? 'Recommendation withheld' : 'Mandatory evidence gaps'}</strong><ul>{mandatoryGaps.map(gap => <li key={gap.id}>{gap.label} — {gap.status.toLowerCase()}</li>)}</ul></div></div>}
    {candidate && assessment && <CandidateSummary candidate={candidate} profile={profile} assessment={assessment} />}
    {run.assessments.length > 1 && <details><summary>Compare all {run.assessments.length} assessed candidates</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Candidate</th><th>Score</th><th>Tier</th><th>Hard gates</th></tr></thead><tbody>{[...run.assessments].sort((a, b) => b.totalScore - a.totalScore).map(item => <tr key={item.candidateId}><td>{run.candidates.find(candidateItem => candidateItem.id === item.candidateId)?.name ?? item.candidateId}</td><td>{item.totalScore}</td><td>{item.tier.toLowerCase()}</td><td>{item.gates.filter(gate => gate.passed).length}/{item.gates.length}</td></tr>)}</tbody></table></div></details>}
    {run.status === 'AWAITING_APPROVAL' && candidate && <div className="scout-approval"><div><strong>Human decision required</strong><p>Approval creates a manifest. It does not download or ingest data.</p></div><div><button className="btn" disabled={Boolean(busy)} onClick={() => onReview('REJECT')}>Reject</button><button className="btn btn-primary" disabled={Boolean(busy)} onClick={() => onReview('APPROVE')}>{busy === 'APPROVE' ? 'Approving…' : 'Approve manifest'}</button></div></div>}
    {run.manifest && <Manifest manifest={run.manifest} onUseSource={onUseSource} />}
    {run.reportMarkdown && <details className="scout-report" onToggle={event => { if ((event.currentTarget as HTMLDetailsElement).open && !report) onLoadReport(); }}><summary><FileText size={13} aria-hidden="true" />Evidence report</summary>{busy === 'report' ? <p>Loading report…</p> : <pre>{report || run.reportMarkdown}</pre>}</details>}
  </div>;
}
