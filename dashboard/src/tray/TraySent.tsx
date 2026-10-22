// What happened to the tray's messages (MIK-R188 rules 9 and 10): the last Send's answer per target,
// and each chosen chat's sent-picks log (time, pick ids, the message file and its SHA-256).
import { useEffect, useState } from 'react';

import { clockTime } from './trayModel';
import { fmtWait } from '../data/selectors';
import { formatLimitTime } from '../panels/detail-panel/pageParts';
import { useChatNames, type ChatName } from './traySeats';
import type { TraySendOutcome, TrayTargetResult } from './trayStore';
import * as look from './trayStyles';
import type { TraySentEntry } from './types';

const sentence = (text: string): string => (/[.!?]$/.test(text) ? text : `${text}.`);

// Each answer names its reason and the server's detail, as the server gave them. A hold caused by a
// provider limit also names the full word and the reset: the compact summary says the time left, and
// the full view says when, to the exact instant (MIK-R190 rules 9 and 13).
export function resultLine({ delivery }: TrayTargetResult, { label }: ChatName): string {
  const why = [delivery.reason, delivery.detail].filter(Boolean).join(': ');
  if (delivery.state === 'delivered') return `Delivered to ${label}.`;
  if (delivery.state === 'held' && delivery.limitedUntil && delivery.reason === 'provider-limit')
    return (
      `Held for ${label}: rate limited: ${limitTimeLeft(delivery.limitedUntil)}, ` +
      `until ${formatLimitTime(delivery.limitedUntil)}. The chat gets it when it can take it; ` +
      `the tray does not send it again.`
    );
  if (delivery.state === 'held')
    return `Held for ${label}${why ? ` (${why})` : ''}. The chat gets it when it can take it; the tray does not send it again.`;
  return `${sentence(`Refused by ${label}${why ? `: ${why}` : ''}`)} The picks and your text stay, with this chat as the target.`;
}

export function TrayOutcome({ outcome }: { outcome: TraySendOutcome }) {
  const name = useChatNames();
  return (
    <ul className={look.outcomeList} aria-label="The last Send" data-testid="tray-outcome">
      {outcome.results.map((result) => {
        const named = name(result.agentId);
        return (
          <li key={result.agentId} data-state={result.delivery.state} title={named.unnamed ?? undefined}>
            {resultLine(result, named)}
          </li>
        );
      })}
    </ul>
  );
}

// The last Send's answer in a few words, for the minimised line: "Sent: delivered to Manager · 260928-MIK
// · b1d2c3e4." The full view lists each target's answer.
export function outcomeSummary(outcome: TraySendOutcome, name: (agentId: string) => ChatName): string {
  const named = (results: TrayTargetResult[]) =>
    results.length === 1 ? name(results[0].agentId).label : `${results.length} chats`;
  const part = (state: TrayTargetResult['delivery']['state'], words: string) => {
    const results = outcome.results.filter(
      (result) => result.delivery.state === state && !limitedHold(result.delivery),
    );
    if (results.length === 0) return null;
    return `${words} ${named(results)}`;
  };
  // MIK-R190 rule 9 as D399 amends MIK-R188 rule 9: a hold for a provider limit says when the
  // recipient may take the message instead of the plain word, one part per reset time.
  const limited = outcome.results.filter((result) => limitedHold(result.delivery));
  const limitedParts = [...new Set(limited.map((result) => result.delivery.limitedUntil as string))].map(
    (until) => {
      const results = limited.filter((result) => result.delivery.limitedUntil === until);
      return `held · rate limited: ${limitTimeLeft(until)} for ${named(results)}`;
    },
  );
  const parts = [
    part('delivered', 'delivered to'),
    part('held', 'held for'),
    ...limitedParts,
    part('refused', 'refused by'),
  ].filter(Boolean);
  return `Sent: ${parts.join(', ')}.`;
}

// MIK-R190 rule 9 as D399 amends MIK-R188 rule 9, with rule 13's unit: the summary is one compact
// line, so the reset is named as the time left in that line's own unit — "3h", not a clock time.
// The full view keeps the exact instant (formatLimitTime) beside it.

/** The time left until a served reset, in the summary's one compact unit; the served string if it will not parse. */
export function limitTimeLeft(iso: string, now = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  // A reset the clock has passed is no longer a countdown: "0s", never a negative one.
  return fmtWait(Math.max(0, at - now) / 1000);
}

/** A held delivery the provider limit set a reset time for. */
function limitedHold(delivery: TrayTargetResult['delivery']): boolean {
  return delivery.state === 'held' && delivery.reason === 'provider-limit' && Boolean(delivery.limitedUntil);
}

