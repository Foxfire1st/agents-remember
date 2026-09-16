import { defineDynamic, defineInstructions } from "eve/instructions";

import { loadVerifiedCapsule } from "../lib/capsule.js";

/**
 * Carry the projected task facts into the conversation as lower-authority context.
 *
 * The markdown is L3's task projection, applied verbatim: the runtime adds no heading, reorders
 * nothing and summarizes nothing. It belongs in the user role, not the system block — task facts are
 * content rather than authority, and the documented lifecycle says a user-role dynamic result is
 * appended to durable history once at its boundary and is replay-safe, so parking, resuming or
 * replaying the session does not duplicate it. Compaction may legitimately summarize this channel;
 * that is why the mandatory instructions do not live here.
 */
export default defineDynamic({
  events: {
    "session.started": (_event, ctx) => {
      if (ctx.session.auth.current === null || ctx.session.auth.current === undefined) {
        throw new Error("no authenticated AR binding on this session");
      }
      const capsule = loadVerifiedCapsule(process.env);
      if (capsule.taskContextMarkdown === "") {
        return null;
      }
      return defineInstructions({ content: capsule.taskContextMarkdown, role: "user" });
    },
  },
});
