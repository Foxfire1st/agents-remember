import { memo } from "react";
import { css } from "../../../styled-system/css";
import { FrameUnavailable, PaseoChatFrame } from "../PaseoChatFrame";
import type { PaseoAgentTarget, PaseoFrameUnavailableReason } from "../paseoFrameModel";
import { RoleChatsPane } from "../RoleChats";
import { useDocumentChat, type DocumentChatProps } from "./state";

const shell = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", minWidth: "0" });

function DocumentChatImpl(props: DocumentChatProps) {
  const state = useDocumentChat(props);
  const launchControl = state.hasControl ? <button type="button" onClick={state.onToggleLauncher}>Launch role</button> : null;
  const launcher = state.showLauncher ? <RoleChatsPane key={state.scope} active={props.active} taskDocuments={props.taskDocuments} series={props.series}
    boundSelection={state.binding.selection} boundRoles={state.binding.roles} onExecution={state.onExecution} /> : null;
  return <DocumentChatView scope={state.scope} launcher={launcher} launchControl={launchControl} bindingProblem={state.binding.problem}
    detail={state.answer?.detail} reason={state.answer?.reason} onRetry={state.onRetry} available={state.available} hideFrame={state.hideFrame}
    active={props.active} target={state.target} onAvailability={state.onAvailability} />;
}

function DocumentChatView({ scope, launcher, launchControl, bindingProblem, detail, reason, onRetry, available, hideFrame, active, target, onAvailability }: {
  scope: string; launcher: React.ReactNode; launchControl: React.ReactNode; bindingProblem?: string; detail?: string; reason?: PaseoFrameUnavailableReason; onRetry: () => void; available: boolean; hideFrame: boolean;
  active: boolean; target: PaseoAgentTarget | null; onAvailability: (available: boolean) => void;
}) {
  return (
    <section className={shell} data-testid="document-chat" data-document={scope}>
      {launchControl}
      {bindingProblem ? <p role="status">{bindingProblem}</p> : null}
      {launcher}
      {available && detail ? <FrameUnavailable reason={reason ?? "backend"} detail={detail} onRetry={onRetry} /> : null}
      <div className={shell} style={hideFrame ? { display: "none" } : undefined}>
        <PaseoChatFrame active={active} scope={scope} target={target} page="document" onAvailability={onAvailability} />
      </div>
    </section>
  );
}

export const DocumentChat = memo(DocumentChatImpl);
