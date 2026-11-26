import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js';
import {
  Rect,
  Txt,
  all,
  createKit,
  easeInOutCubic,
  paint,
  renderFrame,
  scene,
  waitFor,
} from './kit/motion-canvas/3.17.2/motion-canvas-kit.js';
import {
  SVG_CLASSES_CSS,
  THEME_CSS,
} from './kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js';
import { clamp, col, ease, lerp, tween } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';

// Inject OpenGenerativeUI base tokens and diagram classes before exemplar.css overrides.
const oguiStyle = document.getElementById('ogui-kit-styles');
if (oguiStyle) {
  oguiStyle.textContent = `${THEME_CSS}\n${SVG_CLASSES_CSS}`;
}

// 1. Highlighted code excerpt (Shiki github-dark) + foldable unchanged context.
function initCodeExcerpt(highlighter) {
  const btn = document.getElementById('btn-toggle-context');
  const status = document.getElementById('excerpt-status');
  const foldNotice = document.getElementById('fold-notice');
  const contextBlocks = [...document.querySelectorAll('#excerpt-code .excerpt-context')];
  const codeCells = [...document.querySelectorAll('#excerpt-code .diff-src')];

  for (const cell of codeCells) {
    const raw = cell.textContent ?? '';
    if (!raw.trim()) continue;
    const { tokens } = highlighter.codeToTokens(raw, { lang: 'typescript', theme: 'github-dark' });
    const lineTokens = tokens[0] || [];
    cell.textContent = '';
    for (const token of lineTokens) {
      const span = document.createElement('span');
      span.textContent = token.content;
      if (token.color) span.style.color = token.color;
      cell.appendChild(span);
    }
  }

  let expanded = false;
  btn.addEventListener('click', () => {
    expanded = !expanded;
    for (const block of contextBlocks) block.hidden = !expanded;
    foldNotice.hidden = expanded;
    btn.setAttribute('aria-expanded', String(expanded));
    btn.textContent = expanded ? 'Hide 6 unchanged lines' : 'Show 6 unchanged lines';
    status.textContent = expanded
      ? 'Showing full excerpt (lines 6–24) including 6 unchanged context lines.'
      : 'Showing changed hunk (lines 12–23) with 6 unchanged lines folded.';
  });
}

