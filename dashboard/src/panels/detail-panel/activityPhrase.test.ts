import { expect, it } from 'vitest';

import { activityPhrase } from './activityPhrase';

it('names works and paused, and leaves waiting out while nothing waits', () => {
  expect(activityPhrase({ works: 6, waiting: 0, paused: 2 })).toBe('6 works · 2 paused');
});

it('puts the waiting count between works and paused while a task waits', () => {
  expect(activityPhrase({ works: 1, waiting: 3, paused: 0 })).toBe('1 works · 3 waiting · 0 paused');
});
