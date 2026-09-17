import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { ArCapsuleError, type ArCapsule } from "./capsule.js";

/**
 * Prove the directory this runtime executes in is the admitted AR worktree, not merely a directory
 * that exists at that path.
 *
 * The capsule carries the worktree owner's own facts — the repository, the work branch and the base
 * commit the enclosure contract recorded. This reads the git metadata of the workspace root and
 * compares: on a branch, HEAD must be the admitted branch; detached, HEAD must be the admitted base
 * commit. A linked worktree records that metadata through a `.git` file pointing at the main
 * checkout's `worktrees/<name>` directory, and a plain checkout through a `.git` directory, so both
 * shapes are read. Nothing here shells out to git: the runtime reads files it was pointed at.
 */

export interface GitHead {
  readonly kind: "branch" | "detached";
  readonly value: string;
}

export function readGitHead(workspaceRoot: string): GitHead {
  const metadata = gitDirectory(workspaceRoot);
  let head: string;
  try {
    head = readFileSync(resolve(metadata, "HEAD"), "utf8").trim();
  } catch (error) {
    throw new ArCapsuleError(
      "workspace-git-unreadable",
      `the workspace ${workspaceRoot} has git metadata at ${metadata} but no readable HEAD: ` +
        `${(error as Error).message}`,
    );
  }
  if (head.startsWith("ref:")) {
    return { kind: "branch", value: head.slice("ref:".length).trim() };
  }
  if (/^[0-9a-f]{40,64}$/.test(head)) {
    return { kind: "detached", value: head };
  }
  throw new ArCapsuleError(
    "workspace-git-unreadable",
    `the workspace ${workspaceRoot} has an unrecognized HEAD ${JSON.stringify(head)}`,
  );
}

export function verifyAdmittedWorkspace(capsule: ArCapsule): GitHead {
  const head = readGitHead(capsule.workspace.root);
  const admittedBranch = `refs/heads/${capsule.workspace.workBranch}`;
  if (head.kind === "branch") {
    if (head.value !== admittedBranch) {
      throw new ArCapsuleError(
        "workspace-branch-mismatch",
        `this runtime executes in ${capsule.workspace.root} on ${head.value}, but the admitted ` +
          `worktree is ${capsule.workspace.workBranch} (${admittedBranch})`,
      );
    }
    return head;
  }
  if (head.value !== capsule.workspace.baseCommit) {
    throw new ArCapsuleError(
      "workspace-commit-mismatch",
      `this runtime executes in ${capsule.workspace.root} detached at ${head.value}, but the ` +
        `admitted base commit is ${capsule.workspace.baseCommit}`,
    );
  }
  return head;
}

function gitDirectory(workspaceRoot: string): string {
  const dotGit = resolve(workspaceRoot, ".git");
  let text: string;
  try {
    text = readFileSync(dotGit, "utf8");
  } catch {
    try {
      readFileSync(resolve(dotGit, "HEAD"), "utf8");
      return dotGit;
    } catch (error) {
      throw new ArCapsuleError(
        "workspace-not-a-worktree",
        `${workspaceRoot} is not a git worktree: neither .git/HEAD nor a .git gitdir pointer is ` +
          `readable (${(error as Error).message})`,
      );
    }
  }
  const match = /^gitdir:\s*(.+)$/m.exec(text);
  if (match === null) {
    throw new ArCapsuleError(
      "workspace-not-a-worktree",
      `${workspaceRoot}/.git is neither a directory nor a gitdir pointer`,
    );
  }
  return resolve(workspaceRoot, match[1].trim());
}
