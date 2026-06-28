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
          ink: { value: "oklch(0.92 0.03 90)" },
          grid: { value: "oklch(0.3 0.02 250)" },
          muted: { value: "oklch(0.7 0.02 250)" }, // muted control text (idle mode buttons)
          amber: { value: "oklch(0.82 0.16 75)" }, // nominal wireframe
          cyan: { value: "oklch(0.85 0.13 200)" }, // progress / running
          alarm: { value: "oklch(0.63 0.24 25)" }, // whole-silhouette alarm
          mint: { value: "oklch(0.88 0.16 165)" }, // fresh-online / live
          dormant: { value: "oklch(0.45 0.06 25)" }, // skeletal dark-red dormant
        },
        fonts: {
          mono: { value: 'ui-monospace, "JetBrains Mono", "SFMono-Regular", Menlo, monospace' },
        },
      },
    },
  },
});
