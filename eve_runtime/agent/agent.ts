import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { defineAgent } from "eve";

/**
 * One pinned provider handle for the run's selected model.
 *
 * The base URL and credential are launch-time values the AR adapter owns; the runtime never
 * reads a credential the operator did not hand it.
 */
const provider = createOpenAICompatible({
  name: process.env.AR_EVE_PROVIDER_NAME ?? "ar-eve",
  baseURL: process.env.AR_EVE_PROVIDER_BASE_URL ?? "http://127.0.0.1:4799/v1",
  apiKey: process.env.AR_EVE_PROVIDER_API_KEY ?? "ar-eve-launch-key",
});

const modelId = process.env.AR_EVE_MODEL ?? "fixture-deterministic-1";

export default defineAgent({
  // The model is fixed for the lifetime of one AR bridge epoch: a different selection rotates the
  // runtime rather than silently switching providers inside a live session.
  model: provider(modelId),
  // An operator-supplied endpoint is normally absent from the AI Gateway catalog, so the context
  // window is declared instead of looked up.
  modelContextWindowTokens: Number(process.env.AR_EVE_CONTEXT_WINDOW_TOKENS ?? 200_000),
});
