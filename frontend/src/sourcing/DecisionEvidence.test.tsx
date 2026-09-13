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
      description: '', revision: null, relatedUrls: [], sourceRole: 'DATASET_ARTIFACT',
      discoveryDepth: 0, discoveredFromCandidateId: null,
    }],
    profiles: [{
      candidateId: 'ds_9be731e6fb6b', name: 'Robot joint torque measurements',
      canonicalUrl: 'https://zenodo.org/records/6461868', sourceKinds: ['ZENODO'],
      revision: '6461868.r6', licenseId: 'cc-by-4.0', fileCount: 22, totalSizeBytes: 500,
      fileExtensions: ['.csv'], domains: ['robot'], labels: ['collision', 'contact', 'free'], sampleRateHz: 1_000,
      channelCount: 7, isDatasetArtifact: true,
      datasetIdentityReason: 'Primary source is a verified dataset artifact',
      hasTimeSeriesFiles: true, schemaDocumented: true,
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
      gates: [
        { gate: 'dataset_identity', passed: true, reason: 'Primary source is a verified dataset artifact', evidenceIds: [] },
        { gate: 'license', passed: true, reason: 'Explicit allowed licence found', evidenceIds: ['ev_license'] },
      ],
      evidenceConfidence: 'HIGH',
      recommendationConfidence: 'HIGH', missingRequirementIds: [], conflicts: [],
      metPreferredRequirementIds: [], unmetPreferredRequirementIds: [],
      authoritativeSourceKind: 'ZENODO',
      suitabilityLevel: 'HIGH', suitabilityFactors: [{
        kind: 'STRENGTH', label: 'Mandatory requirements',
        explanation: 'All mandatory requirements are supported by native evidence.',
        evidenceIds: ['ev_license'],
      }],
    }],
    recommendedCandidateId: 'ds_9be731e6fb6b', familyQueriesUsed: 0,
    gapQueriesUsed: 0, tavilyCreditsUsed: 6,
    approvedCandidateId: null, excludedCandidateIds: [], reviewFeedback: [], reviewIterationsUsed: 0,
    refinementOutcomes: [],
    executionMode: 'LIVE', errors: [],
    reportMarkdown: '# Dataset sourcing report: raw markdown', manifest: null,
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
    expect(markup).toContain('High suitability');
    expect(markup).toContain('Why high suitability');
    expect(markup).toContain('All mandatory requirements are supported by native evidence.');
    expect(markup).toContain('passed every mandatory gate');
    expect(markup).toContain('Dataset ranking');
    expect(markup).toContain('Found value');
    expect(markup).toContain('cc-by-4.0');
    expect(markup).toContain('Zenodo record');
    expect(markup).toContain('Open native source');
    expect(markup).not.toContain('MIT');
    expect(markup).not.toContain('# Dataset sourcing report');
    expect(markup).not.toContain('/100');
  });

  it('keeps discovery guides out of dataset ranking while retaining their audit reason', () => {
    const run = runFixture();
    run.candidates.push({
      ...run.candidates[0], id: 'ds_aaaaaaaaaaaa', name: 'Computer Vision Guide',
      canonicalUrl: 'https://github.com/example/computer-vision-guide', sourceKind: 'GITHUB',
      sourceRole: 'DISCOVERY_LEAD', discoveryDepth: 0,
    });
    run.profiles.push({
      ...run.profiles[0], candidateId: 'ds_aaaaaaaaaaaa', name: 'Computer Vision Guide',
      canonicalUrl: 'https://github.com/example/computer-vision-guide',
      isDatasetArtifact: false, datasetIdentityReason: 'Primary source is a guide',
    });
    run.assessments.push({
      ...run.assessments[0], candidateId: 'ds_aaaaaaaaaaaa',
      suitabilityLevel: 'LOW', suitabilityFactors: [{
        kind: 'BLOCKER', label: 'Dataset identity', explanation: 'Primary source is a guide',
        evidenceIds: [],
      }],
      gates: [{
        gate: 'dataset_identity', passed: false, reason: 'Primary source is a guide',
        evidenceIds: [],
      }],
    });

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    const ranking = markup.slice(markup.indexOf('Dataset ranking'), markup.indexOf('Discovery leads excluded'));
    expect(ranking).not.toContain('Computer Vision Guide');
    expect(markup).toContain('Discovery leads excluded (1)');
    expect(markup).toContain('Primary source is a guide');
  });

  it('explains categorical assessments using their explicit factors', () => {
    const run = runFixture();

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('High suitability');
    expect(markup).toContain('Why high suitability');
    expect(markup).toContain('Primary source is a verified dataset artifact');
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

  it('offers remaining eligible candidates after the first approval', () => {
    const run = runFixture();
    run.status = 'APPROVED';
    run.approvedCandidateId = run.assessments[0].candidateId;
    run.approvedCandidateIds = [run.assessments[0].candidateId];
    run.candidates.push({
      ...run.candidates[0],
      id: 'ds_aaaaaaaaaaaa',
      name: 'Alternate collision dataset',
      canonicalUrl: 'https://zenodo.org/records/1234',
    });
    run.assessments.push({
      ...run.assessments[0],
      candidateId: 'ds_aaaaaaaaaaaa',
    });

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Approve another dataset');
    expect(markup).toContain('1 eligible dataset approved from this run');
    expect(markup).toContain('1 of 2 eligible datasets remain unapproved');
    expect(markup).toContain('value="ds_aaaaaaaaaaaa"');
    expect(markup).not.toContain(`value="${run.approvedCandidateId}"`);
    expect(markup).not.toContain('Reject and refine');
  });

  it('finishes the approval flow when every eligible candidate is approved', () => {
    const run = runFixture();
    const primaryId = run.assessments[0].candidateId;
    run.status = 'APPROVED';
    run.approvedCandidateId = primaryId;
    run.approvedCandidateIds = [primaryId];
    run.manifest = {
      runId: run.runId, candidateId: primaryId,
      name: run.candidates[0].name, canonicalUrl: run.candidates[0].canonicalUrl,
      revision: run.profiles[0].revision, licenseId: run.profiles[0].licenseId ?? '',
      labels: run.profiles[0].labels, sampleRateHz: run.profiles[0].sampleRateHz,
      fileExtensions: run.profiles[0].fileExtensions,
      totalSizeBytes: run.profiles[0].totalSizeBytes,
      evidenceIds: ['ev_license'], limitations: [], approvedAt: run.updatedAt,
    };

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).not.toContain('Approve another dataset');
    expect(markup).toContain('Open approved sources');
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
      suitabilityLevel: 'LOW',
      suitabilityFactors: [{
        kind: 'BLOCKER', label: 'Explicit licence', explanation: 'No licence evidence',
        evidenceIds: [],
      }],
      gates: [{ gate: 'license', passed: false, reason: 'No licence evidence', evidenceIds: [] }],
      missingRequirementIds: ['req_license'],
    };

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('No eligible datasets');
    expect(markup).toContain('Only datasets that pass every mandatory gate can be approved.');
  });

  it('offers a feedback form when a needs-input run can still refine', () => {
    const run = runFixture();
    run.status = 'NEEDS_INPUT';
    run.feedbackAllowed = true;
    run.recommendedCandidateId = null;
    run.requirements[0].status = 'MISSING';
    run.assessments[0].suitabilityLevel = 'LOW';
    run.assessments[0].suitabilityFactors = [{
      kind: 'BLOCKER', label: 'Explicit licence',
      explanation: 'Mandatory requirement is unsupported by native evidence.', evidenceIds: [],
    }];
    run.assessments[0].missingRequirementIds = ['req_license'];

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup).toContain('Help the scout continue');
    expect(markup).toContain('What should the scout search for next?');
    expect(markup).toContain('Refine search');
    expect(markup).not.toContain('Dataset to approve');
  });

  it('ranks a domain match before an unrelated candidate in the same level', () => {
    const run = runFixture();
    run.brief = 'Find CNC machines where the head makes accidental contact.';
    run.recommendedCandidateId = null;
    run.requirements.push({
      id: 'req_domain', label: 'Equipment or application domain',
      description: 'Native sources match the requested domain.', priority: 'MUST',
      category: 'DOMAIN', expectedValues: ['cnc'], status: 'VERIFIED', evidenceIds: ['ev_cnc'],
    });
    run.assessments[0] = {
      ...run.assessments[0],
      suitabilityLevel: 'LOW', suitabilityFactors: [{
        kind: 'BLOCKER', label: 'Equipment or application domain',
        explanation: 'Native sources do not match the requested domain', evidenceIds: [],
      }],
      gates: [{ gate: 'domain', passed: false, reason: 'Native sources do not match the requested domain', evidenceIds: [] }],
      missingRequirementIds: ['req_domain'],
    };
    run.candidates.push({
      ...run.candidates[0], id: 'ds_aaaaaaaaaaaa', name: 'CNC machining process monitoring',
      canonicalUrl: 'https://github.com/boschresearch/CNC_Machining', sourceKind: 'GITHUB',
    });
    run.assessments.push({
      ...run.assessments[0], candidateId: 'ds_aaaaaaaaaaaa',
      authoritativeSourceKind: 'GITHUB', suitabilityLevel: 'LOW', suitabilityFactors: [{
        kind: 'BLOCKER', label: 'Task labels',
        explanation: 'Mandatory requirement is unsupported by native evidence.', evidenceIds: [],
      }, {
        kind: 'BLOCKER', label: 'Schema documentation',
        explanation: 'Mandatory requirement is unsupported by native evidence.', evidenceIds: [],
      }], gates: [{ gate: 'domain', passed: true, reason: 'Native sources match the requested domain', evidenceIds: ['ev_cnc'] }],
      missingRequirementIds: ['req_task_labels', 'req_schema'],
    });

    const markup = renderToStaticMarkup(<ScoutReview
      run={run} busy="" onReview={() => undefined} onUseSource={() => undefined}
    />);

    expect(markup.indexOf('CNC machining process monitoring')).toBeLessThan(
      markup.indexOf('Robot joint torque measurements'),
    );
    expect(markup).toContain('Native sources do not match the requested domain');
  });
});
