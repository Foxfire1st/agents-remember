import { defineRpc } from "@getpaseo/plugin";
import { z } from "zod";

// The embed list in effect, asked by the client part: it is the client's only source for which
// parent pages may frame and steer the app (PNT-R05). It names origins and URLs, never a secret.
export const arEmbedList = defineRpc({
  name: "ar.embed-list",
  input: z.object({}),
  output: z.object({
    embed: z.array(z.object({ dashboardOrigin: z.string(), frameBaseUrl: z.string() })),
  }),
});

// One explicit full read per dashboard start or plugin boot, never a host poll.
// L184-R1-F08: the answer names the reconciliation this boot began, so the list
// that answers it carries its own origin back and never that of a later boot.
export const arHostActivitySnapshot = defineRpc({
  name: "ar.host-activity-snapshot",
  input: z.object({}),
  output: z.object({
    bootId: z.string(),
    reconcile: z.number().int(),
  }),
});

// MIK-R190 rule 3's second step: the bridge lists the running subagents of the
// agents the host holds with an open session, inside the same process that just
// asked for the snapshot, and hands them over here. Only the bridge can ask
// `listProviderSubagents`; the plugin's own API does not expose it.
export const arHostActivitySubagents = defineRpc({
  name: "ar.host-activity-subagents",
  input: z.object({
    /**
     * L184-R1-F08: the reconciliation this list answers, as the snapshot RPC
     * returned it. A bridge that names it binds the reply to its own boot; one
     * that does not is bound to the one reconciliation still unanswered.
     */
    reconcile: z.number().int().optional(),
    lists: z.array(
      z.object({
        agentId: z.string(),
        subagents: z.array(
          z.object({ id: z.string(), createdAt: z.string(), status: z.string() }),
        ),
      }),
    ),
  }),
  output: z.object({ changed: z.boolean() }),
});
