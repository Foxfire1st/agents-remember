// The reusable dual pane: single (code only) or split (code LEFT, sidecar RIGHT). The markdown
// sidecar reuses grammar/Markdown.tsx (NOT CodeMirror). A missing partner / overview-without-code
// / binary file renders a STABLE-size placeholder so the layout never flip-flops. Split sizes are
// persisted by react-resizable-panels (autoSaveId). L4 reuses this shape for the change-set screen.
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import type { FileContent } from "../../data/files";
import { Markdown } from "../../grammar/Markdown";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import { FilePane } from "./FilePane";

// What the right (sidecar) pane shows, derived by the FileViewer from L1 pairing.
export type SidecarView =
  | { state: "markdown"; body: string }
  | { state: "missing" }
  | { state: "overview" }
  | { state: "empty" };

const fill = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column" });
const placeholder = css({
  height: "100%",
  display: "grid",
  placeItems: "center",
  padding: "1rem",
  color: "muted",
  fontSize: "0.8rem",
  textAlign: "center",
});
const banner = css({
  flexShrink: 0,
  paddingInline: "0.6rem",
  paddingBlock: "0.2rem",
  fontSize: "0.72rem",
  color: "amber",
  background: "bgPanel",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const handle = css({
  width: "3px",
  flexShrink: 0,
  background: "grid",
  cursor: "col-resize",
  transition: "background 0.15s ease",
  _hover: { background: "amber" },
});
const sidecarBox = css({ height: "100%", overflow: "auto", padding: "0.6rem 0.8rem", background: "bgPanel" });

function Placeholder({ label }: { label: string }) {
  return (
    <div className={placeholder} data-testid="pane-placeholder">
      {label}
    </div>
  );
}

function CodeSide({ code }: { code: FileContent | null }) {
  if (!code) return <Placeholder label="Select a code file from the tree" />;
  if (code.language === "binary")
    return <Placeholder label={`Binary file — ${code.size.toLocaleString()} bytes (not shown)`} />;
  return (
    <div className={fill}>
      {code.truncated ? (
        <div className={banner}>Showing the first 2 MiB of {code.size.toLocaleString()} bytes</div>
      ) : null}
      <FilePane content={code.content} language={code.language} />
    </div>
  );
}

function SidecarSide({ sidecar }: { sidecar: SidecarView }) {
  switch (sidecar.state) {
    case "markdown":
      return (
        <div className={sidecarBox} data-testid="sidecar-pane">
          <Markdown>{sidecar.body}</Markdown>
        </div>
      );
    case "overview":
      return <Placeholder label="Overview — no code partner" />;
    case "missing":
      return <Placeholder label="No sidecar for this file" />;
    default:
      return <Placeholder label="No onboarding selected" />;
  }
}

export function DualPane({
  code,
  sidecar,
  split,
}: {
  code: FileContent | null;
  sidecar: SidecarView;
  split: boolean;
}) {
  // Nothing opened yet: a faint effects-gated boomerang backdrop fills the whole pane (mirrors the
  // Operations / Chats empty states), replacing the per-side "select a file" placeholders until a code
  // file or onboarding doc is opened.
  if (code === null && sidecar.state === "empty") {
    return (
      <div className={fill} data-testid="dual-pane">
        {/* The siege-tank clip reads darker than the battlecruiser/adjutant loops, so it gets a touch
            more presence (0.18) than the shared 0.14 default to feel comparably bright on screen. */}
        <EmptyStateBackdrop src="/assets/sc2-siege-tank-boomerang.mp4" opacity={0.18}>
          Select a code file from the tree — or open an onboarding doc to read it.
        </EmptyStateBackdrop>
      </div>
    );
  }
  // A partnerless overview (markdown, no code) fills the pane on its own — in single mode the sidecar
  // side is never shown, so without this an overview opened from the onboarding tree would be invisible.
  const overviewOnly = code === null && sidecar.state === "markdown";
  if (!split || overviewOnly) {
    return (
      <div className={fill} data-testid="dual-pane">
        {overviewOnly ? <SidecarSide sidecar={sidecar} /> : <CodeSide code={code} />}
      </div>
    );
  }
  return (
    <PanelGroup direction="horizontal" autoSaveId="fileviewer.dualpane" data-testid="dual-pane">
      <Panel defaultSize={62} minSize={25}>
        <CodeSide code={code} />
      </Panel>
      <PanelResizeHandle className={handle} />
      <Panel defaultSize={38} minSize={20}>
        <SidecarSide sidecar={sidecar} />
      </Panel>
    </PanelGroup>
  );
}
