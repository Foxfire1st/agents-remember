import { css } from "../../styled-system/css";
import type { TokenSample } from "../types/projection";

// The cumulative-token fuel gauge as a dependency-free SVG sparkline (note 08, cyan charge
// grammar). uPlot stays deferred to slice 08's streaming-telemetry density; a handful of
// cumulative points needs no charting library.
const gauge = css({ display: "flex", alignItems: "center", gap: "0.4rem" });
const gaugeFlat = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  fontSize: "0.78rem",
  color: "cyan",
});
const gaugeLine = css({ stroke: "cyan", strokeWidth: "1.5", fill: "none" });
const gaugeTotal = css({ fontSize: "0.78rem", color: "cyan" });

export function TokenGauge({
  series,
  width = 160,
  height = 36,
}: {
  series: TokenSample[];
  width?: number;
  height?: number;
}) {
  const last = series.at(-1);
  const total = last ? last.cumulative : 0;
  if (!last || series.length < 2) {
    return <div className={gaugeFlat}>{total.toLocaleString()} tok</div>;
  }
  const max = last.cumulative || 1;
  const stepX = width / (series.length - 1);
  const points = series
    .map(
      (sample, i) =>
        `${(i * stepX).toFixed(1)},${(height - (sample.cumulative / max) * height).toFixed(1)}`,
    )
    .join(" ");
  return (
    <div className={gauge}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        aria-label={`${total} cumulative tokens`}
      >
        <polyline points={points} className={gaugeLine} />
      </svg>
      <span className={gaugeTotal}>{total.toLocaleString()} tok</span>
    </div>
  );
}
