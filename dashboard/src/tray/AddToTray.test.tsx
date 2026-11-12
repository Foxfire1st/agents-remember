// The ⊕ of an object is quiet until its own object is hovered or holds focus (MIK-R188 rule 1, D366):
// a host shows its own idle ⊕, not the idle ⊕ of a host nested in it, and a ⊕ in the tray stays
// shown. jsdom's selector engine matches `:hover` on the event target alone and `:focus-within` on a
// focusable element alone, so the rules are matched here with each state as an attribute on the
// hovered or focused element and its ancestors, as a browser applies it. The browser leg measures
// the real cascade.
import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { AddToTray, addToTrayHost } from './AddToTray';
import { EMPTY_TRAY } from './trayPersistence';
import { trayStore } from './trayStore';
import type { TrayPickInput } from './types';

// The selectors of a list, split at its top-level commas.
function topLevel(list: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let at = 0; at < list.length; at += 1) {
    if (list[at] === '(') depth += 1;
    if (list[at] === ')') depth -= 1;
    if (list[at] === ',' && depth === 0) {
      parts.push(list.slice(start, at).trim());
      start = at + 1;
    }
  }
  return [...parts, list.slice(start).trim()];
}

// Selectors 4 specificity for what these rules use: attributes, pseudo-classes, :is() and :not().
function specificity(selector: string): number {
  const wrapped = /:(?:is|not)\(([^()]*)\)/g;
  let count = 0;
  for (const match of selector.matchAll(wrapped)) count += Math.max(...topLevel(match[1]).map(specificity));
  return count + (selector.replace(wrapped, '').match(/\[[^\]]*\]|:[\w-]+/g) ?? []).length;
}

// The host's rules as Panda names its classes, "[<condition>]:op_<opacity>" with "_" for a space;
// `&` is the host, marked here by `data-host`.
const rules = addToTrayHost.split(' ').flatMap((name) => {
  const rule = /^\[(.+)\]:op_(\d)$/.exec(name);
  if (!rule) return [];
  return topLevel(rule[1].replaceAll('_', ' ')).map((selector) => ({
    selector: selector.replaceAll('&', '[data-host]'),
    opacity: Number(rule[2]),
  }));
});

const matchable = (selector: string): string =>
  selector.replaceAll(':hover', '[data-pointer]').replaceAll(':focus-within', '[data-focus-within]');

// The opacity the cascade gives a ⊕: the most specific rule that matches, else fully shown.
function opacity(control: Element): number {
  let best = { specificity: -1, opacity: 1 };
  for (const rule of rules) {
    const weight = specificity(rule.selector);
    if (control.matches(matchable(rule.selector)) && weight >= best.specificity) best = { specificity: weight, opacity: rule.opacity };
  }
  return best.opacity;
}

// A state on `element`, and so on its ancestors: the pointer over it (`:hover`), or its focus
// (`:focus-within`).
function mark(state: 'data-pointer' | 'data-focus-within', element: Element | null): void {
  for (const held of document.querySelectorAll(`[${state}]`)) held.removeAttribute(state);
  for (let at = element; at; at = at.parentElement) at.setAttribute(state, '');
}
const point = (element: Element | null) => mark('data-pointer', element);
function focus(element: HTMLElement): void {
  act(() => element.focus());
  mark('data-focus-within', document.activeElement);
}

const pick = (id: string): TrayPickInput => ({
  kind: 'reference',
  title: `${id} · an object`,
  reference: { kind: 'invariant', repository: 'agents-remember', id, memoryTree: 'a'.repeat(40) },
});

// A subject with two blocks, and a file with two hunks: each object is a host with its ⊕ and a link.
function Nested({ outer, inner }: { outer: string; inner: [string, string] }) {
  return (
    <section className={addToTrayHost} data-host>
      <AddToTray pick={pick(outer)} label={outer} testId={outer} />
      {inner.map((id) => (
        <div key={id} className={addToTrayHost} data-host>
          <AddToTray pick={pick(id)} label={id} testId={id} />
          <a href={`#${id}`}>{id}</a>
        </div>
      ))}
    </section>
  );
}

const shown = (...ids: string[]) => ids.map((id) => opacity(screen.getByTestId(id)));

beforeEach(() => {
  point(null);
  mark('data-focus-within', null);
  trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null });
});

describe('the quiet ⊕ of nested objects', () => {
  for (const [outer, inner] of [
    ['subject', ['block-1', 'block-2']],
    ['file', ['hunk-1', 'hunk-2']],
  ] as const) {
    it(`shows a ${outer}'s idle ⊕ and the ⊕ of the hovered or focused ${inner[0]}, never the other ${inner[1]}'s`, () => {
      render(<Nested outer={outer} inner={[inner[0], inner[1]]} />);
      expect(rules.length).toBeGreaterThan(0);
      expect(shown(outer, ...inner)).toEqual([0, 0, 0]);
      point(screen.getByRole('link', { name: inner[0] }));
      expect(shown(outer, ...inner)).toEqual([1, 1, 0]);
      // The pointer on the outer object outside its nested ones shows the outer ⊕ alone.
      point(screen.getByTestId(outer).parentElement);
      expect(shown(outer, ...inner)).toEqual([1, 0, 0]);
      point(null);
      focus(screen.getByRole('link', { name: inner[0] }));
      expect(shown(outer, ...inner)).toEqual([1, 1, 0]);
      // The outer object's own ⊕, reached by keyboard, shows; its nested objects' stay quiet.
      focus(screen.getByRole('button', { name: `Add ${outer} to the AR tray` }));
      expect(shown(outer, ...inner)).toEqual([1, 0, 0]);
      // A ⊕ in the tray shows "✓ in tray" whether or not its object is hovered or focused.
      act(() => void trayStore.getState().add(pick(inner[1])));
      expect(screen.getByTestId(inner[1]).textContent).toBe('✓ in tray');
      expect(shown(outer, ...inner)).toEqual([1, 0, 1]);
    });
  }
});
