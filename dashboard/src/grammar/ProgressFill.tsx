import { css } from "../../styled-system/css";

// Cyan charge rising bottom-up INSIDE an outline (note 08 progress grammar). Reused by the detail
// panel (task-step progress) and the engine room (provider seed progress).
const fillBox = css({
  position: "relative",
  width: "2.2rem",
  height: "2.2rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "cyan",
  borderRadius: "2px",
  overflow: "hidden",
  display: "grid",
  placeItems: "center",
});
const fillLevel = css({
  position: "absolute",
  left: "0",
  bottom: "0",
  width: "100%",
  background: "oklch(0.85 0.13 200 / 0.35)",
  transition: "height 0.4s ease",
});
const fillPct = css({ position: "relative", fontSize: "0.66rem", color: "cyan" });

export function ProgressFill({
  completed,
  total,
  label,
}: {
  completed: number;
  total: number;
  label?: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return (
    <div className={fillBox} role="img" aria-label={label ?? `${pct}% complete`}>
      <div className={fillLevel} style={{ height: `${pct}%` }} />
      <span className={fillPct}>
        {completed}/{total}
      </span>
    </div>
  );
}
