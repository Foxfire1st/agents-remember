import { defineDynamic, defineInstructions } from "eve/instructions";

/**
 * Apply the Agents Remember binding before the first model call of every session.
 *
 * The binding is a launch-time, adapter-owned value, never caller prose: the runtime echoes the
 * reference and capsule digest the operator handed it, and contributes nothing when the launch
 * carried no binding. The dynamic resolver runs at `session.started`, so the block is part of the
 * system context of the session's first model call.
 */
export default defineDynamic({
  events: {
    "session.started": () => {
      const bindingRef = process.env.AR_BINDING_REF;
      if (!bindingRef) {
        return null;
      }
      const capsuleDigest = process.env.AR_CAPSULE_DIGEST ?? "<unbound>";
      const workspaceRoot = process.env.AR_WORKSPACE_ROOT ?? "<unbound>";
      return defineInstructions({
        content: [
          "<agents-remember-binding>",
          `binding: ${bindingRef}`,
          `capsule: ${capsuleDigest}`,
          `workspace: ${workspaceRoot}`,
          "</agents-remember-binding>",
        ].join("\n"),
      });
    },
  },
});
