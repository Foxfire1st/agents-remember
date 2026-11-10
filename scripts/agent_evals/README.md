# Curator eval tooling

Development tooling for MIK-R62 v8. The Architect owns measurements, the AR-tool
approver copy and discussion with the developer. The Worker supplies the runner,
graders and unchanged D637 case set. A tooling smoke is not a measurement or a
review freeze.

Run native trials with the candidate's bootstrapped interpreter. The runner
uses the product's explicit test execution mode for disposable fixture setup
and for its Paseo provisioning and stop CLI children, so their supplied
`--config` selects the sandbox's settings instead of a checkout-local root.
Actors and the fixture MCP server keep their regular entrypoints and isolated
case settings. Use a disposable `--sandbox-home`; no linked-worktree or
worktree-relative reports location is required by this runner setup.

```sh
mcp/.venv/bin/python scripts/agent_evals/run_curator.py trials \
  --case /absolute/path/to/curator-d637-86f00c47/CE14 \
  --model codex --text old --trials 1 --phase tooling-check \
  --evidence /absolute/path/to/evidence \
  --sandbox-home /absolute/path/to/disposable-sandbox
```

`--case` accepts one case folder or the fourteen-case parent directory. `--model`
accepts these four existing actor selectors or their exact model names:

| Selector | Model | Profile |
| --- | --- | --- |
| `codex` | `gpt-6.1-sol` | high, priority, auto-review |
| `hermes` | `opencode-go:deepseek-v4.1-flash` | accept_edits, auto_accept false |
| `hermes-kimi` | `opencode-go:kimi-k3.1-pro` | accept_edits, auto_accept false |
| `hermes-gpt` | `openai-codex:gpt-6.1-sol-900k` | high, accept_edits, auto_accept false |

The normal measurement uses `--trials 3 --phase measurement`. `--phase tooling-check`
records a smoke with its costs and causes separately, excluded from measurement
outcomes, comparisons and measurement repeat eligibility. `--phase final-full` selects the authorized final
full-set step after targeted work; ordinary graded resampling stays refused.
`--text old` compiles this code line's Curator text; an explicit captured JSON or
plain instruction file selects different text. Captured text uses the production
launch composer: the shared every-role block precedes the role capsule and
operation in the inline first-message instructions. Each declared detail is
written byte-exactly beside the trial's report and linked by its bound path. Old
and new arms keep their own declared detail inventories.
The marker is absent from the case bundle and every detail; each emitted link is
checked before and after the actor. Detail reads are kept-log observations, with
unseen files labelled `not seen in kept log`, never a completeness rate or grade
gate. `--code` selects the candidate checkout; `--timeout` bounds native waits
and defaults to 3600 seconds.

Trials end at the first actor turn end, as production does. Hermes ACP currently
has neither an async helper-completion consumer nor a process-local roster read.
Per-trial helper receipts record launched IDs and that delivery mechanism; an
undelivered result is not an actor fault. Re-entry proof is held. Only an actor
deadline with an unresolved helper gets the separate ungraded state
`helper-liveness-unobservable-at-deadline`; it is neither running nor an unknown
verdict and is excluded from right/wrong arithmetic. No durable row, imported
registry, dispatch receipt or provider upsert establishes process-local liveness.

For Hermes, the runner derives the prepared runtime from the same ambient
launcher and install that `hermes acp` names. It records the launcher, install
version, prepared dependency interpreter and source identities per trial. The
trial's provider data remains in its fresh `HERMES_HOME`; developer skills,
memory and sessions are not copied. The isolated model default comes from the
admitted trial profile. A missing or changed runtime fails before actor creation;
there is no second ambient-startup route.

Each trial declares its read-only provider source in `credential-borrowing.json`;
selected provider configuration and dotenv credentials seed the private home.
Non-GPT Hermes trials compare the source home's `auth.json` before provisioning and
after fixture cleanup, keeping both byte sequences only in process memory. The attempt's
`authContentComparison` contains only `equal` or `differs`; no credential copy,
excerpt or digest is produced by this observation. Equal bytes establish unchanged
auth content regardless of mtime. Different bytes require writer attribution;
the comparison identifies no writer and grants no clean-run verdict. A missing or
unreadable comparison records a named `binding-unavailable` infrastructure unknown.
The GPT arm instead selects the existing `providers["openai-codex"]` and
`credential_pool["openai-codex"]` entries, canonicalizes them with sorted keys, and
compares their presence and content. Equality there ignores serialization and attests
only that these selected entries are unchanged, not other providers or top-level metadata.

