import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ATTENTION_SEVERITIES } from '../types/projection';
import { DOT_VARIANTS, Dot } from './Dot';

afterEach(cleanup);

// Every published severity and the unknown mark remain visually distinct.

const FALLBACK = '__no-such-variant__';
const ALL_VARIANTS = [...DOT_VARIANTS, FALLBACK];

const markOf = (variant: string): Element => {
  const { container } = render(<Dot variant={variant} />);
  const mark = container.firstElementChild;
  if (!mark) throw new Error(`Dot rendered nothing for '${variant}'`);
  return mark;
};

/**
 * What a developer can tell two marks apart by: the character and the ink.
 *
 * jsdom applies no stylesheet, so a computed style is useless here — but Panda's atomic class names
 * ARE the declarations (`c_amber`, `anim-n_pulseSlow`), so the class list is the observable, and
 * reading it back is reading what actually ships. Animation atoms are dropped on purpose: motion is
 * additive here, and a pair whose only difference is that one of them moves is a pair that looks
 * identical to anyone with the Calm toggle on or reduced motion set.
 */
const appearanceOf = (mark: Element): string => {
  const ink = (mark.getAttribute('class') ?? '')
    .split(/\s+/)
    .filter((token) => token.length > 0 && !token.includes('anim'))
    .sort()
    .join(' ');
  return `${mark.textContent ?? ''} ${ink}`;
};

describe('Dot', () => {
  it('treats exactly the severities the queue can rank', () => {
    expect([...DOT_VARIANTS].sort()).toEqual([...ATTENTION_SEVERITIES].sort());
  });

  it('renders every severity differently from each other and the unknown mark', () => {
    const seen = new Map<string, string>();
    for (const variant of ALL_VARIANTS) {
      const mark = markOf(variant);
      expect(mark.textContent, `'${variant}' renders no mark`).not.toBe('');
      const appearance = appearanceOf(mark);
      expect(
        seen.get(appearance),
        `'${variant}' renders identically to '${seen.get(appearance)}'`,
      ).toBeUndefined();
      seen.set(appearance, variant);
    }
    expect(seen.size).toBe(ALL_VARIANTS.length);
  });

  it('gives every variant an ink of its own, so the glyph is redundancy and not a replacement', () => {
    // The unknown tone must not be mistaken for any published severity.
    const inkOf = (variant: string): string | undefined =>
      (markOf(variant).getAttribute('class') ?? '').match(/(?:^|\s)(c_\S+)/)?.[1];
    const base = inkOf(FALLBACK);
    expect(base).toBeTruthy();
    for (const variant of DOT_VARIANTS) {
      expect(inkOf(variant), `'${variant}' has no ink of its own`).toBeTruthy();
      expect(inkOf(variant), `'${variant}' wears the unclassified tone`).not.toBe(base);
    }
  });
});
