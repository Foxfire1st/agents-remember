import { expect, it } from 'vitest';
import { laneCountText } from './laneFocus';

it('calls the attribution count changed files without an invariant', () => {
  expect(laneCountText('unattributed_changed_paths', {
    state: 'read', partial: false, unexplained: ['a.py'], unknown: [],
  })).toBe('changed files without an invariant: 1');
});

it('names an unmeasured change set rather than a failed lane', () => {
  expect(laneCountText('unattributed_changed_paths', { state: 'unknown' }))
    .toBe('changed files without an invariant: the change set could not be measured');
});
