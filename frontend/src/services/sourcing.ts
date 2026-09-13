export type SourcingStatus =
  | 'QUEUED' | 'PLANNING' | 'DISCOVERING' | 'VERIFYING' | 'ASSESSING'
  | 'AWAITING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'NEEDS_INPUT' | 'FAILED';

export type SourcingConstraints = {
  mustHave: string[];
  preferred: string[];
  allowedLicenses: string[];
  maxDownloadBytes: number;
};

export type RequirementPriority = 'MUST' | 'SHOULD';
export type RequirementCategory = 'DOMAIN' | 'TASK_LABELS' | 'SAMPLING_RATE' | 'MODALITY'
  | 'LICENSE' | 'PROVENANCE' | 'SCHEMA' | 'ACQUISITION' | 'OTHER';

export type RequirementDefinition = {
  id: string;
  label: string;
  description: string;
  priority: RequirementPriority;
  category: RequirementCategory;
  expectedValues: string[];
  isSystemRequired?: boolean;
};

export type CreateSourcingRun = {
  brief: string;
  constraints?: Partial<SourcingConstraints>;
  requirements?: RequirementDefinition[];
};

export type RequirementPreviewRequest = {
  brief: string;
  constraints?: Partial<SourcingConstraints>;
  customRequirements: string[];
};

export type RequirementsPreview = { requirements: RequirementDefinition[] };

export type ResearchRequirement = RequirementDefinition & {
  status: 'VERIFIED' | 'MISSING' | 'CONFLICTING' | 'UNVERIFIED';
  evidenceIds: string[];
};

export type DatasetCandidate = {
  id: string;
  name: string;
  canonicalUrl: string;
  sourceKind: 'ZENODO' | 'GITHUB' | 'HUGGING_FACE';
  description: string;
  revision: string | null;
  relatedUrls: string[];
  sourceRole?: 'DISCOVERY_LEAD' | 'DATASET_ARTIFACT';
  discoveryDepth?: number;
  discoveredFromCandidateId?: string | null;
};

export type DatasetProfile = {
  candidateId: string;
  name: string;
  canonicalUrl: string;
  sourceKinds: ('ZENODO' | 'GITHUB' | 'HUGGING_FACE')[];
  revision: string | null;
  licenseId: string | null;
  fileCount: number | null;
  totalSizeBytes: number | null;
  fileExtensions: string[];
  domains: string[];
  labels: string[];
  sampleRateHz: number | null;
  channelCount: number | null;
  hasTimeSeriesFiles: boolean;
  schemaDocumented: boolean;
  acquisitionFeasible: boolean;
  isDatasetArtifact?: boolean;
  datasetIdentityReason?: string;
};

export type EvidenceRecord = {
  id: string;
  candidateId: string;
  requirementId: string | null;
  claimKey: string;
  observedValue: string;
  sourceUrl: string;
  sourceKind: 'ZENODO' | 'GITHUB' | 'HUGGING_FACE';
  status: 'VERIFIED' | 'MISSING' | 'CONFLICTING' | 'UNVERIFIED';
  precedence: number;
  retrievedAt: string;
  note: string | null;
};

export type SuitabilityLevel = 'HIGH' | 'MEDIUM' | 'LOW';
export type SuitabilityFactor = {
  kind: 'STRENGTH' | 'LIMITATION' | 'BLOCKER';
  label: string;
  explanation: string;
  evidenceIds: string[];
};

export type CandidateAssessment = {
  candidateId: string;
  gates: { gate: string; passed: boolean; reason: string; evidenceIds: string[] }[];
  suitabilityLevel: SuitabilityLevel;
  suitabilityFactors: SuitabilityFactor[];
  evidenceConfidence: 'HIGH' | 'MEDIUM' | 'LOW';
  recommendationConfidence: 'HIGH' | 'MEDIUM' | 'LOW';
  missingRequirementIds: string[];
  metPreferredRequirementIds: string[];
  unmetPreferredRequirementIds: string[];
  authoritativeSourceKind: 'ZENODO' | 'GITHUB' | 'HUGGING_FACE' | null;
  conflicts: string[];
};

export function candidateSuitabilityLevel(candidate: CandidateAssessment): SuitabilityLevel {
  return candidate.suitabilityLevel;
}

