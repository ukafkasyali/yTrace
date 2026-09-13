import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowUpRight, Check, CircleX } from 'lucide-react';
import { candidateIsEligibleForApproval, candidateSuitabilityFactors, candidateSuitabilityLabel, candidateSuitabilityLevel } from '../services';
import type { CandidateAssessment, DatasetCandidate, DatasetProfile, SourcingManifest, SourcingReview, SourcingRun } from '../services';
import DecisionEvidence from './DecisionEvidence';
import { friendlyRetrievalNote } from './retrievalNotes';

type Props = {
  run: SourcingRun;
  busy: string;
  onReview: (review: SourcingReview) => void;
  onUseSource: (url: string) => void;
};

function CandidateSummary({ candidate, profile, assessment, isRecommendation }: {
  candidate: DatasetCandidate;
  profile?: DatasetProfile;
  assessment: CandidateAssessment;
  isRecommendation: boolean;
}) {
  const suitability = candidateSuitabilityLevel(assessment);
  const factors = candidateSuitabilityFactors(assessment);
  return <article className="scout-candidate">
    <div className="scout-candidate-heading">
      <div><h3>{candidate.name}</h3><p className="candidate-role">{isRecommendation ? 'Agent recommendation' : 'Selected for review'}</p></div>
      <span className={`suitability-badge suitability-${suitability.toLowerCase()}`}>{candidateSuitabilityLabel(assessment)}</span>
    </div>
    <a href={candidate.canonicalUrl} target="_blank" rel="noopener noreferrer">Open canonical source <ArrowUpRight size={13} aria-hidden="true" /></a>
    <dl className="scout-facts">
      <div><dt>Licence</dt><dd>{profile?.licenseId ?? 'Not verified'}</dd></div>
      <div><dt>Sampling</dt><dd>{profile?.sampleRateHz ? `${profile.sampleRateHz.toLocaleString('en-US')} Hz` : 'Not documented'}</dd></div>
      <div><dt>Evidence confidence</dt><dd>{assessment.evidenceConfidence.toLowerCase()}</dd></div>
      <div><dt>Recommendation confidence</dt><dd>{assessment.recommendationConfidence.toLowerCase()}</dd></div>
    </dl>
    <section className="suitability-rationale" aria-label={`Why ${suitability.toLowerCase()} suitability`}>
      <h4>Why {suitability.toLowerCase()} suitability</h4>
      <ul>{factors.map(factor => <li key={`${factor.kind}-${factor.label}`} className={`factor-${factor.kind.toLowerCase()}`}><span>{factor.kind.toLowerCase()}</span><div><strong>{factor.label}</strong><small>{factor.explanation}</small></div></li>)}</ul>
    </section>
    <div className="gate-list" aria-label="Hard gate results">{assessment.gates.map(gate => <div key={gate.gate} className={gate.passed ? 'gate-pass' : 'gate-fail'}>{gate.passed ? <Check size={13} /> : <CircleX size={13} />}<span><strong>{gate.gate.replaceAll('_', ' ')}</strong><small>{gate.reason}</small></span></div>)}</div>
    {assessment.conflicts.length > 0 && <div className="scout-warning"><AlertTriangle size={15} aria-hidden="true" /><div><strong>Conflicting evidence retained</strong>{assessment.conflicts.map(item => <p key={item}>{item}</p>)}</div></div>}
  </article>;
}

function Manifest({ manifest, onUseSource }: { manifest: SourcingManifest; onUseSource: (url: string) => void }) {
  return <div className="scout-manifest">
    <div><h3>{manifest.name}</h3><p className="candidate-role">Approved manifest</p></div>
    <dl className="scout-facts"><div><dt>Revision</dt><dd>{manifest.revision ?? 'Source default'}</dd></div><div><dt>Licence</dt><dd>{manifest.licenseId}</dd></div><div><dt>Evidence records</dt><dd>{manifest.evidenceIds.length}</dd></div><div><dt>Approved</dt><dd>{new Date(manifest.approvedAt).toLocaleString()}</dd></div></dl>
    {manifest.limitations.length > 0 && <div><strong>Known limitations</strong><ul>{manifest.limitations.map(item => <li key={item}>{item}</li>)}</ul></div>}
    <button className="btn btn-primary" onClick={() => onUseSource(manifest.canonicalUrl)}>Prepare ingestion handoff</button>
  </div>;
}

