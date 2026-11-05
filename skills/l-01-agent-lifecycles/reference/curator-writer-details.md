# Curator — writer and authoring step detail

Read the serving-build command section before a CLI deciding run or write. Read the remaining
authoring detail when you reach **Curation workflow 2: Before ingest**, not before.
Its family/external-reference formats and read-back call are used at **Curation workflow 3: Group only justified obligations**.
The operation owns serving-build authority; this file supplies the command and its report semantics.

## Step 2 — serving-build command and options

Prefer the mounted `knowledge_write`. For a CLI deciding run or write, bind the command to the
starting server's `server_info.servingBuild` as `operations/curation.md` requires. Obtain the
original interpreter from the role launch receipt's `toolServer.command[0]`, or the selected
server's startup definition's `command`. Preserve that spelling: `servingBuild.pythonExecutable`
is resolved and may point through a virtual-environment symlink to an interpreter without its
dependencies. If the admitted launch command is unavailable, report the CLI binding unavailable;
do not infer it from the resolved path or install another build.

Fill these values from that launch and the starting `server_info`, then print the CLI's identity:

```sh
AR_SERVING_PYTHON='<original admitted launch interpreter>'
AR_SERVING_PACKAGE='<server_info.servingBuild.packageRoot>'
AR_SERVING_PARENT="$(dirname -- "$AR_SERVING_PACKAGE")"
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -c \
  'import json; from agents_remember.serving.build_info import process_serving_build; print(json.dumps(process_serving_build().payload().model_dump(mode="json", exclude_none=True)))'
```

Compare its `version`, `sourceDigest`, `packageRoot`, `pythonExecutable` and `commit` with the
starting stamp before using this CLI to decide or write. A missing identity or a mismatch does
not establish the serving build. Report it; use the mounted tool when it admits the operation.
Keep an unreported commit unknown, never call it a verified clean checkout.

Use the same interpreter and explicit package path for the public CLI commands:

```sh
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli \
  knowledge-worklist --contract '<this leaf enclosure contract>'
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli \
  knowledge-ingest --contract '<this leaf enclosure contract>' \
  --list '<the hand-off list>' --authorization-ref '<the admitted authorization>' --commit --json
```

The worklist command computes and persists the leaf's worklist beside its contract. The writer
reads that worklist and the contract's code and memory roots. Without `--commit`, ingest plans,
validates and writes no files; `--commit` authorizes file writes, never a Git commit or acceptance.
The parser has no `--baseline`, `--publish` or `--publish-to` options. Read the actual report state,
written paths, record/entry IDs and refusals: exit zero covers both a plan and a write, and even a
`written` report with no written paths establishes no publication. A correct-input write refusal
uses the procedure linked from the operation's **Failure handling** section.

## Step 2 — check messages

(`realization_rationale_absent`).

The command
   needs converted memory: on the legacy format it refuses with `legacy-format` and names the conversion command.

## Step 2 — weakness and dependent records

**A weakness and a dependent are recorded through the writer, as every record.** An entry of kind `weakness` in a producer's hand-off list becomes a `WKN-` record through the writer, or the report says why not: the writer refuses one without a file, with an unknown kind, or with a statement beyond its one-sentence bound, and the refusal names the field. An entry of kind `dependent` becomes a `DPD-` record: the document, and for each place it stands on the recorded value the writer reads at the task's tip -- a code anchor, a whole file's blob, or an invariant's or a family's revision -- with the confirmation's task, role and commit. Every write of a dependent is a confirmation; no value is typed in. A Curator answers the worklist's row for a weakness (it stands, and the anchor moves with the code; or it is closed) and for a changed document (only the confirmed record answers it). Neither record ever refuses a task.

## Step 3 — family and external-reference format

When you restate a family's
guarantee, name the family in `history` as a `changed` row with your `effect`; the writer fills the
family's own revision.

An external document a card rests on is cited as an
   `external` reference target in the card's sidecar, never as a repository path with a Git blob. Name that
   document in the handoff evidence for the record's `origin`, so the evidence shows which external document
   each backed record rests on.

## Step 3 — read-back call fragment

the records `knowledge_read` with `view="family"` names over this leaf's memory worktree, opened with your own tools
