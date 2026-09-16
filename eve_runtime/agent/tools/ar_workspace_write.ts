import { defineTool } from "eve/tools";
import { z } from "zod";

import { admitWritePath, loadVerifiedCapsule } from "../lib/capsule.js";

export default defineTool({
  description:
    "Write one UTF-8 text file inside a surface this seat was admitted to write (the admitted " +
    "worktree, or this seat's report surface) and report the sha256 of the written bytes. A path " +
    "outside every admitted surface is refused.",
  inputSchema: z.object({
    path: z.string().min(1),
    text: z.string(),
  }),
  async execute({ path, text }) {
    const { createHash } = await import("node:crypto");
    const { mkdir, writeFile } = await import("node:fs/promises");
    const { dirname } = await import("node:path");
    // The write scope list travels with the admitted capsule, so the surfaces this tool may touch
    // are the ones AR admitted for the seat — not the ones a launch or a model asked for.
    const capsule = loadVerifiedCapsule(process.env);
    const absolute = admitWritePath(capsule, path);
    await mkdir(dirname(absolute), { recursive: true });
    await writeFile(absolute, text, "utf8");
    return {
      path,
      absolute,
      bytes: Buffer.byteLength(text, "utf8"),
      sha256: createHash("sha256").update(text).digest("hex"),
    };
  },
});