export default function ScoutReview({ run, busy, onReview, onUseSource }: Props) {
  const excludedCandidateIds = new Set(run.excludedCandidateIds);
  const eligible = run.assessments.filter(item =>
    candidateIsEligibleForApproval(item, excludedCandidateIds));
  const eligibleKey = eligible.map(item => item.candidateId).join('|');
  const defaultCandidateId = run.approvedCandidateId ?? run.recommendedCandidateId
    ?? eligible[0]?.candidateId ?? '';
  const [selectedCandidateId, setSelectedCandidateId] = useState(defaultCandidateId);
  const [feedback, setFeedback] = useState('');
  useEffect(() => {
    setSelectedCandidateId(current => eligible.some(item => item.candidateId === current)
      ? current : defaultCandidateId);
    setFeedback('');
  }, [defaultCandidateId, eligibleKey, run.reviewIterationsUsed]);
  const candidate = run.candidates.find(item => item.id === selectedCandidateId);
  const profile = run.profiles.find(item => item.candidateId === selectedCandidateId);
  const assessment = run.assessments.find(item => item.candidateId === selectedCandidateId);
  const mandatoryGaps = run.requirements.filter(item => item.priority === 'MUST' && item.status !== 'VERIFIED');
  const hasRefinementSlot = run.reviewIterationsUsed < 2;
  const hasRefinementCredits = run.tavilyCreditsUsed + 2 <= 12;
  const refinementAvailable = run.feedbackAllowed
    ?? (run.status === 'AWAITING_APPROVAL' && hasRefinementSlot && hasRefinementCredits);
  const needsInput = run.status === 'NEEDS_INPUT';
  const showReviewPanel = run.status === 'AWAITING_APPROVAL'
    || (needsInput && refinementAvailable);
  const normalizedFeedback = feedback.trim();

  return <div className="scout-review" aria-live="polite">
    <div className="scout-run-line"><span className={`run-status status-${run.status.toLowerCase()}`}>{run.status.replaceAll('_', ' ')}</span><span>mode: {run.executionMode.toLowerCase()} · {run.tavilyCreditsUsed}/12 Tavily credits · {run.gapQueriesUsed}/2 gap searches</span><span className="mono">{run.runId}</span></div>
    {run.errors.length > 0 && <details className="scout-errors" open={run.status === 'FAILED'}><summary>Source coverage notes ({run.errors.length})</summary><ul>{run.errors.map(error => <li role={run.status === 'FAILED' ? 'alert' : undefined} key={error}>{friendlyRetrievalNote(error, run)}</li>)}</ul></details>}
    {mandatoryGaps.length > 0 && <div className="scout-warning"><AlertTriangle size={15} aria-hidden="true" /><div><strong>{needsInput ? 'Recommendation withheld' : 'Mandatory evidence gaps'}</strong><ul>{mandatoryGaps.map(gap => <li key={gap.id}>{gap.label} — {gap.status.toLowerCase()}</li>)}</ul>{needsInput && !refinementAvailable && <p>No refinement budget remains. Start a new run after adjusting the brief or requirements.</p>}</div></div>}
    {candidate && assessment && <CandidateSummary candidate={candidate} profile={profile} assessment={assessment} isRecommendation={selectedCandidateId === run.recommendedCandidateId} />}
    {run.assessments.length > 0 && <DecisionEvidence run={run} candidateId={selectedCandidateId || undefined} />}
    {showReviewPanel && <section className="scout-approval" aria-labelledby="scout-approval-title">
      <div><strong id="scout-approval-title">{needsInput ? 'Help the scout continue' : 'Human decision required'}</strong><p>{needsInput ? 'No dataset passed the current contract. Direct the next bounded search with specific missing evidence, source types, or alternatives.' : 'Approve any eligible dataset, or give the scout specific feedback for another bounded search.'}</p></div>
      <div className={`scout-approval-grid${needsInput ? ' needs-input' : ''}`}>
        {!needsInput && <div className="scout-approval-choice">
          <label htmlFor={`approval-candidate-${run.runId}`}>Dataset to approve</label>
          <select id={`approval-candidate-${run.runId}`} value={selectedCandidateId} onChange={event => setSelectedCandidateId(event.target.value)} disabled={Boolean(busy) || eligible.length === 0}>
            {eligible.length === 0 && <option value="">No eligible datasets</option>}
            {eligible.map(item => <option key={item.candidateId} value={item.candidateId}>{run.candidates.find(candidateItem => candidateItem.id === item.candidateId)?.name ?? item.candidateId} — {candidateSuitabilityLabel(item)}{item.candidateId === run.recommendedCandidateId ? ' (agent recommendation)' : ''}</option>)}
          </select>
          <small>Only datasets that pass every mandatory gate can be approved.</small>
          <button className="btn btn-primary" disabled={Boolean(busy) || !selectedCandidateId} onClick={() => onReview({ decision: 'APPROVE', candidateId: selectedCandidateId })}>{busy === 'APPROVE' ? 'Approving…' : 'Approve selected dataset'}</button>
        </div>}
        <div className="scout-refinement">
          <label htmlFor={`refinement-feedback-${run.runId}`}>{needsInput ? 'What should the scout search for next?' : 'What should the scout improve?'}</label>
          <textarea id={`refinement-feedback-${run.runId}`} rows={3} value={feedback} onChange={event => setFeedback(event.target.value)} placeholder="Example: prioritize datasets with free-motion baselines and CSV files" disabled={Boolean(busy) || !refinementAvailable} />
          <small>{refinementAvailable ? `${run.reviewIterationsUsed}/2 refinement searches used. Feedback is added to the next bounded query.` : hasRefinementSlot ? 'The Tavily credit budget is exhausted; start a new run to continue.' : 'The refinement limit is reached; start a new run to continue.'}</small>
          <button className="btn" disabled={Boolean(busy) || !refinementAvailable || normalizedFeedback.length < 3} onClick={() => onReview({ decision: 'REJECT', candidateId: needsInput ? undefined : selectedCandidateId || undefined, note: normalizedFeedback })}>{busy === 'REJECT' ? 'Refining…' : needsInput ? 'Refine search' : 'Reject and refine'}</button>
        </div>
      </div>
      {run.reviewFeedback.length > 0 && <details className="scout-review-history"><summary>Applied reviewer feedback ({run.reviewFeedback.length})</summary><ol>{run.reviewFeedback.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol></details>}
    </section>}
    {run.manifest && <Manifest manifest={run.manifest} onUseSource={onUseSource} />}
  </div>;
}
