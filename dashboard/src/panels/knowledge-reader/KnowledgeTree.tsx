// Knowledge API adapter for the same async explorer the File Viewer uses.
import { useEffect, useState } from 'react';
import {
  readerGet,
  type ReaderAddress,
  type TreeAnswer,
  type TreeChild,
  type RecordListAnswer,
  type CensusAnswer,
  type WithoutProofAnswer,
} from '../../data/knowledgeReader';
import { ExplorerTree, type ExplorerRow } from '../../grammar/ExplorerTree';

interface Row extends ExplorerRow {
  address?: ReaderAddress;
  child?: TreeChild;
  count?: number | string;
}
type At = Pick<ReaderAddress, 'repo' | 'commit'>;
const KIND_NAMES: Record<string, string> = {
  family: 'Families',
  decision: 'Decisions',
  assumption: 'Assumptions',
  incident: 'Incidents',
  limitation: 'Limitations',
  failure_mode: 'Failure modes',
  scenario: 'Scenarios',
  diagnostic: 'Diagnostics',
  term: 'Terms',
};
function ancestors(address: ReaderAddress): string[] {
  if (address.view === 'record')
    return ['path:.', 'records', `kind:${recordKind(address.id ?? '')}`];
  const parts = address.path && address.path !== '.' ? address.path.split('/') : [];
  return [
    'path:.',
    ...parts.slice(0, -1).map((_, index) => `path:${parts.slice(0, index + 1).join('/')}`),
  ];
}
function recordKind(id: string) {
  return (
    {
      FAM: 'family',
      DEC: 'decision',
      ASM: 'assumption',
      INC: 'incident',
      LIM: 'limitation',
      FLM: 'failure_mode',
      SCN: 'scenario',
      DGN: 'diagnostic',
      TRM: 'term',
    }[id.slice(0, 3)] ?? ''
  );
}
function rootRows(at: At): Row[] {
  return [
    {
      name: 'Invariants without proof',
      path: 'without-proof',
      kind: 'file',
      address: { ...at, view: 'without-proof' },
    },
    {
      name: 'Census',
      path: 'census',
      kind: 'file',
      address: { ...at, view: 'census' },
    },
    {
      name: `${at.repo} /`,
      path: 'path:.',
      kind: 'dir',
      address: { ...at, view: 'path', path: '.' },
    },
    { name: 'Records', path: 'records', kind: 'dir' },
  ];
}

