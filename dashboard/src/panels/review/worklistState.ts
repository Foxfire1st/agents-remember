// Display only the server gate's judgments; never derive answers from history or satisfied_by.
import type {
  ReviewItemVerdict,
  ReviewWorklistItem,
  ReviewWorklistView,
} from '../../data/reviewTrees';

export function itemAnswer(
  worklist: ReviewWorklistView | undefined,
  item: ReviewWorklistItem,
): ReviewItemVerdict | undefined {
  return worklist?.bound ? worklist.item_states?.[item.id] : undefined;
}
export function itemAnswerText(answer: ReviewItemVerdict | undefined): string {
  if (!answer) return 'state not measured for this comparison';
  if (answer.state === 'answered') return `answered by ${answer.witness ?? 'witness unavailable'}`;
  if (answer.state === 'undecided')
    return `undecided: ${answer.reason ?? 'the gate could not read its inputs'}`;
  return answer.row_id
    ? `row ${answer.row_id} is stale: ${answer.reason}`
    : `open: ${answer.reason}`;
}
function openCount(
  worklist: ReviewWorklistView | undefined,
  items: ReviewWorklistItem[],
): number | undefined {
  if (
    !worklist?.bound ||
    worklist.source === 'absent' ||
    worklist.state !== 'complete' ||
    worklist.incomplete.length ||
    !worklist.item_states
  )
    return undefined;
  const answers = items.map((item) => itemAnswer(worklist, item));
  if (answers.some((answer) => !answer || answer.state === 'undecided')) return undefined;
  return answers.filter((answer) => answer?.state === 'open').length;
}
export function worklistAnswer(worklist?: ReviewWorklistView): string | undefined {
  const count = openCount(worklist, worklist?.items ?? []);
  return count === undefined ? undefined : count ? `${count} open` : 'all answered';
}
export function fileAnswer(
  worklist: ReviewWorklistView | undefined,
  path: string,
): string | undefined {
  return fileAnswers(worklist).get(path);
}

// Group once for an entire changeset list. The gate's supplied states remain the only authority.
export function fileAnswers(worklist?: ReviewWorklistView): Map<string, string> {
  const groups = new Map<string, ReviewWorklistItem[]>();
  for (const item of worklist?.items ?? []) {
    const path = item.facts.path;
    if (!['unexplained_hunk', 'unexplained_file'].includes(item.kind) || typeof path !== 'string') continue;
    const group = groups.get(path) ?? [];
    group.push(item);
    groups.set(path, group);
  }
  const answers = new Map<string, string>();
  for (const [path, items] of groups) {
    const count = openCount(worklist, items);
    if (count !== undefined) answers.set(path, count ? `${count} open` : 'answered');
  }
  return answers;
}
