# Cost-aware Bootstrap

Repository bootstrap can be token-heavy because the agent has to inspect broad
source areas before it can write useful onboarding. This guide is for operators
choosing the harness, model, and run shape before starting `C-03-repo-bootstrap`.

The runtime does not try to optimize spend on its own. Treat model choice as an
operator decision, then give the agent a clear bootstrap scope.

## Strategy

Use a cost-efficient, large-context coding or reasoning model for the broad,
evidence-bound parts of bootstrap, and reserve more expensive models for review
or for areas where the first pass gets stuck.

Good candidates for the first pass are models that can:

- read large source files and route overviews without aggressive truncation
- use tools reliably over many turns
- follow path and write-boundary instructions
- produce structured Markdown consistently
- run long enough to finish one wave without harness session limits

DeepSeek V4 Pro is one example of this kind of model when used through a harness
that supports its reasoning and tool-call behavior directly.

## Check Cost During And After Bootstrap

Before running every file onboarding wave, run one representative wave and check
the provider usage dashboard. Bootstrap workloads are usually input-heavy: the
agent reads much more source and onboarding context than it writes. Output
tokens matter, but repeated source/context reads usually dominate the token
count.

As a dated example, on 2026-05-17 a completed TensorFlow memory bootstrap using
DeepSeek V4 Pro through OpenClaw used 252 API requests and processed about 19.57
million total tokens: about 18.51 million input cache-hit tokens, 896.6 thousand
input cache-miss tokens, and 160.5 thousand output tokens. The exported provider
cost for that run was about $0.60, or roughly $0.0305 per 1 million blended
tokens. The low blended rate depended on the workload shape and DeepSeek
pricing/cache behavior at that time.

Always check current provider pricing before extrapolating from an example. For
DeepSeek, see the official [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
page. On 2026-05-17, DeepSeek listed V4 Pro prices per 1 million tokens and
noted a temporary discount through 2026-05-31 15:59 UTC; future prices may differ.

## Suggested Phase Budget

| Bootstrap work | Model choice | Notes |
| --- | --- | --- |
| Source inventory | fast or cost-efficient model | Mostly classification and path discovery. |
| Scout report | cost-efficient large-context model | Broad reading matters more than polished prose. |
| Root overview | cost-efficient model, then review if needed | High-leverage artifact; review if confidence is mixed. |
| Route overviews | cost-efficient model | Template-driven, evidence-bound work. |
| File onboarding waves | cost-efficient model | Repetitive work; checkpoint between waves. |
| Curator or final review | strongest practical model or human review | Use this to catch unsupported durable claims. |

## Wave Size

Large waves can waste tokens when a run times out, hits provider limits, or drifts
off task. Prefer checkpointable chunks:

- route overviews: 1-3 routes per run
- file onboarding: 5-10 files per run
- commit or otherwise checkpoint after each completed wave
- keep source reads and memory writes in separate, explicit roots

For very large repositories, start with root and route overviews before asking
for dozens of file-level onboarding artifacts. That gives later runs a better
map and reduces repeated rediscovery.

## Escalation Triggers

Switch to a stronger model or stop for human review when:

- source and documentation disagree
- a route overview would record low-confidence architecture as durable fact
- cross-repo boundaries or branch-sensitive memory are involved
- the model invents invariants without source evidence
- repeated runs fail to keep the source-read and memory-write boundaries
- the harness starts summarizing away important context or hitting session limits

## Harness Notes

Prefer a harness that lets the chosen model use the provider API directly and can
handle the repository layout you are bootstrapping. For external-memory layouts,
the harness must be able to read the source repository and write the memory
repository without flattening or copying the Agents Remember runtime in a way
that breaks script paths.

For OpenClaw, see [Install For OpenClaw](../install/openclaw.md) for the nested
skill layout, `allowSymlinkTargets`, and long-running turn timeout settings.

## Example Prompt Shape

Keep the prompt scoped to one checkpointable unit:

```text
Read the resolved workspace instructions first.

Read source files from <source-repo>.
Write onboarding files only in <memory-repo>.
Do not modify <source-repo>.

Read onboarding/bootstrap/handoff.md and
onboarding/bootstrap/governing-route-map.md.
Continue Wave 1 only.
Match the existing onboarding style.
Commit only after Wave 1 is complete.
```

This keeps cost control outside the agent's reasoning loop while still giving it
enough structure to finish useful bootstrap work.
