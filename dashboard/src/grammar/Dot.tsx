import { cva } from '../../styled-system/css';

// Attention severity has one cell of ink and a glyph. Motion is additive:
// Calm and reduced-motion modes must keep every severity distinguishable.
// An unknown severity keeps the muted question mark, visibly unclassified.
const dot = cva({
  base: {
    display: 'inline-block',
    // One monospace cell, centred: every variant occupies the same column whatever its glyph, so
    // the rails stay aligned and a state change never reflows the row.
    width: '1ch',
    marginRight: '0.4em',
    // A dot put straight into a flex row is squeezable without this. No consumer does that today:
    // AttentionQueue wraps the dot in a cell of its own.
    flexShrink: '0',
    textAlign: 'center',
    lineHeight: '1',
    fontSize: '0.95em',
    // Decorative: dragging a selection across a row must not pick up `◆`.
    userSelect: 'none',
    color: 'muted',
  },
  variants: {
    variant: {
      alarm: {
        color: 'alarm',
        animation: 'pulse 0.6s steps(1) infinite',
        _motionReduce: { animation: 'none' },
      },
      warn: { color: 'amber' },
      info: { color: 'cyan' },
    },
  },
});

// The recipe owns the supported severity vocabulary.
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
  alarm: '▲', // filled triangle — the loudest severity
  warn: '△', // hollow triangle — caution
  info: 'i', // informational
};

/** What an unrecognised variant renders: the honest "this build has never heard of that". */
const UNKNOWN_DOT_GLYPH = '?';

export function Dot({ variant }: { variant: string }) {
  const v = KNOWN.has(variant) ? (variant as DotVariant) : undefined;
  return (
    // `aria-hidden` because the mark is redundant with the label its consumers render beside it
    // (AttentionQueue's "Severity: …" image) — announcing
    // "◆" on top of that is noise.
    <span className={dot({ variant: v })} aria-hidden="true">
      {v ? DOT_GLYPHS[v] : UNKNOWN_DOT_GLYPH}
    </span>
  );
}
