// One diff column of the Change-Set Viewer: a toolbar of persisted toggles over a DiffPane /
// FilePane. The three states from the task doc map onto two flags (persisted via usePersistedFlag
// so they survive a file switch AND a reload):
//   change-set (default)      -> collapsed diff (split MergeView, or inline unifiedMergeView)
//   full-file + highlight on  -> the whole file with changes still marked (no collapse)
//   full-file + highlight off -> the plain L2 FilePane on the after-content (no highlighter)
// `inline` flips split <-> inline for whichever diff is shown. Each column owns its own flag keys
// (passed in `keyPrefix`) so the code column and the sidecar (3rd) column persist independently.
import { ToggleButton } from "react-aria-components";

import { css, cx } from "../../../styled-system/css";
import { Markdown } from "../../grammar/Markdown";
import type { FileDiff } from "../../data/changeset";
import { FilePane } from "../file-viewer/FilePane";
import { usePersistedFlag } from "../file-viewer/usePersistedFlag";
import { DiffPane } from "./DiffPane";

const col = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column" });
const bar = css({
  flexShrink: 0,
  display: "flex",
  gap: "0.3rem",
  alignItems: "center",
  paddingInline: "0.5rem",
  paddingBlock: "0.25rem",
  background: "bgPanel",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const toggle = css({
  fontSize: "0.68rem",
  fontFamily: "mono",
  color: "muted",
  paddingInline: "0.4rem",
  paddingBlock: "0.1rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  background: "transparent",
  _hover: { color: "ink", borderColor: "amber" },
  "&[data-selected=true]": { color: "bg", background: "amber", borderColor: "amber" },
});
const body = css({ flex: "1", minHeight: "0" });
// The rendered-markdown view: same scrollable, padded surface the File Viewer's sidecar uses, so a
// changed onboarding file reads exactly as nicely here as it does in the file reader.
const mdScroll = css({ height: "100%", overflow: "auto", padding: "0.6rem 0.85rem", background: "bgPanel" });

function ChangeSetToolbar({
  isMarkdown,
  rendered,
  onRendered,
  showRendered,
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
      {!showRendered ? (
        <>
          <ToggleButton
            className={toggle}
            isSelected={fullFile}
            onChange={onFullFile}
            data-selected={fullFile}
          >
            {fullFile ? "full file" : "change-set"}
          </ToggleButton>
          {!plain ? (
            <ToggleButton
              className={toggle}
              isSelected={inline}
              onChange={onInline}
              data-selected={inline}
            >
              {inline ? "inline" : "split"}
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
      <span className={cx(css({ marginLeft: "auto", fontSize: "0.68rem", color: "muted" }))}>
        {summary}
      </span>
    </div>
  );
}

function diffContent(diff: FileDiff): { before: string; after: string } {
  return {
    before: diff.before?.content ?? "",
    after: diff.after?.content ?? "",
  };
}

function ChangeSetBody({
  showRendered,
  plain,
  before,
  after,
  language,
  inline,
  fullFile,
}: {
  showRendered: boolean;
  plain: boolean;
  before: string;
  after: string;
  language: string;
  inline: boolean;
  fullFile: boolean;
}) {
  return (
    <div className={body}>
      {showRendered ? (
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
          mode={inline ? "inline" : "split"}
          collapse={!fullFile}
        />
      )}
    </div>
  );
}

export function ChangeSetPane({ diff, keyPrefix }: { diff: FileDiff; keyPrefix: string }) {
  const [fullFile, setFullFile] = usePersistedFlag(`${keyPrefix}.fullfile`, false);
  const [inline, setInline] = usePersistedFlag(`${keyPrefix}.inline`, false);
  const [highlight, setHighlight] = usePersistedFlag(`${keyPrefix}.highlight`, true);
  // Onboarding (.md) gets a "rendered" mode that draws the prose like the file reader instead of a raw
  // text diff — the developer reads onboarding as formatted markdown, not as CodeMirror source. Only
  // markdown files offer it; the persisted flag survives a file switch + reload (per-column key).
  const isMarkdown = diff.language === "markdown";
  const [rendered, setRendered] = usePersistedFlag(`${keyPrefix}.rendered`, false);
  const showRendered = isMarkdown && rendered;

  const { before, after } = diffContent(diff);
  const plain = fullFile && !highlight;

  return (
    <div className={col} data-testid="changeset-pane">
      <ChangeSetToolbar
        isMarkdown={isMarkdown}
        rendered={rendered}
        onRendered={setRendered}
        showRendered={showRendered}
        fullFile={fullFile}
        onFullFile={setFullFile}
        plain={plain}
        inline={inline}
        onInline={setInline}
        highlight={highlight}
        onHighlight={setHighlight}
        summary={`${diff.kind} · ${diff.path}`}
      />
      <ChangeSetBody
        showRendered={showRendered}
        plain={plain}
        before={before}
        after={after}
        language={diff.language}
        inline={inline}
        fullFile={fullFile}
      />
    </div>
  );
}
