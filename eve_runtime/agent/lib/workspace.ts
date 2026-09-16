import { admitWorkspaceRead, loadVerifiedCapsule } from "./capsule.js";

/**
 * Resolve one tool path inside the admitted Agents Remember worktree.
 *
 * Kept as the one read-path entry point the tools share. The admitted capsule decides the root —
 * the workspace the enclosure contract admitted, verified against the carrier's own digest — so a
 * path that is absolute, or that resolves outside that root, is refused rather than clamped.
 */
export function resolveWorkspacePath(path: string): string {
  return admitWorkspaceRead(loadVerifiedCapsule(process.env), path);
}
