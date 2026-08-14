import { cva } from "../../styled-system/css";

// The status mark: one monospace cell carrying either a lifecycle state or an attention severity.
//
// COLOUR is the channel. It is the fastest read and it GROUPS related facts — cyan is "live or
// routine", amber is "wants you", the fault red is "broken". Seven tones cannot separate nine
// variants and a base, though, so the GLYPH is what makes each one its own: `awaiting-developer`
// and `warn` are both amber, and cockpit/Cockpit.tsx renders their two panels as siblings in a
// single always-visible rail, so without a distinct mark a handoff and a queue warning would be the
// same dot in one glance. Dot.test.tsx pins exactly that and nothing more: every variant, plus an
// unrecognised one, renders differently from every other.
//
// MOTION is additive, never an identity. `html[data-effects="off"]` (the Calm header toggle) nulls
// every animation from an unlayered `!important` rule in index.css and `_motionReduce` does the same
// for `prefers-reduced-motion`, so a state whose only difference is that it moves has no difference
// at all for a large number of the people using this.
//
// The BASE is `muted` + `?` — the palette's "we could not classify this" tone and the literal
// unknown mark. `Dot` is handed raw server strings (LifecycleList passes `lifecycle.state` through
// untouched), so an unrecognised variant does not throw, it renders the base. The base must
// therefore never borrow a live variant's treatment: while it was `amber` that was literally true
// of `warn`, and it is how `awaiting-developer` reached the developer looking like nothing special.
const dot = cva({
  base: {
    display: "inline-block",
    // One monospace cell, centred: every variant occupies the same column whatever its glyph, so
    // the rails stay aligned and a state change never reflows the row.
    width: "1ch",
    marginRight: "0.4em",
    // The dot sits in AttentionQueue's flex row; without this the mark is squeezable.
    flexShrink: "0",
    textAlign: "center",
    lineHeight: "1",
    fontSize: "0.95em",
    // Decorative: dragging a selection across a row must not pick up `◆`.
    userSelect: "none",
    color: "muted",
  },
  variants: {
    variant: {
      running: { color: "cyan" },
      // Not `dormant`. `paused` and `abandoned` both rendered the dormant tone with no other
      // difference, so a live lifecycle a developer can resume looked exactly like a dead one in
      // the same list. `dormant` is the terminal tone (the session grammar spends it on
      // landed/retired/exited), so it stays with `abandoned`; `paused` takes the muted-amber the
      // session grammar already rules for `waiting` — parked, not broken, not over.
      //
      // Mixed in OKLAB, not OKLCH. `color-mix` interpolates polar hue along the SHORTER arc, and
      // amber (h 75) to muted (h 250) is 175° — just under the half turn — so the short way runs
      // through h 145 and an "in oklch" mix of amber and grey renders GREEN, a hand's breadth from
      // `mint`. Verified in Chromium against the built stylesheet, which is the only place that
      // shows: OKLCH computes oklch(0.772 0.104 145), OKLAB computes oklch(0.772 0.088 75).
      // (`panels/session-cockpit/StateDot.tsx` mixes its own `mutedAmber` in oklch and so renders
      // the same green — pre-existing, outside this leaf's files, reported rather than touched.)
      paused: { color: "color-mix(in oklab, token(colors.amber) 60%, token(colors.muted))" },
      // The app-wide alarm register (index.css `@keyframes pulse`, ≤3 flashes/s and so inside WCAG
      // 2.3.1) — shared with signal-lost, `caution--alarm` and the down-engine silhouette. Confined
      // to the two FAULT variants: a handoff asking for the developer must not wear it.
      // `_motionReduce` reaches the same resting state the Calm freeze already does, so the two
      // accessibility paths agree about what a dot looks like.
      blocked: {
        color: "alarm",
        animation: "pulse 0.6s steps(1) infinite",
        _motionReduce: { animation: "none" },
      },
      // The NOTIFY-AND-CONTINUE turn end — the developer holds the turn. Amber is the palette's one
      // "wants you, is not broken" hue (the session grammar rules it for `awaiting-input`), and the
      // diamond is the mark: a decision waiting on a person. The breathe is the slow ease-in-out of
      // the developer's 2026-07-16 ruling, never the fault strobe above, and it sits on top of the
      // colour rather than carrying the state.
      "awaiting-developer": {
        color: "amber",
        animation: "pulseSlow 2.4s ease-in-out infinite",
        _motionReduce: { animation: "none" },
      },
      completed: { color: "mint" },
      abandoned: { color: "dormant" },
      alarm: {
        color: "alarm",
        animation: "pulse 0.6s steps(1) infinite",
        _motionReduce: { animation: "none" },
      },
      warn: { color: "amber" },
      info: { color: "cyan" },
    },
  },
});

// Derived from the recipe rather than hand-copied: a second, hand-maintained list of the same keys
// is exactly what let `awaiting-developer` reach this component and render COLOURLESS (it missed
// the copy, so `variant` resolved to undefined and only the base applied).
export const DOT_VARIANTS = dot.variantMap.variant;

const KNOWN: ReadonlySet<string> = new Set<string>(DOT_VARIANTS);

type DotVariant = (typeof DOT_VARIANTS)[number];

/**
 * One character per variant, chosen for shape difference at rail size out of Basic Latin, Latin-1
 * Supplement, Geometric Shapes and Dingbats — blocks a monospace face carries. Typed as a total
 * `Record<DotVariant, …>`, so a variant added to the recipe without a mark is a type error rather
 * than a dot that silently reads as some other state.
 */
const DOT_GLYPHS: Record<DotVariant, string> = {
  running: "●", // filled — alive and working
  paused: "◐", // half — parked, still there, resumable
  blocked: "×", // crossed out — stopped by a fault
  "awaiting-developer": "◆", // a decision standing on a person
  completed: "✓", // done
  abandoned: "○", // hollow — nothing left to resume
  alarm: "▲", // filled triangle — the loudest severity
  warn: "△", // hollow triangle — caution
  info: "i", // informational
};

/** What an unrecognised variant renders: the honest "this build has never heard of that". */
const UNKNOWN_DOT_GLYPH = "?";

export function Dot({ variant }: { variant: string }) {
  const v = KNOWN.has(variant) ? (variant as DotVariant) : undefined;
  return (
    // `aria-hidden` because the mark is redundant with the label its consumers render beside it
    // (LifecycleList's "Task progress: …" span, AttentionQueue's "Severity: …" image) — announcing
    // "◆" on top of that is noise.
    <span className={dot({ variant: v })} aria-hidden="true">
      {v ? DOT_GLYPHS[v] : UNKNOWN_DOT_GLYPH}
    </span>
  );
}
