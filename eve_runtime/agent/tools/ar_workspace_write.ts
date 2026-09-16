import { defineTool } from "eve/tools";
import { z } from "zod";

import { resolveWorkspacePath } from "../lib/workspace.js";

export default defineTool({
  description:
    "Write one UTF-8 text file inside the admitted Agents Remember worktree and report the " +
    "sha256 of the written bytes. Paths are relative to the workspace root and may not escape it.",
  inputSchema: z.object({
    path: z.string().min(1),
    text: z.string(),
  }),
  async execute({ path, text }) {
    const { createHash } = await import("node:crypto");
    const { mkdir, writeFile } = await import("node:fs/promises");
    const { dirname } = await import("node:path");
    const absolute = resolveWorkspacePath(path);
    await mkdir(dirname(absolute), { recursive: true });
    await writeFile(absolute, text, "utf8");
    return {
      path,
      bytes: Buffer.byteLength(text, "utf8"),
      sha256: createHash("sha256").update(text).digest("hex"),
    };
  },
});
