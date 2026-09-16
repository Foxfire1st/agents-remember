import { localDev, vercelOidc } from "eve/channels/auth";
import { eveChannel } from "eve/channels/eve";

/**
 * The AR-owned eve HTTP surface.
 *
 * Auth is authored explicitly because authoring this file at all replaces eve's default channel
 * source: leaving `auth` out would remove the route's verifier entirely rather than inherit the
 * default chain. The chain is the documented local-development one — a Vercel OIDC bearer when
 * the deployment issues one, otherwise local development access.
 */
export default eveChannel({
  auth: [vercelOidc(), localDev()],
  // Ordinary AR deliveries must never inherit eve's cancellation-backed steer default: a queued
  // follow-up waits for the active turn instead of cancelling it. The adapter still spells the
  // policy on the create call, and this channel default covers every later turn.
  turnPolicy: "queue",
});
