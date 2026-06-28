import { cva } from "../../styled-system/css";

// State/severity carried by colour, never chrome (note 08). `variant` maps a lifecycle state
// (running/blocked/paused/…) or attention severity (alarm/warn/info) to a dot colour; unknown
// variants fall through to the nominal amber base.
const dot = cva({
  base: {
    display: "inline-block",
    width: "0.6em",
    height: "0.6em",
    marginRight: "0.4em",
    borderRadius: "full",
    background: "amber",
  },
  variants: {
    variant: {
      running: { background: "cyan" },
      blocked: { background: "alarm", animation: "pulse 0.6s steps(1) infinite" },
      paused: { background: "dormant" },
      completed: { background: "mint" },
      abandoned: { background: "dormant" },
      alarm: { background: "alarm", animation: "pulse 0.6s steps(1) infinite" },
      warn: { background: "amber" },
      info: { background: "cyan" },
    },
  },
});

const KNOWN = new Set([
  "running",
  "blocked",
  "paused",
  "completed",
  "abandoned",
  "alarm",
  "warn",
  "info",
]);

export function Dot({ variant }: { variant: string }) {
  const v = KNOWN.has(variant) ? (variant as "running") : undefined;
  return <span className={dot({ variant: v })} aria-hidden="true" />;
}
