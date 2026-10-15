// MIK-R128 rule 12: cached step details after pointer rest; no focus stop or request.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { Markdown } from '../../grammar/Markdown';
import type { TaskStepNode } from '../../types/projection';
import type { StepDisplayState } from './model';
import { dim, pip } from './pageStyles';
import { dotTarget, stepTip, tipChildren, tipHead } from './stepDotStyles';

const POINTER_REST_MS = 300;
function StepTooltip({
  step,
  state,
  anchor,
}: {
  step: TaskStepNode;
  state: StepDisplayState;
  anchor: DOMRect;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: anchor.left, top: anchor.bottom + 6 });
  useLayoutEffect(() => {
    const size = ref.current?.getBoundingClientRect();
    if (!size) return;
    setPosition({
      left: Math.max(8, Math.min(anchor.left, window.innerWidth - size.width - 8)),
      top:
        anchor.bottom + size.height + 14 <= window.innerHeight
          ? anchor.bottom + 6
          : Math.max(8, anchor.top - size.height - 6),
    });
  }, [anchor]);
  return createPortal(
    <div ref={ref} role="tooltip" className={stepTip} style={position}>
      <div className={tipHead}>
        <span className={dim}>{step.id}</span>
        <strong>
          <Markdown inline>{step.title}</Markdown>
        </strong>
        <span className={dim}>{state === 'current' ? 'in work' : state}</span>
      </div>
      {step.substeps.length > 0 ? (
        <ol className={tipChildren}>
          {step.substeps.map((item) => (
            <li key={item.id} data-state={item.status}>
              <span className={dim}>{item.id}</span>
              <span>
                <Markdown inline>{item.title}</Markdown>
              </span>
              <span className={dim}>{item.status === 'inProgress' ? 'in work' : item.status}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>,
    document.body,
  );
}

export function StepDot({
  state,
  step,
  index,
}: {
  state: StepDisplayState;
  step?: TaskStepNode;
  index: number;
}) {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [anchor, setAnchor] = useState<DOMRect>();
  const clear = () => {
    clearTimeout(timer.current);
  };
  useEffect(
    () => () => {
      clearTimeout(timer.current);
    },
    [],
  );
  return (
    <>
      <span
        className={dotTarget}
        data-step-dot={step?.id ?? index}
        data-state={state}
        onPointerEnter={(event) => {
          if (!step || event.pointerType === 'touch') return;
          clear();
          const bounds = event.currentTarget.getBoundingClientRect();
          timer.current = setTimeout(() => setAnchor(bounds), POINTER_REST_MS);
        }}
        onPointerLeave={() => {
          clear();
          setAnchor(undefined);
        }}
        onPointerDown={() => {
          clear();
          setAnchor(undefined);
        }}
      >
        <span className={pip({ state })} aria-hidden="true" />
      </span>
      {anchor && step ? <StepTooltip step={step} state={state} anchor={anchor} /> : null}
    </>
  );
}