export function candidateSuitabilityLabel(candidate: CandidateAssessment) {
  const level = candidateSuitabilityLevel(candidate).toLowerCase();
  return `${level[0].toUpperCase()}${level.slice(1)} suitability`;
}

export function candidateSuitabilityFactors(candidate: CandidateAssessment): SuitabilityFactor[] {
  return candidate.suitabilityFactors;
}

export type SourcingManifest = {
  runId: string;
  candidateId: string;
  name: string;
  canonicalUrl: string;
  revision: string | null;
  licenseId: string;
  labels: string[];
  sampleRateHz: number | null;
  fileExtensions: string[];
  totalSizeBytes: number | null;
  evidenceIds: string[];
  limitations: string[];
  approvedAt: string;
};

export type SourcingReview =
  | { decision: 'APPROVE'; candidateId: string; note?: string }
  | { decision: 'REJECT'; candidateId?: string; note: string };

export type RefinementOutcome = {
  iteration: number;
  feedback: string;
  query: string;
  outcome: 'RECOMMENDATION_CHANGED' | 'RECOMMENDATION_WITHHELD' | 'EVIDENCE_EXPANDED' | 'CANDIDATES_ADDED' | 'NO_CHANGE';
  rejectedCandidateId: string | null;
  previousRecommendedCandidateId: string | null;
  recommendedCandidateId: string | null;
  newCandidateIds: string[];
  newEvidenceIds: string[];
};

export type SourcingRun = {
  runId: string;
  status: SourcingStatus;
  brief: string;
  constraints: SourcingConstraints;
  requirements: ResearchRequirement[];
  requirementsConfirmed?: boolean;
  hypotheses: { id: string; rationale: string; query: string; status: 'PLANNED' | 'SEARCHED' | 'EXHAUSTED'; isGapQuery: boolean }[];
  candidates: DatasetCandidate[];
  profiles: DatasetProfile[];
  evidence: EvidenceRecord[];
  assessments: CandidateAssessment[];
  recommendedCandidateId: string | null;
  approvedCandidateId: string | null;
  approvedCandidateIds?: string[];
  excludedCandidateIds: string[];
  reviewFeedback: string[];
  reviewIterationsUsed: number;
  feedbackAllowed?: boolean;
  refinementOutcomes: RefinementOutcome[];
  familyQueriesUsed: number;
  gapQueriesUsed: number;
  tavilyCreditsUsed: number;
  executionMode: 'LIVE' | 'CACHED' | 'PARTIAL';
  errors: string[];
  reportMarkdown: string;
  manifest: SourcingManifest | null;
  createdAt: string;
  updatedAt: string;
};

export function candidateIsEligibleForApproval(
  candidate: CandidateAssessment,
  excludedCandidateIds: ReadonlySet<string>,
) {
  return !excludedCandidateIds.has(candidate.candidateId)
    && candidate.suitabilityLevel !== 'LOW'
    && candidate.missingRequirementIds.length === 0
    && candidate.gates.every(gate => gate.passed);
}

export function approvedCandidateIds(run: SourcingRun) {
  return run.approvedCandidateIds
    ?? (run.approvedCandidateId ? [run.approvedCandidateId] : []);
}

export function compareCandidateAssessments(
  left: CandidateAssessment,
  right: CandidateAssessment,
) {
  const identityMismatch = (candidate: CandidateAssessment) => {
    const gate = candidate.gates.find(item => item.gate === 'dataset_identity');
    return gate === undefined || !gate.passed;
  };
  const identityDifference = Number(identityMismatch(left)) - Number(identityMismatch(right));
  if (identityDifference !== 0) return identityDifference;
  const domainMismatch = (candidate: CandidateAssessment) =>
    candidate.gates.some(gate => gate.gate === 'domain' && !gate.passed);
  const domainDifference = Number(domainMismatch(left)) - Number(domainMismatch(right));
  if (domainDifference !== 0) return domainDifference;
  const levelRank = { HIGH: 0, MEDIUM: 1, LOW: 2 } as const;
  const confidenceRank = { HIGH: 0, MEDIUM: 1, LOW: 2 } as const;
  const sourceRank = { ZENODO: 0, HUGGING_FACE: 1, GITHUB: 2 } as const;
  return levelRank[candidateSuitabilityLevel(left)]
    - levelRank[candidateSuitabilityLevel(right)]
    || left.unmetPreferredRequirementIds.length - right.unmetPreferredRequirementIds.length
    || confidenceRank[left.evidenceConfidence] - confidenceRank[right.evidenceConfidence]
    || (left.authoritativeSourceKind === null ? 3 : sourceRank[left.authoritativeSourceKind])
      - (right.authoritativeSourceKind === null ? 3 : sourceRank[right.authoritativeSourceKind])
    || left.candidateId.localeCompare(right.candidateId);
}

