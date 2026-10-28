# Explainer techniques

This is an example, shipped for guidance. A repository keeps its own technique table in its
memory layer's `system/TECHNIQUES.md`, beside `TREATMENT.md`; nothing falls back to this example.
The table steers the look, motion and interaction of an explanation page and nothing else. It
cannot change what the explainer may read, write or start, the rules of evidence, or the page's
isolation.

Every technique a page uses, with its numbers and still form. Colours are the treatment's roles.
Each row names its kit part (`kit/<name>/<version>/`) or plain DOM, SVG, CSS or Canvas2D. The rows
the reader drives come first; "How it works" uses at least one of them.

## Shared numbers

- **Response:** a control's first visible effect within 100ms; hover and focus highlights at once,
  without a transition.
- **Durations:** 180–420ms per transition; nothing longer than 1.2s without a stop.
- **Easing:** entering `outCubic`, `cubic-bezier(0.33, 1, 0.68, 1)`; between two states
  `inOutCubic`, `cubic-bezier(0.65, 0, 0.35, 1)`; `linear` only for a dot along a path. Names as in
  `ease` of `kit/ar-video/23fd71bf/`.
- **Start:** on the reader's act; a reveal under 1.2s may play once when 60% of its figure is in
  view. Play stops on the last step; nothing loops.
- **Limits:** at most 12 steps, nodes or edges per figure; one row of controls per figure.
- **Step controls:** Back, Play/Pause, Next, a scrubber and a count ("3 / 7"); ←, →, Space, Home
  and End while the figure has focus.
- **Switches:** a segmented control with words, not icons; ← and → move the choice.
- **Selection:** hover or focus previews, click or Enter pins, Escape clears; one pin at a time.
- **State line:** a sentence under each driven figure naming its state, `aria-live="polite"`.
- **Time:** a moving figure is a function of its time ("Frame = f(t)", `VOCABULARY.md` in the same
  folder); the scrubber sets t; reduced motion shows the final frame.
- **Linking key:** a page-internal attribute such as `data-link` ties a claim, its code lines and
  its figure nodes; the host's `data-part` and `data-evidence-claim` are not reused for it.

## Techniques