// 2. Shiki Magic Move code morph.
function initCodeMorph(highlighter) {
  const STATES = {
    before: {
      label: 'Before',
      summary: 'Exponential backoff doubles without a ceiling and outlives the 30 s lease after attempt 5.',
      code: [
        'export function computeRetryDelay(attempt: number, baseMs: number): number {',
        '  const backoff = baseMs * Math.pow(2, attempt - 1);',
        '  return Math.round(backoff);',
        '}',
      ].join('\n'),
    },
    after: {
      label: 'After',
      summary: 'Backoff is clamped to 80% of leaseMs and multiplied by a uniform random factor.',
      code: [
        'export function computeRetryDelay(',
        '  attempt: number,',
        '  baseMs: number,',
        '  leaseMs: number,',
        '  random: () => number = Math.random,',
        '): number {',
        '  const ceiling = Math.floor(leaseMs * 0.8);',
        '  const capped = Math.min(ceiling, baseMs * Math.pow(2, attempt - 1));',
        '  return Math.round(random() * capped);',
        '}',
      ].join('\n'),
    },
  };

  const ANIMATED = {
    duration: 360,
    stagger: 0,
    delayMove: 0.1,
    delayLeave: 0,
    delayEnter: 0.25,
    delayContainer: 0.1,
    easing: 'cubic-bezier(0.65, 0, 0.35, 1)',
    animateContainer: true,
  };

  const switchBtns = [...document.querySelectorAll('#fig-code-morph .segmented button[data-state]')];
  const still = document.getElementById('morph-still');
  const stillBlocks = [...still.querySelectorAll('.still-block[data-state]')];
  const stage = document.getElementById('morph-stage');
  const status = document.getElementById('morph-status');

  for (const block of stillBlocks) {
    const key = block.dataset.state;
    const entry = STATES[key];
    const pre = block.querySelector('pre');
    const codeEl = pre ? pre.querySelector('code') : null;
    if (!entry || !pre || !codeEl) continue;
    const { tokens } = highlighter.codeToTokens(entry.code, { lang: 'typescript', theme: 'github-dark' });
    codeEl.textContent = '';
    tokens.forEach((lineTokens, lineIdx) => {
      if (lineIdx > 0) codeEl.appendChild(document.createTextNode('\n'));
      for (const token of lineTokens) {
        const span = document.createElement('span');
        span.textContent = token.content;
        if (token.color) span.style.color = token.color;
        codeEl.appendChild(span);
      }
    });
    pre.setAttribute('data-highlighted', 'shiki');
  }

  let active = 'after';
  const morph = document.createElement('shiki-magic-move');
  morph.highlighter = highlighter;
  morph.lang = 'typescript';
  morph.theme = 'github-dark';
  morph.options = ANIMATED;
  morph.code = STATES[active].code;
  stage.appendChild(morph);

  function syncStillSelection(key) {
    for (const block of stillBlocks) {
      block.classList.toggle('is-selected', block.dataset.state === key);
    }
  }

  function updateStatus(key) {
    status.textContent = '';
    const tag = document.createElement('span');
    tag.className = 'state-tag';
    tag.textContent = STATES[key].label;
    status.append(tag, document.createTextNode(STATES[key].summary));
  }

  function select(key, focusBtn = false) {
    if (!STATES[key]) return;
    active = key;
    for (const btn of switchBtns) {
      const selected = btn.dataset.state === key;
      btn.setAttribute('aria-pressed', String(selected));
      btn.tabIndex = selected ? 0 : -1;
      if (selected && focusBtn) btn.focus();
    }
    syncStillSelection(key);
    updateStatus(key);
    morph.options = ANIMATED;
    morph.code = STATES[key].code;
  }

  for (const btn of switchBtns) {
    btn.addEventListener('click', () => select(btn.dataset.state));
    btn.addEventListener('keydown', (event) => {
      const order = ['before', 'after'];
      const idx = order.indexOf(active);
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        select(order[(idx + (event.key === 'ArrowRight' ? 1 : -1) + 2) % 2], true);
      } else if (event.key === 'Home') {
        event.preventDefault();
        select(order[0], true);
      } else if (event.key === 'End') {
        event.preventDefault();
        select(order[order.length - 1], true);
      }
    });
  }

  still.hidden = true;
  stage.hidden = false;
  syncStillSelection(active);
  updateStatus(active);
}

