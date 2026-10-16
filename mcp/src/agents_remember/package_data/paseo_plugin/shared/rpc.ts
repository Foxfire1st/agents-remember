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
export const arHostActivitySnapshot = defineRpc({
  name: "ar.host-activity-snapshot",
  input: z.object({}),
  output: z.object({ bootId: z.string() }),
});
