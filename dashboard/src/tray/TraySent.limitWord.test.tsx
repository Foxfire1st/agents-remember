import { describe, expect, it } from 'vitest';

import { limitTimeLeft, outcomeSummary, resultLine } from './TraySent';
import type { ChatName } from './traySeats';
import type { TraySendOutcome, TrayTargetResult } from './trayStore';
import type { TrayDelivery } from './types';

// MIK-R190 rule 9 as D399 amends MIK-R188 rule 9, and rule 13's unit (F20): a hold for a provider
// limit names the TIME LEFT in the word, in one compact unit — "3h", never a clock time — and a hold
// for any other reason keeps the plain word.

const name = (agentId: string): ChatName => ({
  label: agentId.toUpperCase(),
  short: agentId,
  tag: null,
  unnamed: null,
});
const HOUR = 3_600_000;
// Anchored on the run's own clock, because outcomeSummary counts down from "now" by design.
const NOW = Date.now();
const IN_THREE_HOURS = new Date(NOW + 3 * HOUR).toISOString();

function outcome(deliveries: Array<Partial<TrayDelivery>>): TraySendOutcome {
  const results: TrayTargetResult[] = deliveries.map((delivery, index) => ({
    agentId: `agent-${index}`,
    delivery: {
      state: 'held',
      messageId: `m-${index}`,
      messageFile: null,
      messageFileSha256: null,
      ...delivery,
    },
  }));
  return { at: new Date().toISOString(), results, pickIds: [], text: 'the message' };
}

describe('limitTimeLeft — rule 13s compact unit', () => {
  it('writes the time left in one unit, not a clock time', () => {
    expect(limitTimeLeft(IN_THREE_HOURS, NOW)).toBe('3h');
    expect(limitTimeLeft(new Date(NOW + 45 * 60_000).toISOString(), NOW)).toBe('45m');
    expect(limitTimeLeft(new Date(NOW + 30_000).toISOString(), NOW)).toBe('30s');
    expect(limitTimeLeft(new Date(NOW + 2 * 24 * HOUR).toISOString(), NOW)).toBe('2d');
  });

  it('keeps a served string it cannot parse, rather than inventing a time', () => {
    expect(limitTimeLeft('not-a-time', NOW)).toBe('not-a-time');
  });
});

describe('outcomeSummary — the tray word for a provider-limit hold', () => {
  it('says how long until a limited recipient may take the message', () => {
    const word = outcomeSummary(
      outcome([{ state: 'held', reason: 'provider-limit', limitedUntil: IN_THREE_HOURS }]),
      name,
    );
    expect(word).toMatch(/^Sent: held · rate limited: \d+[smhd] for AGENT-0\.$/);
    expect(word).not.toMatch(/\d{2}:\d{2}/);
  });

  it('keeps the plain word for a hold that is not a limit', () => {
    const word = outcomeSummary(outcome([{ state: 'held', reason: 'recipient-in-turn' }]), name);
    expect(word).toBe('Sent: held for AGENT-0.');
  });

  it('names the reset time once when several recipients share it', () => {
    const word = outcomeSummary(
      outcome([
        { state: 'held', reason: 'provider-limit', limitedUntil: IN_THREE_HOURS },
        { state: 'held', reason: 'provider-limit', limitedUntil: IN_THREE_HOURS },
      ]),
      name,
    );
    expect(word).toMatch(/^Sent: held · rate limited: \d+[smhd] for 2 chats\.$/);
  });
});

describe('resultLine — the full view names the full word and the exact reset', () => {
  it('says rate limited with the time left and the exact instant', () => {
    const line = resultLine(
      { agentId: 'agent-0', delivery: { state: 'held', messageId: 'm-0', messageFile: null, messageFileSha256: null, reason: 'provider-limit', limitedUntil: IN_THREE_HOURS } },
      name('agent-0'),
    );
    expect(line).toContain('rate limited: ');
    expect(line).toContain('until ');
    expect(line).toMatch(/\d+[smhd], until /);
    expect(line).toContain('does not send it again');
  });

  it('keeps the plain hold word when the reason is not a limit', () => {
    const line = resultLine(
      { agentId: 'agent-0', delivery: { state: 'held', messageId: 'm-0', messageFile: null, messageFileSha256: null, reason: 'recipient-in-turn' } },
      name('agent-0'),
    );
    expect(line).not.toContain('rate limited');
  });
});
