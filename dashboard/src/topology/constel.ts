// The constellation renderer (ported from mc2's buildConstel/layout/frame). Imperative canvas
// over the pure ConstelNode model: starfield, ring guides, parent→child edges, comets on live
// edges, provider satellites, pulsing nodes, hover hit-testing + click-through. React owns mount,
// sizing, and the projection→model adapter; this stays imperative. Under the effect-freeze flag
// it renders a single static frame (no rAF) so screenshots and visual assertions are stable.

import { RF } from "./model";
import type { ConstelNode, ConstelStatus } from "./model";

export interface ConstelHandle {
  update(nodes: ConstelNode[]): void;
  destroy(): void;
}

/** Reads one CSS custom property, falling back to a literal when the sheet has not defined it. */
export type CssVarReader = (name: string, fallback: string) => string;

// The status → colour grammar, declared ONCE and totally — the same move `model.ts` made for
// state → status, applied to the step this leaf originally left un-migrated. It was
// `Record<string, string>` read through `COLORS[status] ?? COLORS.ok`, which is the pattern that
// module's own comment condemns: "a default that says healthy is not a default, it is a claim."
// It made the same claim, one map further down the pipeline, and it is where the `undefined` from
// an unclassified state landed and came out cyan.
//
// `Record<ConstelStatus, string>` is the load-bearing part: a sixth status in `CONSTEL_STATUSES`
// stops this object literal compiling until someone picks its hue. There is no `??` here on
// purpose — unlike `model.ts`'s state lookup the key is NOT wire data, it is a `ConstelStatus`
// this package's own `buildTopology` produced, so the lookup really is total and a fallback would
// be re-introducing the guess. Extracted from `mountConstel` so it is reachable without a canvas:
// a totality claim nothing can execute is a totality claim nothing can check.
export function constelColors(cssVar: CssVarReader): Record<ConstelStatus, string> {
  return {
    core: cssVar("--ink", "#e8e8e8"),
    ok: cssVar("--cyan", "#7fd6ff"),
    warn: cssVar("--amber", "#e8a020"),
    crit: cssVar("--alarm", "#ff3322"),
    idle: cssVar("--dormant", "#7a3030"),
  };
}

interface Comet {
  a: number;
  b: number;
  t: number;
  sp: number;
  c: ConstelStatus;
}
interface Star {
  x: number;
  y: number;
  a: number;
  tw: number;
  sp: number;
}

const TAU = Math.PI * 2;
const PROV_R = 26; // provider satellite orbit radius around the core

interface ConstelScene {
  ctx: CanvasRenderingContext2D;
  cw: number;
  ch: number;
  frozen: boolean;
  stars: Star[];
  comets: Comet[];
  nodes: ConstelNode[];
  T: number;
  provSpin: number;
  lastComet: number;
  hovered: number;
  colors: Record<ConstelStatus, string>;
  edge: string;
  muted: string;
}

const statusColor = (scene: ConstelScene, status: ConstelStatus): string => scene.colors[status];

function buildStars(cw: number, ch: number): Star[] {
  const stars: Star[] = [];
  const n = Math.round((cw * ch) / 6200);
  for (let i = 0; i < n; i++) {
    stars.push({
      x: Math.random() * cw,
      y: Math.random() * ch,
      a: Math.random() * 0.45 + 0.1,
      tw: Math.random() * 6,
      sp: 0.6 + Math.random(),
    });
  }
  return stars;
}

function layoutNodes(scene: ConstelScene): void {
  const cx = scene.cw / 2;
  const cy = scene.ch / 2;
  const R = Math.min(scene.cw, scene.ch) * 0.44;
  for (const nd of scene.nodes) {
    if (nd.kind === "prov") {
      const par = scene.nodes[nd.parent];
      nd.px = par.px + Math.cos(nd.poff + scene.provSpin) * PROV_R;
      nd.py = par.py + Math.sin(nd.poff + scene.provSpin) * PROV_R;
    } else {
      nd.px = cx + Math.cos(nd.ang) * nd.rf * R;
      nd.py = cy + Math.sin(nd.ang) * nd.rf * R;
    }
  }
}

