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

export function ChangeSetPane({ diff, keyPrefix }: { diff: FileDiff; keyPrefix: string }) {
  const [fullFile, setFullFile] = usePersistedFlag(`${keyPrefix}.fullfile`, false);
  const [inline, setInline] = usePersistedFlag(`${keyPrefix}.inline`, false);
  const [highlight, setHighlight] = usePersistedFlag(`${keyPrefix}.highlight`, true);

  const before = diff.before?.content ?? "";
  const after = diff.after?.content ?? "";
  const plain = fullFile && !highlight;

  return (
    <div className={col} data-testid="changeset-pane">
      <div className={bar}>
        <ToggleButton
          className={toggle}
          isSelected={fullFile}
          onChange={setFullFile}
          data-selected={fullFile}
        >
          {fullFile ? "full file" : "change-set"}
        </ToggleButton>
        {!plain ? (
          <ToggleButton
            className={toggle}
            isSelected={inline}
            onChange={setInline}
            data-selected={inline}
          >
            {inline ? "inline" : "split"}
          </ToggleButton>
        ) : null}
        {fullFile ? (
          <ToggleButton
            className={toggle}
            isSelected={highlight}
            onChange={setHighlight}
            data-selected={highlight}
          >
            highlight
          </ToggleButton>
        ) : null}
        <span className={cx(css({ marginLeft: "auto", fontSize: "0.68rem", color: "muted" }))}>
          {diff.kind} · {diff.path}
        </span>
      </div>
      <div className={body}>
        {plain ? (
          <FilePane content={after} language={diff.language} />
        ) : (
          <DiffPane
            before={before}
            after={after}
            language={diff.language}
            mode={inline ? "inline" : "split"}
            collapse={!fullFile}
          />
        )}
      </div>
    </div>
  );
}
