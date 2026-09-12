export type SourcingStatus =
  | 'QUEUED' | 'PLANNING' | 'DISCOVERING' | 'VERIFYING' | 'ASSESSING'
  | 'AWAITING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'NEEDS_INPUT' | 'FAILED';

export type SourcingConstraints = {
  mustHave: string[];
  preferred: string[];
  allowedLicenses: string[];
  maxDownloadBytes: number;
};

export type CreateSourcingRun = {
  brief: string;
  constraints?: Partial<SourcingConstraints>;
};

export type ResearchRequirement = {
  id: string;
  label: string;
  description: string;
  priority: 'MUST' | 'SHOULD';
  category: 'TASK_LABELS' | 'SAMPLING_RATE' | 'MODALITY' | 'LICENSE' | 'PROVENANCE' | 'SCHEMA' | 'ACQUISITION' | 'OTHER';
  expectedValues: string[];
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
  labels: string[];
  sampleRateHz: number | null;
  channelCount: number | null;
  hasTimeSeriesFiles: boolean;
  schemaDocumented: boolean;
  acquisitionFeasible: boolean;
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

export type CandidateAssessment = {
  candidateId: string;
  gates: { gate: string; passed: boolean; reason: string; evidenceIds: string[] }[];
  score: {
    taskFit: number;
    trainingReadiness: number;
    acquisitionIntegrity: number;
    provenanceDocumentation: number;
    integrationReadiness: number;
    licenseClarity: number;
    evidenceConsistency: number;
  };
  totalScore: number;
  tier: 'RECOMMEND' | 'SHORTLIST' | 'REJECT';
  evidenceConfidence: 'HIGH' | 'MEDIUM' | 'LOW';
  recommendationConfidence: 'HIGH' | 'MEDIUM' | 'LOW';
  missingRequirementIds: string[];
  conflicts: string[];
};

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

export type SourcingRun = {
  runId: string;
  status: SourcingStatus;
  brief: string;
  constraints: SourcingConstraints;
  requirements: ResearchRequirement[];
  hypotheses: { id: string; rationale: string; query: string; status: 'PLANNED' | 'SEARCHED' | 'EXHAUSTED'; isGapQuery: boolean }[];
  candidates: DatasetCandidate[];
  profiles: DatasetProfile[];
  evidence: EvidenceRecord[];
  assessments: CandidateAssessment[];
  recommendedCandidateId: string | null;
  gapQueriesUsed: number;
  tavilyCreditsUsed: number;
  executionMode: 'LIVE' | 'CACHED' | 'PARTIAL';
  errors: string[];
  reportMarkdown: string;
  manifest: SourcingManifest | null;
  createdAt: string;
  updatedAt: string;
};

export type RunAccepted = { runId: string; status: SourcingStatus; statusUrl: string };

const statuses = new Set<SourcingStatus>([
  'QUEUED', 'PLANNING', 'DISCOVERING', 'VERIFYING', 'ASSESSING',
  'AWAITING_APPROVAL', 'APPROVED', 'REJECTED', 'NEEDS_INPUT', 'FAILED',
]);

export function isSourcingRun(value: unknown): value is SourcingRun {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const run = value as Record<string, unknown>;
  return typeof run.runId === 'string' && statuses.has(run.status as SourcingStatus)
    && typeof run.brief === 'string' && Array.isArray(run.requirements)
    && Array.isArray(run.candidates) && Array.isArray(run.profiles)
    && Array.isArray(run.evidence) && Array.isArray(run.assessments)
    && Array.isArray(run.errors) && typeof run.reportMarkdown === 'string';
}

export const sourcingIsActive = (status: SourcingStatus) =>
  ['QUEUED', 'PLANNING', 'DISCOVERING', 'VERIFYING', 'ASSESSING'].includes(status);
