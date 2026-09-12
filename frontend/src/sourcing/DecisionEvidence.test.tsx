import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SourcingRun } from '../services';
import ScoutReview from './ScoutReview';

function runFixture(): SourcingRun {
  return {
    runId: '82e19aab-d94d-4f24-925c-46df124399b9',
    status: 'AWAITING_APPROVAL',
    brief: 'Find robot collision data.',
    constraints: { mustHave: [], preferred: [], allowedLicenses: [], maxDownloadBytes: 1_000 },
    requirements: [{
      id: 'req_license', label: 'Explicit licence', description: 'Reusable licence',
      priority: 'MUST', category: 'LICENSE', expectedValues: ['cc-by-4.0'], status: 'VERIFIED',
      evidenceIds: ['ev_license', 'ev_other_license'],
    }],
    hypotheses: [],
    candidates: [{
      id: 'ds_9be731e6fb6b', name: 'Robot joint torque measurements',
      canonicalUrl: 'https://zenodo.org/records/6461868', sourceKind: 'ZENODO',
      description: '', revision: null, relatedUrls: [],
    }],
    profiles: [{
      candidateId: 'ds_9be731e6fb6b', name: 'Robot joint torque measurements',
      canonicalUrl: 'https://zenodo.org/records/6461868', sourceKinds: ['ZENODO'],
      revision: '6461868.r6', licenseId: 'cc-by-4.0', fileCount: 22, totalSizeBytes: 500,
      fileExtensions: ['.csv'], labels: ['collision', 'contact', 'free'], sampleRateHz: 1_000,
      channelCount: 7, hasTimeSeriesFiles: true, schemaDocumented: true,
      acquisitionFeasible: true,
    }],
    evidence: [
      {
        id: 'ev_license', candidateId: 'ds_9be731e6fb6b', requirementId: null,
        claimKey: 'license', observedValue: 'cc-by-4.0',
        sourceUrl: 'https://zenodo.org/records/6461868', sourceKind: 'ZENODO',
        status: 'VERIFIED', precedence: 100, retrievedAt: '2026-09-12T12:00:00Z', note: null,
      },
      {
        id: 'ev_other_license', candidateId: 'ds_000000000000', requirementId: null,
        claimKey: 'license', observedValue: 'MIT',
        sourceUrl: 'https://github.com/other/rejected', sourceKind: 'GITHUB',
        status: 'VERIFIED', precedence: 70, retrievedAt: '2026-09-12T12:00:00Z', note: null,
      },
    ],
    assessments: [{
      candidateId: 'ds_9be731e6fb6b',
      gates: [{ gate: 'license', passed: true, reason: 'Explicit allowed licence found', evidenceIds: ['ev_license'] }],
      score: { taskFit: 35, trainingReadiness: 20, acquisitionIntegrity: 15, provenanceDocumentation: 10, integrationReadiness: 10, licenseClarity: 5, evidenceConsistency: 5 },
      totalScore: 100, tier: 'RECOMMEND', evidenceConfidence: 'HIGH',
      recommendationConfidence: 'HIGH', missingRequirementIds: [], conflicts: [],
    }],
    recommendedCandidateId: 'ds_9be731e6fb6b', gapQueriesUsed: 0, tavilyCreditsUsed: 6,
    approvedCandidateId: null, excludedCandidateIds: [], reviewFeedback: [], reviewIterationsUsed: 0,
    refinementOutcomes: [],
    executionMode: 'LIVE', errors: [],
    reportMarkdown: '# Dataset sourcing report — raw markdown', manifest: null,
    createdAt: '2026-09-12T12:00:00Z', updatedAt: '2026-09-12T12:01:00Z',
  };
}