`--actor-only` is admitted only for a Hermes `--phase tooling-check`. It runs the
same fixture, host and native actor path, then records a finished turn, its exact
native session model and positive API count in `actor-only-check.json`. A complete
check has status `actor_only_complete`; it produces no case verdict, deterministic
grade or judge invocation, and remains outside measurement arithmetic.

The command provisions one host at the supplied batch sandbox home, then each
trial's fresh case copy, Git baselines and project-bound candidate tool server.
It prints that home and waits for the Architect's approver readiness before
starting the host and actor. `--approver-ready` means that approver is already
attached; it
never answers a permission prompt. AR-tool prompts for both actors use the
Architect's same deny list. Other or unanswered prompts become unknown with
their cause. Only the selected provider credentials/settings enter the fresh Hermes home;
developer skills, memory and sessions are excluded.

Native checking uses the supported external-memory layout at
`coordination/memory-repos/ar-code`. The case-facing `projects/memory` path
points to that single material tree. Effect capture verifies this exact binding
and records the real file hashes under the case paths; other links are refused.

Every trial retains a complete hash-verified case-folder copy outside the actor
project. Only declared actor inputs enter that project; expected outcomes and
baseline notes stay grader-only. Each trial also provisions `reports/scratch` inside its
project and binds that exact writable path in the prompt for temporary scripts,
probes and hand-off files. The first message names the native host seat created
for the trial. Host-global scratch paths are outside that binding; the native
permission policy is unchanged. Each trial has its own settings and server binding,
prompt, native capture, before/after effects, host counts and cleanup. The batch
host stops after its last trial; all trials use the supplied sandbox home.
Calls to a real installed server are recorded and do not fail a trial by
themselves. Binding probes still check the actual trial project and candidate
server. Codex code-mode dispatches use the native completed-item records inside
the paired cell interval: MCP names, arguments and results, command argv and
results, and file changes enter the same event stream read by the graders.
Capture also recognizes bounded direct dispatches whose literal arguments and
returned native receipts match exactly. It never executes the cell to recover
calls. Unsupported shapes remain unknown with their kind, keys and source
location. Retained native-record regressions bind the original source hashes.

Hermes live and retained captures normalize the observed single-child
`tool_call` / `arguments.calls[]` wrapper into the same action list. The native
paired result stays byte-identical, including refusals and planned-before-refusal
counts. Unrecognized wrappers remain unknown with their shape, source location
and hash; normalization establishes no semantic verdict.

The command runs code graders and, where needed, a native cross-family
judge. Codex and Hermes GPT outcomes go to Opus (`claude-opus-5-5`); Hermes
DeepSeek and Kimi outcomes go to Codex (`gpt-6.1-sol`). Routing follows the exact
actor profile, not just its harness. Selected evidence omits old/new labels and
credentials. Judges have ordinary native tools in the evidence workspace, shell
included, with no AR server; Claude memory is disabled and native authentication
is preserved.

Judge evidence starts with the effect-tree delta and full selected artifacts,
including every changed card, sidecar and history file before and after. The
outstanding-memory-actions and full memory-quality reports are excluded from
that diff and listed by hash and size. When a criterion depends on either
report, its changed entries are supplied before and after; the manifest records
the case and criterion using that exception. The original full reports remain
in the retained evidence.

