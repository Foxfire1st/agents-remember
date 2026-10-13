import { useMemo } from "react";

import {
  roleDocumentScope,
  roleOptionsScope,
  type RoleDocumentScope,
  type RoleLaunchSelection,
  type RoleOptionsScope,
} from "./roleLaunchModel";

// The pane's two scopes from its selection. The memos key on the scope's fields, not
// on the selection identity: the factory rebuilds plain document refs from the
// repository/path strings, so a new selection object with the same role, document
// refs and agent id yields memo-identical scopes (a feature-values-only change, for
// example, never changes what the options route is asked for), and the memos — and
// the options load keyed on them — run exactly when the scope content changes, never
// per render. Destructured first so the hook rule sees the true dependencies.
export function useRoleChatScopes(selection: RoleLaunchSelection): {
  documentScope: RoleDocumentScope;
  optionsScope: RoleOptionsScope;
} {
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, agentOverride } = selection;
  const sprintRepository = sprintDocumentRef?.repository;
  const sprintPath = sprintDocumentRef?.path;
  const masterRepository = masterDocumentRef?.repository;
  const masterPath = masterDocumentRef?.path;
  const taskRepository = taskDocumentRef?.repository;
  const taskPath = taskDocumentRef?.path;
  const agentId = agentOverride?.agentId;
  const documentScope = useMemo(
    () =>
      roleDocumentScope({
        role,
        ...(sprintRepository !== undefined && sprintPath !== undefined
          ? { sprintDocumentRef: { repository: sprintRepository, path: sprintPath } }
          : {}),
        ...(masterRepository !== undefined && masterPath !== undefined
          ? { masterDocumentRef: { repository: masterRepository, path: masterPath } }
          : {}),
        ...(taskRepository !== undefined && taskPath !== undefined
          ? { taskDocumentRef: { repository: taskRepository, path: taskPath } }
          : {}),
      }),
    [role, sprintRepository, sprintPath, masterRepository, masterPath, taskRepository, taskPath],
  );
  const optionsScope = useMemo(
    () =>
      roleOptionsScope({
        role,
        ...(sprintRepository !== undefined && sprintPath !== undefined
          ? { sprintDocumentRef: { repository: sprintRepository, path: sprintPath } }
          : {}),
        ...(masterRepository !== undefined && masterPath !== undefined
          ? { masterDocumentRef: { repository: masterRepository, path: masterPath } }
          : {}),
        ...(taskRepository !== undefined && taskPath !== undefined
          ? { taskDocumentRef: { repository: taskRepository, path: taskPath } }
          : {}),
        ...(agentId ? { agentOverride: { agentId } } : {}),
      }),
    [role, sprintRepository, sprintPath, masterRepository, masterPath, taskRepository, taskPath, agentId],
  );
  return { documentScope, optionsScope };
}
