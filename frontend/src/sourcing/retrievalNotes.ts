import type { SourcingRun } from '../services';

export function friendlyRetrievalNote(note: string, run: SourcingRun) {
  const match = note.match(/^Candidate (ds_[a-f0-9]+) verification failed: ([\s\S]*)$/);
  if (!match) return note.split('\n', 1)[0];
  const candidate = run.candidates.find(item => item.id === match[1]);
  const name = candidate?.name ?? match[1];
  const reason = match[2];
  if (reason.includes('response exceeded the configured size limit')) {
    return `${name}: verification hit the previous metadata-size policy. Rerun to retain primary evidence while safely skipping an oversized repository tree.`;
  }
  if (reason.includes('301 Moved Permanently')) {
    return `${name}: GitHub moved a linked repository. Rerun to follow its validated canonical URL.`;
  }
  if (reason.includes('404 Not Found') && reason.includes('/readme')) {
    return `${name}: GitHub has no standard README. Rerun to verify the remaining repository metadata.`;
  }
  if (reason.includes('403')) {
    return `${name}: GitHub denied the verification request or its API rate limit was reached.`;
  }
  return `${name}: ${reason.split('\n', 1)[0]}`;
}