function ringLabel(
  scene: ConstelScene,
  cx: number,
  cy: number,
  R: number,
  rf: number,
  text: string,
): void {
  const { ctx } = scene;
  ctx.globalAlpha = 0.5;
  ctx.fillStyle = scene.muted;
  ctx.font = "9px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillText(text, cx, cy - rf * R - 5);
  ctx.globalAlpha = 1;
}

function drawLabel(
  scene: ConstelScene,
  nd: ConstelNode,
  text: string,
  size: number,
  color: string,
  strong: boolean,
): void {
  const { ctx } = scene;
  const dx = nd.px - scene.cw / 2;
  const dy = nd.py - scene.ch / 2;
  const len = Math.hypot(dx, dy) || 1;
  const ox = nd.px + (dx / len) * (nd.base + 7);
  const oy = nd.py + (dy / len) * (nd.base + 7);
  ctx.globalAlpha = strong ? 0.9 : 0.62;
  ctx.fillStyle = color;
  ctx.font = `${strong ? "700 " : ""}${size}px ui-monospace, monospace`;
  ctx.textAlign = dx >= 0 ? "left" : "right";
  ctx.textBaseline = "middle";
  ctx.fillText(text, ox, oy);
  ctx.globalAlpha = 1;
}

function drawNode(scene: ConstelScene, nd: ConstelNode, idx: number): void {
  const { ctx } = scene;
  const pulse = scene.frozen ? 1 : 1 + 0.24 * Math.sin(scene.T * 0.003 + idx);
  let rad = nd.base * pulse;
  ctx.fillStyle = statusColor(scene, nd.status);
  if (idx === scene.hovered) {
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = scene.colors.core;
    ctx.beginPath();
    ctx.arc(nd.px, nd.py, rad + 5, 0, TAU);
    ctx.stroke();
    rad += 0.8;
  }
  ctx.globalAlpha = nd.status === "ok" ? 0.92 : nd.status === "idle" ? 0.6 : 1;
  ctx.beginPath();
  ctx.arc(nd.px, nd.py, rad, 0, TAU);
  ctx.fill();
  ctx.globalAlpha = 1;
}

function spawnComet(scene: ConstelScene): void {
  const pool: number[] = [];
  for (let i = 0; i < scene.nodes.length; i++) {
    if (scene.nodes[i].parent >= 0 && scene.nodes[i].kind !== "prov") pool.push(i);
  }
  if (!pool.length) return;
  const ci = pool[Math.floor(Math.random() * pool.length)];
  scene.comets.push({
    a: scene.nodes[ci].parent,
    b: ci,
    t: 0,
    sp: 0.012 + Math.random() * 0.01,
    c: scene.nodes[ci].status,
  });
  if (scene.comets.length > 7) scene.comets.shift();
  scene.lastComet = scene.T;
}

function drawStars(scene: ConstelScene): void {
  const { ctx } = scene;
  ctx.fillStyle = scene.edge;
  for (const st of scene.stars) {
    ctx.globalAlpha = st.a * (scene.frozen ? 0.7 : 0.55 + 0.45 * Math.sin(scene.T * 0.001 * st.sp + st.tw));
    ctx.fillRect(st.x, st.y, 1, 1);
  }
  ctx.globalAlpha = 1;
}

function drawOrbits(scene: ConstelScene, cx: number, cy: number, R: number): void {
  const { ctx } = scene;
  ctx.strokeStyle = scene.edge;
  ([[RF.repo, "CHECKOUTS"], [RF.wt, "ENCLOSURES"]] as const).forEach(([rf, label]) => {
    ctx.globalAlpha = 0.05;
    ctx.beginPath();
    ctx.arc(cx, cy, rf * R, 0, TAU);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ringLabel(scene, cx, cy, R, rf, label);
  });
}

