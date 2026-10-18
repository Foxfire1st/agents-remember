// Synthetic current-shape outcome answers, not captured API output.
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewPayload } from '../../data/review';
import captured from './subjectReview.invariant.captured.json';
import { knownEmpty, readFrom, ReviewOutcomeRegion, shownPayload } from './ReviewOutcome';

function syntheticEmpty(): ReviewPayload {
  return {
    surface_version: captured.payload.surface_version,
    candidate: captured.payload.candidate,
    knowledge: {
      invariant_ids: [],
      family_ids: [],
      before_conditions: [],
      after_conditions: [],
      before_statement: { state: 'absent', language: 'text', detail: 'Synthetic no subject' },
      after_statement: { state: 'absent', language: 'text', detail: 'Synthetic no subject' },
      revision_groups: [],
      field_changes: [],
      unresolved: [],
      selection_state: 'task_context',
    },
    source: {
      inventory: {
        state: 'measured',
        entries: [],
        listed_total: 0,
        detail: 'Synthetic zero paths',
        partial: false,
        command: 'synthetic read',
        unrepresentable_paths: [],
      },
      locations: [],
      remaining: [],
      unattributed_changed_paths: [],
      attributed_changed_paths: [],
      unresolved: [],
    },
    judgment: { state: 'none', detail: 'Synthetic no judgment rows', rows: [], proofs: [] },
    staleness: { state: 'current', statement: 'Synthetic current pair', moved: [] },
    submission: {
      state: 'unavailable',
      reason: 'Read-only review',
      next_action: 'Use publication authority',
      proposed_dispositions: [],
      none_is_approval: false,
    },
    limitations: [],
  };
}

afterEach(cleanup);

it('claims known empty only for an answered empty judgment and proofs with measured zero source paths', () => {
  const payload = syntheticEmpty();
  expect(knownEmpty(payload)).toBe(true);
  expect(
    readFrom({ state: 'review', operation: 'synthetic read', repository_id: 'synthetic', payload })
      .phase,
  ).toBe('reviewed');
  const view = render(
    <ReviewOutcomeRegion
      read={{ phase: 'reviewed', payload }}
      instead={null}
      shown={payload}
      lastCoherent={null}
    />,
  );
  expect(view.getByTestId('review-known-empty').textContent).toContain('no judgment or proofs');
  expect(view.container.textContent).not.toMatch(/assessment|evidence claim|detection/i);
  payload.judgment.state = 'unavailable';
  expect(knownEmpty(payload)).toBe(false);
  payload.judgment.state = 'rows';
  expect(knownEmpty(payload)).toBe(false);
  payload.judgment.state = 'none';
  payload.judgment.proofs = [
    {
      id: 'PRF-SYNTHETIC',
      invariant: 'INV-SYNTHETIC',
      path: 'test.py',
      facet: 'Synthetic proof facet',
      state: 'unverifiable',
      detail: 'Synthetic unresolved proof',
    },
  ];
  expect(knownEmpty(payload)).toBe(false);
  payload.judgment.proofs = [];
  payload.source.inventory.state = 'unavailable';
  expect(knownEmpty(payload)).toBe(false);
  payload.source.inventory.state = 'measured';
  payload.source.inventory.listed_total = 1;
  expect(knownEmpty(payload)).toBe(false);
  payload.source.inventory.listed_total = 0;
  payload.comparison = {
    reference: 'synthetic-comparison',
    policy_version: 'synthetic',
    binding_digest: 'synthetic-digest',
    knowledge_compared: true,
  };
  expect(knownEmpty(payload)).toBe(false);
});

it('reports a missing required judgment as a visible failed read rather than known empty', () => {
  const payload = syntheticEmpty();
  Reflect.deleteProperty(payload, 'judgment');
  expect(knownEmpty(payload)).toBe(false);
  const read = readFrom({
    state: 'review',
    operation: 'synthetic read',
    repository_id: 'synthetic',
    payload,
  });
  expect(read.phase).toBe('failed');
  const view = render(
    <ReviewOutcomeRegion
      read={read}
      instead={null}
      shown={shownPayload(read, null)}
      lastCoherent={null}
    />,
  );
  expect(view.getByTestId('review-failure').textContent).toContain('did not supply judgment');
  expect(view.queryByTestId('review-known-empty')).toBeNull();
});

it('keeps retained empty answers distinct from unavailable judgment after a failed refresh', () => {
  const payload = syntheticEmpty();
  const read = {
    phase: 'failed' as const,
    problem: {
      token: 'network' as const,
      code: 'synthetic-network-failure',
      detail: 'Synthetic connection failed',
    },
  };
  const view = render(
    <ReviewOutcomeRegion
      read={read}
      instead={null}
      shown={shownPayload(read, payload)}
      lastCoherent={payload}
    />,
  );
  expect(view.getByTestId('review-failure').textContent).toContain('Synthetic connection failed');
  expect(view.getByTestId('review-known-empty')).toBeTruthy();
  expect(view.queryByTestId('review-retained-generation')).toBeNull();
  payload.judgment = {
    state: 'unavailable',
    detail: 'Synthetic history unreadable',
    rows: [],
    proofs: [],
  };
  view.rerender(
    <ReviewOutcomeRegion
      read={read}
      instead={null}
      shown={shownPayload(read, payload)}
      lastCoherent={payload}
    />,
  );
  expect(view.queryByTestId('review-known-empty')).toBeNull();
  expect(view.getByTestId('review-retained-generation').textContent).toContain(
    'no empty review is claimed',
  );
});