// A refusal is named on the line too, each with its reason, as the full view names it (rule 10).
export function TrayLineRefusals({ outcome }: { outcome: TraySendOutcome }) {
  const name = useChatNames();
  const refused = outcome.results.filter((result) => result.delivery.state === 'refused');
  if (refused.length === 0) return null;
  return (
    <ul className={look.outcomeList} aria-label="Refused by name" data-testid="tray-line-refusals">
      {refused.map((result) => {
        const named = name(result.agentId);
        return (
          <li key={result.agentId} data-state="refused" title={named.unnamed ?? undefined}>
            {resultLine(result, named)}
          </li>
        );
      })}
    </ul>
  );
}

export function TrayOutcomeLine({ outcome }: { outcome: TraySendOutcome }) {
  const name = useChatNames();
  return (
    <span role="status" className={look.dim} data-testid="tray-line-outcome">
      {outcomeSummary(outcome, name)}
    </span>
  );
}

// ---- the sent-picks log ----

type SentLogAnswer = { state: 'entries'; entries: TraySentEntry[] } | { state: 'unavailable'; detail: string };

function readEntry(value: unknown): TraySentEntry | null {
  if (typeof value !== 'object' || value === null) return null;
  const entry = value as Record<string, unknown>;
  const text = (key: string) => (typeof entry[key] === 'string' ? (entry[key] as string) : '');
  if (!text('messageId')) return null;
  return {
    time: text('time'),
    targetAgentId: text('targetAgentId'),
    pickIds: Array.isArray(entry.pickIds) ? entry.pickIds.filter((id): id is string => typeof id === 'string') : [],
    messageId: text('messageId'),
    messageFile: text('messageFile'),
    sha256: text('sha256'),
    state: text('state'),
  };
}

export async function fetchSentLog(agentId: string, limit = 20, fetcher: typeof fetch = fetch): Promise<SentLogAnswer> {
  try {
    const response = await fetcher(`/api/role-chats/${encodeURIComponent(agentId)}/sent?limit=${limit}`);
    if (!response.ok) return { state: 'unavailable', detail: `the dashboard server answered ${response.status}` };
    const answer = (await response.json()) as { entries?: unknown };
    if (!Array.isArray(answer.entries)) return { state: 'unavailable', detail: 'the log answered in an unknown form' };
    return { state: 'entries', entries: answer.entries.map(readEntry).filter((entry): entry is TraySentEntry => entry !== null) };
  } catch (error) {
    return { state: 'unavailable', detail: error instanceof Error ? error.message : 'the dashboard server could not be reached' };
  }
}

// One send of the log (rule 10): its time, target, pick count, state and digest on one line; its exact
// pick ids, the message file and the file's full SHA-256 open below it.
function SentEntry({ entry, label }: { entry: TraySentEntry; label: string }) {
  return (
    <li data-state={entry.state}>
      <details>
        <summary className={look.sentSummary}>
          {clockTime(entry.time) ?? entry.time} · {label} · {entry.pickIds.length} picks · {entry.state} · SHA-256{' '}
          {entry.sha256.slice(0, 12)}…
        </summary>
        <ul className={look.sentIds} aria-label="Pick ids">
          {entry.pickIds.map((id) => (
            <li key={id}>{id}</li>
          ))}
        </ul>
        <div className={look.sentFile}>
          {entry.messageFile} · SHA-256 {entry.sha256}
        </div>
      </details>
    </li>
  );
}

function SentFor({ agentId, refresh }: { agentId: string; refresh: string | null }) {
  const [answer, setAnswer] = useState<SentLogAnswer | null>(null);
  useEffect(() => {
    let live = true;
    void fetchSentLog(agentId).then((next) => {
      if (live) setAnswer(next);
    });
    return () => {
      live = false;
    };
  }, [agentId, refresh]);
  const { label } = useChatNames()(agentId);
  if (!answer) return <p className={look.dim}>Reading what was sent to {label}…</p>;
  if (answer.state === 'unavailable') return <p className={look.dim}>The sent log of {label} is unavailable: {answer.detail}.</p>;
  if (answer.entries.length === 0) return <p className={look.dim}>Nothing was sent to {label} from the tray.</p>;
  return (
    <ul className={look.outcomeList} aria-label={`Sent to ${label}`}>
      {answer.entries.map((entry) => (
        <SentEntry key={entry.messageId} entry={entry} label={label} />
      ))}
    </ul>
  );
}

// The log of each chosen chat, behind one disclosure; it reads again after each Send.
export function TraySentLog({ targets, refresh }: { targets: readonly string[]; refresh: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className={look.textButton} aria-expanded={open} onClick={() => setOpen((now) => !now)}>
        Sent log {open ? '▾' : '▸'}
      </button>
      {open && targets.length === 0 ? <p className={look.dim}>Choose a chat to see what was sent to it.</p> : null}
      {open ? targets.map((agentId) => <SentFor key={agentId} agentId={agentId} refresh={refresh} />) : null}
    </div>
  );
}
