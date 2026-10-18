// Leaf-wide facts are admitted only for the comparison being shown. No fetch or inference here.
import {
  Component,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
  type ReactNode,
} from 'react';
import type { ReviewOutline } from '../../data/review';
import type { ReviewCodeChange, ReviewTreesRead } from '../../data/reviewTrees';

export function matchingTreeRead(read?: ReviewTreesRead | null, comparison?: number) {
  if (
    read?.phase !== 'trees' ||
    (comparison !== undefined && read.trees.comparison?.number !== comparison)
  )
    return undefined;
  return read.trees;
}
export function codeFacts(
  read?: ReviewTreesRead | null,
  comparison?: number,
): Map<string, ReviewCodeChange> | null {
  const facts = matchingTreeRead(read, comparison)?.code_changes;
  return facts?.state === 'facts'
    ? new Map(facts.invariants.map((fact) => [fact.invariant_key, fact]))
    : null;
}
export function codeChangesCount(
  outline: ReviewOutline | undefined,
  read?: ReviewTreesRead | null,
  comparison?: number,
): number | undefined {
  const facts = codeFacts(read, comparison);
  if (facts === null || outline?.state !== 'outlined') return undefined;
  return new Set(
    outline.invariants
      .filter(
        (row) =>
          row.change === 'unchanged' &&
          facts.get(row.selector_id)?.implementation === 'established',
      )
      .map((row) => row.selector_id),
  ).size;
}
export function codeChangesSummary(
  outline: ReviewOutline | undefined,
  read?: ReviewTreesRead | null,
  comparison?: number,
): string {
  if (read?.phase === 'unavailable' || read?.phase === 'not-converted') return 'unavailable';
  if (matchingTreeRead(read, comparison)?.code_changes?.state === 'unavailable')
    return 'unavailable';
  const count = codeChangesCount(outline, read, comparison);
  return count === undefined ? '…' : `${count} code changes`;
}

interface ScrollAnchorProps {
  root: RefObject<HTMLElement | null>;
  count: number | undefined;
  comparison?: number;
  children: ReactNode;
}
type ScrollPosition = { element: HTMLElement; top: number; left: number };

// A before-mutation snapshot is needed: layout-effect cleanup is already too late
// when native scroll anchoring adjusts an ancestor as new rows enter the tree.
export class CodeFactsScrollAnchor extends Component<
  ScrollAnchorProps,
  Record<string, never>,
  ScrollPosition[] | null
> {
  getSnapshotBeforeUpdate(previous: ScrollAnchorProps): ScrollPosition[] | null {
    if (previous.comparison !== this.props.comparison || previous.count === this.props.count)
      return null;
    const positions: ScrollPosition[] = [];
    let element = this.props.root.current;
    const list = element?.querySelector<HTMLElement>('[data-testid="review-family-list"]');
    if (list) positions.push({ element: list, top: list.scrollTop, left: list.scrollLeft });
    while (element) {
      positions.push({ element, top: element.scrollTop, left: element.scrollLeft });
      element = element.parentElement;
    }
    return positions;
  }
  componentDidUpdate(
    _previous: ScrollAnchorProps,
    _state: Record<string, never>,
    snapshot: ScrollPosition[] | null,
  ) {
    for (const { element, top, left } of snapshot ?? []) {
      element.scrollTop = top;
      element.scrollLeft = left;
    }
  }
  render() {
    return this.props.children;
  }
}

// Facts enrich the existing tree in place. Do not focus or select a new row.
export function useCodeFactsNotice(count: number | undefined, comparison?: number): string {
  const seen = useRef<string | undefined>(undefined);
  const [notice, setNotice] = useState({ key: '', text: '' });
  const key = `${comparison ?? ''}:${count ?? ''}`;
  useLayoutEffect(() => {
    if (count !== undefined && seen.current !== key) {
      seen.current = key;
      setNotice({ key, text: `${count} code changes found.` });
    }
  }, [count, key]);
  return notice.key === key ? notice.text : '';
}
