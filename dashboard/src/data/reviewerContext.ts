import type { OpenSession } from './sessions';
import type { TaskDocumentRef } from '../types/terminalCatalog';

export type ReviewerAltitude = 'sprint' | 'master' | 'leaf';

function sameRef(left: TaskDocumentRef | undefined, right: TaskDocumentRef): boolean {
  return Boolean(left && left.repository === right.repository && left.path === right.path);
}

/** Validate the generation-bound owner of one polymorphic reviewer seat. */
export function reviewerParentMatches(
  session: OpenSession,
  altitude: ReviewerAltitude,
  document: TaskDocumentRef,
  owningMaster?: TaskDocumentRef,
): boolean {
  const parent = session.structuralParentTaskDocumentRef;
  const role = session.structuralParentRole;
  if (altitude === 'leaf')
    return Boolean(owningMaster && role === 'manager' && sameRef(parent, owningMaster));
  if (altitude === 'master') return role === 'manager' && sameRef(parent, document);
  return (role === 'architect' || role === 'orchestrator') && sameRef(parent, document);
}

/** Human label that preserves which sprint review plane owns this generation. */
export function reviewerContextLabel(session: OpenSession): string {
  if (session.structuralParentRole === 'architect') return 'plan reviewer';
  if (session.structuralParentRole === 'orchestrator') return 'super reviewer';
  if (
    session.structuralParentRole === 'manager' &&
    session.taskDocumentRef &&
    sameRef(session.structuralParentTaskDocumentRef, session.taskDocumentRef)
  )
    return 'master reviewer';
  return 'leaf reviewer';
}
