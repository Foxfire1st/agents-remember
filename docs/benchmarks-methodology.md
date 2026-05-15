# Benchmark Methodology

Agents Remember benchmarks compare paired Codex headless runs against the same pinned repository commit:

- a source-only `no-onboarding` variant
- a `with-onboarding` variant that resolves a pinned external-memory repo from an isolated benchmark workspace

The suite is meant to show whether mature path-derived memory changes exploration efficiency and answer quality. It is not a universal model evaluation, a leaderboard, or a claim that every prompt benefits from onboarding.

## Case Design

Each case pins:

- repository URL
- commit hash
- approximate file-count band
- prompt set
- benchmark workspace path
- external memory repository URL and commit
- author-provided result reports

The source package does not commit case workspaces. It commits manifests, prompts, author results, and workspace templates. `prepare` generates the case workspace as resettable state, renders the workspace `AGENTS.md` from the template, clones one pinned checkout per target repository under `repos/`, and clones the pinned memory repository under the benchmark-local `ar-coordination/` root. Reinstalling the benchmark package can prune and refresh generated workspaces when the pinned commit, prompt, memory, template, or author results change. User-generated outputs live separately under `benchmarks/user-runs/`.

Agents Remember path rules should exclude resettable benchmark workspaces from onboarding. In particular, workspace-local cloned repos, workspace-local `ar-coordination/` trees, cloned benchmark memory snapshots, and `benchmarks/user-runs/` are benchmark state, not source files that should receive onboarding companions. This prevents benchmark memory from recursively producing more onboarding for itself.

## Task Selection

Prefer tasks with stable completion criteria:

- exploratory architecture explanations
- debugging investigations
- workflow or data-flow explanations
- bug localization without requiring a source edit

Avoid using feature-building tasks as primary benchmark evidence. A coding agent can make many valid implementation choices, which makes exact comparison less repeatable.

## Run Shape

Each prompt should run both variants. The default repetition count is three runs per prompt and variant.

The runner records:

- raw JSONL
- stderr
- process metadata
- parsed metrics
- a Markdown summary

## Metrics

The analyzer reports metrics when they are available in the JSONL stream or runner metadata:

- duration
- event count
- detected command/tool events
- input tokens
- fresh input tokens
- output tokens
- reasoning tokens
- JSONL size
- exit code
- detected errors

Token fields are parsed defensively because Codex JSONL schemas can evolve. When cumulative token fields appear more than once, the analyzer keeps the largest observed value for each field.

## Validity Checks

A useful result report should state whether the variant boundary held:

- The `no-onboarding` run should not read `ar-coordination/memory-repos/`, `ar-memory/`, or target onboarding files.
- The `with-onboarding` run should resolve the benchmark-local coordination root, not the user's normal workspace.
- Both variants should use the same pinned source commit.
- The final answer should complete the primary task, not stop after startup checks.

## Limitations

These benchmarks are evidence, not proof in the mathematical sense.

- Model behavior is stochastic.
- Codex versions, model choices, and tool behavior can change.
- Hardware, filesystem, shell, network, and cache state can affect duration.
- A mature memory snapshot reflects the author's curation choices.
- Benchmarks can become stale when a pinned repo commit or memory fixture changes.

The most useful comparison is a pattern across cases of different sizes: small repositories may not benefit, while larger or more architecturally confusing repositories may show better efficiency and fewer wrong conclusions.
