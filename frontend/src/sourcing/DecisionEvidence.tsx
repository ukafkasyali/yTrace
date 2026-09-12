import { ArrowUpRight, Check, CircleX } from 'lucide-react';
import type { EvidenceRecord, SourcingRun } from '../services';

function label(value: string) {
  return value.replaceAll('_', ' ').toLowerCase();
}

function evidenceRecords(run: SourcingRun, ids: string[]) {
  const expected = new Set(ids);
  return run.evidence.filter(item =>
    expected.has(item.id)
    && (!run.recommendedCandidateId || item.candidateId === run.recommendedCandidateId),
  );
}

function EvidenceLinks({ records }: { records: EvidenceRecord[] }) {
  if (records.length === 0) return <span className="evidence-empty">No supporting record</span>;
  return <ul className="evidence-links">{records.map(record =>
    <li key={record.id}>
      <a href={record.sourceUrl} target="_blank" rel="noopener noreferrer">
        {record.sourceKind}: {record.observedValue} <ArrowUpRight size={11} aria-hidden="true" />
      </a>
    </li>,
  )}</ul>;
}

function decisionReason(run: SourcingRun, candidateId: string) {
  const assessment = run.assessments.find(item => item.candidateId === candidateId);
  if (!assessment) return 'Not assessed';
  const missing = assessment.missingRequirementIds
    .map(id => run.requirements.find(item => item.id === id)?.label ?? id);
  if (missing.length > 0) return `Missing: ${missing.join(', ')}`;
  const failed = assessment.gates.filter(gate => !gate.passed).map(gate => label(gate.gate));
  if (failed.length > 0) return `Failed hard gates: ${failed.join(', ')}`;
  if (assessment.conflicts.length > 0) {
    return `All gates pass; review conflicts in ${assessment.conflicts.map(label).join(', ')}`;
  }
  return 'All mandatory gates passed';
}

export function friendlyRetrievalNote(note: string, run: SourcingRun) {
  const match = note.match(/^Candidate (ds_[a-f0-9]+) verification failed: ([\s\S]*)$/);
  if (!match) return note.split('\n', 1)[0];
  const candidate = run.candidates.find(item => item.id === match[1]);
  const name = candidate?.name ?? match[1];
  const reason = match[2];
  if (reason.includes('response exceeded the configured size limit')) {
    return `${name}: verification hit the previous metadata-size policy. Rerun to retain primary evidence while safely skipping an oversized repository tree.`;
  }
  if (reason.includes("301 Moved Permanently")) {
    return `${name}: GitHub moved a linked repository. Rerun to follow its validated canonical URL.`;
  }
  if (reason.includes("404 Not Found") && reason.includes('/readme')) {
    return `${name}: GitHub has no standard README. Rerun to verify the remaining repository metadata.`;
  }
  if (reason.includes("403")) {
    return `${name}: GitHub denied the verification request or its API rate limit was reached.`;
  }
  return `${name}: ${reason.split('\n', 1)[0]}`;
}

export default function DecisionEvidence({ run }: { run: SourcingRun }) {
  const mandatory = run.requirements.filter(item => item.priority === 'MUST');
  const verifiedRequirements = mandatory.filter(item => item.status === 'VERIFIED').length;
  const recommended = run.candidates.find(item => item.id === run.recommendedCandidateId);
  const recommendedAssessment = run.assessments.find(
    item => item.candidateId === run.recommendedCandidateId,
  );
  const ranked = [...run.assessments].sort((left, right) => right.totalScore - left.totalScore);

  return <section className="decision-evidence" aria-labelledby="decision-evidence-title">
    <div className="decision-evidence-heading">
      <div>
        <p className="eyebrow">Human review</p>
        <h3 id="decision-evidence-title">Decision evidence</h3>
      </div>
      <span className="evidence-count">{run.evidence.length} total native records</span>
    </div>
    <p className="decision-outcome">
      {recommended && recommendedAssessment
        ? <><strong>Recommendation: {recommended.name}</strong> scored {recommendedAssessment.totalScore}/100 and passed every mandatory gate. Verify the linked evidence before approval.</>
        : <><strong>No candidate is ready for approval.</strong> Review the missing requirements and rejection reasons below.</>}
    </p>
    <dl className="decision-metrics">
      <div><dt>Mandatory requirements</dt><dd>{verifiedRequirements}/{mandatory.length} verified</dd></div>
      <div><dt>Candidates assessed</dt><dd>{run.assessments.length}/{run.candidates.length}</dd></div>
      <div><dt>Evidence confidence</dt><dd>{recommendedAssessment?.evidenceConfidence.toLowerCase() ?? 'not established'}</dd></div>
    </dl>

    <h4>Requirement coverage</h4>
    <div className="table-scroll">
      <table className="data-table evidence-table">
        <thead><tr><th>Requirement</th><th>Status</th><th>Native evidence</th></tr></thead>
        <tbody>{mandatory.map(requirement => <tr key={requirement.id}>
          <td><strong>{requirement.label}</strong>{requirement.expectedValues.length > 0 && <small>Expected: {requirement.expectedValues.join(', ')}</small>}</td>
          <td><span className={`evidence-status evidence-${requirement.status.toLowerCase()}`}>{requirement.status === 'VERIFIED' ? <Check size={12} aria-hidden="true" /> : <CircleX size={12} aria-hidden="true" />}{requirement.status.toLowerCase()}</span></td>
          <td><EvidenceLinks records={evidenceRecords(run, requirement.evidenceIds)} /></td>
        </tr>)}</tbody>
      </table>
    </div>

    <h4>Candidate ranking</h4>
    <div className="table-scroll">
      <table className="data-table evidence-table">
        <thead><tr><th>Candidate</th><th>Score</th><th>Hard gates</th><th>Decision reason</th></tr></thead>
        <tbody>{ranked.map(assessment => {
          const candidate = run.candidates.find(item => item.id === assessment.candidateId);
          const passed = assessment.gates.filter(gate => gate.passed).length;
          return <tr key={assessment.candidateId}>
            <td>{candidate ? <a href={candidate.canonicalUrl} target="_blank" rel="noopener noreferrer">{candidate.name} <ArrowUpRight size={11} aria-hidden="true" /></a> : assessment.candidateId}</td>
            <td><strong>{assessment.totalScore}</strong>/100<small>{assessment.tier.toLowerCase()}</small></td>
            <td>{passed}/{assessment.gates.length}</td>
            <td>{decisionReason(run, assessment.candidateId)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </section>;
}