export type RunAccepted = { runId: string; status: SourcingStatus; statusUrl: string };

const statuses = new Set<SourcingStatus>([
  'QUEUED', 'PLANNING', 'DISCOVERING', 'VERIFYING', 'ASSESSING',
  'AWAITING_APPROVAL', 'APPROVED', 'REJECTED', 'NEEDS_INPUT', 'FAILED',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNativeSourceUrl(value: unknown) {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['github.com', 'zenodo.org', 'huggingface.co'].includes(url.hostname);
  }
  catch { return false; }
}

function isRequirementDefinition(value: unknown): value is RequirementDefinition {
  return isRecord(value) && typeof value.id === 'string' && typeof value.label === 'string'
    && typeof value.description === 'string' && ['MUST', 'SHOULD'].includes(value.priority as string)
    && ['DOMAIN', 'TASK_LABELS', 'SAMPLING_RATE', 'MODALITY', 'LICENSE', 'PROVENANCE', 'SCHEMA', 'ACQUISITION', 'OTHER'].includes(value.category as string)
    && Array.isArray(value.expectedValues)
    && value.expectedValues.every(item => typeof item === 'string')
    && (value.isSystemRequired === undefined || typeof value.isSystemRequired === 'boolean');
}

export function isRequirementsPreview(value: unknown): value is RequirementsPreview {
  return isRecord(value) && Array.isArray(value.requirements)
    && value.requirements.every(isRequirementDefinition);
}

function isResearchRequirement(value: unknown): value is ResearchRequirement {
  if (!isRequirementDefinition(value)) return false;
  const record = value as unknown as Record<string, unknown>;
  return ['VERIFIED', 'MISSING', 'CONFLICTING', 'UNVERIFIED'].includes(record.status as string)
    && Array.isArray(record.evidenceIds);
}

export function isRunAccepted(value: unknown): value is RunAccepted {
  return isRecord(value) && typeof value.runId === 'string'
    && statuses.has(value.status as SourcingStatus) && typeof value.statusUrl === 'string';
}

export function isSourcingManifest(value: unknown): value is SourcingManifest {
  return isRecord(value) && typeof value.runId === 'string' && typeof value.candidateId === 'string'
    && typeof value.name === 'string' && isNativeSourceUrl(value.canonicalUrl)
    && typeof value.licenseId === 'string' && Array.isArray(value.labels)
    && Array.isArray(value.fileExtensions) && Array.isArray(value.evidenceIds)
    && Array.isArray(value.limitations) && typeof value.approvedAt === 'string';
}

export function isSourcingRun(value: unknown): value is SourcingRun {
  if (!isRecord(value)) return false;
  const run = value;
  return typeof run.runId === 'string' && statuses.has(run.status as SourcingStatus)
    && ['LIVE', 'CACHED', 'PARTIAL'].includes(run.executionMode as string)
    && typeof run.brief === 'string' && Array.isArray(run.requirements)
    && run.requirements.every(isResearchRequirement)
    && (run.requirementsConfirmed === undefined || typeof run.requirementsConfirmed === 'boolean')
    && Array.isArray(run.candidates) && Array.isArray(run.profiles)
    && run.candidates.every(item => isRecord(item) && typeof item.id === 'string'
      && typeof item.name === 'string' && isNativeSourceUrl(item.canonicalUrl)
      && (item.sourceRole === undefined
        || ['DISCOVERY_LEAD', 'DATASET_ARTIFACT'].includes(item.sourceRole as string))
      && (item.discoveryDepth === undefined
        || (Number.isInteger(item.discoveryDepth) && (item.discoveryDepth as number) >= 0
          && (item.discoveryDepth as number) <= 2))
      && (item.discoveredFromCandidateId === undefined
        || item.discoveredFromCandidateId === null
        || typeof item.discoveredFromCandidateId === 'string'))
    && run.profiles.every(item => isRecord(item) && typeof item.candidateId === 'string'
      && isNativeSourceUrl(item.canonicalUrl) && Array.isArray(item.domains)
      && item.domains.every(domain => typeof domain === 'string') && Array.isArray(item.labels)
      && (item.isDatasetArtifact === undefined || typeof item.isDatasetArtifact === 'boolean')
      && (item.datasetIdentityReason === undefined
        || typeof item.datasetIdentityReason === 'string'))
    && Array.isArray(run.evidence) && Array.isArray(run.assessments)
    && run.evidence.every(item => isRecord(item) && typeof item.id === 'string' && isNativeSourceUrl(item.sourceUrl))
    && run.assessments.every(item => isRecord(item) && typeof item.candidateId === 'string'
      && Array.isArray(item.gates)
      && item.gates.every(gate => isRecord(gate) && typeof gate.gate === 'string'
        && typeof gate.passed === 'boolean' && typeof gate.reason === 'string' && Array.isArray(gate.evidenceIds))
      && ['HIGH', 'MEDIUM', 'LOW'].includes(item.suitabilityLevel as string)
      && (Array.isArray(item.suitabilityFactors)
        && item.suitabilityFactors.every(factor => isRecord(factor)
          && ['STRENGTH', 'LIMITATION', 'BLOCKER'].includes(factor.kind as string)
          && typeof factor.label === 'string' && typeof factor.explanation === 'string'
          && Array.isArray(factor.evidenceIds)
          && factor.evidenceIds.every(id => typeof id === 'string'))
        && item.suitabilityFactors.length > 0)
      && ['HIGH', 'MEDIUM', 'LOW'].includes(item.evidenceConfidence as string)
      && ['HIGH', 'MEDIUM', 'LOW'].includes(item.recommendationConfidence as string)
      && Array.isArray(item.conflicts) && Array.isArray(item.missingRequirementIds)
      && Array.isArray(item.metPreferredRequirementIds)
      && Array.isArray(item.unmetPreferredRequirementIds)
      && (item.authoritativeSourceKind === null
        || ['ZENODO', 'GITHUB', 'HUGGING_FACE'].includes(item.authoritativeSourceKind as string)))
    && (run.approvedCandidateId === null || typeof run.approvedCandidateId === 'string')
    && (run.approvedCandidateIds === undefined
      || (Array.isArray(run.approvedCandidateIds)
        && run.approvedCandidateIds.every(item => typeof item === 'string')))
    && Array.isArray(run.excludedCandidateIds) && run.excludedCandidateIds.length <= 2
    && run.excludedCandidateIds.every(item => typeof item === 'string')
    && Array.isArray(run.reviewFeedback) && run.reviewFeedback.length <= 2
    && run.reviewFeedback.every(item => typeof item === 'string')
    && Number.isInteger(run.reviewIterationsUsed) && (run.reviewIterationsUsed as number) >= 0
    && (run.reviewIterationsUsed as number) <= 2
    && (run.feedbackAllowed === undefined || typeof run.feedbackAllowed === 'boolean')
    && Array.isArray(run.refinementOutcomes) && run.refinementOutcomes.length <= 2
    && run.refinementOutcomes.every(item => isRecord(item)
      && Number.isInteger(item.iteration) && (item.iteration as number) >= 1
      && (item.iteration as number) <= 2 && typeof item.feedback === 'string'
      && typeof item.query === 'string'
      && ['RECOMMENDATION_CHANGED', 'RECOMMENDATION_WITHHELD', 'EVIDENCE_EXPANDED', 'CANDIDATES_ADDED', 'NO_CHANGE'].includes(item.outcome as string)
      && (item.rejectedCandidateId === null || typeof item.rejectedCandidateId === 'string')
      && (item.previousRecommendedCandidateId === null || typeof item.previousRecommendedCandidateId === 'string')
      && (item.recommendedCandidateId === null || typeof item.recommendedCandidateId === 'string')
      && Array.isArray(item.newCandidateIds) && item.newCandidateIds.every(id => typeof id === 'string')
      && Array.isArray(item.newEvidenceIds) && item.newEvidenceIds.every(id => typeof id === 'string'))
    && Array.isArray(run.errors) && typeof run.reportMarkdown === 'string'
    && (run.manifest === null || isSourcingManifest(run.manifest));
}

export const sourcingIsActive = (status: SourcingStatus) =>
  ['QUEUED', 'PLANNING', 'DISCOVERING', 'VERIFYING', 'ASSESSING'].includes(status);
