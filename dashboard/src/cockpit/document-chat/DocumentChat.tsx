import { memo } from 'react';
import { css } from '../../../styled-system/css';
import {
  ExplainerColumnChrome,
  normalizeExplainerTarget,
  type ExplainerColumnTarget,
} from '../../explainer/ExplainerColumn';
import { useExplainerColumn, type ExplainerColumnState } from '../../explainer/columnState';
import { columnSelection } from '../../explainer/model';
import { PaseoChatFrame } from '../PaseoChatFrame';
import {
  UNAVAILABLE_HEADLINE,
  type PaseoFrameUnavailableReason,
} from '../paseoFrameModel';
import { RoleChatsPane } from '../RoleChats';
import { TrayColumnDock } from '../../tray/TrayDock';
import { usePublishShownChat, type ShownChat } from '../../tray/shownChat';
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
const INACTIVE_ADDRESS = { repository: '', master: '', leaf: '' } as const;

/** The pane's words for the shown agent's warmup truth (MIK-R145 rule 6). */
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

function DocumentChatChrome({
  state,
  props,
}: {
  state: ReturnType<typeof useDocumentChat>;
  props: DocumentChatProps;
}) {
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
  const warmupText = state.warmupAgent ? warmupAgentText(state.warmupAgent) : null;
  return (
    <>
      {launchControl}
      {state.binding.problem ? <p role="status">{state.binding.problem}</p> : null}
      {state.warmupLine ? (
        <p role="status" data-testid="host-warmup-line">
          {state.warmupLine}
        </p>
      ) : null}
      {launcher}
      {unreadableMessage(state.available, state.answer?.detail, state.answer?.reason, state.onRetry)}
      {warmupText ? (
        <p role="status" data-testid="host-warmup-agent" data-warmup-state={state.warmupAgent!.state}>
          {warmupText}
        </p>
      ) : null}
    </>
  );
}

function resolveDocumentFrameProps(
  props: DocumentChatProps,
  docState: ReturnType<typeof useDocumentChat>,
  explainerTarget: ReturnType<typeof normalizeExplainerTarget> | null,
  explainerColumn: ExplainerColumnState,
) {
  if (explainerTarget) {
    const { address, preload } = explainerTarget;
    if (props.unbound) {
      return {
        hideFrame: !props.unbound.pending,
        active: props.active,
        preload: Boolean(props.preload || preload),
        scope: `explainer:${address.repository}/${address.master}/${address.leaf}`,
        target: null,
        holding: props.unbound.pending ? props.unbound.line : null,
      };
    }
    return {
      hideFrame: !explainerColumn.showFrame,
      active: props.active && explainerColumn.showFrame,
      preload: Boolean(props.preload || preload),
      scope: `explainer:${address.repository}/${address.master}/${address.leaf}`,
      target: explainerColumn.frameTarget,
      holding: null,
    };
  }
  return {
    hideFrame: docState.hideFrame,
    active: props.active,
    preload: props.preload === true,
    scope: docState.scope,
    target: docState.target,
    holding: docState.asking ? (docState.waitLine ?? ASKING) : null,
  };
}

function documentShownChat(
  isExplainer: boolean,
  active: boolean,
  docState: ReturnType<typeof useDocumentChat>,
): ShownChat | null {
  if (isExplainer || !active || docState.hideFrame || !docState.target) return null;
  return { agentId: docState.target.agentId };
}

function useOptionalExplainerColumn(
  explainerTarget: ReturnType<typeof normalizeExplainerTarget> | null,
  active: boolean,
  taskPath?: string,
): ExplainerColumnState {
  const address = explainerTarget ? explainerTarget.address : INACTIVE_ADDRESS;
  const task = explainerTarget ? explainerTarget.task : '';
  const options = explainerTarget
    ? {
        planned: explainerTarget.planned,
        comparisonNumber: explainerTarget.comparisonNumber,
        history: explainerTarget.history,
        taskPath,
      }
    : undefined;
  return useExplainerColumn(address, task, active && Boolean(explainerTarget), options);
}

function sectionProps(
  isExplainer: boolean,
  docScope: string,
  phase: ExplainerColumnState['phase'],
) {
  if (isExplainer) {
    return {
      'data-testid': 'explainer-column',
      'data-phase': phase,
      'data-selection': columnSelection(phase),
    };
  }
  return {
    'data-testid': 'document-chat',
    'data-document': docScope,
  };
}

function ColumnChrome({
  explainerTarget,
  explainerColumn,
  docState,
  props,
}: {
  explainerTarget: ReturnType<typeof normalizeExplainerTarget> | null;
  explainerColumn: ExplainerColumnState;
  docState: ReturnType<typeof useDocumentChat>;
  props: DocumentChatProps;
}) {
  if (!explainerTarget) return <DocumentChatChrome state={docState} props={props} />;
  if (props.unbound) {
    return props.unbound.pending ? null : <p role="status">{props.unbound.line}</p>;
  }
  return (
    <ExplainerColumnChrome
      column={explainerColumn}
      task={explainerTarget.task}
      unboundLine={null}
    />
  );
}

/**
 * One persistent chat column across Operations and Review (MIK-R122, MIK-R89 v2 rule 12,
 * MIK-R106 v2 rule 1): switching between a task document and the review page's explainer updates
 * the column chrome and steers the same PaseoChatFrame instance without unmounting the iframe.
 */
function DocumentChatImpl(props: DocumentChatProps) {
  const explainerTarget = props.explainer ? normalizeExplainerTarget(props.explainer) : null;
  const isExplainer = explainerTarget !== null;
  const docState = useDocumentChat({ ...props, active: props.active && !isExplainer });
  const explainerColumn = useOptionalExplainerColumn(
    explainerTarget,
    props.active && !props.unbound,
    props.taskDocumentRef?.path,
  );
  usePublishShownChat(documentShownChat(isExplainer, props.active, docState));
  const frame = resolveDocumentFrameProps(props, docState, explainerTarget, explainerColumn);
  const frameStyle = frame.hideFrame ? { display: 'none' } : undefined;
  return (
    <section className={shell} {...sectionProps(isExplainer, docState.scope, explainerColumn.phase)}>
      <ColumnChrome
        explainerTarget={explainerTarget}
        explainerColumn={explainerColumn}
        docState={docState}
        props={props}
      />
      <div
        key="persistent-chat-frame"
        className={shell}
        style={frameStyle}
        aria-hidden={frame.hideFrame || undefined}
        data-testid={isExplainer ? 'explainer-frame-holder' : undefined}
      >
        <PaseoChatFrame
          active={frame.active}
          preload={frame.preload}
          scope={frame.scope}
          target={frame.target}
          page="document"
          holding={frame.holding}
          onAvailability={docState.onAvailability}
        />
      </div>
      <TrayColumnDock key="persistent-tray-dock" visible={props.active} page="document" />
    </section>
  );
}

export const DocumentChat = memo(DocumentChatImpl);
export type { ExplainerColumnTarget };