// 3. Motion Canvas 4-step sequence (deactivated whenever idle).
async function initStepThrough() {
  const STEPS = [
    { frame: 0, text: 'Step 1 of 4: runWorker catches failure on attempt 5 with a 30.0 s lease (src/queue/worker.ts:44).' },
    { frame: 30, text: 'Step 2 of 4: raw exponential backoff computes 64.0 s, 34.0 s past lease expiry (src/queue/retry.ts:20).' },
    { frame: 60, text: 'Step 3 of 4: ceiling clamps the upper bound to 24.0 s, 80% of leaseMs (src/queue/retry.ts:21).' },
    { frame: 90, text: 'Step 4 of 4: requeue schedules attempt 6 at 11.5 s (r = 0.48), inside the 30.0 s lease (src/queue/enqueue.ts:52).' },
  ];

  const PALETTE = {
    well: '#0d121a',
    panel: '#18212e',
    rule: '#313f52',
    ink: '#eae4d7',
    muted: '#9ba6b8',
    spine: '#58c9dc',
    current: '#e5a445',
    fresh: '#49d39c',
    gone: '#e05a47',
  };

  const retryScene = scene('exemplar-retry-steps', function* (view) {
    view.add(new Rect({ width: 880, height: 320, fill: PALETTE.well }));

    const title = new Txt({
      text: 'STEP 01 · ATTEMPT 5 FAILS',
      x: -396,
      y: -124,
      offset: [-1, 0],
      fill: PALETTE.spine,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 13,
      fontWeight: 700,
    });
    const meta = new Txt({
      text: 'Lease: 30.0 s   |   Base: 4.0 s',
      x: 396,
      y: -124,
      offset: [1, 0],
      fill: PALETTE.muted,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 13,
    });

    const track = new Rect({
      x: 0,
      y: 8,
      width: 792,
      height: 44,
      radius: 6,
      fill: PALETTE.panel,
      stroke: PALETTE.rule,
      lineWidth: 1.5,
    });

    const pxPerSec = 792 / 64;
    const leaseX = -396 + 30 * pxPerSec;
    const capX = -396 + 24 * pxPerSec;

    const leaseRule = new Rect({ x: leaseX, y: 8, width: 2, height: 92, fill: PALETTE.gone });
    const leaseLbl = new Txt({
      text: 'Lease expiry (30.0 s)',
      x: leaseX,
      y: -52,
      fill: PALETTE.gone,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 12,
    });

    const capRule = new Rect({ x: capX, y: 8, width: 2, height: 76, fill: PALETTE.spine, opacity: 0 });
    const capLbl = new Txt({
      text: '80% ceiling (24.0 s)',
      x: capX - 8,
      y: 66,
      offset: [1, 0],
      fill: PALETTE.spine,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 12,
      opacity: 0,
    });

    const bar = new Rect({
      x: -396,
      y: 8,
      offset: [-1, 0],
      width: 4 * pxPerSec,
      height: 28,
      radius: 4,
      fill: PALETTE.current,
    });

    const readout = new Txt({
      text: 'Initial delay: 4.0 s',
      x: -396,
      y: 112,
      offset: [-1, 0],
      fill: PALETTE.ink,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 15,
      fontWeight: 600,
    });

    const pill = new Rect({
      x: 396,
      y: 112,
      offset: [1, 0],
      width: 210,
      height: 30,
      radius: 4,
      fill: PALETTE.panel,
      stroke: PALETTE.current,
      lineWidth: 1.5,
    });
    const pillTxt = new Txt({
      text: 'retry pending',
      x: 291,
      y: 112,
      fill: PALETTE.current,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
      fontSize: 12,
    });

    view.add([track, bar, capRule, capLbl, leaseRule, leaseLbl, title, meta, readout, pill, pillTxt]);

    yield* all(
      bar.width(792, 1.0, easeInOutCubic),
      bar.fill(PALETTE.gone, 1.0, easeInOutCubic),
      title.text('STEP 02 · RAW BACKOFF EXCEEDS LEASE', 0.2),
      readout.text('Raw exponential delay: 64.0 s (+34.0 s past lease)', 0.2),
      pill.stroke(PALETTE.gone, 0.4),
      pillTxt.fill(PALETTE.gone, 0.4),
      pillTxt.text('lease expired', 0.2),
    );

    yield* all(
      bar.width(24 * pxPerSec, 1.0, easeInOutCubic),
      bar.fill(PALETTE.spine, 1.0, easeInOutCubic),
      capRule.opacity(1, 0.6),
      capLbl.opacity(1, 0.6),
      title.text('STEP 03 · CLAMP TO 80% LEASE BUDGET', 0.2),
      readout.text('Clamped upper bound: 24.0 s (6.0 s safety margin)', 0.2),
      pill.stroke(PALETTE.spine, 0.4),
      pillTxt.fill(PALETTE.spine, 0.4),
      pillTxt.text('bounded at 24.0 s', 0.2),
    );

    yield* all(
      bar.width(11.52 * pxPerSec, 1.0, easeInOutCubic),
      bar.fill(PALETTE.fresh, 1.0, easeInOutCubic),
      title.text('STEP 04 · SAMPLE FULL JITTER & REQUEUE', 0.2),
      readout.text('Scheduled retry: 11.5 s (r = 0.48, inside 30.0 s lease)', 0.2),
      pill.stroke(PALETTE.fresh, 0.4),
      pillTxt.fill(PALETTE.fresh, 0.4),
      pillTxt.text('safe before expiry', 0.2),
    );

    yield* waitFor(0.1);
  });

  const figure = document.getElementById('fig-step-through');
  const canvas = document.getElementById('mc-canvas');
  const btnBack = document.getElementById('mc-back');
  const btnPlay = document.getElementById('mc-play');
  const btnNext = document.getElementById('mc-next');
  const scrubber = document.getElementById('mc-scrubber');
  const count = document.getElementById('mc-count');
  const status = document.getElementById('mc-status');
  const items = [...document.querySelectorAll('#mc-step-list .step-item')];

  const kit = createKit({ width: 880, height: 320, fps: 30, scenes: [retryScene] });
  await renderFrame(kit, 0);
  kit.player.deactivate();

  let pendingFrame = null;
  let rendering = false;

  async function drawFrame(frame) {
    pendingFrame = frame;
    if (rendering) return;
    rendering = true;
    try {
      while (pendingFrame !== null) {
        const target = pendingFrame;
        pendingFrame = null;
        kit.player.activate();
        try {
          await renderFrame(kit, target);
          await kit.stage.render(kit.player.playback.currentScene, kit.player.playback.previousScene);
          paint(kit, canvas);
        } finally {
          kit.player.deactivate();
        }
      }
    } finally {
      rendering = false;
    }
  }

  let current = STEPS.length - 1;
  let timer = null;

  function stopPlay() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    btnPlay.textContent = 'Play';
    btnPlay.setAttribute('aria-pressed', 'false');
  }

  async function goTo(index) {
    current = clamp(index, 0, STEPS.length - 1);
    scrubber.value = String(current + 1);
    count.textContent = `${current + 1} / ${STEPS.length}`;
    btnBack.disabled = current === 0;
    btnNext.disabled = current === STEPS.length - 1;
    status.textContent = STEPS[current].text;
    items.forEach((item, idx) => {
      item.dataset.state = idx === current ? 'current' : idx < current ? 'past' : 'future';
    });
    await drawFrame(STEPS[current].frame);
  }

  function startPlay() {
    stopPlay();
    if (current >= STEPS.length - 1) void goTo(0);
    btnPlay.textContent = 'Pause';
    btnPlay.setAttribute('aria-pressed', 'true');
    const tick = () => {
      if (current >= STEPS.length - 1) {
        stopPlay();
        return;
      }
      void goTo(current + 1);
      if (current >= STEPS.length - 1) stopPlay();
      else timer = setTimeout(tick, 2400);
    };
    timer = setTimeout(tick, 2400);
  }

  btnBack.addEventListener('click', () => { stopPlay(); void goTo(current - 1); });
  btnNext.addEventListener('click', () => { stopPlay(); void goTo(current + 1); });
  btnPlay.addEventListener('click', () => { if (timer !== null) stopPlay(); else startPlay(); });
  scrubber.addEventListener('input', () => { stopPlay(); void goTo(Number(scrubber.value) - 1); });

  figure.addEventListener('keydown', (event) => {
    if (event.target === scrubber) return;
    if (
      (event.key === ' ' || event.code === 'Space') &&
      event.target instanceof Element &&
      event.target.closest('button, input, select, textarea, a')
    ) {
      return;
    }
    if (event.key === 'ArrowLeft') { event.preventDefault(); stopPlay(); void goTo(current - 1); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); stopPlay(); void goTo(current + 1); }
    else if (event.key === ' ' || event.code === 'Space') { event.preventDefault(); if (timer !== null) stopPlay(); else startPlay(); }
    else if (event.key === 'Home') { event.preventDefault(); stopPlay(); void goTo(0); }
    else if (event.key === 'End') { event.preventDefault(); stopPlay(); void goTo(STEPS.length - 1); }
  });

  await goTo(STEPS.length - 1);
}