Case-specific mappings bind exact rubric versions to their evidence duties.
Calls without a positive path match still remain when negative criteria need
their actual arguments, such as prohibitions on product edits or Git actions.
Criterion-referenced actor decisions and later user replies also remain; the
case's declared task, authority and queue inputs supply their governing context.
Selected calls and results retain IDs, names and arguments, with hash-bound
excerpts for bulk values. The selection manifest accounts for both included
and omitted native observations against the full stream. Original requests,
full effect diffs and artifacts remain evidence. The pre-send guard reports bytes,
estimated tokens and largest contributors. The roughly 100,000-token cost target
is advisory; required evidence remains intact when fidelity conflicts with it.
A separate measured input ceiling blocks oversized requests before any native
judge call: 1,692,965 UTF-8 bytes, derived from the retained CE02 rejection with
20% margin. Codex requests also have a ceiling of 838,860 characters. Rejection
records `mandatory-evidence-over-budget` when the manifest carries
`mandatoryClasses`, and `input-package-over-budget` otherwise, along with the
request hash and contributors. It neither trims evidence nor counts as a judge
invocation.
Estimates are not provider token counts or a guarantee of admission. Each native
result records actual usage. A large required diff remains visible as case size.

Verdicts admit a raw JSON object or the unique schema-valid last complete `json`
code fence in the final assistant message. Surrounding prose is allowed and
retained in parse provenance. Multiple valid fenced candidates or an invalid last
complete fence are refused. Model, criterion order, outcomes and evidence checks
still apply.

The command prints the resulting `attempts.json`. That ledger and the per-trial
evidence folders retain actor/judge token usage separately, actual native
records, code grades, judge requests/verdicts, changed files and unknown causes.
Unknown usage stays unknown. A per-trial Paseo bridge failure, including an
actor-creation timeout, records a `capture-absent` infrastructure unknown with its exact cause;
an uncertain agent creation keeps its fixture until the supported batch host stop
succeeds; remaining trials continue and the command returns normally. This does not
turn a failed attempt into a successful result. Re-running the command with the
same evidence folder resumes missing slots or replaces an unknown attempt by
identity; it never resamples a graded slot.

`capture-text`, `inspect-cases`, `summarize` and `compare` are also available:

```sh
mcp/.venv/bin/python scripts/agent_evals/run_curator.py summarize \
  --attempts /absolute/path/to/evidence/attempts.json \
  --model gpt-6.1-sol --text-signature TEXT_SIGNATURE --batch-id BATCH_ID \
  --output /absolute/path/to/report.json
```

The ledger records the text signature and batch ID used by `summarize` and
`compare`. Reports preserve fourteen slots, seven fixed pairs per model, both
all-three-right and at-least-two-of-three counts, and unknown causes. Below two
of three is a case failure. A theme worsens when fewer of its two cases reach
two of three than in the old run; only worsening themes reopen the loop after
the final full run. Infrastructure-spoiled trials require the Architect to fix
the cause and replace the unknown trial. These reports grant no acceptance.

Each private `CE01` through `CE14` has an `ar-curator-eval-case/v1` manifest.
D637 labels it `cut from the record (D637)`, names its episode and starting code
and memory trees, and binds files by SHA-256. `setup` names prompt, authority,
queue, reset, expected outcome and baseline notes. `actorInputs` selects source
files and fixture destinations; `effectRoots` selects captured state;
`allowedEffects` states the narrower write authority. No historical runtime
replay or original-build matching is required. The confirmed right-outcomes
list supplies expectations; historical authority findings remain baseline notes.

`runner.run_trial` remains the lower-level native execution API. Its hooks
prepare, execute, capture, count host agents and clean up. The concrete command
uses `trials.run_trials` and `trial_setup.provision_trial`; callers need not
write hooks. Graders inspect actual records, history, paths, actions and native
model identity. Actor success prose earns no credit. A deterministic failure
cannot be overridden by a rubric judge, and absent evidence remains unknown.

Focused verification is in `mcp/tests/test_agent_eval_*.py`: reset isolation,
provisioning, native adapters, grading, accounting and command orchestration.
These tests use native subprocess/bridge doubles and do not count as model
measurements. The Worker hands the Manager the settled candidate and case set;
the Architect's go must return through the Manager before role or operation
texts change.


`census` and `regrade` operate on retained Codex or Hermes attempts; they never rerun an
actor. The original attempt's actor model determines the same profile-based judge as
live trials: Codex and Hermes GPT use `claude-opus-5-5`; Hermes DeepSeek and Kimi
use `gpt-6.1-sol`. A census batch must
name one actor family; the bound executable must match its judge route. Freeze a batch after its producer stops. Use absolute paths so the same
commands work from any working directory:

