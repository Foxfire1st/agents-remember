// A chat a surface names by its exact agent, and what the Chats pane does with it (L190-R1-F09).
//
// The attention bell names the served agent of a seat (the held/permission row's `agentId`) and,
// when the seat has one, its role and task document. The dashboard's way into a chat is the Chats
// pane's frame target — the agent the embedded application is asked to show — and the launcher's
// selection, which is the seat's role and document. This module is the seam between the two: it
// turns a row's payload into that request and resolves the frame's target from it.
//
// It never writes the observed-chat channel (`frameTrayChannel.setFrameChat`): that records the
// chat a frame reports showing, and only the frame's own answer may say it.
import type { TaskDocumentRef } from '../types/projection';
import { paseoAgentTarget, type PaseoAgentTarget } from './paseoFrameModel';
import {
  LAUNCHER_ROLES,
  roleDocumentScope,
  type LauncherRole,
  type RoleLaunchSelection,
} from './roleLaunchModel';

/** The exact chat a surface named, and the seat it serves (`role` and document are optional). */
export interface AgentChatTarget {
  agentId: string;
  role?: string;
  taskDocumentRef?: TaskDocumentRef;
}

/** What a bell row carries: the same seat, with its document under the row's own field name. */
export interface ServedAgentSeat {
  agentId: string;
  role?: string;
  taskKey?: string;
  taskReference?: TaskDocumentRef;
}

/** The launcher's role for a served role name, or null for one the launcher does not have. */
export function launcherRoleOf(role: string | undefined): LauncherRole | null {
  if (!role) return null;
  const known = LAUNCHER_ROLES.find((entry) => entry.id === role)?.id;
  if (known) return known;
  // A seat still served under the investigator's old name is the launcher's one investigator.
  return role === 'system-specialist' ? 'investigator' : null;
}

/** The row's payload as the shell's chat request; no task is invented for a taskless seat. */
export function seatChatTarget(seat: ServedAgentSeat): AgentChatTarget {
  return {
    agentId: seat.agentId,
    ...(seat.role !== undefined ? { role: seat.role } : {}),
    ...(seat.taskReference !== undefined ? { taskDocumentRef: seat.taskReference } : {}),
  };
}

/**
 * The launcher selection the seat is: its role and its own document. Null when the row names no
 * role the launcher has — then the request still steers the frame, and the launcher's selection
 * is left as the developer had it.
 */
export function seatChatSelection(target: AgentChatTarget | null): RoleLaunchSelection | null {
  const role = launcherRoleOf(target?.role);
  if (!role) return null;
  return {
    role,
    ...(target?.taskDocumentRef ? { taskDocumentRef: target.taskDocumentRef } : {}),
  };
}

/**
 * The frame's target. A chat a surface named wins: it is the exact served agent the row reports,
 * while the host's receipt for a selection may name another seat of the same role and document
 * (a closed run, a replaced agent). Without one, the target is the agent of the execution the
 * launcher's selection resolves — the pane's one other way of knowing what to show.
 */
export function chatFrameTarget(
  requested: AgentChatTarget | null | undefined,
  execution: unknown,
): PaseoAgentTarget | null {
  if (requested?.agentId) return { agentId: requested.agentId };
  return paseoAgentTarget(execution);
}

/**
 * The frame's scope: the seat a surface named, as its own role and document, while that chat is
 * the frame's; else the launcher selection's scope. The launcher prunes a document it cannot
 * resolve on its own (a leaf needs its sprint and master above it), so the seat's document would
 * be lost between the row and the chat if the frame read the launcher's scope alone.
 */
export function chatFrameScope(
  requested: AgentChatTarget | null | undefined,
  selectionScope: string,
): string {
  const seat = seatChatSelection(requested ?? null);
  return seat ? JSON.stringify(roleDocumentScope(seat)) : selectionScope;
}

/**
 * Whether a launcher selection ends the chat a surface named. The developer's own pick does, and so
 * does a selection of another role. The launcher also repairs a selection it cannot resolve on its
 * own — a bell seat's leaf, whose sprint and master the row does not carry — and that repair keeps
 * the role and names no document: it is no navigation, so the row's chat stays.
 */
export function selectionEndsNamedChat(
  selection: RoleLaunchSelection,
  named: AgentChatTarget | null,
): boolean {
  if (!named) return false;
  if (launcherRoleOf(named.role) !== selection.role) return true;
  return Boolean(
    selection.sprintDocumentRef ?? selection.masterDocumentRef ?? selection.taskDocumentRef,
  );
}
