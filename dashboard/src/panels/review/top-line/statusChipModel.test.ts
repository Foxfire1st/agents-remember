// A recorded time as the mockup writes it (ruling 9498d6e4; mockup data `at`, "10-19 13:38"): month,
// day, hours and minutes for a time in the reader's current year, the full date for another year, and
// the raw value when it is not a time. The current date is passed in; nothing here reads the clock.
import { expect, it } from 'vitest';

import { chipFacts, localTime, type ChipInput } from './statusChipModel';

const AT = '2026-10-19T11:38:46+00:00';
const two = (value: number) => String(value).padStart(2, '0');
// The instant's local fields, as the reader's clock shows it.
const at = new Date(AT);
const DAY = `${two(at.getMonth() + 1)}-${two(at.getDate())} ${two(at.getHours())}:${two(at.getMinutes())}`;

it("writes a time of the current year without its year, and another year's with it", () => {
  expect(localTime(AT, new Date(2026, 9, 20, 12, 0))).toBe(DAY);
  expect(localTime(AT, new Date(2027, 0, 2, 9, 0))).toBe(`${at.getFullYear()}-${DAY}`);
  expect(localTime('not a time', new Date(2026, 9, 20))).toBe('not a time');
});

it("gives the status popover's comparison row the short time", () => {
  const input = {
    phase: 'reviewed',
    problem: null,
    payload: null,
    comparison: { number: 8, recorded_at: AT },
    latest: 8,
    now: new Date(2026, 9, 20, 12, 0),
  } as unknown as ChipInput;
  const row = chipFacts(input).rows.find(([name]) => name === 'Bound comparison')!;
  expect(row[1]).toBe(`#8 · recorded ${DAY}`);
});