```sh
/absolute/code/mcp/.venv/bin/python /absolute/code/scripts/agent_evals/run_curator.py census \
  --batch /absolute/old-text/codex --evidence /absolute/new-census \
  --judge-executable /absolute/native/claude
/absolute/code/mcp/.venv/bin/python /absolute/code/scripts/agent_evals/run_curator.py regrade \
  --manifest /absolute/new-census/manifest.json --dry-run
/absolute/code/mcp/.venv/bin/python /absolute/code/scripts/agent_evals/run_curator.py regrade \
  --manifest /absolute/new-census/manifest.json --execute
```

Census is free of inference: it snapshots unknown attempts and every attempt
whose producing grader or shared source differs from the current checkout.
Missing producer metadata is stale by default, including previously right or
wrong verdicts. It retains original causes and hashes, replays retained native
records, and prepares packages with the current graders. Repeated `--attempt ID` arguments select a bounded proof, including previously
completed attempts whose grade needs correction; otherwise
all unknown or stale attempts in that census are selected. Later arrivals are not
added during execution. The manifest binds the absolute judge executable and
its hash. Native authentication remains the caller's; credentials are not copied.
Dry-run reports package bytes, projected tokens, existing modes and verdicts,
and fresh versus previously tooling-checked costs from that same manifest.
`wouldRunByMode` identifies missing mode-specific records; `wouldJudgeByMode`
identifies which of those need inference. A deterministic failure still needs
its measurement record but projects zero judge tokens. Dry-run output names
`judgeInvocationsThisRun` separately from each mode's `projectedJudgeInvocations`,
with deterministic results, unresolved results and stalled records counted apart.

Execution appends output under each original attempt's `regrades/` directory,
leaving its original unknown and files intact. Add `--tooling-check` for a tooling
proof: its records say `actor: false`, `measurement: false`. Ordinary re-grades
are measurement records and still say `actor: false`. Deduplication uses original
attempt, result identity and mode; an unchanged tooling check is skipped in tooling
mode but does not replace a measurement. Each skip names its mode. A pending
older invocation is stalled and returns nonzero without another judge call.
The command also returns nonzero if any selected attempt remains unknown;
`right` and `wrong` are both completed judgments.

Packages use changed hunks with three context lines. Large unreferenced hunks
carry excerpts, hashes and sizes; every hunk of a rubric-referenced file remains
whole. Native history ledgers supply complete changed rows and header deltas,
with before/after source hashes and row-order identities. An exact unchanged
scratch copy binds its original bytes rather than inventing newly added rows.
Repeated artifact images may reference identical supplied content; final changed
cards and property carriers remain whole. The common pre-send ceiling applies
to frozen requests too; a local rejection records zero invocations. Native exec
capture names each supported wire shape; unsupported
shapes retain their kind, keys, source path and location rather than a generic
opaque-dispatch label.

Git admission compares retained repository semantics: the HEAD commit and branch,
refs including stash, cached-diff paths and indexed mode/blob/stage entries.
Index stat-cache refreshes, unreachable objects, reflogs and equivalent ref
storage do not establish an actor effect. Real staging, commit, branch and stash
changes remain effects; incomplete metadata is unknown. Ordinary working-tree
writes and unadmitted configuration or hook changes remain subject to the case.
Raw Git files remain hash-bound source evidence, but are omitted from textual
effect diffs and judged through those semantic checks. Other effect files are
read as complete byte records before text handling: NUL-containing or invalid
UTF-8 records are binary, retain their hashes and sizes, and emit no text patch.

A corrected Git-byte admission verdict appends a `superseded-false-fail`
supplement with the old verdict and causing paths, and a reference to the new
deterministic grade. It never rewrites the original attempt or establishes a
semantic pass. The census includes these changed grades as well as unknowns;
current valid findings remain findings after the instrument fault is removed.

Every new grade names its grader ID, producing source path and SHA-256, and
each source in its transitive local Python import closure by path and hash.
Census reports the missing or mismatching source paths. A judged result derived
from stale deterministic inputs cannot be reused: census prepares a corrected
package and execution appends a new result. The execution identity includes
that preparation source snapshot even when the resulting package bytes match.

