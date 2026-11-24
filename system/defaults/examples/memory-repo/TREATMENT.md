# Explainer treatment

This is an example, shipped for guidance. A repository keeps its own treatment in its memory
layer's `system/TREATMENT.md`, beside `TECHNIQUES.md`; nothing falls back to this example. It
steers how an explanation page looks, reads, moves and responds, and nothing else. It cannot
change what the explainer may read, write or start, the rules of evidence, or the page's
isolation; where it seems to disagree with them, they hold. Techniques and their numbers are in `TECHNIQUES.md`; a web UI that frames the page, if any, is
in `DESIGN.md`.

## Intent

The essay and its driven figures explain the change together: the page reads in full without its
animations, and the central mechanism is shown by at least one figure the reader drives—stepping a
sequence, switching a condition, tracing a path, morphing code or testing inputs in a sandbox.
Every statement shows what it rests on.

Each figure is built from this comparison's own material: its hunks, records and trees, the real
call chain, the conditions the code checks. An illustration that would fit any change explains
none.

The markup holds the full explanation so the static page still reads with motion or script off.

## Who reads, and in what order

The reader is the developer who asked, a reviewer coming to it cold, or a later reader of the
documentation, with the code a click away and none of its context. Tell it in this order, so a
reader who stops early still holds a true picture:

1. **The answer.** A title stating what the change does or answering the question; a lede of at
   most four sentences: before, now, for whom. Beneath it, an overview map when it clarifies the
   whole change as regions that open to their detail.
2. **What it means for the rules.** Which invariants changed, weakened, strengthened or stayed,
   and why. Selecting a rule lights its code and its place in the figures.
3. **How it works now.** The central mechanism, before and after, shown by at least one figure the
   reader drives from `TECHNIQUES.md` (for a planned binding, the intended mechanism, marked
   intended), paired with concise prose that reads in full on its own.
4. **Where to look first.** Ordered entry points into the code, one reason each, each opening its
   highlighted excerpt in place. Say that the order is yours.
5. **What stayed the same.** The guarantees a reader might fear were touched.
6. **Risks and open questions,** marked as your reading, in a table the reader can filter.
7. **Not covered.** What was not read, not comparable or unavailable.

A focused explanation keeps this order inside its part. Skip a step with nothing to say; never pad
one.

## How the essay and its figures work

- **Prose and figures together.** The essay reads in full without animations, and the central
  mechanism is carried by at least one driven figure. Secondary parts stay in prose when a figure
  adds nothing.
- **Drive.** Show a mechanism through a figure the reader drives: step, scrub, morph code, toggle
  a condition, switch before and after on one input, test a rule in a sandbox, or select a node to
  trace its path.
- **Link.** A claim, its code lines and its figure nodes are one thing in three places: hover or
  focus on one lights the others, a click pins, Escape clears.
- **Descend.** A ladder of abstraction: the whole change, a part, its hunk. Detail opens in place
  below what the reader chose; the way back stays visible.
- **Ask.** Every control answers a reader's question: what happens when…, where is this in the
  code, what changed here, which of these matter. A control that answers none is decoration.
