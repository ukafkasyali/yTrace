export type SourceKind = 'ZENODO' | 'GITHUB' | 'HUGGING_FACE';

export type ApprovedSource = {
  approvedSourceId: string;
  name: string;
  canonicalUrl: string;
  sourceKind: SourceKind;
  sourceRevision: string;
  licenseId: string;
  datasetLicenseId: string | null;
  codeLicenseId: string | null;
  labels: string[];
  fileExtensions: string[];
  totalSizeBytes: number | null;
  isAcquisitionReady: boolean;
  approvalCount: number;
  latestManifestSha256: string;
  createdAt: string;
  latestApprovedAt: string;
};

export type ApprovalEvent = {
  sourcingRunId: string;
  candidateId: string;
  manifestSha256: string;
  approvedAt: string;
};

export type ApprovedSourceDetail = ApprovedSource & { approvals: ApprovalEvent[] };
export type ApprovedAsset = {
  assetId: string; name: string; role: 'DATA' | 'DOCUMENTATION' | 'CHECKSUM'; sizeBytes: number;
};
export type ApprovedManifest = {
  schemaVersion: '1.1'; assets: ApprovedAsset[]; limitations: string[];
};
export type ApprovedSourcePage = {
  data: ApprovedSource[];
  pagination: { page: number; pageSize: number; totalItems: number; totalPages: number };
};

export type ImportState = 'queued' | 'acquiring' | 'inspecting' | 'mapping'
  | 'validating' | 'importing' | 'ready' | 'unsupported_format' | 'needs_input' | 'failed';

export type ImportJob = {
  ingestionId: string;
  approvedSourceId: string;
  manifestSha256: string;
  sourceUrl: string;
  sourceKind: string;
  sourceRevision: string;
  datasetLicenseId: string | null;
  assetIds: string[];
  jobRevision: number;
  state: ImportState;
  message: string;
  createdAt: string;
  updatedAt: string;
};

export type AssetReceipt = {
  ingestionId: string;
  assetId: string;
  providerLocator: string;
  expectedSizeBytes: number;
  sourceChecksumAlgorithm: string | null;
  sourceChecksumValue: string | null;
  observedSizeBytes: number;
  contentSha256: string;
  contentKey: string;
  acquiredAt: string;
};

export type MappingChannel = { selector: string; name: string; unit: string | null };
export type MappingSpec = {
  schemaVersion: '1.0';
  jobRevision: number;
  resourceId: string;
  resourceSha256: string;
  layout: 'WIDE_TABLE' | 'LONG_TABLE' | 'NAMED_ARRAYS';
  recordSelector: string | null;
  timeSelector: string;
  channelSelector: string | null;
  valueSelector: string | null;
  signalSelector: string | null;
  sampleAxis: number | null;
  channelAxis: number | null;
  channels: MappingChannel[];
  annotations: string[];
};
export type MappingProposal = {
  resourceId: string;
  candidates: MappingSpec[];
  issues: string[];
  requiresConfirmation: boolean;
};

export type FinalReceipt = {
  receiptSha256: string;
  ingestionId: string;
  approvedSourceId: string;
  output: { datasetId: string; datasetVersion: string; registryKey: string };
  validation: {
    status: string; recordCount: number; seriesCount: number; valueCount: number;
    readbackSha256: string;
  };
};

export type ImportedRecord = {
  recordId: string;
  seriesCount: number;
  valueCount: number;
  durationSeconds: number | null;
  signals: string[];
  annotationKeys: string[];
};

export type ImportedRecordPage = {
  datasetId: string;
  datasetVersion: string;
  data: ImportedRecord[];
  pagination: { page: number; pageSize: number; totalItems: number; totalPages: number };
};

export type ImportedDatasetSelection = {
  ingestionId: string;
  datasetId: string;
  datasetVersion: string;
};

const sourceKinds = new Set<SourceKind>(['ZENODO', 'GITHUB', 'HUGGING_FACE']);
const importStates = new Set<ImportState>([
  'queued', 'acquiring', 'inspecting', 'mapping', 'validating', 'importing',
  'ready', 'unsupported_format', 'needs_input', 'failed',
]);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string');
}

function isNativeUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:'
      && ['github.com', 'zenodo.org', 'huggingface.co'].includes(url.hostname);
  } catch { return false; }
}

export function isApprovedSource(value: unknown): value is ApprovedSource {
  if (!isRecord(value)) return false;
  return /^src_[a-f0-9]{24}$/.test(String(value.approvedSourceId))
    && typeof value.name === 'string' && isNativeUrl(value.canonicalUrl)
    && sourceKinds.has(value.sourceKind as SourceKind)
    && typeof value.sourceRevision === 'string' && typeof value.licenseId === 'string'
    && (value.datasetLicenseId === null || typeof value.datasetLicenseId === 'string')
    && (value.codeLicenseId === null || typeof value.codeLicenseId === 'string')
    && isStringArray(value.labels) && isStringArray(value.fileExtensions)
    && (value.totalSizeBytes === null || (Number.isInteger(value.totalSizeBytes) && Number(value.totalSizeBytes) >= 0))
    && typeof value.isAcquisitionReady === 'boolean'
    && Number.isInteger(value.approvalCount) && Number(value.approvalCount) >= 1
    && /^[a-f0-9]{64}$/.test(String(value.latestManifestSha256))
    && typeof value.createdAt === 'string' && typeof value.latestApprovedAt === 'string';
}

export function isApprovedSourcePage(value: unknown): value is ApprovedSourcePage {
  if (!isRecord(value) || !Array.isArray(value.data) || !value.data.every(isApprovedSource)
    || !isRecord(value.pagination)) return false;
  const pagination = value.pagination;
  return ['page', 'pageSize', 'totalItems', 'totalPages'].every(
    key => Number.isInteger(pagination[key]) && Number(pagination[key]) >= 0,
  ) && Number(pagination.page) >= 1 && Number(pagination.pageSize) >= 1;
}

export function isApprovedSourceDetail(value: unknown): value is ApprovedSourceDetail {
  if (!isApprovedSource(value)) return false;
  const approvals = (value as unknown as Record<string, unknown>).approvals;
  return Array.isArray(approvals)
    && approvals.every((item: unknown) => isRecord(item)
      && typeof item.sourcingRunId === 'string' && typeof item.candidateId === 'string'
      && /^[a-f0-9]{64}$/.test(String(item.manifestSha256))
      && typeof item.approvedAt === 'string');
}

export function isApprovedManifest(value: unknown): value is ApprovedManifest {
  return isRecord(value) && value.schemaVersion === '1.1' && isStringArray(value.limitations)
    && Array.isArray(value.assets) && value.assets.length > 0 && value.assets.every(item =>
      isRecord(item) && /^asset_[a-f0-9]{16}$/.test(String(item.assetId))
      && typeof item.name === 'string' && ['DATA', 'DOCUMENTATION', 'CHECKSUM'].includes(String(item.role))
      && Number.isInteger(item.sizeBytes) && Number(item.sizeBytes) > 0);
}

export function isImportJob(value: unknown): value is ImportJob {
  if (!isRecord(value)) return false;
  return typeof value.ingestionId === 'string'
    && /^src_[a-f0-9]{24}$/.test(String(value.approvedSourceId))
    && /^[a-f0-9]{64}$/.test(String(value.manifestSha256))
    && typeof value.sourceUrl === 'string' && typeof value.sourceKind === 'string'
    && typeof value.sourceRevision === 'string'
    && (value.datasetLicenseId === null || typeof value.datasetLicenseId === 'string')
    && isStringArray(value.assetIds) && Number.isInteger(value.jobRevision)
    && importStates.has(value.state as ImportState) && typeof value.message === 'string'
    && typeof value.createdAt === 'string' && typeof value.updatedAt === 'string';
}

