import { forwardRef } from "react";

import {
  attnBadge,
  attnText,
  engineDiv,
  engineReindexCharge,
  engineSpine,
  fxOverlaySvg,
  warpSurge,
} from "./styles";

type Point = {
  x: number;
  y: number;
};

type EngineFxOverlayProps = {
  attention: boolean;
  engineHeight: number;
  engineWidth: number;
  reindexAt?: Point;
  surgeXs: readonly number[];
};

const SURGE_CENTER_Y = 342;

/**
 * Repeating decorative effects isolated from the structural Engine Room SVG.
 *
 * Chrome relays out an entire SVG whenever GSAP updates a descendant transform.
 * Keeping the original SVG primitives in a sparse sibling limits that work to
 * the animated geometry without changing its paint, layering, or choreography.
 */
export const EngineFxOverlay = forwardRef<SVGSVGElement, EngineFxOverlayProps>(
  function EngineFxOverlay(
    { attention, engineHeight, engineWidth, reindexAt, surgeXs },
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
        {surgeXs.flatMap((x) => [
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

        {reindexAt ? (
          <g transform={`translate(${reindexAt.x},${reindexAt.y})`}>
            <rect
              className={engineReindexCharge}
              data-fx="reindex"
              data-testid="engine-reindex-fx"
              x={2}
              y={2}
              width={engineWidth - 4}
              height={engineHeight - 4}
              rx={3}
            />
            {[14, 26, 38, 50, 62, 74, 86].map((y) => (
              <line
                className={engineDiv}
                key={y}
                x1={0}
                y1={y}
                x2={engineWidth}
                y2={y}
              />
            ))}
            <line
              className={engineSpine}
              x1={engineWidth / 2}
              y1={4}
              x2={engineWidth / 2}
              y2={engineHeight - 4}
            />
          </g>
        ) : null}

        {attention ? (
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
        ) : null}
      </svg>
    );
  },
);
