# The Cost Of A Repository's First Memory

This guide is about the cost of a repository's first memory. It says which part of the first
setup uses many tokens, which settings choose the model of each role, and how to size the first
tasks.

Agents Remember chooses no model by cost and sets no token budget. Most of this page is advice,
and each such paragraph starts with the word "Advice". The other statements describe the product.

## Where The Tokens Go

The setup by the Bootstrap role agent is small. Bootstrap explains the setup, asks you for your
decisions and calls a few tools. It writes these files and no others:

| What | Written through |
| --- | --- |
| The user settings file, `~/.config/agents-remember/settings.json` | `user_settings_init`. The file records the harness of each role and nothing else. A file that exists stays as it is. |
| The folders and first files of `<coordination-root>/memory-repos/ar-<repo-id>/` | `memory_init`; see [Onboard An Existing Repository](onboard-existing-repo.md#2-create-the-memory-repository). |
| The first commit of the memory repository and the cache `<memory-root>/memory.md` | `memory_baseline_adopt`; see [Adopt Existing Memory](adopt-existing-memory.md). |
| The folders and files of the coordination root, only when they are missing | `runtime_install`. In a first setup the Bootstrap call has created them before Bootstrap starts. |
| One report file | Bootstrap itself. The first line of the report states its path. |

Bootstrap writes no card and no knowledge record, creates no task and starts no other role.

The work that reads much source follows the setup: the
[knowledge foundation](../concepts.md#knowledge-foundation) and the first cards. An Architect
plans this work as tasks, and these roles do the reading:

- An Investigator, when the Architect starts one, researches the repository and writes its
  evidence into one report, which the task document of the foundation task names. It writes no
  memory.
- A Curator writes the knowledge records and the cards.
- A Reviewer checks the result of a task.

The
[research procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/repository-research.md),
the Investigator's and the Curator's instructions for this research, keeps it within bounds. A
first pass reads the repository broadly and assigns each source path to an area, a functional part
of the repository with a boundary and a priority. The research then covers one area at a time and
records the areas it did not reach.

Advice: expect this work to read far more than it writes. An agent reads source files and
existing memory many times and writes short records. The price of input tokens, and of cached
input where your [model provider](../concepts.md#model-provider) offers it, decides most of the
cost.

## Choose The Model Of Each Role

The role settings choose the harness, the model and the effort of each role. They stand in the
user settings file under `orchestration.roles.<role>`:

```json
{
  "orchestration": {
    "roles": {
      "investigator": { "harness": "<harness id>", "model": "<model>", "effort": "<effort>" },
      "curator": { "harness": "<harness id>", "model": "<model>" },
      "reviewer": { "harness": "<harness id>", "model": "<model>" }
    }
  }
}
```

A start checks `harness`, `model` and `effort` against the catalog of the host: the harnesses that
the host can start, with the models and efforts that the host reports for each. A start refuses a
choice that the catalog does not hold, with `agent_not_offered`, `model_not_offered` or
`effort_not_offered`; a part that the host does not report, for example the models of a harness,
is not checked. So a row names only what the host offers. A row without `model` takes the host's
default model for that harness. After a setup by Bootstrap, every row names a harness and nothing
else.

One start can differ from the settings: the role launcher of the dashboard and the arguments
`agent`, `model` and `effort` of `role_start` choose for that start only.

[Harnesses and role settings](../reference/harnesses.md) describes the keys of a row, the order
of the choices and how to edit the user settings file.

Advice: choose by the kind of work.

| Work | Advice |
| --- | --- |
| Research by an Investigator | A cost-efficient model with a large context. Broad reading matters more than polished prose. |
| Records and cards by a Curator | A cost-efficient model that uses tools reliably over many turns and keeps to its instructions. |
| Review | The strongest model that you can afford. The review catches a statement that the source does not support. |

Advice: a model fits the broad reading when it reads large files without cutting them short,
calls tools reliably over many turns, keeps to the folders it may write, and finishes one task in
one chat, before its context window is full and before it reaches the provider limit of its model
provider.

A role can have a second set of choices, the step-up, under `orchestration.roles.<role>.stepUp`.
For a Worker and a Curator, the step-up is taken after a number of review rounds that left
findings; the number is 3 unless the row names another in `afterTries`.

Advice: give the Curator a stronger model as its step-up. The cost-efficient model then does the
ordinary work, and the stronger model takes only the work that the first one did not finish.

## Check The Cost Early

Advice: run one representative task before you plan many. Then read the usage page of your
model provider and compare input, cached input and output. Extrapolate from your own numbers, and
check the current prices of the provider first: prices and discounts change.

## Size The First Tasks

Advice: keep each task small enough to reach its closeout without a stop at a provider limit.

- Give the knowledge foundation a bounded set of sources, and add areas in later tasks.
- Give one task one source area. A task that covers the whole repository can stop at a provider
  limit with nothing closed out.
- Let each task reach its closeout before the next one starts on the same area. A closeout is a
  commit of code and memory, so closed-out work survives a later stop.
- Start with the areas that hold hidden rules and fragile logic. Leave routine helpers until a
  task touches them.

## When To Use A Stronger Model Or Stop

Advice: change to a stronger model, or stop and review the work yourself, when one of these
holds:

- the source and the documentation disagree;
- a record would state uncertain architecture as a fact;
- the work reaches into another repository or depends on a branch;
- the model states an invariant without evidence in the source;
- repeated runs write outside the folders of the task;
- the harness shortens the context so far that the agent loses facts, or the agent reaches its
  provider limit.

## Harness Notes

The host runs each role agent as a chat of a harness. A role can use only a harness that the host
offers. [The host](../host.md#what-the-host-needs) says how to see which harnesses it offers
and how to add one. The page [Install](../install/README.md) leads to the page of each harness,
which lists what that harness needs.

Advice: prefer a harness that reaches the provider of your chosen model directly.
