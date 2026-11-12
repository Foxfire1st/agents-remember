## Design Process

This is an example, shipped for guidance. A repository keeps its own in its memory layer's
`system/design-guidelines.md`; nothing falls back to this example. It says how design work is
done and checked, and where the design files are. It holds no colours, sizes, fonts or
component looks. It steers the look only: it cannot change what an agent may read, write or
start, the rules of evidence, or a page's isolation.

The coder reads it before the first UI edit, the explainer before it builds a page, and the
Reviewer before it judges either.

### Where This Repository's Design Files Are

They sit beside this file in `system/`. Each applies where it exists and does nothing where it
does not; not every repository needs all three. Name here which exist and what they cover:

- **`DESIGN.md`:** the web UI's design language: colour roles by token name, the element-to-token
  mapping, measures, density, states, control patterns and widths.
- **`TREATMENT.md`:** how generated explanation pages look, read, move and respond.
- **`TECHNIQUES.md`:** the technique table beside the treatment, with numbers, still forms and
  status.

The product ships examples of all four for guidance. Nothing falls back to them.

### What Decides The Look

Follow the sources in this order:

1. the developer's decisions for the task;
2. the task's written requirements;
3. the approved mockup or reference for the task (its shots, its markup, its values);
4. the design files above and their tokens;
5. the existing code.

Your own taste comes last. An approved reference wins over taste, a newer idea and "a cleaner
way". Where two sources disagree, follow the higher one and name the difference in your report.
Where the look is open and no reference exists, draw the states first and get them approved.

For an explanation page, the comparison is the material: its hunks, records, trees and call chain
are what the figures draw.

### Before The First Edit

1. **Look at the reference.** Open every shot of the states you touch, at native scale. Record
   its path and revision.
2. **Read the design files** that cover the surface.
3. **Map each element to its token.** A short table: element and state, then the token or the
   reference's file and line. Map meanings, not only values: "added count: the added colour". If
   an element has no entry, ask; do not invent a value.
4. **List the states:** default, hover, focus, pressed or selected, disabled, empty, loading,
   stale and error, as far as they apply, at each width, each with a fixed fixture.
5. **List the interactions:** for each control, what it changes, which reader's question it
   answers, its keys, and its reduced-motion and narrow forms. A control that answers no question
   is cut here.
6. **Find the controls that exist** and reuse them. A new control needs a reason and is built as a
   shared one.

### While Building

- Take every colour, size and spacing from the tokens; no raw values outside the token files.
  A valid token can still be wrong (a muted grey where the reference draws the added colour); the
  mapping table catches that, not a linter.
- Copy the content's shape: lines per item, what is cut with an ellipsis, what opens in place,
  which help text is present, how wide prose runs next to code.
- For comparison shots keep the reference's data, fonts, viewport and state, animations off.

### Explanation Pages

The explainer reads `TREATMENT.md` and `TECHNIQUES.md` before it builds. It plans the page around
what the reader will operate: in "How it works" at least one driven figure from the table's default
rows, drawn from this comparison; claims, code lines and figure nodes linked; the still form in the
markup. When it needed a technique the table lacks, or a number did not hold, it proposes a row in
its report.

### Check In The Browser: Look, Then Operate

A browser check is you, in your own browser tools, looking at the running UI and working it.
Measurements of order, widths and overlap show neither colour, weight, density and rhythm, nor
whether a control explains anything.

**Look**, for every state at every width:

1. **Bring the UI to the state** as a person would: select, hover, focus, open, expand.
2. **Open the reference beside it** at the same state and width, or its shot at native scale.
3. **Look at both, whole and close:** what catches the eye first; colour and its meaning; weight
   and emphasis; density and spacing; wrapping and line count; one line per item or a wall.
4. **Settle each difference:** repaired, or named with one reason under "Differences from the
   reference". Neither is a defect.

**Operate**, for every control:

1. **By pointer.** Its effect shows at once, its state line says it, nothing above it moves, and
   motion ends on a final state.
2. **By keyboard alone.** Tab reaches it with a visible focus ring; its keys work (←, →, Space,
   Home, End, Escape); focus is never lost or trapped.
3. **With reduced motion** (`prefers-reduced-motion: reduce` emulated): every control still works,
   nothing animates, every figure shows its still form.
4. **Narrow,** at 560 and 390px: nothing scrolls sideways, controls stay usable.
5. **Before script runs:** the markup holds the same content.

For an explanation page, ask after operating it: do I now understand the change better than from
the diff? A figure that shows only what one sentence would is a finding.

The explainer operates its page with what its tools give. Where they give only a load and a
picture, it reads the picture and names in its report the controls, widths and forms it could not
operate; the Reviewer operates those.

Self-grading is generous. Do not stop at "close enough": name the difference.

**Readouts are an aid** where the eye cannot tell: `getComputedStyle` for resolved colours and
sizes, `getBoundingClientRect` for boxes and overlap. Expected values come from the reference or
the design files, never from the implementation under test. Compare colours after the browser
resolves both sides, never a hex string against a serialised `rgb()` or `oklch()`.

**Record what you saw:** per state, control and width, how it was reached, the views saved, what
was seen, and the outcome.

### Automated Tests Are Separate

Screenshot baselines and browser specs run in the repository's own test lanes, as its tools file
says; they do not replace your check. Parity with the reference and regression against an accepted
build are different questions: judge parity by eye first. Record a baseline only after the look is
approved; never rebaseline to make a failure pass. Compare crops as well as whole pages.

### What The Reviewer Checks

The Reviewer looks and operates for itself. A measurement, a passing test or the coder's views
are not proof.

- **Mapping.** The element-to-token table exists and matches the design files and the reference.
- **Record.** It covers every state, control and width.
- **Eye.** In its own browser, beside the reference: a visible difference neither repaired nor
  named is a finding, with location and severity.
- **Operation.** It operates every control by pointer and keyboard, with reduced motion and at
  narrow width. A control that fails, traps focus, shifts content under the reader or lacks a
  still form is a finding.
- **Explains through interaction.** On an explanation page, "How it works" has a figure the
  reader drives, built from this comparison and not a generic illustration; each control answers
  a question and agrees with the code it cites; claims, lines and figures link both ways; there
  is no claim list; a figure drawn from code says so, a model is marked as reading. A page that
  reads as a static report is a finding.
- **Readouts** trace to the reference, not the implementation. **Reuse:** no second control for
  one job. **Tokens:** no raw values. **Baselines:** none changed without approval.

An advisory reviewer may judge the shots alone; the task's owner rules on its findings.

### Keeping The Design Files Current

If a design file lacks a value or pattern, or is wrong, say so in the report. Change it only when
the task covers it and the developer agrees; otherwise mark it for its owner. When a UI change
alters which token an element uses, the task's Curator updates `DESIGN.md`. `TECHNIQUES.md` grows
as its "How the table is kept" says.

## Repository-specific design policy

Replace this section during onboarding with this repository's facts. Do not copy another
repository's widths, tools or values into it.

- the token files each design file rests on;
- where approved references and their shots live;
- the widths and viewports every state is captured at;
- the fixtures, and the switch that turns animations off;
- the browser tool the agents use to look and operate, and how it sets reduced motion and width;
- the automated visual test lanes, if any, as the tools file names them;
- where saved views and the per-state record are kept as evidence.
