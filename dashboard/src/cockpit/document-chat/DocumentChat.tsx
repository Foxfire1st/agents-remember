import { memo } from "react";
import { css } from "../../../styled-system/css";
import { PaseoChatFrame } from "../PaseoChatFrame";
import { UNAVAILABLE_HEADLINE, type PaseoAgentTarget, type PaseoFrameUnavailableReason } from "../paseoFrameModel";
import { RoleChatsPane } from "../RoleChats";
import { documentLauncherRoles } from "./model";
import { useDocumentChat, type DocumentChatProps } from "./state";

const shell = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", minWidth: "0" });
const unreadable = css({
  display: "flex", flexShrink: "0", flexWrap: "wrap", alignItems: "center", gap: "0.6rem", padding: "0.3rem 0.75rem",
  borderBottom: "1px solid var(--grid)", background: "var(--bg-panel)", color: "amber",
  fontFamily: "var(--font-mono)", fontSize: "0.72rem",
});
const ASKING = "Waiting for the dashboard to name this document's chat…";

function DocumentChatImpl(props: DocumentChatProps) {
  const state = useDocumentChat(props);
  const launchControl = state.hasControl ? <button type="button" onClick={state.onToggleLauncher}>Launch role</button> : null;
  const launcher = state.showLauncher ? <RoleChatsPane key={state.scope} active={props.active} taskDocuments={props.taskDocuments} series={props.series}
    boundSelection={state.binding.selection} boundRoles={documentLauncherRoles(state.binding)} onExecution={state.onExecution} /> : null;
  return <DocumentChatView scope={state.scope} launcher={launcher} launchControl={launchControl} bindingProblem={state.binding.problem}
    detail={state.answer?.detail} reason={state.answer?.reason} onRetry={state.onRetry} available={state.available} hideFrame={state.hideFrame}
    asking={state.asking} active={props.active} target={state.target} onAvailability={state.onAvailability} />;
}

function DocumentChatView({ scope, launcher, launchControl, bindingProblem, detail, reason, onRetry, available, hideFrame, asking, active, target, onAvailability }: {
  scope: string; launcher: React.ReactNode; launchControl: React.ReactNode; bindingProblem?: string; detail?: string; reason?: PaseoFrameUnavailableReason; onRetry: () => void; available: boolean; hideFrame: boolean;
  asking: boolean; active: boolean; target: PaseoAgentTarget | null; onAvailability: (available: boolean) => void;
}) {
  return (
    <section className={shell} data-testid="document-chat" data-document={scope}>
      {launchControl}
      {bindingProblem ? <p role="status">{bindingProblem}</p> : null}
      {launcher}
      {/* An answer that could not be read is one line with Retry; the chat stays. */}
      {available && detail ? (
        <div role="status" className={unreadable} data-testid="document-chat-unreadable" data-reason={reason ?? "backend"}>
          <span>{UNAVAILABLE_HEADLINE[reason ?? "backend"]}: {detail}</span>
          <button type="button" onClick={onRetry}>Retry</button>
        </div>
      ) : null}
      {/* MIK-R122 rule 6: a hidden chat keeps its frame loaded but stays out of keyboard
          focus and the accessibility tree (display:none already drops focusability). */}
      <div className={shell} style={hideFrame ? { display: "none" } : undefined} aria-hidden={hideFrame || undefined}>
        <PaseoChatFrame active={active} scope={scope} target={target} page="document" holding={asking ? ASKING : null} onAvailability={onAvailability} />
      </div>
    </section>
  );
}

export const DocumentChat = memo(DocumentChatImpl);
