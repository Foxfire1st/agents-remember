// A subject's two closed groups (MIK-R89 v2 rule 5; MIK-R86 v2 rule 12's class `unchanged`; mockup
// `groups`): its unchanged locations and its not-comparable ones. The heading shows the count and why.
// Opened, a group shows one line per location (path, full role, range; the mockup's `l.role`, ruling
// 9498d6e4, where the tree keeps the short word) for the first GROUP_PREVIEW of them,
// then "… N more, one line each" (the mockup's words, app.js `groupBox`), which opens the rest in
// place. A group shows no cards and holds no changed content.
// Its open state belongs to the reading column, so a tree group row can open it and a read keeps it.
import { useState } from 'react';

import { css } from '../../../../styled-system/css';
import type { LocationNode } from '../changeSetOrder';
import { sideStateText } from '../ExpressionCards';
import { groupDomId } from './blockDom';
import { group, groupHead, groupRow } from './readingStyles';

const GROUP_PREVIEW = 4;

// Drawn as the mockup's group (D401; styles.css `.group > button.gh`, `.lrow .p.dim`): the heading a
// full-width bar on the panel ground, and "… N more, one line each" a dim line of the list. Both stay
// buttons; `data-own-look` keeps the review's shared control look off them (ReviewLayout.tsx).
const headLook = css({
  background: 'bgPanel',
  border: 'none',
  paddingBlock: '0.45rem',
  paddingInline: '0.7rem',
  color: 'ink',
  fontFamily: 'inherit',
  cursor: 'pointer',
  _hover: { color: 'amber' },
});

const moreLine = css({
  width: '100%',
  textAlign: 'left',
  background: 'transparent',
  borderInline: 'none',
  borderBottom: 'none',
  color: 'color-mix(in oklab, token(colors.muted) 82%, token(colors.bg))',
  fontFamily: 'inherit',
  cursor: 'pointer',
  _hover: { color: 'amber' },
});

export type GroupKind = 'unchanged' | 'not-comparable';

const WHY: Record<GroupKind, string> = {
  unchanged: 'their recorded code is byte-identical on both sides',
  'not-comparable': 'one side is absent, unresolved or unreadable, so there is nothing to compare',
};

function heading(kind: GroupKind, count: number): string {
  if (kind === 'unchanged') return `${count} unchanged location${count === 1 ? '' : 's'}`;
  return `${count} not comparable`;
}

// A not-comparable line names the side that makes it so (mockup: "· before: unresolved").
function rangeOf(node: LocationNode, kind: GroupKind): string {
  if (kind === 'unchanged') return node.range;
  const other = node.identity.side === 'after' ? 'before' : 'after';
  const side = node.entry[other];
  return side.state === 'resolved' ? node.range : `${node.range} · ${other}: ${sideStateText(side)}`;
}

export function LocationGroup({
  subjectKey,
  kind,
  nodes,
  open,
  onToggle,
}: {
  subjectKey: string;
  kind: GroupKind;
  nodes: readonly LocationNode[];
  open: boolean;
  onToggle: () => void;
}) {
  const [all, setAll] = useState(false);
  if (nodes.length === 0) return null;
  const id = groupDomId(subjectKey, kind);
  const shown = all ? nodes : nodes.slice(0, GROUP_PREVIEW);
  const more = nodes.length - shown.length;
  return (
    <div className={group} id={id} data-disclosure="unchanged" data-testid="review-location-group" data-group={kind}>
      <button
        type="button"
        className={`${groupHead} ${headLook}`}
        data-own-look
        aria-expanded={open}
        aria-controls={`${id}-rows`}
        data-testid="review-location-group-toggle"
        onClick={onToggle}
      >
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span>{heading(kind, nodes.length)}</span>
        <span data-why>{WHY[kind]}</span>
      </button>
      {open ? (
        <div id={`${id}-rows`} data-testid="review-location-group-rows">
          {shown.map((node) => (
            <div key={node.key} className={groupRow} data-testid="review-location-line" data-entry={node.entry.id}>
              <span title={node.path}>{node.path}</span>
              <span>{node.role}</span>
              <span>{rangeOf(node, kind)}</span>
            </div>
          ))}
          {more > 0 ? (
            <button
              type="button"
              className={`${groupRow} ${moreLine}`}
              data-own-look
              data-testid="review-location-group-more"
              onClick={() => setAll(true)}
            >
              <span>… {more} more, one line each</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
