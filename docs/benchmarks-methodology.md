# Benchmark Methodology

The benchmarks of Agents Remember compare headless runs of the Codex command-line program on one
pinned commit of a repository: each run is one call of `codex exec` that gets its prompt on
standard input and asks for no approval ([Run Shape](#run-shape)). Each case runs the same task
in two environments:

- `no-onboarding`: a source-only environment without memory, in the folder `source-only/`;
- `with-onboarding`: an environment that resolves a pinned memory repository from a coordination
  root of its own, in the folder `with-memory/`.

A case may add a variant `with-onboarding-warm`. It runs in the same memory-enabled environment,
`with-memory/`. Its prompt tells the agent that the benchmark memory matches the pinned source, so
the run shows the use of memory without the cost of the first checks.

The benchmarks show whether memory changes how much an agent explores and how good its answer is.
They are no general evaluation of a model and no ranking. They do not claim that every prompt
gains from memory.

## The Benchmark Runner

The benchmark tooling is one Python module of the package, the benchmark runner. The tool server
registers no benchmark tool, and `agents-remember` has no sub-command for benchmarks. Run the
module with the Python interpreter of the environment that holds the package. For the package
installed with `uv tool install`, that interpreter is
`"$(uv tool dir)/agents-remember-mcp/bin/python"`; in a development checkout it is
`mcp/.venv/bin/python`. The lines below write it as `python`:

```bash
python -m agents_remember.benchmarks.runner list
python -m agents_remember.benchmarks.runner prepare case <case-id>
python -m agents_remember.benchmarks.runner run case <case-id>
python -m agents_remember.benchmarks.runner analyze <run-root> --write-summary
```

| Sub-command | What it does |
| --- | --- |
| `list` | Prints each case with its status, its size band and its pinned repository commit. |
| `prepare all`, `prepare case <case-id>` | Creates the workspaces of the cases. Options: `--force-clone`, `--dry-run`, `--provider-timeout <seconds>`: the time allowed for each install or index command of a search provider that a variant names (default 1800). |
| `run all`, `run case <case-id>` | Prepares the workspaces and runs the prompts. Options: `--prompt`, `--variant`, `--repetitions`, `--jobs`, `--skip-prepare`, `--force-clone`, `--dry-run`, `--codex-sandbox`, and `--provider-timeout <seconds>` as for `prepare`. |
| `analyze <run-root>` | Reads the results of one run and prints a summary. With `--write-summary` it writes the summary to `<run-root>/summary.md`. |

The option `--benchmarks-root <folder>` stands before the sub-command and names the benchmark
root: the folder that holds `cases/` and `templates/`. The runner writes the workspaces and the
results under that root. Without the option, the runner uses the benchmark root inside the
installed package.

Advice: copy the benchmark material into the coordination root and name that copy as the
benchmark root. The tool `runtime_install` makes the copy when it is called with
`include_benchmarks=true`:

```text
runtime_install(include_benchmarks=true)
```

The copy lies in `<coordination-root>/benchmarks/`. A later call renews the cases and templates,
removes the generated folder `<coordination-root>/benchmarks/workspaces/` and keeps
`<coordination-root>/benchmarks/user-runs/`.

The runner needs three things on the machine: the program `codex` on the `PATH`, which the npm
package `@openai/codex` installs, Git, and network access to the repositories that a case pins.

## Case Design

A case is a folder `cases/<case-id>/` of the benchmark root. Its manifest `case.json` pins:

- the URL and the commit of the repository;
- the number of tracked files and a size band;
- the prompts, and for each prompt its variants and its number of runs;
- the paths of the workspace;
- the URL and the commit of the memory repository;
- the result reports of the author.

The package holds manifests, prompts, author results and workspace templates. It holds no
workspace. `prepare` creates the workspace of a case under `<benchmark-root>/workspaces/<case-id>/`
with two environments:

| Environment | Contents |
| --- | --- |
| `source-only/` | The pinned source checkout under `repos/`, a marker file `.benchmark-root` and an `AGENTS.md` made from a template. No memory. |
| `with-memory/` | The same source checkout under `repos/`, the marker file and an `AGENTS.md`. In addition: a coordination root `ar-coordination/` with the pinned memory repository under `ar-coordination/memory-repos/`, and a file `.codex/config.toml` that registers the tool server of this package for the environment. |

The marker file makes Codex take the environment as the project root: `run` names it in the
Codex setting `project_root_markers`, whose default is `.git`. `run` passes the registration of
`.codex/config.toml` to `codex exec` as explicit `-c mcp_servers...` options.

The tool server of the memory-enabled environment serves the skills of the package. Its resource
`skill://index.json` lists them.

A workspace is generated state. `prepare` reuses a checkout that exists and resets it to the
pinned commit, so a change that you made in the checkout is lost. `--force-clone` removes the
checkouts and clones again.

Advice: when a benchmark root lies inside a code repository that Agents Remember manages, exclude
its folders `workspaces/` and `user-runs/` by [exclude rules](reference/path-rules.md#exclude-rules)
of the path rules of that repository. Benchmark memory then gets no cards of its own.

For work on a source checkout: when the environment of the benchmark's tool server does not hold
the package, set `AGENTS_REMEMBER_BENCHMARK_MCP_SRC` to the folder `mcp/src` of the checkout. The
generated `.codex/config.toml` then starts the tool server from that source.

## Task Selection

Advice: prefer tasks with a stable criterion for a complete answer:

- an explanation of an architecture;
- the investigation of a defect;
- an explanation of a workflow or of a data flow;
- the localization of a defect without a source edit.

Advice: do not use the building of a feature as the main evidence. A coding agent can make many
valid implementation choices, so two runs are hard to compare.

## Run Shape

`run` executes each selected variant of a prompt. The number of runs for a prompt and a variant
is the number of the manifest, or 3 when the manifest names none; `--repetitions` sets another
number. All runs of one case in one call share one run root,
`<benchmark-root>/user-runs/<case-id>/<run id>/`.

The variants of one repetition start together. `--jobs` bounds the number of Codex runs at the
same time; its default is the number of selected variants.

Each run is one call of `codex exec --json --ephemeral` in the folder of its environment. The
prompt arrives on standard input, and the approval policy is `never`. `--codex-sandbox` is
`default` or `danger-full-access`. With `default` the runner passes no sandbox option, and Codex
uses its configured sandbox.

For each run the runner writes four files under `<run-root>/<prompt-id>/<variant-id>/`:

| File | Contents |
| --- | --- |
| `run-<nnn>.jsonl` | The event stream that Codex printed. |
| `run-<nnn>.stderr` | The error output of Codex. |
| `run-<nnn>.final.md` | The last message of the agent. |
| `run-<nnn>.metadata.json` | The case, the prompt, the variant, the repetition, the folder, the command, the sandbox policy, the duration and the exit code. |

After the last run, `run` writes `<run-root>/summary.md`. A Codex process that ends with an exit
code other than 0 does not stop the other runs: the metadata of that run records the code.

## Metrics

The summary groups the runs by prompt and variant. For each group it gives the range of these
values:

| Metric | Source |
| --- | --- |
| `duration_seconds` | The duration in the metadata. |
| `event_count` | The number of events in the JSONL stream. |
| `command_event_count` | The number of completed items of the type `command_execution` in the stream. |
| `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` | The usage that Codex reports when a turn completes, summed over the turns of the run. |
| `jsonl_size_bytes` | The size of the JSONL file. |
| `exit_code` | The exit codes of the runs. |

For each run the summary also gives the duration, the size of the JSONL file and the number of
detected errors. A value that the stream does not hold is reported as `n/a` or as 0, because the
JSONL format of Codex can change between versions.

## Validity Checks

Advice: a result report states whether the boundary between the variants held:

- The `no-onboarding` run read neither `ar-coordination/memory-repos/` nor a card.
- The `with-onboarding` run resolved the coordination root of its environment, not the
  coordination root of your own installation.
- Both variants used the same pinned source commit.
- The final answer completes the task and does not stop after the first checks.

## Limitations

A benchmark result is evidence, not a proof.

- The behavior of a model varies between runs.
- Codex versions, models and tools change.
- Hardware, file system, shell, network and caches change the duration.
- The memory of a case reflects the choices of the author who curated it.
- A case goes out of date when its pinned repository commit or its memory changes.

Advice: read a pattern across cases of different sizes. A small repository may show no gain, and
a large or confusing one may show fewer wrong conclusions and less exploration.

The cases and their author results lie in the folder
[`benchmarks/`](https://github.com/Foxfire1st/agents-remember/tree/main/benchmarks) of the
repository.
