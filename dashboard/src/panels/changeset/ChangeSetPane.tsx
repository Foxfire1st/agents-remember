// One diff column of the Change-Set Viewer: a toolbar of persisted toggles over a DiffPane /
// FilePane. The three states from the task doc map onto two flags (persisted via usePersistedFlag
// so they survive a file switch AND a reload):
//   change-set (default)      -> collapsed diff (split MergeView, or inline unifiedMergeView)
//   full-file + highlight on  -> the whole file with changes still marked (no collapse)
//   full-file + highlight off -> the plain L2 FilePane on the after-content (no highlighter)
// `inline` flips split <-> inline for whichever diff is shown. Each column owns its own flag keys
// (passed in `keyPrefix`) so the code column and the sidecar (3rd) column persist independently.
import { useState } from 'react';
import { ToggleButton } from 'react-aria-components';

import { css, cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import { MediaView } from '../../grammar/Media';
import { changeSetMediaUrl, mediaForType, formatBytes, type MediaType } from '../../data/media';
import type { DiffSide, FileDiff } from '../../data/changeset';
import { FilePane } from '../file-viewer/FilePane';
import { usePersistedFlag } from '../file-viewer/usePersistedFlag';
import { DiffPane } from './DiffPane';

const col = css({ height: '100%', minHeight: '0', display: 'flex', flexDirection: 'column' });
const bar = css({
  flexShrink: 0,
  display: 'flex',
  gap: '0.3rem',
  alignItems: 'center',
  paddingInline: '0.5rem',
  paddingBlock: '0.25rem',
  background: 'bgPanel',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
});
const toggle = css({
  fontSize: '0.68rem',
  fontFamily: 'mono',
  color: 'muted',
  paddingInline: '0.4rem',
  paddingBlock: '0.1rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  cursor: 'pointer',
  background: 'transparent',
  _hover: { color: 'ink', borderColor: 'amber' },
  '&[data-selected=true]': { color: 'bg', background: 'amber', borderColor: 'amber' },
});
const body = css({ flex: '1', minHeight: '0' });
// The rendered-markdown view: same scrollable, padded surface the File Viewer's sidecar uses, so a
// changed onboarding file reads exactly as nicely here as it does in the file reader.
const mdScroll = css({
  height: "100%",
  overflow: "auto",
  padding: "0.6rem 0.85rem",
  background: "bgPanel",
});
const diffColumns = css({
  height: "100%",
  minHeight: "0",
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: "0.5rem",
  padding: "0.5rem",
  overflow: "auto",
});
const diffColumn = css({ minWidth: "0", display: "flex", flexDirection: "column", gap: "0.2rem" });
const sideLabel = css({ fontSize: "0.68rem", color: "muted", fontFamily: "mono" });
const sideMissing = css({
  padding: "0.6rem 0.8rem",
  fontSize: "0.78rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "dashed",
  borderColor: "grid",
  borderRadius: "2px",
});
const sideBinary = css({ padding: "0.6rem 0.8rem", fontSize: "0.78rem", color: "muted" });
const sideText = css({
  margin: "0",
  padding: "0.6rem 0.8rem",
  fontSize: "0.76rem",
  fontFamily: "mono",
  whiteSpace: "pre-wrap",
  overflow: "auto",
  maxHeight: "24rem",
});

// The one-request address of a change-set side, provided by the viewer that knows its selectors.
export interface ChangeSetMediaSelector {
  repo: string;
  scope?: string;
  master?: string;
  leaf?: string;
  mode?: string;
  comparison?: number;
  generation?: {
    codeBase?: string;
    codeTip?: string;
    memoryBase?: string;
    memoryTip?: string;
  };
}

function sideMedia(
  side: DiffSide | null,
  path: string,
  selector: (ChangeSetMediaSelector & { kind: "code" | "memory" }) | undefined,
  which: "before" | "after",
) {
  const media: MediaType | null = side?.mediaType ? mediaForType(side.mediaType) : null;
  if (!media || !selector) return null;
  return {
    media,
    src: changeSetMediaUrl({
      repo: selector.repo,
      scope: selector.scope,
      master: selector.master,
      leaf: selector.leaf,
      mode: selector.mode,
      comparison: selector.comparison,
      generation: selector.generation,
      kind: selector.kind,
      path,
      side: which,
    }),
    size: side?.size ?? null,
  };
}

// A changed picture or video shows before and after side by side; an added or removed one shows
// its one side and says that the other does not exist. A non-media binary is one line per side.
function MediaDiff({
  diff,
  selector,
  kind,
}: {
  diff: FileDiff;
  selector?: ChangeSetMediaSelector;
  kind: 'code' | 'memory';
}) {
  const describe = (side: DiffSide | null, which: 'before' | 'after') => {
    if (side === null)
      return (
        <div className={sideMissing} data-testid={`media-side-missing-${which}`}>
          the {which} side does not exist
        </div>
      );
    const media = sideMedia(side, diff.path, selector ? { ...selector, kind } : undefined, which);
    if (media)
      return (
        <MediaView
          src={media.src}
          media={media.media}
          kind={media.media.kind}
          size={media.size}
          testId={`media-side-${which}`}
        />
      );
    if (side.content !== null && side.content !== undefined)
      return (
        <pre className={sideText} data-testid={`text-side-${which}`}>
          {side.content}
        </pre>
      );
    return (
      <div className={sideBinary} data-testid={`binary-side-${which}`}>
        binary file · {formatBytes(side.size ?? 0)}
      </div>
    );
  };
  return (
    <div className={diffColumns} data-testid="media-diff">
      <div className={diffColumn}>
        <span className={sideLabel}>before</span>
        {describe(diff.before, 'before')}
      </div>
      <div className={diffColumn}>
        <span className={sideLabel}>after</span>
        {describe(diff.after, 'after')}
      </div>
    </div>
  );
}

function ChangeSetToolbar({
  isMarkdown,
  rendered,
  onRendered,
  showRendered,
  binaryDiff,
  fullFile,
  onFullFile,
  plain,
  inline,
  onInline,
  highlight,
  onHighlight,
  summary,
}: {
  isMarkdown: boolean;
  rendered: boolean;
  onRendered: (value: boolean) => void;
  showRendered: boolean;
  binaryDiff: boolean;
  fullFile: boolean;
  onFullFile: (value: boolean) => void;
  plain: boolean;
  inline: boolean;
  onInline: (value: boolean) => void;
  highlight: boolean;
  onHighlight: (value: boolean) => void;
  summary: string;
}) {
  return (
    <div className={bar}>
      {isMarkdown ? (
        <ToggleButton
          className={toggle}
          isSelected={rendered}
          onChange={onRendered}
          data-selected={rendered}
          data-testid="changeset-rendered-toggle"
        >
          rendered
        </ToggleButton>
      ) : null}
      {!showRendered && !binaryDiff ? (
        <>
          <ToggleButton
            className={toggle}
            isSelected={fullFile}
            onChange={onFullFile}
            data-selected={fullFile}
          >
            {fullFile ? 'full file' : 'change-set'}
          </ToggleButton>
          {!plain ? (
            <ToggleButton
              className={toggle}
              isSelected={inline}
              onChange={onInline}
              data-selected={inline}
            >
              {inline ? 'inline' : 'split'}
            </ToggleButton>
          ) : null}
          {fullFile ? (
            <ToggleButton
              className={toggle}
              isSelected={highlight}
              onChange={onHighlight}
              data-selected={highlight}
            >
              highlight
            </ToggleButton>
          ) : null}
        </>
      ) : null}
      <span className={cx(css({ marginLeft: 'auto', fontSize: '0.68rem', color: 'muted' }))}>
        {summary}
      </span>
    </div>
  );
}

function diffContent(diff: FileDiff): { before: string; after: string } {
  return {
    before: diff.before?.content ?? '',
    after: diff.after?.content ?? '',
  };
}

function ChangeSetBody({
  showRendered,
  binaryDiff,
  selector,
  plain,
  before,
  after,
  language,
  inline,
  fullFile,
  diff,
  kind,
}: {
  showRendered: boolean;
  binaryDiff: boolean;
  selector?: ChangeSetMediaSelector;
  plain: boolean;
  before: string;
  after: string;
  language: string;
  inline: boolean;
  fullFile: boolean;
  diff: FileDiff;
  kind: 'code' | 'memory';
}) {
  return (
    <div className={body}>
      {binaryDiff ? (
        <MediaDiff diff={diff} selector={selector} kind={kind} />
      ) : showRendered ? (
        // The after-content rendered; a pure deletion (no after) falls back to the removed prose so
        // the pane is never blank.
        <div className={mdScroll} data-testid="changeset-rendered">
          <Markdown>{after || before}</Markdown>
        </div>
      ) : plain ? (
        <FilePane content={after} language={language} />
      ) : (
        <DiffPane
          before={before}
          after={after}
          language={language}
          mode={inline ? 'inline' : 'split'}
          collapse={!fullFile}
        />
      )}
    </div>
  );
}

function paneFlags(diff: FileDiff, rendered: boolean) {
  const isMarkdown = diff.language === "markdown";
  const binaryDiff = Boolean(diff.before?.binary || diff.after?.binary);
  return { isMarkdown, binaryDiff, showRendered: isMarkdown && rendered && !binaryDiff };
}

export function ChangeSetPane({
  diff,
  keyPrefix,
  narrow = false,
  mediaSelector,
}: {
  diff: FileDiff;
  keyPrefix: string;
  narrow?: boolean;
  mediaSelector?: ChangeSetMediaSelector;
}) {
  const [fullFile, setFullFile] = usePersistedFlag(`${keyPrefix}.fullfile`, false);
  const [inline, setInline] = usePersistedFlag(`${keyPrefix}.inline`, false);
  const [narrowInline, setNarrowInline] = useState(true);
  const shownInline = narrow ? narrowInline : inline;
  const changeInline = narrow ? setNarrowInline : setInline;
  const [highlight, setHighlight] = usePersistedFlag(`${keyPrefix}.highlight`, true);
  // Onboarding (.md) gets a "rendered" mode that draws the prose like the file reader instead of a raw
  // text diff — the developer reads onboarding as formatted markdown, not as CodeMirror source. Only
  // markdown files offer it; the persisted flag survives a file switch + reload (per-column key).
  const isMarkdown = diff.language === 'markdown';
  const [rendered, setRendered] = usePersistedFlag(`${keyPrefix}.rendered`, false);
  // A side that is not text is drawn as media / stated as binary; the text toggles do not apply.
  const { binaryDiff, showRendered } = paneFlags(diff, rendered);

  const { before, after } = diffContent(diff);
  const plain = fullFile && !highlight;

  return (
    <div className={col} data-testid="changeset-pane">
      <ChangeSetToolbar
        isMarkdown={isMarkdown && !binaryDiff}
        rendered={rendered}
        onRendered={setRendered}
        showRendered={showRendered}
        binaryDiff={binaryDiff}
        fullFile={fullFile}
        onFullFile={setFullFile}
        plain={plain}
        inline={shownInline}
        onInline={changeInline}
        highlight={highlight}
        onHighlight={setHighlight}
        summary={`${diff.kind} · ${diff.path}`}
      />
      <ChangeSetBody
        showRendered={showRendered}
        binaryDiff={binaryDiff}
        selector={mediaSelector}
        plain={plain}
        before={before}
        after={after}
        language={diff.language}
        inline={shownInline}
        fullFile={fullFile}
        diff={diff}
        kind={diff.kind}
      />
    </div>
  );
}
