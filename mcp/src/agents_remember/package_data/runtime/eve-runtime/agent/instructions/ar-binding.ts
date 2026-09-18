import { defineDynamic, defineInstructions } from "eve/instructions";

import { loadVerifiedCapsule } from "../lib/capsule.js";
import { verifyAdmittedWorkspace } from "../lib/git-workspace.js";

/**
 * State the Agents Remember identity this session runs under, before the first model call.
 *
 * The identity comes from the session's authenticated context — the record eve created when the
 * channel's AR binder verified this launch's capsule — and is re-checked against the carrier on
 * disk before it is stated, so a user message cannot forge or move it. The block is a system-role
 * instruction, which eve keeps outside conversation history and includes on every model call: it
 * therefore survives turn boundaries, compaction and clear.
 *
 * A session that reaches this resolver already has a verified carrier, because the channel refuses
 * the session route otherwise. The throws below are defect signals, not the policy gate: eve logs a
 * throwing instruction resolver and leaves the wider static selection in place, which is precisely
 * why mandatory material is enforced at the route instead of here.
 */
export default defineDynamic({
  events: {
    "session.started": (_event, ctx) => {
      const auth = ctx.session.auth.current;
      if (auth === null || auth === undefined) {
        throw new Error("this session carries no authenticated Agents Remember binding");
      }
      const capsule = loadVerifiedCapsule(process.env);
      verifyAdmittedWorkspace(capsule);
      const authenticated = auth.attributes["bindingRef"];
      if (authenticated !== capsule.identity.bindingRef) {
        throw new Error(
          `the authenticated binding ${String(authenticated)} is not the carrier's ` +
            `${capsule.identity.bindingRef}`,
        );
      }
      return defineInstructions({
        content: [
          "<agents-remember-binding>",
          `role: ${capsule.identity.role}`,
          `task: ${capsule.identity.taskReference}`,
          `operation: ${capsule.identity.operation}`,
          `binding: ${capsule.identity.bindingRef}`,
          `capsule: ${capsule.carrierDigest} (semantic ${capsule.identity.semanticDigest})`,
          `workspace: ${capsule.workspace.root}`,
          `branch: ${capsule.workspace.workBranch}`,
          "</agents-remember-binding>",
        ].join("\n"),
      });
    },
  },
});
