# Engine Room · Pod Stage — FAILURE-MODE SCENARIOS

End-to-end animations for every **non-happy path** in the 5f spec, alongside the happy-path
Build-up + Tear-down. Authority: `05f_engine-room-phase-motion-and-design-spec.md` §5 (state
machine), §6 (triptychs), §7.5 (canvas mapping). Built in `podstage.html` as selectable scenarios.

## Doctrine (non-negotiable, §3 / §6)
- **blocked = STEADY red gate** (human choice required). **fault/stale = red FLICKER ≤3/s** on the
  *specific* engine. They MUST read differently.
- Every blocked/fault raises an **attention badge** (alarm parity).
- Pre-contract blocks render as a **fleeting ghost enclosure** (planned/ghost register) that says
  *creation is blocked and why* + offers recovery choices; it dissolves / morphs on recovery.
- Reduced-motion → render the end-state (no flicker/animation).

## Stage primitives (shared by the failure scenarios)
- `#gate` — steady red bar + label, repositioned over the blocked lane. Helper `gate(show,x,y,w,label)`.
- `#fleeting` — dashed ghost enclosure + BLOCKED title/sub. Helper `fleeting(show,title,sub)`.
- `.ghost` class — ghost a specific lane (e.g. memory) while the other stays solid. Helper `ghost(id,on)`.
- `#chip1..3` — recovery choice chips (row under the enclosure). Helper `chips([labels])`.
- `#attn` — "⚠ ATTENTION" badge (top-right). Helper `attn(show)`.
- fault-flicker — reuse `.prov.fault` (already built, ≤3/s).
- reindex reroute (T9c) — a rerouted/bent cyan conduit (energy-redistribution).
- abandon dissolve (T18) — enclosure fade + slight collapse.

## Controller
Build steps are factored into `STEP.{reset,codeWt,memWt,enginesDim,charge,running}` so happy build +
failure tails share one source. Scenarios live in a `SCENARIOS` registry; a `<select>` chooses one.
Stagger/`seq()` + flow draw-in/despawn + `clearStag` already exist.

## Scenarios (beat arcs)

### Happy (existing)
- **build** B0..B5 · **tear** D0..D6

### Batch A — pre-contract gate / fleeting blocks
- **t1b — Stale-base block** (T1b, canvas B0→B1)
  reset → preflight scan (official-line sweep) → **BLOCKED** (gate over official line + fleeting "BLOCKED ·
  stale base" + chips[fast-forward | proceed-stale] + attn) → recover(fast-forward: gate lifts, base goes
  fresh, ghost dissolves) → codeWt → memWt → enginesDim → charge → running.
- **t3b — Memory block** (T3b, canvas B2)
  reset → codeWt (code lane solid) → ledger gate verifying → **BLOCKED** (gate over MEMORY lane, memory
  lane ghosted, code solid; chips[reconciliation | disabled-memory]; attn) → recover(reconciliation: gate
  lifts, memWt materialises, coupler binds) → enginesDim → charge → running.
  *variant tail (note only): disabled-memory → code-only running, no memory lane.*
- **t7b — Provider-plan block** (T7b, canvas B2→B3, pre-contract)
  reset → codeWt → memWt + coupler (contract NOT anchored) → runtime plan checking → **BLOCKED** (gate
  *before* contract; engines never light; chips[retry | disabled | abandon]; attn) → recover(retry/config:
  contract anchors, engines deploy) → charge → running.

### Batch B — seeding outcomes
- **t9b — Seed/clone FAULT** (T9b, canvas B3→B4)
  build to seeding → GrepAI **FAULT** (isolated red flicker ≤3/s; CGC stays cyan; retry affordance; attn) →
  retry → GrepAI recovers (charge → green) → running.
- **t9c — Reindex reroute** (T9c, canvas B3→B4)
  build to seeding → CGC seed refused → conduit **reroutes to full reindex** (energy-redistribution, cyan,
  NOT red; "indexing" label) → indexing → green → running. *(soft / non-terminal)*

### Batch C — live & teardown
- **t12b — Sync block** (T12b, canvas live)
  running → official memory line moves → steady gate on MEMORY lane (code may advance); chips[merge-memory |
  skip-memory]; attn → recover(merge: memory syncs ff) → running.
- **t14c — Integration conflict** (T14c, canvas D2)
  idle → closeout commits → integrate replay attempt → **CONFLICT flash → steady STOP gate**; source branch
  does NOT move (all-or-nothing); attn → terminal blocked (developer must resolve). *(no auto-recovery)*
- **t18 — Abandon** (T18, canvas D5+D6 alt)
  working enclosure → worktree_abandon → enclosure **DISSOLVES** (fade + slight collapse), no landing
  beats → gone + "abandoned" record chip.