describe('decision evidence', () => {
  it('renders decision reasons and native evidence instead of raw Markdown', () => {
    const markup = renderToStaticMarkup(<ScoutReview
      run={runFixture()} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Decision evidence');
    expect(markup).toContain('Recommendation: Robot joint torque measurements');
    expect(markup).toContain('All mandatory gates passed');
    expect(markup).toContain('ZENODO: cc-by-4.0');
    expect(markup).not.toContain('GITHUB: MIT');
    expect(markup).not.toContain('# Dataset sourcing report');
  });

  it('turns historical transport errors into actionable source coverage notes', () => {
    const run = runFixture();
    run.errors = [
      "Candidate ds_9be731e6fb6b verification failed: Redirect response '301 Moved Permanently' for url 'https://api.github.com/repos/old/repo'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/301",
    ];

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Source coverage notes');
    expect(markup).toContain('Rerun to follow its validated canonical URL.');
    expect(markup).not.toContain('developer.mozilla.org');
  });

  it('offers eligible datasets and requires feedback for another research cycle', () => {
    const run = runFixture();
    run.candidates.push({
      ...run.candidates[0],
      id: 'ds_aaaaaaaaaaaa',
      name: 'Alternate collision dataset',
      canonicalUrl: 'https://zenodo.org/records/1234',
    });
    run.assessments.push({
      ...run.assessments[0],
      candidateId: 'ds_aaaaaaaaaaaa',
      totalScore: 95,
      score: { ...run.assessments[0].score, evidenceConsistency: 0 },
    });

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Dataset to approve');
    expect(markup).toContain('Alternate collision dataset');
    expect(markup).toContain('value="ds_aaaaaaaaaaaa"');
    expect(markup).toContain('What should the scout improve?');
    expect(markup).toContain('Reject and refine');
  });

  it('explains when refinement found no decision-changing evidence', () => {
    const run = runFixture();
    run.reviewFeedback = ['Prioritize free-motion baselines.'];
    run.reviewIterationsUsed = 1;
    run.refinementOutcomes = [{
      iteration: 1,
      feedback: 'Prioritize free-motion baselines.',
      query: 'robot collision free-motion baseline dataset',
      outcome: 'NO_CHANGE',
      rejectedCandidateId: null,
      previousRecommendedCandidateId: run.recommendedCandidateId,
      recommendedCandidateId: run.recommendedCandidateId,
      newCandidateIds: [],
      newEvidenceIds: [],
    }];

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Refinement 1: no decision change');
    expect(markup).toContain('No new candidates or native evidence were found');
    expect(markup).toContain('Robot joint torque measurements remains the recommendation');
    expect(markup).toContain('robot collision free-motion baseline dataset');
  });

  it('withholds a rejected recommendation instead of recommending it again', () => {
    const run = runFixture();
    run.recommendedCandidateId = null;
    run.excludedCandidateIds = ['ds_9be731e6fb6b'];
    run.reviewFeedback = ["I don't want this dataset. Find an alternative."];
    run.reviewIterationsUsed = 1;
    run.refinementOutcomes = [{
      iteration: 1,
      feedback: run.reviewFeedback[0],
      query: 'robot collision dataset alternative',
      outcome: 'RECOMMENDATION_WITHHELD',
      rejectedCandidateId: 'ds_9be731e6fb6b',
      previousRecommendedCandidateId: 'ds_9be731e6fb6b',
      recommendedCandidateId: null,
      newCandidateIds: [],
      newEvidenceIds: [],
    }];

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Refinement 1: recommendation withheld');
    expect(markup).toContain('was excluded from this run');
    expect(markup).toContain('No eligible alternative was found');
    expect(markup).toContain('Excluded by reviewer');
    expect(markup).toContain('No eligible datasets');
    expect(markup).not.toContain('remains the recommendation');
  });

  it('keeps approval explicit when no candidate clears mandatory gates', () => {
    const run = runFixture();
    run.assessments[0] = {
      ...run.assessments[0],
      tier: 'REJECT',
      totalScore: 60,
      gates: [{ gate: 'license', passed: false, reason: 'No licence evidence', evidenceIds: [] }],
      missingRequirementIds: ['req_license'],
    };

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('No eligible datasets');
    expect(markup).toContain('Only datasets that pass every mandatory gate can be approved.');
  });
});
