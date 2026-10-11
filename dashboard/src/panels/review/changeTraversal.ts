// Next and previous real change in the shown outline (MIK-R86). Unknown and unchanged rows
// are not stops. At either end the selection stays and a polite message says so.
//
// `j`/`k` are the keymap owner's `review.nextChange`/`review.previousChange` chords (data/keymap):
// bound on the reviewer's own zone, so they act only while focus is inside the reviewer, and routed
// by the owner's contract, so they are inert in inputs, textareas and contenteditable regions.

import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { tinykeys, type KeybindingsMap } from 'tinykeys';

import { bindingFor, useEffectiveKeymap } from '../../data/keymap/preferences';
import { routeKey, zoneForTarget } from '../../data/keymap/zones';

export type TraversalDirection = 1 | -1;

export type TraversalOutcome =
  { kind: 'select'; element: HTMLElement } | { kind: 'end'; direction: TraversalDirection };

export const REVIEW_ZONE_SELECTOR = '[data-kbzone="review"]';
const LIST_SELECTOR = '[data-testid="review-family-list"]';
const ITEM_SELECTOR = '[data-tree-node]';
const TRAVERSAL_COMMANDS: [string, TraversalDirection][] = [
  ['review.nextChange', 1],
  ['review.previousChange', -1],
];

interface Item {
  element: HTMLElement;
  occurrence: string;
  stop: boolean;
}

function itemsOf(list: HTMLElement): Item[] {
  return Array.from(list.querySelectorAll<HTMLElement>(ITEM_SELECTOR)).map((element, index) => {
    const primary = element.dataset.changePrimary;
    return {
      element,
      occurrence: element.dataset.occurrence ?? `item:${index}`,
      stop: primary !== undefined && primary !== 'unchanged' && primary !== 'unknown',
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

function forward(items: Item[], at: number | undefined): TraversalOutcome {
  const from = at === undefined ? undefined : items[at];
  const item = items
    .slice((at ?? -1) + 1)
    .find((candidate) => candidate.stop && candidate.occurrence !== from?.occurrence);
  if (item === undefined) return { kind: 'end', direction: 1 };
  return { kind: 'select', element: item.element };
}

function backward(items: Item[], at: number | undefined): TraversalOutcome {
  const from = at === undefined ? undefined : items[at];
  for (let index = (at ?? items.length) - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (!item.stop || item.occurrence === from?.occurrence) continue;
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
  return outcome.direction === 1
    ? 'No later change in this tree; the selection stays.'
    : 'No earlier change in this tree; the selection stays.';
}

// The status of the last move, kept with the rows it was said for. `rows` names what the tree shows
// (its rows and their order). A status answers one move and speaks of the tree as it was shown
// then: a held `j` can outrun the answer that brings the next family, and "No later change" must
// not stay beside the later change that answer shows. So the render gives a status out only while
// the tree shows the rows it was said for, and once the tree has shown other rows the status is
// dropped for good: the tree may show the same rows again with another selection. Nothing is
// updated while rendering. React can queue a status without rendering it (the same message again,
// from a key held at an end) and apply it after a later render; a status dropped by an update made
// in that render would then come back.
function useStatusFor(rows: string): [string, (text: string) => void] {
  const [said, setSaid] = useState({ rows, text: '' });
  // The rows of the tree as committed, which is the tree a move reads.
  const shown = useRef(rows);
  useLayoutEffect(() => {
    shown.current = rows;
    // In the commit that shows other rows, so that no later turn finds the status still stored.
    setSaid((last) => (last.text === '' ? last : { rows, text: '' }));
  }, [rows]);
  const setStatus = useCallback((text: string) => {
    const next = { rows: shown.current, text };
    // The same status for the same rows is no change, so a key held at an end renders nothing.
    setSaid((last) => (last.rows === next.rows && last.text === next.text ? last : next));
  }, []);
  return [said.rows === rows ? said.text : '', setStatus];
}

// Moves over the tree inside `tree`, with the polite status of the last move. When `enabled`, the
// keymap's traversal chords are bound on the enclosing reviewer zone. `rows` names what the tree
// shows; the status is dropped when that changes (`useStatusFor`).
export function useChangeTraversal(
  tree: RefObject<HTMLElement | null>,
  enabled: boolean,
  rows: string,
): { move: (direction: TraversalDirection) => void; status: string } {
  const [status, setStatus] = useStatusFor(rows);
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
    [tree, setStatus],
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
