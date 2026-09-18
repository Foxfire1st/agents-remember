import { type AuthFn, localDev, UnauthenticatedError, vercelOidc } from "eve/channels/auth";
import { eveChannel } from "eve/channels/eve";

import { ArCapsuleError, loadVerifiedCapsule } from "../lib/capsule.js";
import { verifyAdmittedWorkspace } from "../lib/git-workspace.js";

/**
 * The AR-owned eve HTTP surface.
 *
 * The first entry of the auth walk is the Agents Remember binder: it verifies this launch's capsule
 * carrier and the admitted worktree behind it, and accepts the request as the seat the carrier names.
 * Auth runs at the channel layer, before any model work, and it guards every session route — create,
 * follow-up, cancel, compact, clear, reset and the stream. An unbound launch, a carrier whose bytes
 * do not match the declared digest, a carrier written for another binding, or a workspace that is not
 * the admitted branch therefore ends the walk with eve's own 401 and no model call happens at all.
 *
 * That gate is load-bearing rather than decorative: eve's documented behaviour is that a throwing or
 * empty *capability resolver* is logged and skipped, leaving any wider valid selection in place. The
 * mandatory capsule must not depend on a resolver that can be skipped, so admission happens here.
 *
 * The remaining entries are the ordinary principal chain, kept in their documented order: vercelOidc
 * first so a deployment bearer resolves a real user, localDev last as the local-development
 * fallback. They are only reached by a runtime whose launch declares no AR binding, and eve refuses
 * that launch here before they can accept it.
 */
export function arCapsuleAuth(): AuthFn<Request> {
  return () => {
    let capsule;
    try {
      capsule = loadVerifiedCapsule(process.env);
      verifyAdmittedWorkspace(capsule);
    } catch (error) {
      const detail = error instanceof ArCapsuleError ? `${error.code}: ${error.message}` : String(error);
      throw new UnauthenticatedError({
        code: "ar_binding_required",
        message: `this eve runtime has no usable Agents Remember binding (${detail})`,
      });
    }
    return {
      attributes: {
        bindingRef: capsule.identity.bindingRef,
        role: capsule.identity.role,
        taskReference: capsule.identity.taskReference,
        operation: capsule.identity.operation,
        capsuleDigest: capsule.carrierDigest,
        semanticDigest: capsule.identity.semanticDigest,
        workspaceRoot: capsule.workspace.root,
        workBranch: capsule.workspace.workBranch,
        grantedTools: [...capsule.grantedTools],
      },
      authenticator: "ar-capsule",
      principalId: capsule.identity.bindingRef,
      principalType: "user",
    };
  };
}

export default eveChannel({
  auth: [arCapsuleAuth(), vercelOidc(), localDev()],
  // Ordinary AR deliveries must never inherit eve's cancellation-backed steer default: a queued
  // follow-up waits for the active turn instead of cancelling it. The adapter still spells the
  // policy on the create call, and this channel default covers every later turn.
  turnPolicy: "queue",
});
