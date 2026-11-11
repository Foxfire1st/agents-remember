import type { WaitingSeat } from '../../types/projection';
import { formatLimitTime } from './limitTime';

const WAIT_NAMES: Record<WaitingSeat['waitsFor'][number], string> = {
  subagent: 'subagents',
  background: 'background',
};

/** What a waiting task waits for, in its seats' own words: `subagents`, `background` or both. */
export function waitWhat(seats: WaitingSeat[] | undefined): string {
  const names = new Set((seats ?? []).flatMap((seat) => seat.waitsFor.map((name) => WAIT_NAMES[name])));
  return [...names].join(' and ');
}

/** One line per waiting seat: role and agent, what it waits for since when, the check due, its words. */
export function waitLines(seats: WaitingSeat[] | undefined): string[] {
  return (seats ?? []).map((seat) =>
    [
      seat.role ?? 'seat',
      seat.agentId,
      `waits for ${waitWhat([seat])} since ${formatLimitTime(seat.since)}`,
      seat.checkDueAt ? `check due ${formatLimitTime(seat.checkDueAt)}` : '',
      seat.detail ?? '',
    ]
      .filter(Boolean)
      .join(' · '),
  );
}
