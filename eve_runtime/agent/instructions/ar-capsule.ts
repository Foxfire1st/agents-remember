import { defineDynamic, defineInstructions } from "eve/instructions";

import { loadVerifiedCapsule } from "../lib/capsule.js";

/**
 * Apply the compiled Agents Remember capsule as this session's trusted system instructions.
 *
 * The content is the capsule's own instruction blocks concatenated in the composition order AR's one
 * compiler produced — not re-rendered, not re-ordered, not summarized. File order in
 * `agent/instructions/` is alphabetical, so this entry applies after the static root instructions
 * and whatever precedes it, and the capsule text appears exactly once because exactly one file
 * carries it.
 *
 * System role is deliberate: eve includes system instructions on every model call and keeps them
 * outside history, so the obligations govern the first call, every later turn, and the call after a
 * resume or compaction.
 */
export default defineDynamic({
  events: {
    "session.started": (_event, ctx) => {
      if (ctx.session.auth.current === null || ctx.session.auth.current === undefined) {
        throw new Error("no authenticated AR binding on this session");
      }
      const capsule = loadVerifiedCapsule(process.env);
      return defineInstructions({ content: capsule.instructionText });
    },
  },
});
