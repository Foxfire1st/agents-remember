// One shared async tree for File Viewer and Knowledge. Headless Tree owns loading,
// selection and arrow keys; the caller owns what a row opens and what its suffix says.
import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from 'react';
import {
  asyncDataLoaderFeature,
  hotkeysCoreFeature,
  selectionFeature,
  type TreeInstance,
} from '@headless-tree/core';
import { useTree } from '@headless-tree/react';

import { css, cx } from '../../styled-system/css';

export interface ExplorerRow {
  path: string;
  name: string;
  kind: 'dir' | 'file';
}
const container = css({ flex: '1', minHeight: '0', overflow: 'auto', outline: 'none' });
const row = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.3rem',
  width: '100%',
  border: 'none',
  background: 'transparent',
  color: 'ink',
  font: 'inherit',
  fontSize: '0.76rem',
  textAlign: 'left',
  padding: '0.15rem 0.3rem',
  cursor: 'pointer',
  _hover: { background: 'color-mix(in oklab, var(--amber) 12%, transparent)' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '-1px' },
  '&[aria-selected=true]': { background: 'color-mix(in oklab, var(--amber) 20%, transparent)' },
});
const name = css({ minWidth: '3ch', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
const glyph = css({ width: '1ch', flexShrink: 0, color: 'muted' });
const suffix = css({ marginLeft: 'auto', minWidth: '0', maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis', color: 'cyan', whiteSpace: 'nowrap' });

interface ExplorerProps<T extends ExplorerRow> {
  label: string;
  testid: string;
  root: T;
  loadChildren: (directory: string) => Promise<T[]>;
  onOpen: (entry: T) => void;
  current?: string;
  ancestors?: string[];
  openFolders?: boolean;
  renderSuffix?: (entry: T) => ReactNode;
  rowAttributes?: (entry: T) => Record<string, string | undefined>;
  revision?: string;
}
function useExplorerTree<T extends ExplorerRow>({
  root,
  loadChildren,
  current,
  ancestors = [],
  revision,
  onOpen,
  openFolders,
}: ExplorerProps<T>) {
  const cache = useRef(new Map<string, T>([[root.path, root]])).current;
  const loadedParents = useRef(new Set<string>()).current;
  const [failures, setFailures] = useState<Record<string, string>>({});
  const [expandedItems, setExpandedItems] = useState(ancestors);
  const tree = useTree<T>({
    rootItemId: root.path,
    state: { expandedItems, ...(current ? { selectedItems: [current] } : {}) },
    setExpandedItems,
    getItemName: (item) => item.getItemData()?.name ?? '…',
    isItemFolder: (item) => item.getItemData()?.kind === 'dir',
    createLoadingItemData: () => ({ ...root, name: '…' }),
    onPrimaryAction: (item) => {
      const data = item.getItemData();
      if (data && (openFolders || !item.isFolder())) onOpen(data);
    },
    dataLoader: {
      getItem: async (id) => {
        const data = cache.get(id);
        if (!data) throw new Error(`Tree row not loaded: ${id}`);
        return data;
      },
      getChildren: async (id) => {
        loadedParents.add(id);
        try {
          const children = await loadChildren(id);
          for (const entry of children) cache.set(entry.path, entry);
          setFailures((known) => {
            const next = { ...known };
            delete next[id];
            return next;
          });
          return children.map((entry) => entry.path);
        } catch (error) {
          setFailures((known) => ({ ...known, [id]: String(error) }));
          return [];
        }
      },
    },
    features: [asyncDataLoaderFeature, selectionFeature, hotkeysCoreFeature],
  });
  const ancestorKey = ancestors.join('\0');
  useEffect(() => {
    setExpandedItems((known) => [
      ...new Set([...known, ...ancestorKey.split('\0').filter(Boolean)]),
    ]);
  }, [ancestorKey]);
  const previousRevision = useRef(revision);
  useEffect(() => {
    if (previousRevision.current === revision) return;
    previousRevision.current = revision;
    for (const id of loadedParents)
      void tree.getItemInstance(id).invalidateChildrenIds();
  }, [tree, revision, loadedParents]);
  useRevealItem(tree, cache, current, ancestorKey);
  return { tree, failures };
}

export function ExplorerTree<T extends ExplorerRow>(props: ExplorerProps<T>) {
  const { tree, failures } = useExplorerTree(props);
  const { testid, label, rowAttributes, renderSuffix, onOpen, openFolders } = props;
  const viewport = useRef<HTMLDivElement>(null);
  const revealed = useRef<string | undefined>(undefined);
  useEffect(() => {
    const pane = viewport.current;
    const selected = pane?.querySelector<HTMLElement>('[aria-selected=true]');
    if (!pane?.clientHeight || !selected || revealed.current === props.current) return;
    const bounds = pane.getBoundingClientRect();
    const rowBounds = selected.getBoundingClientRect();
    if (rowBounds.top < bounds.top) pane.scrollTop += rowBounds.top - bounds.top;
    else if (rowBounds.bottom > bounds.bottom) pane.scrollTop += rowBounds.bottom - bounds.bottom;
    revealed.current = props.current;
  });
  return (
    <div ref={viewport} className={container} data-testid={testid}>
      {Object.entries(failures).map(([id, detail]) => (
        <p key={id} role="alert">
          {detail}
        </p>
      ))}
      <div {...tree.getContainerProps(label)}>
        {tree.getItems().map((item) => {
          const data = item.getItemData();
          const props = item.getProps();
          return (
            <button
              {...props}
              {...(data ? rowAttributes?.(data) : {})}
              key={item.getId()}
              className={cx(row, props.className as string | undefined)}
              title={data?.name}
              style={{
                ...(props.style as CSSProperties),
                paddingLeft: `${item.getItemMeta().level * 0.85 + 0.3}rem`,
              }}
              onClick={() => {
                tree.setSelectedItems([item.getId()]);
                if (item.isFolder()) {
                  if (item.isExpanded()) item.collapse();
                  else item.expand();
                }
                if (data && (openFolders || !item.isFolder())) onOpen(data);
              }}
            >
              <span className={glyph} aria-hidden>
                {item.isFolder() ? (item.isExpanded() ? '▾' : '▸') : ''}
              </span>
              <span className={name}>{item.getItemName()}</span>
              {data && renderSuffix ? <span className={suffix}>{renderSuffix(data)}</span> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// A changed address can select a row a previous knowledge filter excluded. Re-read
// only that loaded parent, once for the changed address; ordinary renders and Back
// to a known row keep the existing Headless Tree cache.
function useRevealItem<T>(
  tree: TreeInstance<T>,
  cache: Map<string, T>,
  current: string | undefined,
  ancestorKey: string,
) {
  const attempted = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!current || cache.has(current) || attempted.current === current) return;
    const parentId = ancestorKey
      .split('\0')
      .reverse()
      .find((id) => cache.has(id));
    if (!parentId) return;
    const parent = tree.getItemInstance(parentId);
    if (parent.isLoading()) return;
    attempted.current = current;
    void parent.invalidateChildrenIds();
  });
}
