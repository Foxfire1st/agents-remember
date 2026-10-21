// The status chip's facts (MIK-R89 v2 rule 6, W18): one chip in the top line answers "is what I see
// still the candidate's current comparison?" from the owners' own measurements, and its popover lists
// them in four rows. The reviewer writes no explanation of its own: the popover's sentence is the
// first sentence of the owner's own text for the state it shows.
//
// States:
//   reading      the read the chip describes is in flight: "reading…", in the chip's reserved place;
//   unavailable  that read failed or was refused: "unavailable", with the owner's reason;
//   warning      a movement owner measured the branch moved, or the comparison went stale;
//   grey         neither movement owner measured anything ("branches: not measured"), or one could not;
//   current      both movement owners measured the branch current.
import type { ReviewFailure, ReviewMovement, ReviewPayload } from '../../../data/review';
import type { ReviewTreeComparison } from '../../../data/reviewTrees';

type ChipTone = 'reading' | 'unavailable' | 'warning' | 'grey' | 'current';

export interface ChipFacts {
  tone: ChipTone;
  label: string;
  rows: [string, string][];
  reason: string;
}

export interface ChipInput {
  // The read the chip describes: the task-context read at opening, a selection's read, a refresh.
  phase: 'loading' | 'reviewed' | 'refused' | 'failed';
  problem: ReviewFailure | null;
  // The last admitted payload of this task context (kept across reads, never emptied).
  payload: Pick<ReviewPayload, 'sync_movement' | 'external_git_movement' | 'staleness'> | null;
  // The comparison on screen, from the trees answer; `latest` its newest number.
  comparison: Pick<ReviewTreeComparison, 'number' | 'recorded_at'> | null;
  latest: number | null;
  // The reader's current date: a recorded time of this year is written without its year.
  now: Date;
}

const NOT_MEASURED = 'not measured';

function movementValue(movement: ReviewMovement | null | undefined): string {
  if (!movement || movement.binding_state === 'not-measured') return NOT_MEASURED;
  return movement.binding_state === 'stale' ? 'moved' : movement.binding_state;
}

function stalenessValue(payload: ChipInput['payload']): string {
  const state = payload?.staleness.state;
  if (state === undefined) return NOT_MEASURED;
  if (state === 'not-measured') return NOT_MEASURED;
  return state === 'not_compared' ? 'not compared' : state;
}

// A recorded time in the reader's local time, minutes precision, as the mockup writes it (ruling
// 9498d6e4): "10-19 13:41" in the current year of `now`, "2025-10-19 13:41" in another; the raw value
// when it is not a time. The caller passes the current date.
export function localTime(iso: string, now: Date): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const two = (value: number) => String(value).padStart(2, '0');
  const day = `${two(at.getMonth() + 1)}-${two(at.getDate())} ${two(at.getHours())}:${two(at.getMinutes())}`;
  return at.getFullYear() === now.getFullYear() ? day : `${at.getFullYear()}-${day}`;
}

function comparisonValue(input: ChipInput): string {
  const { comparison, latest } = input;
  if (!comparison) return 'not read';
  const recorded = comparison.recorded_at ? ` · recorded ${localTime(comparison.recorded_at, input.now)}` : '';
  const older = latest !== null && comparison.number !== latest ? ` · shown read-only, latest #${latest}` : '';
  return `#${comparison.number}${recorded}${older}`;
}

// The first sentence of an owner's text: the chip's popover gives one sentence; the whole record is in
// Technical details.
function firstSentence(text: string | undefined): string {
  const trimmed = (text ?? '').trim();
  const end = trimmed.search(/[.!?](\s|$)/);
  return end === -1 ? trimmed : trimmed.slice(0, end + 1);
}

function movementsOf(payload: ChipInput['payload']): ReviewMovement[] {
  return [payload?.sync_movement, payload?.external_git_movement].filter(
    (one): one is ReviewMovement => one !== null && one !== undefined,
  );
}

type Tone = Pick<ChipFacts, 'tone' | 'label' | 'reason'>;

const ownerText = (movement: ReviewMovement) => firstSentence(movement.reason ?? movement.statement);

// A movement owner's or the staleness owner's warning, first.
function warningTone(payload: ChipInput['payload'], movements: ReviewMovement[]): Tone | null {
  const moved = movements.find((one) => one.binding_state === 'stale');
  if (moved) return { tone: 'warning', label: 'branches: moved', reason: ownerText(moved) };
  if (payload?.staleness.state !== 'stale') return null;
  return { tone: 'warning', label: 'comparison: stale', reason: firstSentence(payload.staleness.statement) };
}

function measuredTone(payload: ChipInput['payload']): Tone {
  const movements = movementsOf(payload);
  const warning = warningTone(payload, movements);
  if (warning) return warning;
  const unreadable = movements.find((one) => one.binding_state === 'unavailable');
  if (unreadable) return { tone: 'grey', label: 'branches: unavailable', reason: ownerText(unreadable) };
  const current = movements.length === 2 && movements.every((one) => one.binding_state === 'current');
  if (current) return { tone: 'current', label: 'branches: current', reason: firstSentence(movements[0].statement) };
  const said = movements.find((one) => one.binding_state === 'not-measured');
  return {
    tone: 'grey',
    label: `branches: ${NOT_MEASURED}`,
    reason: said ? ownerText(said) : firstSentence(payload?.staleness.statement),
  };
}

export function chipFacts(input: ChipInput): ChipFacts {
  const { payload } = input;
  const rows: [string, string][] = [
    ['Sync movement', movementValue(payload?.sync_movement)],
    ['External Git movement', movementValue(payload?.external_git_movement)],
    ['Subject staleness', stalenessValue(payload)],
    ['Bound comparison', comparisonValue(input)],
  ];
  if (input.phase === 'loading') return { tone: 'reading', label: 'reading…', rows, reason: 'The review is being read.' };
  if (input.problem !== null)
    return { tone: 'unavailable', label: 'unavailable', rows, reason: firstSentence(input.problem.detail) };
  return { ...measuredTone(payload), rows };
}
