// Next and previous change in the family tree (MIK-R33, adopting ICR-R32 rules 6 and 7).
//
// The stops are the tree's own nodes in displayed order -- so an active filter's hidden families are
// never visited -- whose primary badge is not `unchanged` (an `unknown` is visited). The two revision
// rows of one member occurrence are one stop. Going forward past the returned members of a family
// with unreturned members (its node says `data-members-unreturned`) stops at that family's
// continuation control instead, saying so; the next `j` from there moves on. At either end the
// selection stays and a polite message says so.
//
// `j`/`k` are the keymap owner's `review.nextChange`/`review.previousChange` chords (data/keymap):
// bound on the reviewer's own zone, so they act only while focus is inside the reviewer, and routed
// by the owner's contract, so they are inert in inputs, textareas and contenteditable regions.

import { type RefObject, useCallback, useEffect, useState } from 'react';
import { tinykeys, type KeybindingsMap } from 'tinykeys';

import { bindingFor, useEffectiveKeymap } from '../../data/keymap/preferences';
import { routeKey, zoneForTarget } from '../../data/keymap/zones';

export type TraversalDirection = 1 | -1;

export type TraversalOutcome =
  | { kind: 'select'; element: HTMLElement }
  | { kind: 'continuation'; element: HTMLElement; label: string }
  | { kind: 'end'; direction: TraversalDirection };

export const REVIEW_ZONE_SELECTOR = '[data-kbzone="review"]';
const LIST_SELECTOR = '[data-testid="review-family-list"]';
const ITEM_SELECTOR = '[data-tree-node], [data-testid="review-family-roster-next"]';
const TRAVERSAL_COMMANDS: [string, TraversalDirection][] = [
  ['review.nextChange', 1],
  ['review.previousChange', -1],
];

interface Item {
  element: HTMLElement;
  continuation: boolean;
  family: string;
  occurrence: string;
  stop: boolean;
}

function itemsOf(list: HTMLElement): Item[] {
  return Array.from(list.querySelectorAll<HTMLElement>(ITEM_SELECTOR)).map((element, index) => {
    const continuation = element.dataset.testid === 'review-family-roster-next';
    const primary = element.dataset.changePrimary;
    const unreturned =
      element.closest<HTMLElement>('[data-testid="review-family"]')?.dataset.membersUnreturned ===
      'true';
    return {
      element,
      continuation,
      family: element.dataset.family ?? '',
      occurrence: element.dataset.occurrence ?? `item:${index}`,
      stop: continuation ? unreturned : primary !== undefined && primary !== 'unchanged',
    };
  });
}

// Where the reader is: the focused tree control, else the selected node.
function positionOf(items: Item[], active: Element | null): number | undefined {
  const focused = items.findIndex((item) => item.element === active);
  if (focused !== -1) return focused;
  const selected = items.findIndex((item) => item.element.getAttribute('aria-current') === 'true');
  return selected === -1 ? undefined : selected;
}

// Whether going forward from `from` stops at `item`: a change of another occurrence, or a family's
// continuation control -- the family's controls are one stop, so from one of them the walk moves on.
function stopsForward(item: Item, from: Item | undefined): boolean {
  if (!item.stop) return false;
  if (item.continuation) return !(from?.continuation && from.family === item.family);
  return item.occurrence !== from?.occurrence;
}

function forward(items: Item[], at: number | undefined): TraversalOutcome {
  const from = at === undefined ? undefined : items[at];
  const item = items.slice((at ?? -1) + 1).find((candidate) => stopsForward(candidate, from));
  if (item === undefined) return { kind: 'end', direction: 1 };
  if (!item.continuation) return { kind: 'select', element: item.element };
  const label = item.element.dataset.familyLabel ?? item.family;
  return { kind: 'continuation', element: item.element, label };
}

function backward(items: Item[], at: number | undefined): TraversalOutcome {
  const from = at === undefined ? undefined : items[at];
  for (let index = (at ?? items.length) - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.continuation || !item.stop || item.occurrence === from?.occurrence) continue;
    let first = index;
    while (first > 0 && items[first - 1].occurrence === item.occurrence) first -= 1;
    return { kind: 'select', element: items[first].element };
  }
  return { kind: 'end', direction: -1 };
}

// The next stop from where the reader is, in displayed order.
export function nextChange(
  list: HTMLElement,
  direction: TraversalDirection,
  active: Element | null,
): TraversalOutcome {
  const items = itemsOf(list);
  const at = positionOf(items, active);
  return direction === 1 ? forward(items, at) : backward(items, at);
}

export function outcomeMessage(outcome: TraversalOutcome): string {
  if (outcome.kind === 'select') return '';
  if (outcome.kind === 'continuation')
    return `Further members of ${outcome.label} are not yet returned; continue its roster walk to reach them, or press next again to move on.`;
  return outcome.direction === 1
    ? 'No later change in this tree; the selection stays.'
    : 'No earlier change in this tree; the selection stays.';
}

// Moves over the tree inside `tree`, with the polite status of the last move. When `enabled`, the
// keymap's traversal chords are bound on the enclosing reviewer zone.
export function useChangeTraversal(
  tree: RefObject<HTMLElement | null>,
  enabled: boolean,
): { move: (direction: TraversalDirection) => void; status: string } {
  const [status, setStatus] = useState('');
  const move = useCallback(
    (direction: TraversalDirection) => {
      const list = tree.current?.querySelector<HTMLElement>(LIST_SELECTOR);
      if (!list) return;
      const outcome = nextChange(list, direction, document.activeElement);
      setStatus(outcomeMessage(outcome));
      if (outcome.kind === 'end') {
        // The selection stays; bring it into view, so the status in the sticky triage bar is read
        // where the selection is (a stacked layout may have scrolled it away).
        list
          .querySelector<HTMLElement>('[data-tree-node][aria-current="true"]')
          ?.scrollIntoView?.({ block: 'center' });
        return;
      }
      // Focus first, so the selection's answer returns focus to the node the reader moved to.
      outcome.element.focus();
      if (outcome.kind === 'select') outcome.element.click();
    },
    [tree],
  );
  const keymap = useEffectiveKeymap();
  useEffect(() => {
    const zone = tree.current?.closest<HTMLElement>(REVIEW_ZONE_SELECTOR);
    if (!enabled || !zone) return undefined;
    const map: KeybindingsMap = {};
    for (const [commandId, direction] of TRAVERSAL_COMMANDS) {
      const binding = bindingFor(keymap, commandId);
      if (!binding) continue;
      map[binding.chord] = (event) => {
        const target = event.target as Element | null;
        const owner = zoneForTarget(target);
        if (!binding.zones.includes(owner)) return;
        if (routeKey(owner, event, target) !== 'handle') return;
        event.preventDefault();
        move(direction);
      };
    }
    return tinykeys(zone, map, { ignore: () => false });
  }, [enabled, keymap, move, tree]);
  return { move, status };
}
