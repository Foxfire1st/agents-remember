import { defineTool } from "eve/tools";
import { z } from "zod";

import { resolveWorkspacePath } from "../lib/workspace.js";

export default defineTool({
  description:
    "Read one UTF-8 text file inside the admitted Agents Remember worktree. Paths are relative " +
    "to the workspace root and may not escape it.",
  inputSchema: z.object({ path: z.string().min(1) }),
  async execute({ path }) {
    const { readFile } = await import("node:fs/promises");
    // The admitted capsule is the authority for where this tool may look: without a verified
    // binding there is no admitted workspace, so the tool refuses instead of reading something a
    // launch happened to point at.
    const absolute = resolveWorkspacePath(path);
    const text = await readFile(absolute, "utf8");
    return { path, bytes: Buffer.byteLength(text, "utf8"), text };
  },
});