// 4. OpenGenerativeUI traced call chain with linked highlight & pin.
function initTracePath(highlighter) {
  const ORDER = ['enqueue', 'worker', 'failure', 'retryPolicy', 'clampToLease', 'requeue'];
  const DETAILS = {
    enqueue: 'Hop 1 (enqueue · enqueue.ts:18): inserts the job with leaseMs = 30_000 and attempt = 1.',
    worker: 'Hop 2 (worker · worker.ts:38): claims the lease and invokes the job handler.',
    failure: 'Hop 3 (failure · worker.ts:44): catches a retryable error before lease expiry.',
    retryPolicy: 'Hop 4 (retryPolicy · retry.ts:12): passes attempt, baseMs, and leaseMs to computeRetryDelay.',
    clampToLease: 'Hop 5 (clampToLease · retry.ts:19-22): caps backoff at 80% of leaseMs and draws full jitter.',
    requeue: 'Hop 6 (requeue · enqueue.ts:52): schedules the next attempt inside the active lease window.',
  };

  const figure = document.getElementById('fig-trace-path');
  const status = document.getElementById('trace-status');
  const clearBtn = document.getElementById('trace-clear');
  const linked = [...figure.querySelectorAll('[data-link]')];
  const edges = [...figure.querySelectorAll('[data-edge]')];

  if (highlighter) {
    for (const cell of figure.querySelectorAll('.code-panel .code-src')) {
      const raw = cell.textContent ?? '';
      if (!raw.trim()) continue;
      const { tokens } = highlighter.codeToTokens(raw, { lang: 'typescript', theme: 'github-dark' });
      const lineTokens = tokens[0] || [];
      cell.textContent = '';
      for (const token of lineTokens) {
        const span = document.createElement('span');
        span.textContent = token.content;
        if (token.color) span.style.color = token.color;
        cell.appendChild(span);
      }
    }
  }

  let pinned = null;
  let preview = null;
  let lastPinnedEl = null;
  let suppressFocusPreview = false;

  function apply() {
    const active = preview || pinned;
    figure.classList.toggle('has-active', Boolean(active));
    const activeIdx = active ? ORDER.indexOf(active) : -1;

    for (const el of linked) {
      const key = el.dataset.link;
      el.classList.toggle('is-active', key === active);
      el.classList.toggle('is-pinned', key === pinned);
      if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') {
        el.setAttribute('aria-pressed', String(key === pinned));
      }
    }
    for (const edge of edges) {
      const idx = ORDER.indexOf(edge.dataset.edge);
      edge.classList.toggle('is-active', activeIdx > 0 && idx < activeIdx);
    }

    clearBtn.hidden = !pinned;
    if (pinned) status.textContent = `Pinned — ${DETAILS[pinned]}`;
    else if (preview) status.textContent = `Preview — ${DETAILS[preview]}`;
    else status.textContent = 'No hop pinned. Hover or focus any node, claim, or code line to preview its link.';
  }

  function togglePin(key, triggerEl) {
    if (pinned === key) {
      pinned = null;
    } else {
      pinned = key;
      lastPinnedEl = triggerEl || figure.querySelector(`[data-link="${key}"]`);
    }
    preview = null;
    apply();
  }

  function clearPin(restoreFocus = false) {
    const fallback = figure.querySelector('.trace-svg .node, .code-line, .hop-item');
    const target = lastPinnedEl && lastPinnedEl.isConnected ? lastPinnedEl : fallback;
    pinned = null;
    preview = null;
    if (restoreFocus && target && typeof target.focus === 'function') {
      suppressFocusPreview = true;
      target.focus();
      suppressFocusPreview = false;
    }
    apply();
  }

  for (const el of linked) {
    const key = el.dataset.link;
    el.addEventListener('mouseenter', () => { preview = key; apply(); });
    el.addEventListener('mouseleave', () => { preview = null; apply(); });
    el.addEventListener('focus', () => {
      if (suppressFocusPreview) return;
      preview = key;
      apply();
    });
    el.addEventListener('blur', () => { preview = null; apply(); });
    el.addEventListener('click', () => togglePin(key, el));
    if (el.tagName !== 'BUTTON') {
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          togglePin(key, el);
        }
      });
    }
  }

  clearBtn.addEventListener('click', () => clearPin(true));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && (pinned || preview)) {
      const activeInside =
        document.activeElement === clearBtn ||
        (document.activeElement instanceof Node && figure.contains(document.activeElement));
      clearPin(activeInside);
    }
  });
}

