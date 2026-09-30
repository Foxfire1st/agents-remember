// The reader's explorer (MIK-R29 rule 1): the repository's directories and files at the selected
// memory tree, one level per read, each child with its knowledge entry count. A path known only to
// the onboarding mirror (a card whose code is gone) is listed and marked; a code tree that cannot
// be listed is named, and the onboarding mirror is still shown.
import { useEffect, useRef, useState } from 'react';

import { css, cx } from '../../../styled-system/css';
import { readerGet, type TreeAnswer, type TreeChild } from '../../data/knowledgeReader';
import { muted } from './readerParts';

const tree = css({ listStyle: 'none', margin: '0', paddingLeft: '0.8rem', fontSize: '0.8rem' });
const row = css({ display: 'flex', alignItems: 'baseline', gap: '0.25rem', minWidth: '0' });
const toggle = css({
  font: 'inherit',
  width: '1rem',
  flexShrink: 0,
  background: 'transparent',
  border: '0',
  color: 'muted',
  cursor: 'pointer',
  padding: '0',
});
const name = css({
  font: 'inherit',
  background: 'transparent',
  border: '0',
  padding: '0',
  color: 'ink',
  cursor: 'pointer',
  textAlign: 'left',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});
const selected = css({ color: 'amber' });
const count = css({ color: 'cyan', fontSize: '0.72rem' });

type Level = TreeAnswer | { state: 'loading' } | { state: 'failed'; detail: string };

function ancestors(path: string | undefined): string[] {
  if (!path || path === '.') return [];
  const parts = path.split('/');
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
}

function useTreeLevels(repo: string, commit: string, open: Set<string>) {
  const [levels, setLevels] = useState<Record<string, Level>>({});
  // The tree the levels belong to. An answer that arrives after the repository or the memory tree
  // changed is another tree's listing and is dropped (review F13).
  const tree = `${repo}\u0000${commit}`;
  const current = useRef(tree);
  useEffect(() => {
    current.current = tree;
    setLevels({});
  }, [tree]);
  useEffect(() => {
    for (const directory of open) {
      if (levels[directory] !== undefined || !repo) continue;
      const asked = current.current;
      const settle = (level: Level) => {
        if (current.current === asked) setLevels((known) => ({ ...known, [directory]: level }));
      };
      settle({ state: 'loading' });
      readerGet<TreeAnswer>('tree', { repo, commit, path: directory }).then(
        settle,
        (error: unknown) => settle({ state: 'failed', detail: String(error) }),
      );
    }
  }, [open, levels, repo, commit]);
  return levels;
}

interface LevelProps {
  directory: string;
  levels: Record<string, Level>;
  open: Set<string>;
  current?: string;
  flip: (directory: string) => void;
  onOpen: (path: string) => void;
}

function Toggle({
  child,
  expanded,
  flip,
}: {
  child: TreeChild;
  expanded: boolean;
  flip: (directory: string) => void;
}) {
  if (child.kind !== 'dir') return <span className={toggle} />;
  return (
    <button
      type="button"
      className={toggle}
      aria-label={`${expanded ? 'Collapse' : 'Expand'} ${child.name}`}
      aria-expanded={expanded}
      onClick={() => flip(child.path)}
    >
      {expanded ? '▾' : '▸'}
    </button>
  );
}

function TreeNode({ child, ...props }: LevelProps & { child: TreeChild }) {
  const { open, current, flip, onOpen } = props;
  // A child lies below its directory; a listing that says otherwise is never descended into.
  const below = props.directory === '' || child.path.startsWith(`${props.directory}/`);
  const expanded = below && child.kind === 'dir' && open.has(child.path);
  return (
    <li data-testid="tree-node" data-path={child.path} data-kind={child.kind}>
      <div className={row}>
        <Toggle child={child} expanded={expanded} flip={flip} />
        <button
          type="button"
          className={cx(name, current === child.path ? selected : '')}
          title={child.path}
          onClick={() => onOpen(child.path)}
        >
          {child.name}
        </button>
        {child.entries > 0 ? <span className={count}>{child.entries}</span> : null}
        {!child.inCode ? <span className={muted}>(onboarding only)</span> : null}
      </div>
      {expanded ? (
        <ul className={tree}>
          <TreeLevel {...props} directory={child.path} />
        </ul>
      ) : null}
    </li>
  );
}

function TreeLevel(props: LevelProps) {
  const level = props.levels[props.directory];
  if (level === undefined || level.state === 'loading') return <li className={muted}>loading…</li>;
  if (!('children' in level)) {
    return (
      <li className={muted} data-testid="tree-unavailable">
        {'detail' in level ? level.detail : level.state}
      </li>
    );
  }
  return (
    <>
      {level.code.state !== 'listed' ? (
        <li className={muted} data-testid="tree-code-unavailable">
          code tree {level.code.state}: {level.code.detail}
        </li>
      ) : null}
      {level.children.map((child) => (
        <TreeNode key={child.path} {...props} child={child} />
      ))}
    </>
  );
}

export function KnowledgeTree({
  repo,
  commit,
  current,
  onOpen,
}: {
  repo: string;
  commit: string;
  current?: string;
  onOpen: (path: string) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(['', ...ancestors(current)]));
  const levels = useTreeLevels(repo, commit, open);
  useEffect(() => {
    setOpen((known) => new Set([...known, ...ancestors(current)]));
  }, [current]);
  const flip = (directory: string) =>
    setOpen((known) => {
      const next = new Set(known);
      if (next.has(directory)) next.delete(directory);
      else next.add(directory);
      return next;
    });
  return (
    <nav aria-label="Repository paths" data-testid="knowledge-tree">
      <button
        type="button"
        className={cx(name, current === '.' || current === '' ? selected : '')}
        onClick={() => onOpen('.')}
      >
        {repo || 'repository'} /
      </button>
      <ul className={tree} style={{ paddingLeft: 0 }}>
        <TreeLevel
          directory=""
          levels={levels}
          open={open}
          current={current}
          flip={flip}
          onOpen={onOpen}
        />
      </ul>
    </nav>
  );
}