function useRootCounts(at: At) {
  const [counts, setCounts] = useState<Record<string, string>>({});
  const key = JSON.stringify(at);
  useEffect(() => {
    let live = true;
    const params = JSON.parse(key) as At;
    const update = (view: string, value: string) => {
      if (live) setCounts((known) => ({ ...known, [view]: value }));
    };
    const fail = (view: string) => (error: unknown) => update(view, `unavailable: ${String(error)}`);
    void readerGet<WithoutProofAnswer>('without-proof', params).then(
      (answer) => update('without-proof', answer.state === 'view' ? String(answer.total) : `${answer.state}: ${answer.detail ?? ''}`),
      fail('without-proof'),
    );
    void readerGet<CensusAnswer>('census', params).then(
      (answer) => update('census', answer.state === 'census' ? String(answer.censuses.length) : `${answer.state}: ${answer.detail ?? ''}`),
      fail('census'),
    );
    return () => { live = false; };
  }, [key]);
  return counts;
}
function recordRows(answer: RecordListAnswer, directory: string, at: At): Row[] {
  if (directory === 'records')
    return Object.entries(answer.kinds)
      .filter(([kind, rows]) => kind !== 'invariant' && rows.length)
      .map(([kind, rows]) => ({
        name: KIND_NAMES[kind] ?? kind,
        path: `kind:${kind}`,
        kind: 'dir',
        count: rows.length,
      }));
  return (answer.kinds[directory.slice(5)] ?? []).map((record) => ({
    name: record.title ?? record.id,
    path: `record:${record.id}`,
    kind: 'file',
    address: { ...at, view: 'record', id: record.id },
  }));
}
function useKnowledgeLoader(at: At, allPaths: boolean, loadRecords: () => Promise<RecordListAnswer>, currentPath?: string) {
  const [notice, setNotice] = useState('');
  const load = async (directory: string): Promise<Row[]> => {
    if (directory === 'root') return rootRows(at);
    if (directory === 'records' || directory.startsWith('kind:')) {
      const answer = await loadRecords();
      if (answer.state !== 'view')
        throw new Error(`record list ${answer.state}: ${answer.detail ?? ''}`);
      return recordRows(answer, directory, at);
    }
    return pathRows(directory, at, allPaths, setNotice, currentPath);
  };
  return { load, notice };
}
function RootSuffix({ row, counts }: { row: Row; counts: Record<string, string> }) {
  return row.path === 'census' || row.path === 'without-proof'
    ? <span title={counts[row.path]}>{counts[row.path] ?? 'reading…'}</span>
    : row.count;
}
function RowSuffix({ row, counts }: { row: Row; counts: Record<string, string> }) {
  const child = row.child;
  if (!child) return <RootSuffix row={row} counts={counts} />;
  const coverage = child.coverage;
  return (
    <>
      {child.kind === 'dir' ? (
        child.hasOverview ? (
          <span title="has overview">◖ </span>
        ) : null
      ) : child.onboarding ? (
        <span title="has card">◖ </span>
      ) : null}
      {child.entries}
      {child.kind === 'dir' ? (
        <span title={coverage?.detail}>
          {' '}
          ·{' '}
          {coverage?.state === 'counted'
            ? `${coverage.cards}/${coverage.files} cards`
            : 'coverage unavailable'}
        </span>
      ) : null}
      {!child.inCode ? <span> · onboarding only</span> : null}
    </>
  );
}
function currentRow(address: ReaderAddress) {
  if (address.view === 'path' || address.view === 'code') return `path:${address.path ?? '.'}`;
  return address.view === 'record' ? `record:${address.id}` : address.view;
}
export function KnowledgeTree({
  address,
  onOpen,
  loadRecords,
}: {
  address: ReaderAddress;
  onOpen: (address: ReaderAddress) => void;
  loadRecords: () => Promise<RecordListAnswer>;
}) {
  const [allPaths, setAllPaths] = useState(false);
  const counts = useRootCounts({ repo: address.repo, commit: address.commit });
  const { load, notice } = useKnowledgeLoader(
    { repo: address.repo, commit: address.commit },
    allPaths,
    loadRecords,
    address.path,
  );
  return (
    <nav
      aria-label="Repository paths"
      data-testid="knowledge-tree"
      style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
    >
      <label style={{ fontSize: '0.75rem', padding: '0.3rem' }}>
        <input
          type="checkbox"
          checked={allPaths}
          onChange={(event) => setAllPaths(event.target.checked)}
        />{' '}
        Show every path
      </label>
      {notice ? <p data-testid="tree-code-unavailable">{notice}</p> : null}
      <ExplorerTree<Row>
        revision={String(allPaths)}
        label="Knowledge paths and records"
        testid="knowledge-explorer"
        root={{ name: 'Knowledge', path: 'root', kind: 'dir' }}
        loadChildren={load}
        current={currentRow(address)}
        ancestors={ancestors(address)}
        openFolders
        onOpen={(row) => {
          if (row.address) onOpen(row.address);
        }}
        rowAttributes={(row) =>
          row.child
            ? { 'data-testid': 'tree-node', 'data-path': row.child.path, 'data-kind': row.kind }
            : { 'data-record-row': row.path }
        }
        renderSuffix={(row) => <RowSuffix row={row} counts={counts} />}
      />
    </nav>
  );
}

async function pathRows(
  directory: string,
  at: At,
  allPaths: boolean,
  setNotice: (notice: string) => void,
  currentPath?: string,
): Promise<Row[]> {
  const path = directory.slice(5);
  const answer = await readerGet<TreeAnswer>('tree', { ...at, path: path === '.' ? '' : path });
  if (answer.state !== 'view') throw new Error(`tree ${answer.state}: ${answer.detail ?? ''}`);
  setNotice(
    answer.code.state === 'listed'
      ? ''
      : `code tree ${answer.code.state}: ${answer.code.detail ?? ''}`,
  );
  return answer.children
    .filter((child) => showPath(child, allPaths, currentPath))
    .map((child) => ({
      name: child.name,
      path: `path:${child.path}`,
      kind: child.kind,
      child,
      address: { ...at, view: 'path', path: child.path },
    }));
}

// Keep the open path and its ancestors visible even when a cited code file has no card.
function showPath(child: TreeChild, allPaths: boolean, current?: string) {
  return (
    allPaths ||
    child.hasKnowledge !== false ||
    current === child.path ||
    current?.startsWith(`${child.path}/`) === true
  );
}
