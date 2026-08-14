import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LIFECYCLE_STATES } from "../types/projection";
import { DOT_VARIANTS, Dot } from "./Dot";

afterEach(cleanup);

// The dot is the only place a lifecycle state or an attention severity becomes something a
// developer can see. An unrecognised variant does NOT throw and does NOT render blank — it falls
// through to the cva base, so a state this build has never heard of ships looking like whatever the
// base looks like. That is how `awaiting-developer` reached the developer in the first place, and
// it is why the fallback is in the list below as an eleventh citizen rather than as a special case:
// "distinct from the fallback" and "distinct from each other" are the same requirement.

const FALLBACK = "__no-such-variant__";
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
  const ink = (mark.getAttribute("class") ?? "")
    .split(/\s+/)
    .filter((token) => token.length > 0 && !token.includes("anim"))
    .sort()
    .join(" ");
  return `${mark.textContent ?? ""} ${ink}`;
};

describe("Dot", () => {
  it("treats exactly the states the wire can send and the severities the queue can rank", () => {
    // Read off the recipe, never hand-copied: a second list of the same keys is precisely what let
    // `awaiting-developer` reach this component with no treatment at all. Asserted in BOTH
    // directions — a new lifecycle state with no dot treatment fails here, and so does a variant in
    // the recipe that nothing can ever send.
    expect([...DOT_VARIANTS].sort()).toEqual([...LIFECYCLE_STATES, "alarm", "warn", "info"].sort());
  });

  it("renders every state and severity differently from every other, and from a variant it does not know", () => {
    // The whole property, flat. Colour alone cannot carry it — `blocked`/`alarm`, `running`/`info`
    // and `awaiting-developer`/`warn` are each one hue shared by a state and a severity that the
    // cockpit's left rail shows at the same time — so the glyph is what makes this pass, and a
    // variant that loses either channel to another collides here.
    const seen = new Map<string, string>();
    for (const variant of ALL_VARIANTS) {
      const mark = markOf(variant);
      expect(mark.textContent, `'${variant}' renders no mark`).not.toBe("");
      const appearance = appearanceOf(mark);
      expect(
        seen.get(appearance),
        `'${variant}' renders identically to '${seen.get(appearance)}'`,
      ).toBeUndefined();
      seen.set(appearance, variant);
    }
    expect(seen.size).toBe(ALL_VARIANTS.length);
  });

  it("gives every variant an ink of its own, so the glyph is redundancy and not a replacement", () => {
    // Colour is the channel: it is the fastest read and it is what works at the size these are
    // drawn. The glyph exists to separate the pairs colour deliberately GROUPS, not as a licence to
    // drop colour — without this, a build that stripped every hue would still pass the test above.
    // Sharing the base's tone is the one collision that is never safe, because every consumer can
    // hit the base: while it was `amber` that was literally true of `warn`, and it is how
    // `awaiting-developer` reached the developer looking like nothing special.
    const inkOf = (variant: string): string | undefined =>
      (markOf(variant).getAttribute("class") ?? "").match(/(?:^|\s)(c_\S+)/)?.[1];
    const base = inkOf(FALLBACK);
    expect(base).toBeTruthy();
    for (const variant of DOT_VARIANTS) {
      expect(inkOf(variant), `'${variant}' has no ink of its own`).toBeTruthy();
      expect(inkOf(variant), `'${variant}' wears the unclassified tone`).not.toBe(base);
    }
  });
});
