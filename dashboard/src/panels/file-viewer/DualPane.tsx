// The reusable dual pane: single (code only) or split (code LEFT, sidecar RIGHT). The markdown
// sidecar reuses grammar/Markdown.tsx (NOT CodeMirror). A missing partner / overview-without-code
// / binary file renders a STABLE-size placeholder so the layout never flip-flops. Split sizes are
// persisted by react-resizable-panels (autoSaveId). L4 reuses this shape for the change-set screen.
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import type { FileContent } from "../../data/files";
import { MediaView } from "../../grammar/Media";
import { Markdown } from "../../grammar/Markdown";
import type { DocumentContext } from "../../grammar/DocumentMarkdown";
import { mediaForType } from "../../data/media";
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
// The rendered code file's scroll box: the reader's one scrolling surface for a document.
const documentScroll = css({
  height: "100%",
  overflow: "auto",
  padding: "0.8rem 1.1rem",
  background: "bg",
});

function Placeholder({ label }: { label: string }) {
  return (
    <div className={placeholder} data-testid="pane-placeholder">
      {label}
    </div>
  );
}

function MediaCodeSide({ code, mediaUrl }: { code: FileContent; mediaUrl?: string | null }) {
  const media = code.mediaType ? mediaForType(code.mediaType) : null;
  if (!media) return null;
  return (
    <div className={fill} data-pane-scroll="true">
      <MediaView
        src={mediaUrl ?? ""}
        media={media}
        kind={media.kind}
        size={code.size}
        tooLarge={code.tooLarge}
        testId="code-media"
      />
    </div>
  );
}

function RenderedDocument({
  code,
  document,
}: {
  code: FileContent;
  document: DocumentContext;
}) {
  return (
    <div className={documentScroll} data-pane-scroll="true" data-testid="document-rendered">
      {code.truncated ? (
        <div className={banner} data-testid="truncation-banner">
          Showing the first 2 MiB of {code.size.toLocaleString()} bytes
        </div>
      ) : null}
      <Markdown document={document}>{code.content}</Markdown>
    </div>
  );
}

// `repository` names the File Viewer's repository: its pane carries the line gutter's ⊕ (MIK-R188
// rule 1). Without it the pane has no tray gutter.
function CodeSide({
  code,
  repository,
  mediaUrl,
  rendered = false,
  document: documentContext,
}: {
  code: FileContent | null;
  repository?: string;
  mediaUrl?: string | null;
  rendered?: boolean;
  document?: DocumentContext;
}) {
  if (!code) return <Placeholder label="Select a code file from the tree" />;
  if (code.mediaType) return <MediaCodeSide code={code} mediaUrl={mediaUrl} />;
  if (code.language === "binary")
    return <Placeholder label={`Binary file — ${code.size.toLocaleString()} bytes (not shown)`} />;
  if (rendered && code.language === "markdown" && documentContext)
    return <RenderedDocument code={code} document={documentContext} />;
  return (
    <div className={fill}>
      {code.truncated ? (
        <div className={banner} data-testid="truncation-banner">Showing the first 2 MiB of {code.size.toLocaleString()} bytes</div>
      ) : null}
      <FilePane
        content={code.content}
        language={code.language}
        trayLines={repository ? { repository, scope: code.scope, path: code.path } : undefined}
      />
    </div>
  );
}

function SidecarSide({
  sidecar,
  document: documentContext,
}: {
  sidecar: SidecarView;
  document?: DocumentContext;
}) {
  switch (sidecar.state) {
    case "markdown":
      return (
        <div className={sidecarBox} data-testid="sidecar-pane" data-pane-scroll="true">
          <Markdown document={documentContext}>{sidecar.body}</Markdown>
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
  repository,
  mediaUrl,
  rendered,
  document,
  sidecarDocument,
}: {
  code: FileContent | null;
  sidecar: SidecarView;
  split: boolean;
  repository?: string;
  mediaUrl?: string | null;
  rendered?: boolean;
  document?: DocumentContext;
  sidecarDocument?: DocumentContext;
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
        {overviewOnly ? <SidecarSide sidecar={sidecar} document={sidecarDocument} /> : <CodeSide code={code} repository={repository} mediaUrl={mediaUrl} rendered={rendered} document={document} />}
      </div>
    );
  }
  return (
    <PanelGroup direction="horizontal" autoSaveId="fileviewer.dualpane" data-testid="dual-pane">
      <Panel defaultSize={62} minSize={25}>
        <CodeSide code={code} repository={repository} mediaUrl={mediaUrl} rendered={rendered} document={document} />
      </Panel>
      <PanelResizeHandle className={handle} />
      <Panel defaultSize={38} minSize={20}>
        <SidecarSide sidecar={sidecar} document={sidecarDocument} />
      </Panel>
    </PanelGroup>
  );
}
