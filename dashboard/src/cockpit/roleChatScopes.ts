import { useMemo } from "react";

import {
  roleOptionsScope,
  type RoleDocumentScope,
  type RoleLaunchSelection,
  type RoleOptionsScope,
} from "./roleLaunchModel";

// Document and metadata reads share the same address. Rebuild it only when the
// role or document references change, including across replacements of the
// selection object by a document update.
export function useRoleChatScopes(selection: RoleLaunchSelection): {
  documentScope: RoleDocumentScope;
  optionsScope: RoleOptionsScope;
} {
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef } = selection;
  const sprintRepository = sprintDocumentRef?.repository;
  const sprintPath = sprintDocumentRef?.path;
  const masterRepository = masterDocumentRef?.repository;
  const masterPath = masterDocumentRef?.path;
  const taskRepository = taskDocumentRef?.repository;
  const taskPath = taskDocumentRef?.path;
  const documentScope = useMemo(
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
      }),
    [role, sprintRepository, sprintPath, masterRepository, masterPath, taskRepository, taskPath],
  );
  return { documentScope, optionsScope: documentScope };
}
