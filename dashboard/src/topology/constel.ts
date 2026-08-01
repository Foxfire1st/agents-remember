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

export function mountConstel(
  els: { canvas: HTMLCanvasElement; wrap: HTMLElement; tip: HTMLElement },
  initial: ConstelNode[],
  opts: { onSelect: (id: string) => void },
): ConstelHandle {
  const { canvas, wrap, tip } = els;
  let nodes = initial; // swappable via update() so projection ticks never remount the renderer
  const ctx = canvas.getContext("2d");
  if (!ctx) return { update() {}, destroy() {} };
  const frozen = document.documentElement.dataset.effects === "off";

  const css = getComputedStyle(document.documentElement);
  const v: CssVarReader = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const COLORS = constelColors(v);
  const EDGE = v("--ink", "#cccccc");
  const MUTED = "oklch(0.7 0.02 250)";
  const col = (status: ConstelStatus): string => COLORS[status];

  let cw = 0;
  let ch = 0;
  let stars: Star[] = [];
  const comets: Comet[] = [];
  let hovered = -1;
  let T = 0;
  let provSpin = 0;
  let lastComet = 0;
  let raf = 0;

  function buildStars(): void {
    stars = [];
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
  }

  function layout(): void {
    const cx = cw / 2;
    const cy = ch / 2;
    const R = Math.min(cw, ch) * 0.44;
    for (const nd of nodes) {
      if (nd.kind === "prov") {
        const par = nodes[nd.parent];
        nd.px = par.px + Math.cos(nd.poff + provSpin) * PROV_R;
        nd.py = par.py + Math.sin(nd.poff + provSpin) * PROV_R;
      } else {
        nd.px = cx + Math.cos(nd.ang) * nd.rf * R;
        nd.py = cy + Math.sin(nd.ang) * nd.rf * R;
      }
    }
  }

  function resize(): void {
    const rect = wrap.getBoundingClientRect();
    cw = rect.width;
    ch = rect.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(cw * dpr));
    canvas.height = Math.max(1, Math.round(ch * dpr));
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildStars();
    layout();
    render(); // always paint a frame on (re)size — don't depend on the rAF loop (throttled in hidden tabs)
  }

  function ringLabel(cx: number, cy: number, R: number, rf: number, text: string): void {
    if (!ctx) return;
    ctx.globalAlpha = 0.5;
    ctx.fillStyle = MUTED;
    ctx.font = "9px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(text, cx, cy - rf * R - 5);
    ctx.globalAlpha = 1;
  }

  function drawLabel(nd: ConstelNode, text: string, size: number, color: string, strong: boolean): void {
    if (!ctx) return;
    const dx = nd.px - cw / 2;
    const dy = nd.py - ch / 2;
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

  function drawNode(nd: ConstelNode, idx: number): void {
    if (!ctx) return;
    const pulse = frozen ? 1 : 1 + 0.24 * Math.sin(T * 0.003 + idx);
    let rad = nd.base * pulse;
    ctx.fillStyle = col(nd.status);
    if (idx === hovered) {
      ctx.globalAlpha = 0.9;
      ctx.strokeStyle = COLORS.core;
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

  function spawnComet(): void {
    const pool: number[] = [];
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].parent >= 0 && nodes[i].kind !== "prov") pool.push(i);
    }
    if (!pool.length) return;
    const ci = pool[Math.floor(Math.random() * pool.length)];
    comets.push({ a: nodes[ci].parent, b: ci, t: 0, sp: 0.012 + Math.random() * 0.01, c: nodes[ci].status });
    if (comets.length > 7) comets.shift();
    lastComet = T;
  }

  function render(): void {
    if (!ctx) return;
    const cx = cw / 2;
    const cy = ch / 2;
    const R = Math.min(cw, ch) * 0.44;
    ctx.clearRect(0, 0, cw, ch);

    ctx.fillStyle = EDGE;
    for (const st of stars) {
      ctx.globalAlpha = st.a * (frozen ? 0.7 : 0.55 + 0.45 * Math.sin(T * 0.001 * st.sp + st.tw));
      ctx.fillRect(st.x, st.y, 1, 1);
    }
    ctx.globalAlpha = 1;

    ctx.strokeStyle = EDGE;
    ([[RF.repo, "CHECKOUTS"], [RF.wt, "ENCLOSURES"]] as const).forEach(([rf, label]) => {
      ctx.globalAlpha = 0.05;
      ctx.beginPath();
      ctx.arc(cx, cy, rf * R, 0, TAU);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ringLabel(cx, cy, R, rf, label);
    });

    for (const nd of nodes) {
      if (nd.parent < 0) continue;
      const par = nodes[nd.parent];
      if (nd.kind === "prov") {
        ctx.globalAlpha = 0.1;
        ctx.strokeStyle = nd.status === "ok" ? EDGE : col(nd.status);
        ctx.lineWidth = 0.6;
      } else if (nd.kind === "wt") {
        // The enclosure node now carries the lifecycle status — colour its edge when not ok
        // (the signal the old task-rim edge used to provide before the rim was folded in).
        ctx.globalAlpha = nd.status === "ok" ? 0.16 : 0.3;
        ctx.strokeStyle = nd.status === "ok" ? EDGE : col(nd.status);
        ctx.lineWidth = 1;
      } else {
        ctx.globalAlpha = 0.16;
        ctx.strokeStyle = EDGE;
        ctx.lineWidth = 1;
      }
      ctx.beginPath();
      ctx.moveTo(par.px, par.py);
      ctx.lineTo(nd.px, nd.py);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;

    if (!frozen) {
      if (T - lastComet > 650) spawnComet();
      for (let k = comets.length - 1; k >= 0; k--) {
        const cm = comets[k];
        cm.t += cm.sp;
        if (cm.t >= 1) {
          comets.splice(k, 1);
          continue;
        }
        const na = nodes[cm.a];
        const nb = nodes[cm.b];
        const ee = cm.t * cm.t * (3 - 2 * cm.t);
        const x = na.px + (nb.px - na.px) * ee;
        const y = na.py + (nb.py - na.py) * ee;
        ctx.globalAlpha = 0.9 * (1 - Math.abs(cm.t - 0.5) * 1.2);
        ctx.fillStyle = col(cm.c);
        ctx.beginPath();
        ctx.arc(x, y, 1.7, 0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].kind === "prov") drawNode(nodes[i], i);
    }
    for (let g = 5; g >= 1; g--) {
      ctx.globalAlpha = 0.05 * g;
      ctx.fillStyle = COLORS.core;
      ctx.beginPath();
      ctx.arc(cx, cy, g * 4 + 4, 0, TAU);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].kind !== "prov") drawNode(nodes[i], i);
    }

    for (const nd of nodes) {
      if (nd.kind === "repo") drawLabel(nd, nd.label.toUpperCase(), 10, EDGE, true);
      else if (nd.kind === "wt") drawLabel(nd, nd.label.length > 22 ? `${nd.label.slice(0, 21)}…` : nd.label, 9, MUTED, false);
    }
    ctx.globalAlpha = 0.85;
    ctx.fillStyle = COLORS.core;
    ctx.font = "700 10px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("WORKSPACE", cx, cy + 12);
    ctx.globalAlpha = 1;
  }

  function frame(): void {
    T += 16;
    provSpin += 0.004;
    if (cw && nodes.length) {
      layout();
      render();
    }
    raf = requestAnimationFrame(frame);
  }

  function hit(event: MouseEvent): number {
    const rect = wrap.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    let best = -1;
    let bd = 15;
    for (let i = 0; i < nodes.length; i++) {
      const d = Math.hypot(nodes[i].px - mx, nodes[i].py - my);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    return best;
  }

  function onMove(event: MouseEvent): void {
    hovered = hit(event);
    if (hovered >= 0) {
      const nd = nodes[hovered];
      tip.style.left = `${nd.px}px`;
      tip.style.top = `${nd.py}px`;
      tip.textContent = `${nd.label} — ${nd.sub}`;
      tip.style.opacity = "1";
      wrap.style.cursor = nd.id ? "pointer" : "default";
    } else {
      tip.style.opacity = "0";
      wrap.style.cursor = "default";
    }
    if (frozen) render();
  }

  function onLeave(): void {
    hovered = -1;
    tip.style.opacity = "0";
    if (frozen) render();
  }

  function onClick(event: MouseEvent): void {
    const best = hit(event);
    const id = best >= 0 ? nodes[best].id : null;
    if (id) opts.onSelect(id);
  }

  wrap.addEventListener("mousemove", onMove);
  wrap.addEventListener("mouseleave", onLeave);
  wrap.addEventListener("click", onClick);
  const ro = new ResizeObserver(resize);
  ro.observe(wrap);
  resize();
  if (!frozen) raf = requestAnimationFrame(frame);

  function update(next: ConstelNode[]): void {
    nodes = next; // swap the model in place — the rAF loop keeps running, so nothing resets
    comets.length = 0; // node indices may have shifted; drop in-flight comets
    hovered = -1;
    if (cw) {
      layout();
      render(); // repaint on data updates too, not only inside the rAF loop
    }
  }

  return {
    update,
    destroy(): void {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      wrap.removeEventListener("mousemove", onMove);
      wrap.removeEventListener("mouseleave", onLeave);
      wrap.removeEventListener("click", onClick);
    },
  };
}
