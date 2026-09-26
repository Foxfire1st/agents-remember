import { css } from '../../../styled-system/css';
import type {
  ReviewChangedFile,
  ReviewSourceInventory,
  ReviewUnrepresentablePath,
} from '../../data/review';
import { SourceContent } from './SourceContent';

export type DiffLayout = 'split' | 'inline';

const shell = css({
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.6rem 0.7rem',
});

const bar = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'center',
  flexWrap: 'wrap',
  marginBottom: '0.4rem',
});

const sectionLabel = css({
  color: 'cyan',
  fontSize: '0.72rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0',
});

const muted = css({ color: 'muted', fontSize: '0.8rem', margin: '0.2rem 0' });

const rows = css({
  margin: '0.5rem 0',
  padding: 0,
  listStyle: 'none',
  '& > li': { padding: '0.6rem 0', borderBottom: '1px solid var(--grid)', fontSize: '0.75rem' },
});

const rowButton = css({
  background: 'transparent',
  border: 'none',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0',
  textAlign: 'left',
  maxWidth: '100%',
  overflowWrap: 'anywhere',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '2px' },
});

function inventoryEntry(
  entry: ReviewChangedFile,
  repo: string,
  master: string,
  leaf: string,
  generation: { before?: string; after?: string },
  open: string | null,
  onOpen: (path: string | null) => void,
  layout: DiffLayout,
  fullFile: boolean,
  showContent: boolean,
  attribution: Record<string, string>,
) {
  const notes = sourceNotes(entry);
  const expandable = generation.before !== undefined && generation.after !== undefined;
  const isOpen = open === entry.path;
  return (
    <li key={entry.path} data-testid="review-inventory-entry" data-status={entry.status}>
      {expandable ? (
        <button
          type="button"
          className={rowButton}
          data-testid="review-inventory-open"
          data-path={entry.path}
          aria-expanded={isOpen}
          onClick={() => onOpen(isOpen ? null : entry.path)}
        >
          {isOpen ? '▾ ' : '▸ '}
          {entry.path}
        </button>
      ) : (
        <code>{entry.path}</code>
      )}{' '}
      · {entry.status}
      <span className={muted} data-attribution={attribution[entry.path]}>
        {' · '}
        {attribution[entry.path]}
      </span>
      {notes.length ? (
        <details>
          <summary className={muted}>File details</summary>
          {notes.join(' · ')}
        </details>
      ) : null}
      {isOpen && expandable && showContent ? (
        <SourceContent
          repo={repo}
          master={master}
          leaf={leaf}
          entry={entry}
          beforeCodeTreeId={generation.before as string}
          afterCodeTreeId={generation.after as string}
          mode={layout}
          collapse={!fullFile}
        />
      ) : null}
    </li>
  );
}

function byteNamedEntry(entry: ReviewUnrepresentablePath) {
  return (
    <li key={entry.path_bytes} data-testid="review-inventory-byte-path" data-status={entry.status}>
      <code>{entry.path_bytes}</code> · {entry.status}
      {entry.mode_change ? ' · mode changed' : ''}
      <div className={muted}>{entry.detail}</div>
      <div className={muted} data-testid="review-byte-path-not-addressable">
        this row&apos;s content is not openable through this surface: its name is carried as bytes
        for identification, and no expansion request can name it.
      </div>
    </li>
  );
}

function DisplayControls({
  layout,
  onLayout,
  fullFile,
  onFullFile,
}: {
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
}) {
  return (
    <span className={bar} data-testid="review-display-controls" data-diff-layout={layout}>
      <label className={muted} htmlFor="review-diff-layout">
        diff layout
      </label>
      <select
        id="review-diff-layout"
        data-testid="review-diff-layout"
        value={layout}
        onChange={(event) => onLayout(event.target.value === 'inline' ? 'inline' : 'split')}
      >
        <option value="split">split</option>
        <option value="inline">inline</option>
      </select>
      <button
        type="button"
        data-testid="review-full-file"
        data-full-file={fullFile ? 'true' : 'false'}
        aria-pressed={fullFile}
        onClick={() => onFullFile(!fullFile)}
      >
        {fullFile ? 'showing full file' : 'showing changed regions'}
      </button>
    </span>
  );
}

