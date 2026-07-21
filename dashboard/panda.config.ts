import { defineConfig } from "@pandacss/dev";

export default defineConfig({
  // We keep our own reset during the migration; revisit adopting Panda's preflight once the
  // base/effects layers settle.
  preflight: false,

  jsxFramework: "react",
  include: ["./src/**/*.{ts,tsx}"],
  exclude: [],

  // Generated runtime output. At the dashboard root → gitignored AND outside the
  // `dashboard/src/**` memory scope, so it never needs onboarding.
  outdir: "styled-system",

  // React Aria emits `data-hovered` / `data-focused`; Panda's defaults target `data-hover` /
  // `data-focus`. Reconcile so `_hover` / `_focus` also match React Aria components.
  // (`_selected` / `_pressed` / `_focusVisible` / `_disabled` already match RA out of the box.)
  conditions: {
    extend: {
      hover: "&:is(:hover, [data-hovered])",
      focus: "&:is(:focus, [data-focused])",
    },
  },

  theme: {
    extend: {
      tokens: {
        // The podracer OKLCH palette, ported from styles/tokens.css :root (note 08).
        colors: {
          bg: { value: "oklch(0.16 0.02 250)" },
          bgPanel: { value: "oklch(0.2 0.02 250)" },
          // The terminal "well" — the darker inset the xterm pty pane already uses (was hardcoded
          // #070b0f in panels/Terminal.tsx; 260718-CHATS-L5P FB7.1/V31 promotes it to a token so the
          // structured conversation stage inherits the SAME well tone as the legacy-raw pane).
          well: { value: "#070b0f" },
          ink: { value: "oklch(0.92 0.03 90)" },
          grid: { value: "oklch(0.3 0.02 250)" },
          muted: { value: "oklch(0.7 0.02 250)" }, // muted control text (idle mode buttons)
          amber: { value: "oklch(0.82 0.16 75)" }, // nominal wireframe
          cyan: { value: "oklch(0.85 0.13 200)" }, // progress / running
          alarm: { value: "oklch(0.67 0.22 25)" }, // AA status text + whole-silhouette alarm
          mint: { value: "oklch(0.88 0.16 165)" }, // fresh-online / live
          dormant: { value: "oklch(0.67 0.06 25)" }, // AA status text + skeletal red dormant
          // Rank-insignia tiers (L14, V4 spec): gold = orchestration seat, purple = management.
          gold: { value: "oklch(0.87 0.15 95)" }, // orchestration tier (chevrons, corner)
          goldDim: { value: "oklch(0.55 0.09 95)" }, // orchestration hairline
          goldGhost: { value: "oklch(0.32 0.05 95)" }, // orchestration row wash
          purple: { value: "oklch(0.76 0.14 305)" }, // management tier (chevrons)
          purpleDim: { value: "oklch(0.5 0.09 305)" }, // management corner
          purpleGhost: { value: "oklch(0.3 0.05 305)" }, // management row wash
        },
        fonts: {
          mono: { value: 'ui-monospace, "JetBrains Mono", "SFMono-Regular", Menlo, monospace' },
        },
      },
    },
  },
});
