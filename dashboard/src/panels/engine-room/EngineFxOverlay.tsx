import { forwardRef } from "react";

import {
  attnBadge,
  attnText,
  fxOverlaySvg,
  warpSurge,
} from "./styles";

type EngineFxOverlayProps = {
  attention: boolean;
  surgeXs: readonly number[];
};

const SURGE_CENTER_Y = 342;

function SurgeLines({ xs }: { xs: readonly number[] }) {
  return (
    <>
      {xs.flatMap((x) => [
        <line
          key={`${x}-up`}
          className={warpSurge}
          data-fx="surge"
          data-dir="up"
          data-testid="warp-surge-fx"
          x1={x}
          y1={SURGE_CENTER_Y - 26}
          x2={x}
          y2={SURGE_CENTER_Y - 4}
        />,
        <line
          key={`${x}-down`}
          className={warpSurge}
          data-fx="surge"
          data-dir="down"
          data-testid="warp-surge-fx"
          x1={x}
          y1={SURGE_CENTER_Y + 26}
          x2={x}
          y2={SURGE_CENTER_Y + 4}
        />,
      ])}
    </>
  );
}

function AttentionGroup() {
  return (
    <g data-testid="attention-breath-fx">
      <rect
        className={attnBadge}
        data-fx="breath"
        x={958}
        y={10}
        width={172}
        height={24}
        rx={5}
      />
      <text className={attnText} x={1044} y={26} textAnchor="middle">
        ⚠ ATTENTION
      </text>
    </g>
  );
}

/**
 * Repeating decorative effects isolated from the structural Engine Room SVG.
 *
 * Chrome relays out an entire SVG whenever GSAP updates a descendant transform.
 * Keeping the original SVG primitives in a sparse sibling limits that work to
 * the animated geometry without changing its paint, layering, or choreography.
 */
export const EngineFxOverlay = forwardRef<SVGSVGElement, EngineFxOverlayProps>(
  function EngineFxOverlay(
    { attention, surgeXs },
    ref,
  ) {
    return (
      <svg
        ref={ref}
        className={fxOverlaySvg}
        viewBox="0 0 1200 660"
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
        data-testid="engine-fx-overlay"
      >
        <SurgeLines xs={surgeXs} />
        {attention ? <AttentionGroup /> : null}
      </svg>
    );
  },
);
