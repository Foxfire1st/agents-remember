import { useEffect, useState } from 'react';

import { reviewerParentMatches, type ReviewerAltitude } from './reviewerContext';
import {
  sessionRole,
  sessionSeatRole,
  useSessions,
  type OpenSession,
  type SessionRole,
} from './sessions';
import { sameTaskDocumentRef, taskDocumentRefForDoc } from './taskIdentity';
import type { TaskDocNode } from '../types/projection';
import type { TaskDocumentRef } from '../types/terminalCatalog';

export const SPRINT_ROLE_ORDER = [
  'architect',
  'orchestrator',
  'strategist',
  'designer',
  'system-specialist',
  'reviewer',
] as const;
export const CREATABLE_SPRINT_ROLES = SPRINT_ROLE_ORDER.filter((role) => role !== 'reviewer');
export const MASTER_ROLE_ORDER = ['manager', 'reviewer'] as const;
const LEAF_ROLE_ORDER = ['worker', 'reviewer', 'curator'] as const;

export type SprintRole = (typeof SPRINT_ROLE_ORDER)[number];
export type TaskChatRole = SprintRole | (typeof MASTER_ROLE_ORDER)[number];

export const ROLE_LABELS: Record<TaskChatRole, string> = {
  architect: 'Architect',
  orchestrator: 'Orchestrator',
  strategist: 'Strategist',
  designer: 'Designer',
  'system-specialist': 'System Specialist',
  manager: 'Manager',
  reviewer: 'Reviewer',
};

function taskDocumentForRef(
  ref: TaskDocumentRef | undefined,
  taskDocuments: TaskDocNode[],
): TaskDocNode | undefined {
  return ref
    ? taskDocuments.find((doc) => sameTaskDocumentRef(taskDocumentRefForDoc(doc), ref))
    : undefined;
}

function taskAltitude(doc: TaskDocNode | undefined): ReviewerAltitude | undefined {
  if (!doc) return undefined;
  if (doc.kind !== 'master') return 'leaf';
  return doc.orchestrates.length > 0 ? 'sprint' : 'master';
}

function owningMaster(ref: TaskDocumentRef): TaskDocumentRef {
  const directory = ref.path.split('/').slice(0, -1).join('/');
  return { repository: ref.repository, path: `${directory}/task.json` };
}

function reviewerIsValid(
  session: OpenSession,
  altitude: ReviewerAltitude,
  document: TaskDocumentRef,
): boolean {
  return reviewerParentMatches(
    session,
    altitude,
    document,
    altitude === 'leaf' ? owningMaster(document) : undefined,
  );
}

function workingLeafSeat(left: OpenSession, right: OpenSession): number {
  const working = (session: OpenSession) =>
    session.liveTurnWorking ||
    session.turnState === 'working' ||
    session.controlActivity === 'running';
  const activeDelta = Number(working(right)) - Number(working(left));
  if (activeDelta !== 0) return activeDelta;
  return (
    LEAF_ROLE_ORDER.indexOf(sessionSeatRole(left) as (typeof LEAF_ROLE_ORDER)[number]) -
      LEAF_ROLE_ORDER.indexOf(sessionSeatRole(right) as (typeof LEAF_ROLE_ORDER)[number]) ||
    left.id.localeCompare(right.id)
  );
}

function roleSeats(
  bound: OpenSession[],
  altitude: ReviewerAltitude | undefined,
  document: TaskDocumentRef | undefined,
): OpenSession[] {
  if (!altitude || !document) return [];
  const order = altitude === 'sprint' ? SPRINT_ROLE_ORDER : MASTER_ROLE_ORDER;
  if (altitude === 'leaf')
    return bound
      .filter((session) => {
        const role = sessionSeatRole(session);
        return (
          LEAF_ROLE_ORDER.includes(role as (typeof LEAF_ROLE_ORDER)[number]) &&
          (role !== 'reviewer' || reviewerIsValid(session, altitude, document))
        );
      })
      .sort(workingLeafSeat);
  return bound
    .filter((session) => {
      const role = sessionSeatRole(session);
      return (
        order.includes(role as never) &&
        (role !== 'reviewer' || reviewerIsValid(session, altitude, document))
      );
    })
    .sort(
      (left, right) =>
        order.indexOf(sessionSeatRole(left) as never) -
        order.indexOf(sessionSeatRole(right) as never),
    );
}

export function useRailChatSessions(
  taskDocumentRef: TaskDocumentRef | undefined,
  taskDocuments: TaskDocNode[],
  selectedRole: TaskChatRole | undefined,
) {
  const sessions = useSessions((state) => state.sessions);
  const altitude = taskAltitude(taskDocumentForRef(taskDocumentRef, taskDocuments));
  const bound = sessions.filter(
    (session) =>
      (session.status ?? 'running') === 'running' &&
      sameTaskDocumentRef(session.taskDocumentRef, taskDocumentRef),
  );
  const seats = roleSeats(bound, altitude, taskDocumentRef);
  const taskChat =
    altitude === 'leaf'
      ? seats[0]
      : (seats.find((session) => sessionSeatRole(session) === selectedRole) ?? seats[0]);
  const matches = (session: OpenSession, role: SessionRole): boolean =>
    (session.status ?? 'running') === 'running' &&
    sessionRole(session) === role &&
    !session.taskDocumentRef;
  const currentSession = (role: SessionRole): OpenSession | undefined =>
    [...sessions].reverse().find((session) => matches(session, role));
  const chatSession = taskDocumentRef ? taskChat : currentSession('chat');
  const terminalSession = taskDocumentRef ? undefined : currentSession('terminal');
  const freeChat = chatSession && !chatSession.taskDocumentRef ? chatSession : undefined;
  const [mountedSessionIds, setMountedSessionIds] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setMountedSessionIds((current) => {
      const sessionIds = new Set(sessions.map((session) => session.id));
      const next = new Set([...current].filter((id) => sessionIds.has(id)));
      if (chatSession) next.add(chatSession.id);
      if (terminalSession) next.add(terminalSession.id);
      if (next.size === current.size && [...next].every((id) => current.has(id))) return current;
      return next;
    });
  }, [chatSession, terminalSession, sessions]);
  return {
    sessions,
    chatSession,
    terminalSession,
    freeChat,
    mountedSessionIds,
    altitude,
    sprintSeats: altitude === 'sprint' ? seats : [],
    masterSeats: altitude === 'master' ? seats : [],
    missingSprintRoles: CREATABLE_SPRINT_ROLES.filter(
      (role) => !seats.some((session) => sessionSeatRole(session) === role),
    ),
  };
}
