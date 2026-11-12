# Design language

This is an example, shipped for guidance. A repository keeps its own web UI's design language in
its memory layer's `system/DESIGN.md`; nothing falls back to this example. It shows the sections
and the kind of entry each holds. Its values are placeholders: replace every one with the
repository's own, cited from its token files and approved references.

The design process is in `design-guidelines.md` beside this file. Generated explanation pages, if
the repository has them, follow `TREATMENT.md` and `TECHNIQUES.md`.

**Sources.** Name the token files and the approved references. Cite colours by token name, not by
file and line: lines move with every change of the token files. A value with no token names the
reference state it comes from. The tokens hold the values; this file gives their meaning and
place.

## Character

Two or three sentences: what the UI should feel like, and what carries state (colour, silhouette,
position). For example: "A light working surface, one accent, one sans face. State is carried by
colour and shape together, never by colour alone."

## Colour roles

One meaning per colour per surface. Colour never carries a meaning alone: a label, an icon or a
line style goes with it.

| Token | Meaning |
|---|---|
| `surface` | Page and reading column |
| `surface-raised` | Panels, popovers, menus |
| `text` | Main text |
| `text-muted` | Secondary text, idle controls |
| `border` | Borders and rules |
| `accent` | Selected, current, focus, primary action |
| `positive` | Added, done, live |
| `negative` | Removed, destructive, fault |

List values the reference uses that have no token yet in a second table, with the reference state
each comes from, and under "Open differences".

## Element to token

The mapping coders and Reviewers check. Map meanings, not only values.

| Element | Look | Source |
|---|---|---|
| Added count | `positive` | the reference's change list state |
| Selected tab | `accent` text and border | the reference's tab state |
| Focus | 2px `accent` outline | the reference's focus state |

## Type and reading measures

Faces, base size, and a row per surface:

| Surface | Size, line height | Measure | Source |
|---|---|---|---|
| Body prose | 16px, 1.6 | at most 75ch | the reference |
| Code | 14px, 1.5 | full width | the reference |

Prose keeps its measure; code may take the full width. Each surface keeps its own measure.

## Density

How many lines an item takes, what is cut with an ellipsis, what opens in place, where help text
goes when a row is narrow, what shows for the normal case (often nothing), and which parts scroll.

## States

A row each for hover, focus, selected, disabled, empty, loading, working, stale and error, as far
as the UI has them: the look and its source.

## Control patterns

The shared controls to reuse, by component name and file: tabs, segmented switch, menu, toggle,
diff or table views. A second look for a job a control already does is a defect.

## Embedded pages

If the UI frames generated pages (for example in an iframe), say what it gives them: the width
and height, whether the page scrolls inside itself, whether keys pressed inside stay there, and
which motion switch reaches the page. A framed page cannot read the UI's stylesheet; its
`TREATMENT.md` names the tokens it mirrors.

## Motion

The switch that turns all animation off and how `prefers-reduced-motion` is honoured; the flash
limit; durations of control changes; and the rule that content does not move under the reader.

## Widths

The viewports every state is captured at, the breakpoints and what changes at each.

## Open differences

Numbered: where the code and the reference disagree, values without tokens, and decisions not yet
built. Each item names both sides.