// 5. Interactive Sandbox & Branch Switch (illustrative model).
function initSandbox() {
  const PRESETS = {
    under: { attempt: 2, base: 2.0, lease: 30, factor: 0.48, label: 'Under 80% budget' },
    exceeds: { attempt: 5, base: 4.0, lease: 30, factor: 0.48, label: 'Clamped to 80% cap' },
    zero: { attempt: 4, base: 2.0, lease: 20, factor: 0.0, label: 'Zero jitter' },
  };

  const tabs = [...document.querySelectorAll('#fig-sandbox [role="tab"]')];
  const resetBtn = document.getElementById('sb-reset');
  const inAttempt = document.getElementById('in-attempt');
  const inBase = document.getElementById('in-base');
  const inLease = document.getElementById('in-lease');
  const outAttempt = document.getElementById('out-attempt');
  const outBase = document.getElementById('out-base');
  const outLease = document.getElementById('out-lease');
  const valRaw = document.getElementById('val-raw');
  const valBudget = document.getElementById('val-budget');
  const valCap = document.getElementById('val-cap');
  const lblSampled = document.getElementById('lbl-sampled');
  const valSampled = document.getElementById('val-sampled');
  const branchText = document.getElementById('sb-branch-text');
  const status = document.getElementById('sb-status');

  let jitterFactor = PRESETS.exceeds.factor;
  let activePreset = 'exceeds';

  function syncBranchTab(branchKey) {
    activePreset = branchKey;
    for (const tab of tabs) {
      const selected = tab.dataset.preset === branchKey;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
  }

  function render() {
    const attempt = Number(inAttempt.value);
    const base = Number(inBase.value);
    const lease = Number(inLease.value);
    const raw = base * Math.pow(2, attempt - 1);
    const budget = lease * 0.8;
    const capped = Math.min(budget, raw);
    const sampled = capped * jitterFactor;

    outAttempt.textContent = String(attempt);
    outBase.textContent = `${base.toFixed(1)} s`;
    outLease.textContent = `${lease} s`;

    valRaw.textContent = `${raw.toFixed(1)} s`;
    valRaw.className = raw > lease ? 'metric-val metric-val--gone' : 'metric-val';
    valBudget.textContent = `${budget.toFixed(1)} s`;
    valCap.textContent = `${capped.toFixed(1)} s`;
    if (lblSampled) {
      lblSampled.textContent = `Sampled delay (r = ${jitterFactor.toFixed(2)})`;
    }
    valSampled.textContent = `${sampled.toFixed(1)} s`;

    let branchKey;
    let branch;
    if (jitterFactor === 0) {
      branchKey = 'zero';
      branch = `Zero-jitter floor (r = 0.00): raw ${raw.toFixed(1)} s against ${budget.toFixed(1)} s budget, clamped to ${capped.toFixed(1)} s cap, immediate 0.0 s retry beats the ${lease} s lease.`;
    } else if (raw > budget) {
      branchKey = 'exceeds';
      const comparison =
        raw > lease
          ? `${raw.toFixed(1)} s raw > ${lease} s lease and ${budget.toFixed(1)} s budget`
          : `${raw.toFixed(1)} s raw exceeds ${budget.toFixed(1)} s budget while within ${lease} s lease`;
      branch = `Clamped to 80% of lease (${comparison}). Safe: sampled ${sampled.toFixed(1)} s (r = ${jitterFactor.toFixed(2)}) finishes inside the ${lease} s lease.`;
    } else {
      branchKey = 'under';
      branch = `Within 80% lease budget (${raw.toFixed(1)} s raw <= ${budget.toFixed(1)} s budget). Safe: full jitter draws ${sampled.toFixed(1)} s (r = ${jitterFactor.toFixed(2)}) inside the ${lease} s lease.`;
    }
    syncBranchTab(branchKey);
    branchText.textContent = `Branch taken: ${branch}`;
    status.textContent = `Attempt ${attempt}, base ${base.toFixed(1)} s, lease ${lease} s, r = ${jitterFactor.toFixed(2)} → clamped cap ${capped.toFixed(1)} s, sampled ${sampled.toFixed(1)} s.`;
  }

  function applyPreset(key, focusTab = false) {
    const preset = PRESETS[key];
    if (!preset) return;
    jitterFactor = preset.factor;
    inAttempt.value = String(preset.attempt);
    inBase.value = String(preset.base);
    inLease.value = String(preset.lease);
    render();
    if (focusTab) {
      const selectedTab = tabs.find((tab) => tab.dataset.preset === activePreset);
      if (selectedTab) selectedTab.focus();
    }
  }

  for (const tab of tabs) {
    tab.addEventListener('click', () => applyPreset(tab.dataset.preset));
    tab.addEventListener('keydown', (event) => {
      const order = ['under', 'exceeds', 'zero'];
      const idx = order.indexOf(activePreset);
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        applyPreset(order[(idx + (event.key === 'ArrowRight' ? 1 : -1) + 3) % 3], true);
      }
    });
  }

  for (const input of [inAttempt, inBase, inLease]) {
    input.addEventListener('input', () => {
      render();
    });
  }

  resetBtn.addEventListener('click', () => applyPreset('exceeds'));
  render();
}

