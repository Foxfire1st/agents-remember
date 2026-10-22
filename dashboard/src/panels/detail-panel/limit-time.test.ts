// MIK-R190 rule 13: the exact-reset detail is an exact served instant. It carries its date, its
// seconds, its milliseconds and its time zone, so two resets inside one second are two different
// times to the reader (F28). A served value finer than a millisecond is kept as served.
import { expect, it } from 'vitest';

import { formatLimitTime } from './pageParts';

const EXACT =
  /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{2}:\d{2}:\d{2}\.\d{3} (?:GMT[+-]\d{1,2}(?::\d{2})?|UTC)(?: \(served .+\))?$/;

it('keeps the seconds and milliseconds of a served instant', () => {
  expect(formatLimitTime('2026-10-21T12:00:00+02:00')).toMatch(EXACT);
  expect(formatLimitTime('2026-10-21T12:00:47+02:00')).toContain(':47');
  expect(formatLimitTime('2026-10-21T12:00:47.250+02:00')).toContain('.250');
});

it('never maps two served instants inside one minute to the same time', () => {
  const first = formatLimitTime('2026-10-21T12:00:01+02:00');
  const last = formatLimitTime('2026-10-21T12:00:59+02:00');

  expect(first).not.toBe(last);
  // The seconds are the whole difference: the rest of the instant is identical.
  expect(first.replace(':01', ':SS')).toBe(last.replace(':59', ':SS'));
});

// F28's own counterexample, as the producer serves it: the same second, two instants.
it('separates two served instants inside one second', () => {
  const early = formatLimitTime('2026-10-21T10:00:01.001000Z');
  const late = formatLimitTime('2026-10-21T10:00:01.999000Z');

  expect(early).not.toBe(late);
  expect(early).toContain('.001');
  expect(late).toContain('.999');
  // The trailing zeros of the producer's microseconds are not a difference, so nothing extra shows.
  expect(early).toMatch(EXACT);
  expect(late).toMatch(EXACT);
});

// The no-truncation proviso: precision the producer actually supplies beyond a millisecond is kept.
it('keeps a served remainder finer than a millisecond', () => {
  const first = formatLimitTime('2026-10-21T10:00:01.001500Z');
  const second = formatLimitTime('2026-10-21T10:00:01.001900Z');

  expect(first).not.toBe(second);
  expect(first).toContain('(served 2026-10-21T10:00:01.001500Z)');
  expect(second).toContain('(served 2026-10-21T10:00:01.001900Z)');
});

it('keeps the last second of a day and the first of the next apart', () => {
  const before = formatLimitTime('2026-12-31T23:59:59Z');
  const after = formatLimitTime('2027-01-01T00:00:00Z');

  expect(before).toMatch(EXACT);
  expect(after).toMatch(EXACT);
  expect(before).not.toBe(after);
  expect(before).toContain(':59');
  expect(after).toContain(':00');
});

it('shows the served offset rather than dropping it', () => {
  // The same wall clock on two accounts in different zones is two different instants.
  const plusTwo = formatLimitTime('2026-10-21T12:00:00+02:00');
  const minusFive = formatLimitTime('2026-10-21T12:00:00-05:00');

  expect(plusTwo).not.toBe(minusFive);
  expect(Date.parse('2026-10-21T12:00:00+02:00')).not.toBe(Date.parse('2026-10-21T12:00:00-05:00'));
});

it('shows an unreadable instant as served instead of inventing one', () => {
  expect(formatLimitTime('soon')).toBe('soon');
  expect(formatLimitTime('')).toBe('');
});
