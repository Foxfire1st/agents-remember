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

/**
 * The AR sentinel for "no explicit reasoning": the selection names it, and the runtime answers by
 * declaring no `reasoning` at all rather than by forwarding the token. Its one definition lives in
 * the adapter (`serving/eve_runtime_launch.py`), which is also where a configured value is validated
 * against the levels this runtime accepts -- so a value that reaches this module has already been
 * refused-or-accepted by the launch, and nothing here is silently dropped or coerced.
 */
const PROVIDER_DEFAULT_EFFORT = "provider-default";

/**
 * The reasoning control this application actually applies, as the AI SDK's own call setting.
 *
 * `AgentReasoningDefinition` is `NonNullable<CallSettings["reasoning"]>`, and this module holds that
 * value -- no AR-side vocabulary, no second menu: the accepted set is exactly what `defineAgent`
 * accepts, and the adapter's `REASONING_EFFORTS` is the one declaration naming those levels for the
 * launch gate. Returning `undefined` is the "omit the key" case.
 */
const reasoning = process.env.AR_EVE_EFFORT;

export default defineAgent({
  // The model is fixed for the lifetime of one AR bridge epoch: a different selection rotates the
  // runtime rather than silently switching providers inside a live session.
  model: provider(modelId),
  // An operator-supplied endpoint is normally absent from the AI Gateway catalog, so the context
  // window is declared instead of looked up.
  modelContextWindowTokens: Number(process.env.AR_EVE_CONTEXT_WINDOW_TOKENS ?? 200_000),
  // The effort selection is a per-launch-epoch value the runtime compiles: eve forwards this to the
  // model call, which is why the capability catalog may advertise the axis as launch-settable. The
  // spread omits the property entirely for the sentinel (and for an absent selection), so the
  // provider receives no reasoning key rather than a literal token.
  ...(reasoning === undefined || reasoning === PROVIDER_DEFAULT_EFFORT ? {} : { reasoning }),
});