| Technique | Shows | Drawn as | Numbers | Still, reduced, narrow | Status |
|---|---|---|---|---|---|
| Linked highlight | what a claim, its lines and its figure node share | DOM and SVG on the linking key: hover or focus on one lights the others in `current`, dims the rest; a click pins. | at once; others at 45%; pin: 2px `current` outline | the claim names its lines and nodes in text; narrow: a pin opens the lines below the claim | default |
| Step-through | a sequence in time: a request's life, a sync, a failure path | Numbered steps on the step controls; current `current`, past `ink`, future `muted`; the state line names function and file:line. Keyframed scenes: Motion Canvas (`kit/motion-canvas/3.17.2/`, `renderFrame` at t); otherwise SVG. | 280ms a change; ≤ 12 steps; Play dwells 2.4s | ordered list, a line and small state per step | default |
| Trace a path | which code a request or record passes, entry to effect | The real call chain in inline SVG with the diagram classes of `kit/open-generative-ui/0.1.0-8391a94/` (`.box`, `.node`, `.arr`, `.leader`): 1px boxes, 6px corners, 11px mono labels, leader lines where labels collide. Selecting a node lights its path in `current` and opens its hunk below. | ≤ 12 nodes; path lights in 180ms | numbered hops with file:line; caption "drawn from the code at <revision>" | default |
| Propagation | an effect travelling the traced path | A 6px `spine` dot runs each edge on Play or Next; a node takes a `spine` border on arrival; fan-outs run together. On a canvas, the vocabulary's `spark`. | 300ms an edge, linear; node 180ms | final state, edges numbered by arrival | default |
| Branch taken (what-if) | which path a condition takes | A switch names the cases the hunk checks ("the root is a link"); taken edge solid, untaken dashed at 35%; the deciding lines light in the excerpt. | 220ms crossfade, `inOutCubic`; ≤ 4 cases | every branch drawn and labelled, its deciding line beneath | default |
| Before and after | a state, structure or record before and after, on one input | One figure, a "before · after" switch, each side labelled with its revision; the same input through both; the difference in `new` and `gone`, the rest `ink`. From 960px also side by side. | morph 360ms, `inOutCubic` | before above after | default |
| Code morph | one hunk becoming another under the reader's switch | Shiki's magic-move (`kit/shiki/4.5.0/`): kept tokens slide, new ones fade in, removed ones fade out; driven by the before/after switch or a scrubber over revisions. | 360ms, `inOutCubic`; hunks ≤ 24 lines sharing most tokens | both snippets, before above after | default |
| Ladder of abstraction | the whole change, then a part, then its hunk | DOM and SVG. Top rung: the change's parts (invariants, modules) as boxes with their real changed-line counts; a click descends in place; a breadcrumb climbs back. | ≤ 12 boxes a rung, 3 rungs; opens in 240ms, height reserved | nested list, all rungs open | default |
| Table | like facts: risks, invariants, files | Header 11px mono capitals, `muted`; 1px row rules; grades as `reading` marks. Chips above filter by kind or grade and keep their counts; "n of m shown"; a header sorts. | ≤ 5 chips; 8 rows, then "show all" | all rows; key/value rows below 560px | default |
| Code excerpt | the lines a claim is about | Shiki (`kit/shiki/4.5.0/`, `github-dark`) in a `raised` block, path and revision above; added + on a 12% `new` wash, removed − on a 12% `gone` wash; unchanged stretches fold into "n unchanged lines", opening in place. | ≤ 24 lines open | long lines wrap, hanging indent | default |
| Claim and basis mark | what a statement rests on | A mono label after the claim, named by the evidence rules: solid `rule` border for the record, dashed `reading` for the explainer's reading and grades, dotted for intended, none for open or unavailable. Its link: path and lines, `spine`, dotted underline; hover lights the lines. A legend above the title. | marks 11px, 3px corners; links 0.85em | same | default |
| Moved block | code or a responsibility that moved | On Next or the switch, the block leaves a dashed ghost and travels on a curve to its destination; changed lines are marked after it lands. | 420ms, `inOutCubic`; ghost 30%; ≤ 5 moves, stagger 120ms | origin "moved to →", destination "moved from ←" | default |
| Scrollytelling | a sequence told in prose steps beside one figure | The figure sticks at the top of its part (`position: sticky`); a step reaching the middle of the frame (IntersectionObserver) sets the figure's step; the step controls do the same. | ≤ 8 steps; figure ≤ 60% of the frame's height | final figure, steps listed; below 560px a figure above each step | optional |
| Scrubbable number | how a bound in the code shapes behaviour, such as a batch size | A number in the prose with a dotted `spine` underline: drag or ←/→ changes it within the code's range, and what depends on it follows; the caption names the lines computed from, as `reading`. | ≤ 2 a part | the code's value plus two worked values | optional |
| Sandbox | the code's rule applied to a case the reader builds | The reader adds or removes a few items (a root, an ended generation, a link); the page applies the rule and shows the outcome; a reset. A model of named lines, marked `reading`. | ≤ 8 items | three worked cases side by side, one figure each | optional |
| Term popover | a domain term's meaning where it is used | A dotted-underlined term shows a one-line meaning and its memory record on hover or focus. | one line | a short glossary at the part's end | optional |
| Repository tree | where the changed files sit | The vocabulary's radial tree in Canvas2D with its camera (`Proj`, `orbitKeys`), changed files lit; drag or arrow keys orbit; a lit file opens its excerpt. Only when structure is the point. | one camera move, ≤ 1.2s | indented tree, changed files marked | optional |
| Reveal order | the order to read a figure on first view | The open-generative-ui first-render stagger on one figure's elements, never the page: fade in, rising 8px. | 400ms ease-out; stagger 60ms; sixth on together; once | all shown, numbered | optional: was default; a step-through shows order better |
| Stale part | a part written against older code than the one on screen | The host marks the parts it lists (give each a title and an id): 2px `stale` left rule, a fading `stale` wash, a mono "stale" note; reasons stay in its trusted list. | no motion | same | default |
| Limits | what was not read, not comparable or unavailable | A titled part, "Not covered", `muted`, 1px border; a limit that changes a claim also stands beside it. | none | same | default |
| Flow figure | parts and how they connect, as a still picture | Inline SVG, open-generative-ui diagram classes. | ≤ 12 nodes | caption and list | retired: successor "Trace a path", which carries its drawing rules; nothing to select explains less |
| Pending shimmer | content not yet written | A looping skeleton gradient. | 1.6s loop | none | retired: nothing loops; a part being redone keeps its old text and stale mark |

The vocabulary's arithmetic, easing and camera are free to use; its film look is not (bloom,
scanlines, grain, dust, title slams, console typing, music cuts).

## How the table is kept

The table is kept through tasks; the explainer does not edit it. When a page needed a missing
technique, or a number did not hold, the explainer proposes the change in its report, as a row with
the page where it served. A task adds it as `optional`, and a later one makes it `default` once it
has served a second page. A replaced technique is marked `retired` with its successor and reason,
never deleted, so no page brings it back. Nothing lives only in a chat.