export function isAssetReceipts(value: unknown): value is AssetReceipt[] {
  return Array.isArray(value) && value.every(item => isRecord(item)
    && typeof item.ingestionId === 'string'
    && /^asset_[a-f0-9]{16}$/.test(String(item.assetId))
    && typeof item.providerLocator === 'string'
    && Number.isInteger(item.expectedSizeBytes) && Number(item.expectedSizeBytes) > 0
    && (item.sourceChecksumAlgorithm === null || typeof item.sourceChecksumAlgorithm === 'string')
    && (item.sourceChecksumValue === null || typeof item.sourceChecksumValue === 'string')
    && Number.isInteger(item.observedSizeBytes) && Number(item.observedSizeBytes) > 0
    && /^[a-f0-9]{64}$/.test(String(item.contentSha256))
    && /^sha256\/[a-f0-9]{2}\/[a-f0-9]{64}$/.test(String(item.contentKey))
    && typeof item.acquiredAt === 'string');
}

function isMappingChannel(value: unknown): value is MappingChannel {
  return isRecord(value) && typeof value.selector === 'string' && typeof value.name === 'string'
    && (value.unit === null || typeof value.unit === 'string');
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isNullableInteger(value: unknown): value is number | null {
  return value === null || Number.isInteger(value);
}

export function isMappingSpec(value: unknown): value is MappingSpec {
  if (!isRecord(value)) return false;
  return value.schemaVersion === '1.0' && Number.isInteger(value.jobRevision)
    && typeof value.resourceId === 'string' && /^[a-f0-9]{64}$/.test(String(value.resourceSha256))
    && ['WIDE_TABLE', 'LONG_TABLE', 'NAMED_ARRAYS'].includes(String(value.layout))
    && isNullableString(value.recordSelector) && typeof value.timeSelector === 'string'
    && isNullableString(value.channelSelector) && isNullableString(value.valueSelector)
    && isNullableString(value.signalSelector) && isNullableInteger(value.sampleAxis)
    && isNullableInteger(value.channelAxis) && Array.isArray(value.channels)
    && value.channels.length > 0 && value.channels.every(isMappingChannel)
    && isStringArray(value.annotations);
}

export function isMappingProposals(value: unknown): value is MappingProposal[] {
  return Array.isArray(value) && value.every(item => isRecord(item)
    && typeof item.resourceId === 'string' && Array.isArray(item.candidates)
    && item.candidates.every(isMappingSpec) && isStringArray(item.issues)
    && typeof item.requiresConfirmation === 'boolean');
}

export function isFinalReceipt(value: unknown): value is FinalReceipt {
  if (!isRecord(value) || !isRecord(value.output) || !isRecord(value.validation)) return false;
  const validation = value.validation;
  return /^[a-f0-9]{64}$/.test(String(value.receiptSha256))
    && typeof value.ingestionId === 'string' && typeof value.approvedSourceId === 'string'
    && typeof value.output.datasetId === 'string'
    && typeof value.output.datasetVersion === 'string' && typeof value.output.registryKey === 'string'
    && validation.status === 'passed'
    && ['recordCount', 'seriesCount', 'valueCount'].every(
      key => Number.isInteger(validation[key]) && Number(validation[key]) >= 0,
    ) && /^[a-f0-9]{64}$/.test(String(validation.readbackSha256));
}

export function isImportedRecordPage(value: unknown): value is ImportedRecordPage {
  if (!isRecord(value) || typeof value.datasetId !== 'string'
    || typeof value.datasetVersion !== 'string' || !Array.isArray(value.data)
    || !isRecord(value.pagination)) return false;
  const pagination = value.pagination;
  const validPagination = ['page', 'pageSize', 'totalItems', 'totalPages'].every(
    key => Number.isInteger(pagination[key]) && Number(pagination[key]) >= 0,
  ) && Number(pagination.page) >= 1 && Number(pagination.pageSize) >= 1;
  return validPagination && value.data.every(item => isRecord(item)
    && typeof item.recordId === 'string'
    && Number.isInteger(item.seriesCount) && Number(item.seriesCount) >= 0
    && Number.isInteger(item.valueCount) && Number(item.valueCount) >= 0
    && (item.durationSeconds === null
      || (typeof item.durationSeconds === 'number' && Number.isFinite(item.durationSeconds)
        && item.durationSeconds >= 0))
    && isStringArray(item.signals) && isStringArray(item.annotationKeys));
}