## Progress
- [x] Primitives (gate / fleeting / ghost / chips / attn / scan / dissolve) in SVG + CSS + helpers
- [x] `STEP` extraction (reset/codeWt/memWt/enginesDim/charge/running) + `SCENARIOS` registry + `<select>` selector
- [x] Batch A — t1b (F0–F8), t3b (M0–M7), t7b (P0–P7) — verified blocked + recover + running
- [x] Batch B — t9b (S0–S7, FAULT flicker on w-grep, isolated; retry) + t9c (R0–R6, soft reroute loop,
      cyan not red). Added generic `imsg(show,cx,cy,text,soft)` badge + `#flow-reroute` loop conduit
      (`p-reroute-cgc`). Fault uses alarm badge + attn; reroute uses `soft` (cyan) badge + NO attn.
- [x] Batch C — t12b (Y0–Y4, steady memory-lane gate + code-advance commit pulse + merge/skip → ff
      recover), t14c (C0–C4, closeout → replay → conflict flash → steady STOP gate, source unmoved,
      TERMINAL / no recover), t18 (X0–X3, worktree_abandon → `.dissolve` fade+collapse → gone +
      "abandoned" record). Added `STEP.idle`/`STEP.idleClean` (shared running-idle baseline);
      `.dissolve` now collapses in place (`transform-box:fill-box`); `clearFail` also clears `.dissolve`
      + resets the hist-chip text so it can't bleed between scenarios.
- [x] Verify via playwright-cli — all beats of all 10 scenarios step error-free; happy paths
      unregressed after the shared-helper changes; published to OD (disk-sync refreshes the canvas).

### Notes for resuming
- Helpers: `gate(show,x,y,w,reason,dir)` — the gate carries its OWN local reason badge (cyan-dot
  pointer + reason pill); `dir` = +1 badge below the bar / -1 above (use -1 when a node sits below).
  Each indicator must carry its reason locally (dev feedback); the bottom caption stays too.
  Also `fleeting(show,title,sub)`, `ghost(id,on)`, `scan(on,cx,cy)`, `chips([labels])` (max 3),
  `attn(show)`, `clearFail()`. STEP.* are the build steps.
- Each scenario's beat[0] calls `STEP.reset()` (which calls clearFail). Add new scenarios to the
  `SCENARIOS` registry AND the `<select>` optgroups.
- Batch C built (all DONE): t14c reuses `refuse('flow-int-code',true)` for the conflict flash then a
  steady `gate(...,-1)` STOP (no recover beat — the arc just ends BLOCKED; loop wraps to C0). t18
  applies `.dissolve` to `enc-border`/`w-code`/`w-mem` only (those have no positioning transform; the
  engines/wires just fade via opacity since `w-cgc`/`w-grep` rely on `translate(...)` and would fly to
  the origin if given a `transform` animation). t12b is a live gate on the memory lane from `STEP.idle`,
  with a `commit-code` pulse to show the code lane advancing while memory is gated.

## Backdrop loop (settled — single pre-rendered boomerang)
The faint blueprint video loops seamlessly as ONE pre-rendered boomerang clip — the forward clip then its
reverse, crossfaded at the seam — played by a single `<video loop>`. NOT two stacked clips, NOT a seam
fade (both were rejected by the dev):
- Build: `blueprint-engine-rev.mp4 = ffmpeg -i blueprint-engine.mp4 -vf reverse -an …`; then
  `blueprint-boomerang.mp4 = ffmpeg -i blueprint-engine.mp4 -i blueprint-engine-rev.mp4 -filter_complex`
  `"[0:v][1:v]xfade=transition=fade:duration=0.8:offset=7.2,format=yuv420p" -an` → 15.2 s, forward
  zoom-IN then reverse zoom-OUT. (`blueprint-engine.mp4`/`-rev.mp4` are build inputs; the page ships only
  the boomerang.)
- Page uses ONLY `blueprint-boomerang.mp4`. `.backdrop` wrapper is plain; the `<video>` owns `opacity:.14`,
  the amber tint filter, `mix-blend-mode:screen`. JS = a one-line autoplay nudge (no counter-zoom, no rAF).
- Why seamless: starts+ends on the same frame0 (loop-seam MSE 7.2 ≈ frame-continuous); the peak turnaround
  (fwd→rev) is the baked 0.8 s xfade. The backdrop gently BREATHES (zoom in ~7.6 s, out ~7.6 s) — the dev
  chose motion over the earlier static counter-zoom. The loop-wrap turnaround (trough/frame0) is
  frame-continuous but NOT xfaded; if a subtle pulse shows there, add a tail-over-head loop-crossfade.
