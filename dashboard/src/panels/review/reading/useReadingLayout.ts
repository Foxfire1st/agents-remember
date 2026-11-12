// How the reading column draws a diff (MIK-R89 v2 rule 4, ruling Q-A): side by side when the
// column's CONTENT box is at least SIDE_BY_SIDE_MIN_PX wide, unified below that. The content box is
// the column's client width (which leaves out its scrollbar) less its horizontal padding, as the
// approved mockup's container query measures it: at 1440 with the chat closed the column is 1,098 px
// but its content box is under 1,080, so diffs are unified there, and side by side appears at 1920
// with the chat closed only (evidence 8). Switching the measure is this one constant and function.
import { useLayoutEffect, useState } from 'react';

import type { DiffMode } from '../../changeset/DiffPane';

export const SIDE_BY_SIDE_MIN_PX = 1080;

export function contentWidth(node: HTMLElement): number {
  const style = window.getComputedStyle(node);
  const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
  return node.clientWidth - padding;
}

export function layoutForWidth(width: number): DiffMode {
  return width >= SIDE_BY_SIDE_MIN_PX ? 'split' : 'inline';
}

export function useReadingLayout(ref: React.RefObject<HTMLElement | null>): DiffMode {
  const [mode, setMode] = useState<DiffMode>('inline');
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => setMode(layoutForWidth(contentWidth(node)));
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return mode;
}