function drawEdges(scene: ConstelScene): void {
  const { ctx } = scene;
  ctx.strokeStyle = scene.edge;
  for (const nd of scene.nodes) {
    if (nd.parent < 0) continue;
    const par = scene.nodes[nd.parent];
    if (nd.kind === "prov") {
      ctx.globalAlpha = 0.1;
      ctx.strokeStyle = nd.status === "ok" ? scene.edge : statusColor(scene, nd.status);
      ctx.lineWidth = 0.6;
    } else if (nd.kind === "wt") {
      // The enclosure node now carries the lifecycle status — colour its edge when not ok
      // (the signal the old task-rim edge used to provide before the rim was folded in).
      ctx.globalAlpha = nd.status === "ok" ? 0.16 : 0.3;
      ctx.strokeStyle = nd.status === "ok" ? scene.edge : statusColor(scene, nd.status);
      ctx.lineWidth = 1;
    } else {
      ctx.globalAlpha = 0.16;
      ctx.strokeStyle = scene.edge;
      ctx.lineWidth = 1;
    }
    ctx.beginPath();
    ctx.moveTo(par.px, par.py);
    ctx.lineTo(nd.px, nd.py);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  ctx.lineWidth = 1;
}

function drawComets(scene: ConstelScene): void {
  const { ctx } = scene;
  if (scene.T - scene.lastComet > 650) spawnComet(scene);
  for (let k = scene.comets.length - 1; k >= 0; k--) {
    const cm = scene.comets[k];
    cm.t += cm.sp;
    if (cm.t >= 1) {
      scene.comets.splice(k, 1);
      continue;
    }
    const na = scene.nodes[cm.a];
    const nb = scene.nodes[cm.b];
    const ee = cm.t * cm.t * (3 - 2 * cm.t);
    const x = na.px + (nb.px - na.px) * ee;
    const y = na.py + (nb.py - na.py) * ee;
    ctx.globalAlpha = 0.9 * (1 - Math.abs(cm.t - 0.5) * 1.2);
    ctx.fillStyle = statusColor(scene, cm.c);
    ctx.beginPath();
    ctx.arc(x, y, 1.7, 0, TAU);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawCore(scene: ConstelScene, cx: number, cy: number): void {
  const { ctx } = scene;
  for (let g = 5; g >= 1; g--) {
    ctx.globalAlpha = 0.05 * g;
    ctx.fillStyle = scene.colors.core;
    ctx.beginPath();
    ctx.arc(cx, cy, g * 4 + 4, 0, TAU);
    ctx.fill();
  }
  ctx.globalAlpha = 0.85;
  ctx.fillStyle = scene.colors.core;
  ctx.font = "700 10px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillText("WORKSPACE", cx, cy + 12);
  ctx.globalAlpha = 1;
}

function drawNodes(scene: ConstelScene, providerPass: boolean): void {
  for (let i = 0; i < scene.nodes.length; i++) {
    const isProvider = scene.nodes[i].kind === "prov";
    if (isProvider === providerPass) drawNode(scene, scene.nodes[i], i);
  }
}

function drawLabels(scene: ConstelScene): void {
  for (const nd of scene.nodes) {
    if (nd.kind === "repo") {
      drawLabel(scene, nd, nd.label.toUpperCase(), 10, scene.edge, true);
    } else if (nd.kind === "wt") {
      drawLabel(
        scene,
        nd,
        nd.label.length > 22 ? `${nd.label.slice(0, 21)}…` : nd.label,
        9,
        scene.muted,
        false,
      );
    }
  }
}

function renderScene(scene: ConstelScene): void {
  const { ctx } = scene;
  const cx = scene.cw / 2;
  const cy = scene.ch / 2;
  const R = Math.min(scene.cw, scene.ch) * 0.44;
  ctx.clearRect(0, 0, scene.cw, scene.ch);

  drawStars(scene);
  drawOrbits(scene, cx, cy, R);
  drawEdges(scene);
  if (!scene.frozen) drawComets(scene);

  drawNodes(scene, true);
  drawCore(scene, cx, cy);
  drawNodes(scene, false);
  drawLabels(scene);
}

function hitTest(scene: ConstelScene, mx: number, my: number): number {
  let best = -1;
  let bd = 15;
  for (let i = 0; i < scene.nodes.length; i++) {
    const d = Math.hypot(scene.nodes[i].px - mx, scene.nodes[i].py - my);
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return best;
}

function updateHoverTip(scene: ConstelScene, els: { wrap: HTMLElement; tip: HTMLElement }): void {
  const { wrap, tip } = els;
  if (scene.hovered >= 0) {
    const nd = scene.nodes[scene.hovered];
    tip.style.left = `${nd.px}px`;
    tip.style.top = `${nd.py}px`;
    tip.textContent = `${nd.label} — ${nd.sub}`;
    tip.style.opacity = "1";
    wrap.style.cursor = nd.id ? "pointer" : "default";
  } else {
    tip.style.opacity = "0";
    wrap.style.cursor = "default";
  }
}

function resizeScene(
  scene: ConstelScene,
  els: { canvas: HTMLCanvasElement; wrap: HTMLElement },
): void {
  const rect = els.wrap.getBoundingClientRect();
  scene.cw = rect.width;
  scene.ch = rect.height;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  els.canvas.width = Math.max(1, Math.round(scene.cw * dpr));
  els.canvas.height = Math.max(1, Math.round(scene.ch * dpr));
  scene.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  scene.stars = buildStars(scene.cw, scene.ch);
  layoutNodes(scene);
  renderScene(scene); // always paint a frame on (re)size — don't depend on the rAF loop (throttled in hidden tabs)
}

function moveScene(
  event: MouseEvent,
  scene: ConstelScene,
  els: { wrap: HTMLElement; tip: HTMLElement },
): void {
  const rect = els.wrap.getBoundingClientRect();
  scene.hovered = hitTest(scene, event.clientX - rect.left, event.clientY - rect.top);
  updateHoverTip(scene, els);
  if (scene.frozen) renderScene(scene);
}

function leaveScene(scene: ConstelScene, tip: HTMLElement): void {
  scene.hovered = -1;
  tip.style.opacity = "0";
  if (scene.frozen) renderScene(scene);
}

function clickScene(
  event: MouseEvent,
  scene: ConstelScene,
  els: { wrap: HTMLElement },
  onSelect: (id: string) => void,
): void {
  const rect = els.wrap.getBoundingClientRect();
  const best = hitTest(scene, event.clientX - rect.left, event.clientY - rect.top);
  const id = best >= 0 ? scene.nodes[best].id : null;
  if (id) onSelect(id);
}

function updateScene(scene: ConstelScene, next: ConstelNode[]): void {
  scene.nodes = next; // swap the model in place — the rAF loop keeps running, so nothing resets
  scene.comets.length = 0; // node indices may have shifted; drop in-flight comets
  scene.hovered = -1;
  if (scene.cw) {
    layoutNodes(scene);
    renderScene(scene); // repaint on data updates too, not only inside the rAF loop
  }
}

export function mountConstel(
  els: { canvas: HTMLCanvasElement; wrap: HTMLElement; tip: HTMLElement },
  initial: ConstelNode[],
  opts: { onSelect: (id: string) => void },
): ConstelHandle {
  const { canvas, wrap, tip } = els;
  const ctx = canvas.getContext("2d");
  if (!ctx) return { update() {}, destroy() {} };
  const css = getComputedStyle(document.documentElement);
  const v: CssVarReader = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const scene: ConstelScene = {
    ctx,
    cw: 0,
    ch: 0,
    frozen: document.documentElement.dataset.effects === "off",
    stars: [],
    comets: [],
    nodes: initial, // swappable via update() so projection ticks never remount the renderer
    T: 0,
    provSpin: 0,
    lastComet: 0,
    hovered: -1,
    colors: constelColors(v),
    edge: v("--ink", "#cccccc"),
    muted: "oklch(0.7 0.02 250)",
  };
  let raf = 0;

  function frame(): void {
    scene.T += 16;
    scene.provSpin += 0.004;
    if (scene.cw && scene.nodes.length) {
      layoutNodes(scene);
      renderScene(scene);
    }
    raf = requestAnimationFrame(frame);
  }

  const onMove = (event: MouseEvent): void => moveScene(event, scene, els);
  const onLeave = (): void => leaveScene(scene, tip);
  const onClick = (event: MouseEvent): void => clickScene(event, scene, els, opts.onSelect);

  wrap.addEventListener("mousemove", onMove);
  wrap.addEventListener("mouseleave", onLeave);
  wrap.addEventListener("click", onClick);
  const ro = new ResizeObserver(() => resizeScene(scene, els));
  ro.observe(wrap);
  resizeScene(scene, els);
  if (!scene.frozen) raf = requestAnimationFrame(frame);

  return {
    update: (next) => updateScene(scene, next),
    destroy(): void {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      wrap.removeEventListener("mousemove", onMove);
      wrap.removeEventListener("mouseleave", onLeave);
      wrap.removeEventListener("click", onClick);
    },
  };
}