function InventoryRows({
  inventory,
  repo,
  master,
  leaf,
  layout,
  fullFile,
  open,
  onOpen,
  showContent,
  attribution,
}: {
  inventory: ReviewSourceInventory;
  repo: string;
  master: string;
  leaf: string;
  layout: DiffLayout;
  fullFile: boolean;
  open: string | null;
  onOpen: (path: string | null) => void;
  showContent: boolean;
  attribution: Record<string, string>;
}) {
  const generation = {
    before: inventory.before_code_tree_id,
    after: inventory.after_code_tree_id,
  };
  const byByteForm = inventory.unrepresentable_paths ?? [];
  return (
    <>
      {inventory.entries.length ? (
        <ul className={rows}>
          {inventory.entries.map((entry) =>
            inventoryEntry(
              entry,
              repo,
              master,
              leaf,
              generation,
              open,
              onOpen,
              layout,
              fullFile,
              showContent,
              attribution,
            ),
          )}
        </ul>
      ) : null}
      {byByteForm.length ? <ul className={rows}>{byByteForm.map(byteNamedEntry)}</ul> : null}
    </>
  );
}

export function SourceExplorer({
  inventory,
  repo,
  master,
  leaf,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  open,
  onOpen,
  showContent = true,
  attribution,
}: {
  inventory: ReviewSourceInventory;
  repo: string;
  master: string;
  leaf: string;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  open: string | null;
  onOpen: (path: string | null) => void;
  showContent?: boolean;
  attribution: Record<string, string>;
}) {
  const unclassified = inventory.entries.filter(
    (entry) => entry.status === 'unknown' || entry.content === 'unknown',
  );
  const byByteForm = inventory.unrepresentable_paths ?? [];
  return (
    <section
      className={shell}
      data-testid="review-source-explorer"
      data-inventory-state={inventory.state}
    >
      <div className={bar}>
        <h2 className={sectionLabel}>Source changes</h2>
        {showContent ? (
          <DisplayControls
            layout={layout}
            onLayout={onLayout}
            fullFile={fullFile}
            onFullFile={onFullFile}
          />
        ) : null}
      </div>
      <p data-testid="review-inventory" data-inventory-state={inventory.state}>
        {inventory.state === 'unavailable'
          ? 'Source inventory unavailable · changed-file count not measured'
          : `${inventory.listed_total} changed files · ${inventory.state}${inventory.partial ? ' · partial' : ''}`}
        {byByteForm.length ? ` + ${byByteForm.length} byte-named paths` : ''}
      </p>
      <p className={muted} data-testid="review-population-scope">
        Complete source population, including unattributed changes.
      </p>
      <InventoryRows
        inventory={inventory}
        repo={repo}
        master={master}
        leaf={leaf}
        layout={layout}
        fullFile={fullFile}
        open={open}
        onOpen={onOpen}
        showContent={showContent}
        attribution={attribution}
      />
      {unclassified.length ? (
        <p data-testid="review-inventory-unclassified">
          {unclassified.length} listed path(s) carry no classified kind or status these owners could
          report: {unclassified.map((entry) => entry.path).join(', ')}
        </p>
      ) : null}
      <InventoryDetails inventory={inventory} />
    </section>
  );
}

function sourceNotes(entry: ReviewChangedFile): string[] {
  return [
    entry.mode_change ? 'mode changed' : null,
    entry.content === 'unknown' ? null : `content: ${entry.content}`,
    entry.detail ?? null,
  ].filter((note): note is string => note !== null);
}

function InventoryDetails({ inventory }: { inventory: ReviewSourceInventory }) {
  return (
    <details>
      <summary>Source inventory details</summary>
      <p>{inventory.detail}</p>
      <p className={muted} data-testid="review-inventory-command">
        reproduce: {inventory.command}
        {inventory.before_code_tree_id && inventory.after_code_tree_id
          ? ` · ${inventory.before_code_tree_id} → ${inventory.after_code_tree_id}`
          : ''}
      </p>
    </details>
  );
}
