import { memo } from 'react';
import { css } from '../../../styled-system/css';
import { PaseoChatFrame } from '../PaseoChatFrame';
import {
  UNAVAILABLE_HEADLINE,
  type PaseoAgentTarget,
  type PaseoFrameUnavailableReason,
} from '../paseoFrameModel';
import { RoleChatsPane } from '../RoleChats';
import { TrayColumnDock } from '../../tray/TrayDock';
import { usePublishShownChat } from '../../tray/shownChat';
import { documentLauncherRoles } from './model';
import { useDocumentChat, type DocumentChatProps, type WarmupAgentPicture } from './state';

const shell = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minHeight: '0',
  minWidth: '0',
});
const unreadable = css({
  display: 'flex',
  flexShrink: '0',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.6rem',
  padding: '0.3rem 0.75rem',
  borderBottom: '1px solid var(--grid)',
  background: 'var(--bg-panel)',
  color: 'amber',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
});
const ASKING = "Waiting for the dashboard to name this document's chat…";

function DocumentChatImpl(props: DocumentChatProps) {
  const state = useDocumentChat(props);
  // The tray's default target for an object pick is the chat this column shows (MIK-R188 rule 7).
  // The view docks the tray (rule 5) as a sibling right after the frame's wrapper, drawn over the
  // frame above Paseo's message box: it reads rectangles only and never moves or reloads the frame.
  usePublishShownChat(props.active && !state.hideFrame && state.target ? { agentId: state.target.agentId } : null);
  const launchControl = state.hasControl ? (
    <button type="button" onClick={state.onToggleLauncher}>
      Launch role
    </button>
  ) : null;
  const launcher = state.showLauncher ? (
    <RoleChatsPane
      key={state.scope}
      active={props.active}
      tasks={props.tasks}
      boundSelection={state.binding.selection}
      boundRoles={documentLauncherRoles(state.binding)}
      onExecution={state.onExecution}
    />
  ) : null;
  return (
    <DocumentChatView
      scope={state.scope}
      launcher={launcher}
      launchControl={launchControl}
      bindingProblem={state.binding.problem}
      detail={state.answer?.detail}
      reason={state.answer?.reason}
      onRetry={state.onRetry}
      available={state.available}
      hideFrame={state.hideFrame}
      asking={state.asking}
      waitLine={state.waitLine}
      active={props.active}
      preload={props.preload === true}
      target={state.target}
      onAvailability={state.onAvailability}
      warmupLine={state.warmupLine}
      warmupAgent={state.warmupAgent}
    />
  );
}

/** The pane's words for the shown agent's warmup truth (L81-R1-F28). */
export function warmupAgentText(picture: WarmupAgentPicture): string | null {
  if (picture.state === 'loaded') return null;
  if (picture.state === 'failed')
    return `Loading this agent failed${picture.reason ? `: ${picture.reason}` : ''}.`;
  if (picture.state === 'skipped')
    return `This agent was not loaded${picture.reason ? `: ${picture.reason}` : ''}.`;
  if (picture.state === 'pending')
    return `This agent is waiting to load${picture.reason ? `: ${picture.reason}` : ''}.`;
  return 'This agent is being loaded now…';
}

function unreadableMessage(
  available: boolean,
  detail: string | undefined,
  reason: PaseoFrameUnavailableReason | undefined,
  onRetry: () => void,
) {
  if (!available || !detail) return null;
  return (
    <div
      role="status"
      className={unreadable}
      data-testid="document-chat-unreadable"
      data-reason={reason ?? 'backend'}
    >
      <span>
        {UNAVAILABLE_HEADLINE[reason ?? 'backend']}: {detail}
      </span>
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

function DocumentChatView({
  scope,
  launcher,
  launchControl,
  bindingProblem,
  detail,
  reason,
  onRetry,
  available,
  hideFrame,
  asking,
  waitLine,
  active,
  preload,
  target,
  onAvailability,
  warmupLine,
  warmupAgent,
}: {
  scope: string;
  launcher: React.ReactNode;
  launchControl: React.ReactNode;
  bindingProblem?: string;
  detail?: string;
  reason?: PaseoFrameUnavailableReason;
  onRetry: () => void;
  available: boolean;
  hideFrame: boolean;
  asking: boolean;
  waitLine: string | null;
  active: boolean;
  preload: boolean;
  target: PaseoAgentTarget | null;
  onAvailability: (available: boolean) => void;
  warmupLine: string | null;
  warmupAgent: WarmupAgentPicture | null;
}) {
  const warmupText = warmupAgent ? warmupAgentText(warmupAgent) : null;
  return (
    <section className={shell} data-testid="document-chat" data-document={scope}>
      {launchControl}
      {bindingProblem ? <p role="status">{bindingProblem}</p> : null}
      {/* MIK-R145 rule 6: one line while the host-start loading runs; gone when it is done. */}
      {warmupLine ? (
        <p role="status" data-testid="host-warmup-line">
          {warmupLine}
        </p>
      ) : null}
      {launcher}
      {/* An answer that could not be read is one line with Retry; the chat stays. */}
      {unreadableMessage(available, detail, reason, onRetry)}
      {/* MIK-R145 rule 6 (L81-R1-F28): the pane of an agent the loading has not
          finished says that agent's own truth: pending/in flight, failed with the
          host's words, or not loaded with the run's reason. */}
      {warmupText ? (
        <p role="status" data-testid="host-warmup-agent" data-warmup-state={warmupAgent!.state}>
          {warmupText}
        </p>
      ) : null}
      {/* MIK-R122 rule 6: a hidden chat keeps its frame loaded but stays out of keyboard
          focus and the accessibility tree (display:none already drops focusability). */}
      <div
        className={shell}
        style={hideFrame ? { display: 'none' } : undefined}
        aria-hidden={hideFrame || undefined}
      >
        <PaseoChatFrame
          active={active}
          preload={preload}
          scope={scope}
          target={target}
          page="document"
          holding={asking ? (waitLine ?? ASKING) : null}
          onAvailability={onAvailability}
        />
      </div>
      <TrayColumnDock visible={active} page="document" />
    </section>
  );
}

export const DocumentChat = memo(DocumentChatImpl);
