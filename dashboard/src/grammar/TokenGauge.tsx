import type { TokenSample } from "../types/projection";

// The cumulative-token fuel gauge as a dependency-free SVG sparkline. uPlot is deferred to
// 5c, where streaming-telemetry density (engine room / event river) justifies the canvas dep;
// a handful of cumulative points needs no charting library. Cyan = the progress/charge grammar.
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
    return <div className="gauge gauge--flat">{total.toLocaleString()} tok</div>;
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
    <div className="gauge">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        aria-label={`${total} cumulative tokens`}
      >
        <polyline points={points} className="gauge__line" fill="none" />
      </svg>
      <span className="gauge__total">{total.toLocaleString()} tok</span>
    </div>
  );
}
