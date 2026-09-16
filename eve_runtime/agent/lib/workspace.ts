import { isAbsolute, relative, resolve, sep } from "node:path";

/**
 * Resolve one tool path inside the admitted Agents Remember worktree.
 *
 * The workspace root is a launch-time value the AR adapter owns. A path that is absolute, or that
 * resolves outside that root, is refused: the runtime's tools never operate on a surface the
 * operator did not admit.
 */
export function resolveWorkspacePath(path: string): string {
  const root = process.env.AR_WORKSPACE_ROOT;
  if (!root) {
    throw new Error(
      "AR_WORKSPACE_ROOT is not set: this runtime has no admitted workspace to operate on.",
    );
  }
  const rootPath = resolve(root);
  const candidate = isAbsolute(path) ? resolve(path) : resolve(rootPath, path);
  const relocated = relative(rootPath, candidate);
  if (relocated === "" || relocated.startsWith("..") || isAbsolute(relocated)) {
    throw new Error(`path ${JSON.stringify(path)} is outside the admitted workspace root.`);
  }
  return candidate.split(sep).join(sep);
}