// 6. ar-video Canvas2D measured before and after (Frame = f(t)).
function initOutcomeChart() {
  const METRICS = {
    wait: {
      headline: 'p99 retry wait: 48.0 s → 6.0 s (8x lower)',
      axis: 'ILLUSTRATIVE P99 RETRY WAIT (0–50 s)',
      before: 48.0,
      after: 6.0,
      max: 50,
      unit: 's',
      decimals: 1,
      gap: '8x lower',
    },
    dupes: {
      headline: 'Duplicate-run rate: 14% → 0% (eliminated)',
      axis: 'ILLUSTRATIVE DUPLICATE-RUN RATE (0–20%)',
      before: 14,
      after: 0,
      max: 20,
      unit: '%',
      decimals: 0,
      gap: 'eliminated',
    },
  };

  const DURATION_MS = 900;
  const shell = document.getElementById('outcome-shell');
  const canvas = document.getElementById('outcome-canvas');
  const headline = document.getElementById('outcome-headline');
  const status = document.getElementById('outcome-status');
  const replayBtn = document.getElementById('outcome-replay');
  const tabs = [...document.querySelectorAll('#fig-measured-outcome [role="tab"]')];

  let metricKey = 'wait';
  let rafId = null;

  function formatVal(val, spec) {
    return spec.unit === '%' ? `${Math.round(val)}%` : `${val.toFixed(spec.decimals)} ${spec.unit}`;
  }

  function drawFrame(t) {
    const spec = METRICS[metricKey];
    const rect = shell.getBoundingClientRect();
    const width = Math.max(300, Math.round(rect.width || 880));
    const height = 220;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const padX = width < 480 ? 14 : 24;
    const labelW = width < 480 ? 64 : 84;
    const valueW = width < 480 ? 64 : 82;
    const trackX = padX + labelW;
    const trackW = Math.max(80, width - trackX - valueW - padX);

    ctx.fillStyle = col('well');
    ctx.fillRect(0, 0, width, height);

    ctx.font = '11px ui-monospace, Menlo, Consolas, monospace';
    ctx.fillStyle = col('muted');
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    ctx.fillText(spec.axis, trackX, 16);

    const ticks = [0, spec.max / 2, spec.max];
    ctx.strokeStyle = col('grid', 0.6);
    ctx.lineWidth = 1;
    for (const tick of ticks) {
      const x = Math.round(trackX + (tick / spec.max) * trackW) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 38);
      ctx.lineTo(x, 174);
      ctx.stroke();
      ctx.fillStyle = col('muted');
      ctx.textAlign = tick === spec.max ? 'right' : 'left';
      ctx.fillText(formatVal(tick, spec), x + (tick === spec.max ? -2 : 2), 182);
    }

    const pBefore = tween(t, 0, 1, ease.outCubic);
    const pAfter = tween(t, 0.1, 1, ease.outCubic);
    const rows = [
      { label: 'Before', value: spec.before, prog: pBefore, tone: 'alarm', y: 54 },
      { label: 'After', value: spec.after, prog: pAfter, tone: 'mint', y: 118 },
    ];

    for (const row of rows) {
      const val = lerp(0, row.value, row.prog);
      const ratio = clamp(val / spec.max, 0, 1);
      const barW = row.value === 0 ? 3 : Math.max(3, ratio * trackW);

      ctx.fillStyle = col('panel');
      ctx.fillRect(trackX, row.y, trackW, 34);

      ctx.fillStyle = col(row.tone, 0.9);
      ctx.fillRect(trackX, row.y, barW, 34);

      ctx.font = '600 13px system-ui, -apple-system, sans-serif';
      ctx.fillStyle = col('ink');
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.fillText(row.label, padX, row.y + 17);

      ctx.font = '600 13px ui-monospace, Menlo, Consolas, monospace';
      ctx.fillStyle = col(row.tone);
      ctx.textAlign = 'right';
      ctx.fillText(formatVal(val, spec), width - padX, row.y + 17);
    }
  }

  function stopAnim() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function updateStatus(note = '') {
    const spec = METRICS[metricKey];
    headline.textContent = spec.headline;
    status.textContent = `Illustrative ${spec.headline}.${note ? ` ${note}` : ''}`;
  }

  function playOnce() {
    stopAnim();
    const start = performance.now();
    const step = (now) => {
      const t = clamp((now - start) / DURATION_MS, 0, 1);
      drawFrame(t);
      if (t < 1) rafId = requestAnimationFrame(step);
      else {
        rafId = null;
        updateStatus();
      }
    };
    rafId = requestAnimationFrame(step);
  }

  function selectMetric(key, focusTab = false) {
    if (!METRICS[key]) return;
    metricKey = key;
    for (const tab of tabs) {
      const selected = tab.dataset.metric === key;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) tab.focus();
    }
    stopAnim();
    drawFrame(1);
    updateStatus();
  }

  for (const tab of tabs) {
    tab.addEventListener('click', () => selectMetric(tab.dataset.metric));
    tab.addEventListener('keydown', (event) => {
      const order = ['wait', 'dupes'];
      const idx = order.indexOf(metricKey);
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        selectMetric(order[(idx + (event.key === 'ArrowRight' ? 1 : -1) + 2) % 2], true);
      }
    });
  }

  replayBtn.addEventListener('click', playOnce);

  new ResizeObserver(() => {
    if (rafId === null) drawFrame(1);
  }).observe(shell);

  drawFrame(1);
  updateStatus();
}

const highlighter = await createHighlighter();
initCodeExcerpt(highlighter);
initCodeMorph(highlighter);
await initStepThrough();
initTracePath(highlighter);
initSandbox();
initOutcomeChart();
