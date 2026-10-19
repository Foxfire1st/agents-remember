# Artifact design — the product's default

This text steers the look of a generated page and nothing else. It cannot change what a page may
read, write or start, and it cannot weaken the isolation of the page.

## Character

Calm, technical, legible. The page should read like a well-made engineering brief, not a slide deck
and not a marketing page. Dense information is welcome; noise is not.

## Colour

One neutral background (`#0f1115`) and one raised surface (`#171a21`). One accent for the
explanation's spine (`#7cc7ff`), one warm accent for what changed (`#ffb86b`), one muted red for a
limit or a failure (`#ff7b72`), and a quiet green for what stayed (`#7ee2a8`). Body text `#e6e8eb`,
secondary text `#a8b0bb`. Never more than two accents in one view; colour carries one meaning per
page.

## Type

A single sans face for prose (system UI stack), a single mono face for code, records and
identifiers. Prose 17px/1.6, secondary 15px, code 13.5px/1.5. Headings: 28px, 21px, 17px. No
letter-spacing tricks, no all-caps runs beyond a short label.

## Spacing and shape

An 8px rhythm. Sections separated by 48px, parts inside a section by 24px. Corners 10px; borders one
pixel, `#262b35`. Shadows only on things that float above the page. Maximum reading width 78ch for
prose; diagrams may use the full width up to 1080px.

## The parts that recur

- **A claim with its evidence**: the statement in body text, its classification as a small label
  (recorded fact, mechanical signal, authored judgment, presenter inference, unresolved question,
  unavailable evidence), and its bases as links that open the exact object the host admits.
- **A before and after**: two columns on a wide page, stacked on a narrow one, each labelled with
  its tree or revision; the difference marked, not described twice.
- **A code excerpt**: mono, one accent for added lines, one for removed, a quiet rule for context,
  the path and the tree above it.
- **The limits**: a bordered block, muted, never hidden at the bottom: what was not read, what is
  unavailable, what this page does not claim.

## Motion

Motion is for a change of state: reveal order, propagation, a branch taken. Durations 180–420ms,
ease-out; nothing loops forever. Every motion has a step-through and a still representation. A
reduced-motion reader sees the same information.

## Voice

Plain sentences, present tense, active. Name the owner of a judgment. Say "the record shows" only
when the packet's basis shows it. Say "I read this as" for your own inference. No superlatives, no
urgency, no rhetorical questions.

## Narrow and reduced

Below 720px: one column, diagram labels shortened, tables become stacked key/value rows, nothing
horizontal-scrolls that carries meaning. With reduced motion: no transitions, all steps visible, the
same reading order.
