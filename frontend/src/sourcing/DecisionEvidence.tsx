import { ArrowUpRight, Check, CircleX } from 'lucide-react';
import { candidateIsEligibleForApproval, candidateSuitabilityFactors, candidateSuitabilityLabel, candidateSuitabilityLevel, compareCandidateAssessments } from '../services';
import type { CandidateAssessment, EvidenceRecord, RefinementOutcome, SourcingRun } from '../services';

function evidenceRecords(run: SourcingRun, ids: string[], candidateId?: string) {
  const expected = new Set(ids);
  return run.evidence.filter(item =>
    expected.has(item.id)
    && (!candidateId || item.candidateId === candidateId),
  );
}

function sourceName(record: EvidenceRecord) {
  if (record.sourceKind === 'ZENODO') return 'Zenodo record';
  if (record.sourceKind === 'HUGGING_FACE') return 'Hugging Face dataset page';
  return 'GitHub repository';
}

function EvidenceItem({ record, requirementLabel }: {
  record: EvidenceRecord;
  requirementLabel: string;
}) {
  return <li className="requirement-evidence-item">
    <div><span>Found value</span><strong>{record.observedValue}</strong></div>
    <div><span>{sourceName(record)}</span><small>Supports “{requirementLabel}”</small></div>
    <a href={record.sourceUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open ${sourceName(record)} supporting ${requirementLabel}`}>
      Open native source <ArrowUpRight size={11} aria-hidden="true" />
    </a>
    {record.note && <p>{record.note.replace(/^Native excerpt:\s*/i, 'Source excerpt: ')}</p>}
  </li>;
}

function EvidenceLinks({ records, requirementLabel }: {
  records: EvidenceRecord[];
  requirementLabel: string;
}) {
  if (records.length === 0) return <span className="evidence-empty">No supporting record</span>;
  const ordered = [...records].sort((left, right) => right.precedence - left.precedence);
  return <div className="requirement-evidence">
    <ul><EvidenceItem record={ordered[0]} requirementLabel={requirementLabel} /></ul>
    {ordered.length > 1 && <details>
      <summary>{ordered.length - 1} additional supporting source{ordered.length === 2 ? '' : 's'}</summary>
      <ul>{ordered.slice(1).map(record => <EvidenceItem key={record.id} record={record} requirementLabel={requirementLabel} />)}</ul>
    </details>}
  </div>;
}

function SuitabilityReasons({ run, assessment }: {
  run: SourcingRun;
  assessment: CandidateAssessment;
}) {
  const suitability = candidateSuitabilityLevel(assessment);
  const factors = candidateSuitabilityFactors(assessment);
  const visible = factors.slice(0, 2);
  const remaining = factors.slice(2);
  return <div className="ranking-rationale">
    {run.excludedCandidateIds.includes(assessment.candidateId) && <strong>Excluded by reviewer</strong>}
    <span>Why {suitability.toLowerCase()}</span>
    <ul>{visible.map(factor => <li key={`${factor.kind}-${factor.label}`}><strong>{factor.label}</strong>: {factor.explanation}</li>)}</ul>
    {remaining.length > 0 && <details><summary>{remaining.length} more factor{remaining.length === 1 ? '' : 's'}</summary><ul>{remaining.map(factor => <li key={`${factor.kind}-${factor.label}`}><strong>{factor.label}</strong>: {factor.explanation}</li>)}</ul></details>}
  </div>;
}

function candidateName(run: SourcingRun, candidateId: string | null) {
  if (!candidateId) return 'No eligible dataset';
  return run.candidates.find(item => item.id === candidateId)?.name ?? candidateId;
}

function isDatasetArtifact(run: SourcingRun, candidateId: string) {
  const assessment = run.assessments.find(item => item.candidateId === candidateId);
  const identityGate = assessment?.gates.find(gate => gate.gate === 'dataset_identity');
  if (identityGate) return identityGate.passed;
  const profile = run.profiles.find(item => item.candidateId === candidateId);
  // Runs persisted before dataset-identity evidence was introduced remain readable.
  return profile?.isDatasetArtifact ?? true;
}

function discoveryLeadReason(run: SourcingRun, candidateId: string) {
  const profile = run.profiles.find(item => item.candidateId === candidateId);
  const identityGate = run.assessments
    .find(item => item.candidateId === candidateId)
    ?.gates.find(gate => gate.gate === 'dataset_identity');
  return identityGate?.reason
    ?? profile?.datasetIdentityReason
    ?? 'The native source was not verified as directly publishing dataset files.';
}

function refinementMessage(run: SourcingRun, refinement: RefinementOutcome) {
  const current = candidateName(run, refinement.recommendedCandidateId);
  const candidateCount = refinement.newCandidateIds.length;
  const evidenceCount = refinement.newEvidenceIds.length;
  if (refinement.outcome === 'RECOMMENDATION_WITHHELD') {
    const rejected = candidateName(run, refinement.rejectedCandidateId);
    const excludedCandidateIds = new Set(run.excludedCandidateIds);
    const alternatives = run.assessments.filter(item =>
      candidateIsEligibleForApproval(item, excludedCandidateIds));
    return <><strong>{rejected} was excluded from this run.</strong> {alternatives.length > 0 ? `${alternatives.length} eligible alternative${alternatives.length === 1 ? ' remains' : 's remain'}, but none reached the recommendation threshold.` : 'No eligible alternative was found; the agent recommendation is withheld.'}</>;
  }
  if (refinement.outcome === 'RECOMMENDATION_CHANGED') {
    return <>{candidateName(run, refinement.previousRecommendedCandidateId)} was replaced by <strong>{current}</strong> after adding {candidateCount} candidate{candidateCount === 1 ? '' : 's'} and {evidenceCount} native evidence record{evidenceCount === 1 ? '' : 's'}.</>;
  }
  if (refinement.outcome === 'EVIDENCE_EXPANDED') {
    return <>The scout added {evidenceCount} native evidence record{evidenceCount === 1 ? '' : 's'} across {candidateCount} new candidate{candidateCount === 1 ? '' : 's'}. <strong>{current} remains the recommendation</strong> after rescoring.</>;
  }
  if (refinement.outcome === 'CANDIDATES_ADDED') {
    return <>{candidateCount} new candidate{candidateCount === 1 ? ' was' : 's were'} found, but no new native evidence cleared verification. <strong>{current} remains the recommendation</strong>.</>;
  }
  return <>No new candidates or native evidence were found. <strong>{current} remains the recommendation</strong>; deterministic gates and suitability classification therefore did not change.</>;
}

function RefinementResults({ run }: { run: SourcingRun }) {
  if (run.refinementOutcomes.length === 0) return null;
  return <div className="refinement-results" aria-label="Refinement results">{run.refinementOutcomes.map(refinement =>
    <article className={`refinement-result refinement-${refinement.outcome.toLowerCase()}`} key={refinement.iteration} role="status">
      <strong>Refinement {refinement.iteration}: {refinement.outcome === 'RECOMMENDATION_CHANGED' ? 'recommendation changed' : refinement.outcome === 'RECOMMENDATION_WITHHELD' ? 'recommendation withheld' : refinement.outcome === 'EVIDENCE_EXPANDED' ? 'evidence expanded' : refinement.outcome === 'CANDIDATES_ADDED' ? 'candidates added' : 'no decision change'}</strong>
      <p>{refinementMessage(run, refinement)}</p>
      <details><summary>Search direction</summary><p><span>Reviewer feedback</span>{refinement.feedback}</p><p><span>Executed query</span>{refinement.query}</p></details>
    </article>,
  )}</div>;
}

export default function DecisionEvidence({
  run,
  candidateId = run.recommendedCandidateId ?? undefined,
}: {
  run: SourcingRun;
  candidateId?: string;
}) {
  const mandatory = run.requirements.filter(item => item.priority === 'MUST');
  const verifiedRequirements = mandatory.filter(item => item.status === 'VERIFIED').length;
  const recommended = run.candidates.find(item => item.id === run.recommendedCandidateId);
  const recommendedAssessment = run.assessments.find(
    item => item.candidateId === run.recommendedCandidateId,
  );
  const ranked = run.assessments
    .filter(item => isDatasetArtifact(run, item.candidateId))
    .sort(compareCandidateAssessments);
  const discoveryLeads = run.assessments
    .filter(item => !isDatasetArtifact(run, item.candidateId))
    .sort(compareCandidateAssessments);
  const excludedCandidateIds = new Set(run.excludedCandidateIds);
  const eligibleAlternatives = ranked.filter(item =>
    candidateIsEligibleForApproval(item, excludedCandidateIds));
  const evidenceCandidateId = candidateId ?? run.recommendedCandidateId
    ?? ranked[0]?.candidateId;
  const evidenceCandidate = run.candidates.find(item => item.id === evidenceCandidateId);
  const evidenceAssessment = run.assessments.find(
    item => item.candidateId === evidenceCandidateId,
  );

  return <section className="decision-evidence" aria-labelledby="decision-evidence-title">
    <div className="decision-evidence-heading">
      <div>
        <p className="eyebrow">Human review</p>
        <h3 id="decision-evidence-title">Decision evidence</h3>
      </div>
      <span className="evidence-count">{run.evidence.length} total native records</span>
    </div>
    <RefinementResults run={run} />
    <p className="decision-outcome">
      {recommended && recommendedAssessment
        ? <><strong>Recommendation: {recommended.name}</strong> has {candidateSuitabilityLabel(recommendedAssessment).toLowerCase()} and passed every mandatory gate. Verify the classification factors and linked evidence before approval.</>
        : eligibleAlternatives.length > 0
          ? <><strong>No agent recommendation.</strong> You may still approve one of the eligible shortlisted datasets.</>
          : <><strong>No eligible alternative is ready for approval.</strong> Continue refinement if another search remains.</>}
    </p>
    <dl className="decision-metrics">
      <div><dt>Mandatory requirements</dt><dd>{verifiedRequirements}/{mandatory.length} verified</dd></div>
      <div><dt>Datasets assessed</dt><dd>{ranked.length}</dd></div>
      <div><dt>Evidence confidence</dt><dd>{recommendedAssessment?.evidenceConfidence.toLowerCase() ?? 'not established'}</dd></div>
    </dl>

    <h4>Requirement coverage{evidenceCandidate ? ` for ${evidenceCandidate.name}` : ''}</h4>
    <p className="requirement-evidence-help">Each record shows the exact value the scout found, the native page that supports it, and which requirement it addresses.</p>
    <div className="table-scroll">
      <table className="data-table evidence-table">
        <thead><tr><th>Requirement</th><th>Need</th><th>Result</th><th>Supporting evidence</th></tr></thead>
        <tbody>{run.requirements.map(requirement => {
          const records = evidenceRecords(run, requirement.evidenceIds, evidenceCandidateId);
          const missing = evidenceAssessment?.missingRequirementIds.includes(requirement.id);
          const status = missing ? 'missing' : records.length > 0 ? 'verified' : 'not found';
          return <tr key={requirement.id}>
            <td><strong>{requirement.label}</strong>{requirement.expectedValues.length > 0 && <small>Expected: {requirement.expectedValues.join(', ')}</small>}</td>
            <td>{requirement.priority === 'MUST' ? 'Required' : 'Preferred'}{requirement.isSystemRequired && <small>Fixed integrity check</small>}</td>
            <td><span className={`evidence-status evidence-${status.replace(' ', '_')}`}>{status === 'verified' ? <Check size={12} aria-hidden="true" /> : <CircleX size={12} aria-hidden="true" />}{status}</span></td>
            <td><EvidenceLinks records={records} requirementLabel={requirement.label} /></td>
          </tr>;
        })}</tbody>
      </table>
    </div>

    <h4>Dataset ranking</h4>
    <div className="table-scroll">
      <table className="data-table evidence-table">
        <thead><tr><th>Candidate</th><th>Suitability</th><th>Hard gates</th><th>Why this level</th></tr></thead>
        <tbody>{ranked.length === 0 ? <tr><td colSpan={4}><span className="evidence-empty">No source was verified as a dataset artifact.</span></td></tr> : ranked.map(assessment => {
          const candidate = run.candidates.find(item => item.id === assessment.candidateId);
          const passed = assessment.gates.filter(gate => gate.passed).length;
          return <tr key={assessment.candidateId}>
            <td>{candidate ? <a href={candidate.canonicalUrl} target="_blank" rel="noopener noreferrer">{candidate.name} <ArrowUpRight size={11} aria-hidden="true" /></a> : assessment.candidateId}</td>
            <td><span className={`suitability-badge suitability-${candidateSuitabilityLevel(assessment).toLowerCase()}`}>{candidateSuitabilityLabel(assessment).replace(' suitability', '')}</span></td>
            <td>{passed}/{assessment.gates.length}</td>
            <td><SuitabilityReasons run={run} assessment={assessment} /></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    {discoveryLeads.length > 0 && <details className="discovery-leads">
      <summary>Discovery leads excluded ({discoveryLeads.length})</summary>
      <p>These pages may lead to useful sources, but their native content did not prove that they directly publish a dataset. They are never approval options.</p>
      <ul>{discoveryLeads.map(assessment => {
        const candidate = run.candidates.find(item => item.id === assessment.candidateId);
        return <li key={assessment.candidateId}>
          {candidate
            ? <a href={candidate.canonicalUrl} target="_blank" rel="noopener noreferrer">{candidate.name} <ArrowUpRight size={11} aria-hidden="true" /></a>
            : assessment.candidateId}
          <span>{discoveryLeadReason(run, assessment.candidateId)}</span>
        </li>;
      })}</ul>
    </details>}
  </section>;
}