Result identities retain their digest components. Deterministic identities bind
the grader ID and source/runtime snapshot, normalized capture, before/after
effect inventories and admission configuration. Judged identities bind the
exact package SHA-256 and intended judge model. Changing any of these inputs
produces a different identity. Null or absent identity fields cannot authorize
a write or skip; legacy records remain visible as invalid identities and need
a new census before corrected execution. Original void verdicts are preserved.

History obligations use the product meaning comparison, with separate checks
for revision, status, existence, entry sets and captured rows. Admission-only
metadata does not create a generic missing-row failure. A hash-bound expected outcome with explicit subject, revision and disposition
requires that row even for an admission-only change. A case-specific rubric
may also impose a history requirement and is judged on its stated authority.
Withdrawn duties are retained as `superseded-instrument-duty` evidence; they
are not silently relabelled as actor success.

Dry-run token estimates use the approved linear fit: 26,394 fixed tokens plus
0.4782 tokens per prompt byte, rounded up. The estimate names its coefficients
and authority. Native actuals remain separately recorded for refitting; the
projection never replaces usage or trims evidence.

The selected-provider configuration/dotenv seed is accepted; the source auth
store is never copied. Normal fixture `rmtree` includes its private Hermes home.
If agent creation is uncertain, the runner removes `.env`, `auth.json` and
`auth.lock` immediately before deferring the fixture until supported host stop.
Batch start and end sweep only `trials/*/hermes` for those same three files,
including when stop fails; receipts contain paths only. Credential-named files
are refused before any effect-evidence reading, hashing or copy. No live home
outside this explicitly given batch is swept.

## Preflight before actor execution

Run `run_curator.py preflight --cases /absolute/cases/curator --output /absolute/evidence/preflight.json` to validate all fourteen actor input trees without an actor, host or model call. The report names each blocking rule, path, field and resolved target, retains report-only findings, and exits nonzero when any case is invalid. `trials` runs the same preflight before reading the measured text, prompting for approver readiness or provisioning a host, and validates the actual native case copy again before starting its host or actor.

The CLI above checks tree validity only. The trial also runs a full configured
native quality intake on its actual copied repositories before starting the host
or actor. A native scope refusal or missing required worklist subject records
`binding-unavailable`, even when tree validation passed. Full quality findings
remain recorded separately: accepting the scope is not a quality PASS.

Explicit native leaf cases use canonical enclosure publication and their declared
B/C blob bindings. CE11's governing route is `onboarding/overview.md`, subject
`onboarding:overview`; native intake must earn covering worklist entries before
any writer request. Its physical sibling repositories are captured through the
same logical `projects/code` and `projects/memory` paths. Other links are refused.

Preflight reads hash-bound `actorInputs`, not desired results. It models the existing native layout-marker provision and writer-owned dependent lookup in memory, then calls the product's whole-tree validator with the initial tree as base and the writer's exact blocking/report-only split. It changes no fixture files. A writer refusal caused by initial fixture data is infrastructure evidence, not an actor omission; retained verdict corrections remain append-only.

A queue's explicit `currentSourceAbsent: true` resolves that source as intentionally absent. Its missing-Markdown findings are named, retained with unchanged file hashes, and admitted as pre-existing eval evidence. `admissionStatus` describes that eval decision; `nativeWriterRefusesStartingTree` records the unchanged product gate. A starting refusal does not establish an unreachable outcome: CE11 explicitly grants base-pair restoration before writer authoring, and that restoration clears its 27 starting blockers. Its existing expected-outcome rubric still requires the restoration. An actor that skips available authorized restoration and leaves the expected work undone is graded on that conduct, rather than being excused by the initial findings. Other absent current subjects remain binding-unavailable. No base code or memory Markdown is silently substituted.

Grading validates captured before and after trees. It fails newly introduced blocking findings, including findings on unchanged referrers, and remaining findings on files the actor wrote. Unchanged inherited findings stay in `validation-grade.json` with before/after file hashes. Actual byte changes and completed native FileChange records identify written paths. Missing initial case inputs remain unknown rather than becoming invented changes. The product writer and commit/landing validators keep their absolute whole-tree guarantees; this comparison changes only eval attribution.
