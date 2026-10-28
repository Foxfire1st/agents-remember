import { describe, expect, it } from 'vitest';

import type { EvidenceDestination } from './canvasApi';
import { destinationLine } from './canvasModel';

const TARGET = {
  repository: 'agents-remember',
  master: '260928_maintained-invariant-knowledge',
  leaf: '260928-MIK-L104',
};
const COMPARISON = { reference: '1'.repeat(64), number: 7, history: 'live' };
const BOUND = 'review-comparison:260928_maintained-invariant-knowledge/260928-MIK-L104/7';
const OTHER_BOUND = 'review-comparison:260928_maintained-invariant-knowledge/260928-MIK-L104/8';
const context = { ...TARGET, comparison: COMPARISON };
const evidence = (overrides: Partial<EvidenceDestination> = {}): EvidenceDestination => ({
  state: 'opened',
  operation: 'open-evidence',
  reference: 'INV-1',
  kind: 'knowledge-record',
  comparison: BOUND,
  target: { ...TARGET, comparison: BOUND },
  location: { record_id: 'INV-1', record_revision: 3, tree: 'b'.repeat(40) },
  ...overrides,
});

describe('evidence navigation from the current frame', () => {
  it('names the full identity of an exact destination and refuses a foreign one', () => {
    const exact = destinationLine(evidence(), context);
    expect(exact.selector).toEqual({ kind: 'invariant', id: 'INV-1' });
    expect(exact.line).toContain(`tree ${'b'.repeat(40)}`);
    expect(exact.line).toContain('revision 3');
    expect(exact.line).toContain(`comparison ${BOUND}`);

    const foreign = destinationLine(
      evidence({ comparison: OTHER_BOUND, target: { ...TARGET, comparison: OTHER_BOUND } }),
      context,
    );
    expect(foreign.selector).toBeUndefined();
    expect(foreign.line).toContain('belongs to comparison 8');

    const unrepresentable = destinationLine(
      evidence({ location: { tree: 'b'.repeat(40), path: 'src/app.py' } }),
      context,
    );
    expect(unrepresentable.selector).toBeUndefined();
    expect(unrepresentable.line).toContain('does not represent that exact destination');
  });

  it('keeps unavailable, refused, foreign-target and unbound evidence unopened', () => {
    for (const destination of [
      evidence({ state: 'unavailable', detail: 'the bound object is missing' }),
      evidence({ state: 'refused', detail: 'not admitted' }),
      evidence({ target: { ...TARGET, leaf: 'another-leaf' } }),
      evidence({ target: undefined }),
      evidence({ comparison: 'unknown' }),
    ]) {
      expect(destinationLine(destination, context).selector).toBeUndefined();
    }
  });
});