- **Say the state.** Each driven figure states its state in a sentence beside it ("Step 3 of 7:
  the record is written before the root exists").

## Character and voice

Lively and legible: a working instrument for one change, closer to an explorable explanation than
to a report or a slide deck. It answers the reader's hand at once and invites the next question.
Dense information is welcome; noise is not.

Plain sentences, present tense, active voice; at most four sentences a paragraph. Name the owner
of each judgment. Say "the record shows" only when the basis shows it, and "I read this as" for
your own inference. No superlatives, urgency, rhetorical questions, exclamation marks or emoji.

## Colour

A dark page. Each role keeps one meaning across the page; `TECHNIQUES.md` names roles. A
repository whose web UI has tokens maps each role to a token by name here.

| Role | Value | Meaning |
|---|---|---|
| `bg` | `#0f1115` | the page |
| `raised` | `#171a21` | figures, code, tables, controls |
| `rule` | `#262b35` | 1px borders, idle edges, a scrubber's track |
| `ink` | `#e6e8eb` | body text, what stayed |
| `muted` | `#a8b0bb` | secondary text, limits, context lines, steps not yet reached |
| `spine` | `#7cc7ff` | section labels, links, a signal travelling a path |
| `current` | `#ffd479` | where the reader is: selected node, current step, pinned claim, focus |
| `new` | `#7ee2a8` | added, the after state, a new path |
| `gone` | `#ff7b72` | removed, broken, a failing path |
| `stale` | `#ffb86b`, dashed | a part older than the code it explains |
| `reading` | `#c9a7ff`, dashed | the explainer's inference and grades |

A figure uses `spine`, `current` and at most two meaning colours. Colour never carries meaning
alone: a word, a sign (+, −, →) or a border style goes with it. Body text keeps at least 7:1
contrast, labels at least 4.5:1.

## Type and measure

One sans face for prose, the system UI stack; one mono face for code, paths, identifiers, marks,
figure labels and step counts. No web fonts.

- Title 28px/1.25, weight 650; lede 18px/1.6; prose 17px/1.6; secondary 15px; code 13.5px/1.5;
  marks and figure labels 11px.
- Section labels 13px, weight 700, capitals, 0.12em tracking, `spine`. Nothing else in capitals.
- Prose at most 78ch; figures, tables and code take the page's width, up to 1080px.
- An 8px rhythm: 48px between parts, 24px inside. Corners 8px on blocks, 3px on marks. Shadows
  only on what floats.

## Motion and response

Motion clarifies how one state becomes the next: an order, a propagation, a code morph, a sequence
or a measured change. A control shows its effect at once, and every moving figure stays inside
these bounds:

- **Bounded playback.** A scripted figure plays once when it comes into view or on the reader's
  act, leads the eye to one place, and stops on its final state without looping. Run one figure at
  a time.
- **Separate from the dashboard's quiet switch.** An explanation page is viewed apart from the
  dashboard: no reduced-motion or quiet-mode check applies to it, and the dashboard's reduced-motion
  setting and quiet switch do not apply to it.
- **Stable layout.** Reserve each figure's box before it draws, and open detail below the control
  that opened it so surrounding text and scroll position stay put. A part driven by scrolling also
  provides step controls.
- **Complete still form.** The markup holds the same content and steps as the moving figure.
- **Keyboard control.** Every moving figure pauses, steps and scrubs from the keyboard.

## Honest figures

- A figure drawn from the code says so in its caption: "drawn from the code at <revision>, not a
  recorded run". A pass or a failure shows only where a record holds it.
- A what-if switch offers the cases the hunk's conditions handle and shows what the code decides.
- A number the reader can change spans only values the code or record defines, or a range marked
  illustrative. What the page computes from it is a model: mark it as your reading and name the
  lines it follows.
- No invented numbers, no passed badge without a recorded run, no partial graph drawn as whole.
- A limit that changes a claim stands beside it, not only under "Not covered".

## Prohibitions

- No claim list. The claim map is for the product; on the page each claim is a sentence in the
  part it explains, beside its code or figure, with its mark.
- No text walls. One item per row; the diff is marked, not retold in prose.
- No glow, neon, gradient fills, glass, stock "AI" imagery or meaningless icons.
- No name or logo of the tool that wrote the page; the page speaks for the repository.
- No WebGL or shaders by default; HTML, CSS, SVG and Canvas2D suffice.

## Still, narrow and keyboard

**Still form.** The markup, before any script runs, holds the whole explanation: each figure's
steps as an ordered list, both sides of every switch, every rung open; a canvas figure has a
markup twin. Script turns it into the driven figure and never adds content the still form lacks.

**Dashboard quiet switch.** The dashboard's reduced-motion setting and quiet switch apply to the
dashboard application, not to explanation pages. No reduced-motion or quiet-mode check applies to
an explanation page.

**Narrow.** 600–700px is a normal width. Below 560px: one column, before above after, a sticky
figure becomes a figure above each step, labels shortened, tables as key/value rows, code wrapped.
Down to 390px nothing scrolls sideways; controls stay at least 32px high.

**Keyboard.** Tab reaches every control, with a visible 2px `current` focus ring; each driven
figure names its keys beneath it.
