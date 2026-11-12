# Repository Certification Profiles

A certification is a full run of a code repository's checks and tests in a clean container: a
new container that a [Dagger function](#executor-adapter) builds for the run from the tree to
certify and a Git bundle of its history. A repository certification profile is the file in which
the repository says what such a run executes. This page says when a certification runs, and it
describes the format of the profile.

Agents Remember fixes the meaning and the order of four
[certification gates](../concepts.md#certification-gate). The repository owns everything else:
the commands, the runtimes, the [selectors](#selector), the [artifacts](#published-artifact), the
[result decoder](#result-decoder) and the [executor adapter](#executor-adapter).

## When A Certification Runs

Requested certification runs through Dagger and commits nothing.
[Dagger](https://dagger.io/) is a program that runs the steps of a build in containers. A run
starts only when a person asks for one. The tool server registers no tool that starts a
certification, and `agents-remember` has no sub-command for it.

Ordinary closeout runs no gate but the invariant gate. That gate is the
[knowledge gate](../concepts.md#knowledge-gate); "invariant gate" is the word of the product's
refusal text. A closeout also runs the pre-commit hook of the repository on each tree that it
commits. It starts no certification, no test suite and no code-quality run, and it reads no
profile. A landing does the same. [Worktrees And Closeout](worktrees-c09.md#closeout-and-recovery)
describes both steps.

A repository without a profile therefore closes out and lands like every other repository.

In the repository of Agents Remember, a contributor asks for a run with the command that
[Certification](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#certification)
gives. The command names the profile file, the mode `targeted` or `full`, and the commit that the
candidate is compared with. Its module lies in the folder `mcp/test_support/` of that repository,
which the installed package does not ship. The installed package has no command and no tool that
starts a run; with its library you write a profile and check it, as
[Canonical Form And Digest](#canonical-form-and-digest) shows.

## How A Run Finds The Profile

A certification request, the asking for one run, names the profile by a path relative to the root
of the repository: the command above takes it as `--certification-profile`. The run resolves
the path inside the candidate that it certifies. It uses no default file name and scans no
folder.

A run refuses with the status `certification-profile-invalid` before any command of the
repository starts. Each refusal carries findings with a code:

| Code | Cause |
| --- | --- |
| `profile-authority-missing` | The request names no profile. |
| `profile-authority-ambiguous` | The request names the profile by something other than one path. |
| `profile-path-invalid` | The path is absolute, holds `..` or a backslash, or is not in canonical form. |
| `profile-root-unavailable` | The root of the repository cannot be resolved. |
| `profile-path-symlink` | A part of the path is a symbolic link. |
| `profile-path-unavailable` | A part of the path cannot be inspected. |
| `profile-file-unavailable` | The file does not exist or cannot be read. |
| `profile-path-escape` | The path does not lead to a regular file inside the repository. |
| `profile-size-exceeded` | The file is larger than 8 MiB. |
| `profile-json-invalid` | The file is not valid JSON. |
| `profile-schema-invalid` | A field is missing, unknown or of the wrong form. The finding names the field. |
| `profile-digest-mismatch` | `profileDigest` does not equal the digest of the content. |
| `profile-repository-mismatch` | `repositoryId` differs from the repository id of the request. |

Each finding holds `code`, `path` and `detail`. The findings `profile-authority-missing`,
`profile-authority-ambiguous` and `profile-path-invalid` give `repositories.certificationProfile`
as their `path`, after the key
[`repositories.<id>.certificationProfile`](settings-json.md#repositories) of the settings file.
The other findings give `repository`, `profile` or the place in the profile.

The checks of [Graph Rules](#graph-rules) refuse with the same status.

## Gate Meanings

A profile selects the work of a certification gate: its [rails](#rail). It cannot change what
such a gate means:

| Gate | Rail class | Meaning | What the profile must declare |
| --- | --- | --- | --- |
| 1 | `pre-test-quality` | Deterministic checks before the tests. | Rails that use no result of Gate 2 and no clean room. |
| 2 | `ordinary-test-suite` | The ordinary test suites. | The selector that decides which tests run, and the artifacts of the suites. |
| 3 | `post-test-quality` | Checks that read the results of the suites. | For every rail, an artifact of Gate 2 as its input. |
| 4 | `integration-test` | Integration and end-to-end tests. | For every rail, a clean room and a teardown that always runs. |

The rail class is the value of a rail's field `railClass`. Each class belongs to the gate of its
row, and the check of the profile refuses a rail whose `gate` is another one.

A clean room is the folder in which the Dagger function has rebuilt the tree to certify. In the
Dagger module of the Agents Remember repository it is the folder `/workspace` of the container:
the function copies the tree there, removes its Git folder and fetches the history again from the
Git bundle. A command reaches the folder through the placeholder `{clean-room}`. A rail declares
with `cleanRoom: true` that it needs the clean room. The field changes no step of the run: the
check of the profile requires it on every rail of Gate 4 and refuses it on the other gates.

Every selection holds the gates 1, 2, 3 and 4 in this order. A repository without work for a gate
records the gate with `status: "not-applicable"`, a `reason` and no rail. Not applicable is a
result of its own: it is neither a pass nor a skip.

## Selections

A selection names the rails of each gate for one purpose and one mode. It refers to rails by
their identity, `railId` and `version`, and holds no command.

| Purpose | Mode | Content |
| --- | --- | --- |
| `local-precommit` | `targeted` | The selection for a local check before a commit. |
| `closeout` | `targeted` | The checks, and the tests that the change reaches. |
| `closeout` | `full` | Every check and every test. |
| `atomic-leaf-closeout` | `targeted` or `full` | A further selection that a profile may hold. |

A profile must hold exactly one selection for each of the first three rows. A missing one is
refused as `required-profile-selection-missing`, and two selections with the same purpose and
mode are refused as `ambiguous-profile-selection`. A selection of the fourth row is optional.

A certification request selects the purpose `closeout` with the mode that it names. It selects
neither `local-precommit` nor `atomic-leaf-closeout`. The purpose is a name inside the profile.
It does not make a closeout start a certification.

## Document Shape

The profile is one JSON object. The field `schemaVersion` has the value
`repository-certification-profile/v1`. An unknown field is refused at every level.

| Field | Meaning |
| --- | --- |
| `semanticRevision` | The version of the profile's content, as a semantic version. |
| `repositoryId` | The id of the code repository. It must equal the repository id of the request. |
| `profileId` | A stable name of the profile. |
| `profileDigest` | The SHA-256 digest of the canonical content without this field: 64 lowercase hexadecimal digits without a prefix. |
| `selections` | The selections. At least one. |
| `rails` | The rail definitions. At least one. |
| `environments` | Optional. Dependency environments that one rail builds and later gates rebuild. |
| `generatedInputs` | Optional. Generated files of the candidate and the rail that checks each. |
| `selectors` | Optional. The programs that decide which tests of Gate 2 run. |
| `executorAdapters` | The executor adapters. At least one. The only kind is `dagger-module`. |
| `resultDecoders` | The result decoders. At least one. The only kind is `json-exit-status`. |
| `publishedArtifacts` | The complete list of files that a run may publish. |

### Selection

| Field | Meaning |
| --- | --- |
| `selectionId` | The name of the selection. |
| `purpose`, `mode` | As in [Selections](#selections). |
| `executorAdapterId` | The executor adapter that runs the selection. |
| `resultDecoderId` | The decoder that reads the result of the run. |
| `gates` | Four entries, one for each gate. |

Each entry of `gates` holds `gate`, `status` (`applicable` or `not-applicable`), `railIds`,
`selectionIdentity`, and the optional texts `population` and `reason`. An entry of `railIds`
holds `railId` and `version`.

### Rail

A rail is one check or one test run.

| Field | Meaning |
| --- | --- |
| `identity` | `railId` and `version`. |
| `gate`, `railClass` | The gate and its rail class, as in [Gate Meanings](#gate-meanings). |
| `ownerClass`, `correctiveOwner` | Who owns the rail and who repairs a failure. |
| `posture` | `enforcing` or `report-only`. |
| `orderKey` | The key that fixes the order of the rails inside a gate. |
| `prerequisites` | Rails that must run before this one. |
| `conditionalPrerequisites` | Those entries of `prerequisites` in the same gate that have a `sourceApplicability` of their own. Such a prerequisite counts only when it applies to the candidate. |
| `requiredArtifacts` | Artifacts of earlier rails that this rail reads. |
| `sourceApplicability` | Optional. It limits the rail to candidates that change given paths. It holds `selectorId`, `version`, `dependencyPrefixes`, `evidencePath` and `notApplicableReason`, and optionally `schemaVersion` with the value `source-path-applicability/v1`. |
| `runtimeInputs` | The identities of the runtime: `runtimeIdentity`, `toolchainDigest`, `imageDigest`, `lockDigest`, `environmentDigest`, `secretPolicyDigest`. |
| `evidenceContract` | The evidence that the rail produces. Each entry holds `evidenceId`, `mediaType` and `maxBytes`. |
| `outputArtifacts` | The artifacts that the rail produces. Each entry holds `artifactId`, `schemaVersion`, `mediaType` and `requiredOnPass`. |
| `execution` | How the rail runs; see the next table. |

Fields of `execution`:

| Field | Meaning |
| --- | --- |
| `adapterKind` | `container-command`. |
| `adapterId` | The name of the rail's adapter. |
| `workingDirectory`, `command` | The folder inside the candidate and the argument list. |
| `environmentContract` | The names of the environment variables of the command. |
| `scopeProviderId`, `inputSelectors` | The selector that sets the scope of the rail, and the names of the selection inputs that the rail uses. A rail of Gate 2 needs both fields: a `scopeProviderId` and at least one entry of `inputSelectors`. |
| `resultDecoderId` | The decoder of the rail's result. |
| `timeoutSeconds`, `resourcePolicyId` | The time limit and the resource policy. |
| `successExitCodes`, `skippedExitCodes` | The exit codes that count as passed and as skipped. `successExitCodes` is `[0]` by default. Every skipped code must also be a success code. |
| `cleanRoom` | `true` for a rail that needs a [clean room](#gate-meanings). Only a rail of Gate 4 may set it. |
| `teardownPolicy` | `not-required` or `always`. A rail of Gate 4 needs `always`. |
| `executionEvidence` | A reference to the evidence of the run. |

### Selector

A selector is a program of the repository that decides which tests of Gate 2 run for a candidate.

| Field | Meaning |
| --- | --- |
| `selectorId`, `version` | The identity of the selector. |
| `schemaVersion` | `repository-selector-result/v2`. |
| `configurationDigest` | The digest of the selector's configuration. |
| `inputUniverse`, `externalInputs` | The inputs that the selector reads. |
| `outputArtifacts` | The artifacts that the selector writes. |
| `workingDirectory`, `command` | The folder and the argument list. |
| `resultPath` | The file that holds the selector's result. |

The command of a rail or a selector may hold placeholders in braces. The check of the profile
refuses an unknown placeholder. These are all the placeholders it knows:

- In the command of every rail and every selector: `{reports}`, `{clean-room}`,
  `{selection-mode}`, `{candidate-kind}`, `{candidate-value}`, `{diff-base}`,
  `{memory-cap-bytes}`, `{selector-id}`, `{selector-version}` and
  `{selector-configuration-digest}`.
- In the command of a selector also `{selector-output}`, the path of its result. The command must
  use it and the five placeholders `{selector-id}`, `{selector-version}`,
  `{selector-configuration-digest}`, `{candidate-kind}` and `{candidate-value}`.
- In the command of a rail with a `scopeProviderId` also `{selector-output}`, and each name that
  the selector lists in `outputArtifacts`; such a name stands alone as one argument.
- In the command of a rail also `{source-selection:<selectorId>}`, once for its own
  `sourceApplicability` and once for that of each conditional prerequisite. The command must use
  exactly these.

In the Dagger module of the Agents Remember repository, a selector that exits with 0 counts as a
failed step of the run when its result is missing, is no regular file (a symbolic link counts as
none), is larger than 8 MiB, is not valid JSON or does not have the form
`repository-selector-result/v2`. The result of the run names the selector and the fault.

### Executor Adapter

The adapter kind `dagger-module` calls one function of a Dagger module.

| Field | Meaning |
| --- | --- |
| `adapterId`, `version` | The identity of the adapter. |
| `executable` | The program to start. The value must be exactly `dagger`. |
| `functionName` | The function of the Dagger module. |
| `sourceArgument`, `repositoryBundleArgument`, `planArgument` | The names of the function's arguments for the candidate source, the Git bundle and the plan of the run. |
| `memoryCapArgument`, `retainedReportsArgument` | The names of the arguments for the memory limit and for reports of earlier gates. The second is optional. |
| `reportsField` | The field of the function's result that holds the reports. |
| `imageReference`, `runtimeDigest` | The container image and the digest of the runtime. |
| `consumingGates` | The gates that the adapter runs. |

The commands of the rails are data of the repository. A run never starts them on your machine: it
starts the program `dagger` with the declared function, and that function runs the rails inside
its container.

When `dagger` cannot start, the run refuses with the status
`certification-executor-prerequisite-failed`. The finding has the code
`executor-prerequisite-unavailable` and names `adapterId`, `runtimeDigest`, the first affected
`gate`, `affectedGates` and `correctiveOwners`.

### Result Decoder

The decoder kind `json-exit-status` reads one JSON file of the run as its result.

| Field | Meaning |
| --- | --- |
| `decoderId` | The name of the decoder. |
| `artifactPath` | The published file that holds the result. |
| `statusField`, `exitCodeField` | The names of the two fields that hold the status and the exit code. |
| `passedValue`, `failedValue` | The status values for an exit code of 0 and for another exit code. A result whose status contradicts its exit code is refused. |
| `artifactReferences` | Optional. Fields of the result that name other published files. |
| `referenceActivations` | Optional. Rules that require or forbid such fields. |
| `consumingGates` | The gates whose result the decoder reads. |

An entry of `artifactReferences` holds `fieldPath` with its `segments`, and `cardinality`:
`single` for one path, `list` for a list of paths. Every path that the result names must be a
relative path of a published file. `nullPolicy: "ignore-reference"` accepts JSON `null` in the
field as no reference. `nullParentPolicy: "ignore-reference"` accepts a `null` parent object as
an absent field. Both are `invalid` by default.

An entry of `referenceActivations` holds `listFieldPath`, `containsValue` and
`referenceFieldPaths`. When the list field exists and holds the value, every named reference
field must be present. When the list field exists without the value, none may be present. An
absent list field decides nothing. `nullPolicy: "ignore-activation"` treats `null` like an absent
list field; the default is `invalid`.

The decoder knows no field name and no step name of a repository. All of them come from the
profile.

### Published Artifact

| Field | Meaning |
| --- | --- |
| `path` | The path of the file in the reports of a run. |
| `mediaType` | The media type of the file. |
| `required` | `true` when a passed run must hold the file. The default is `true`. |
| `maxBytes` | The largest allowed size. |
| `publisherGates` | The gates that publish the file. |

A run publishes only the files of this list. A file outside the list, and a file larger than its
`maxBytes`, refuse the publication. A passed run needs every file marked `required`. A failed run
is published without the files that it did not reach, so its result still names the failing rail.

### Environment

An entry of `environments` names folders of installed dependencies. A rail of Gate 1 creates the
folders and publishes a list of their content. Each later gate that uses them creates them again
and publishes a proof that the content agrees with the list.

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Optional. The value `repository-environment-reconstruction/v1`. |
| `environmentId` | The name of the environment. |
| `producerRail` | The rail of Gate 1 that creates the folders. |
| `consumingGates` | The later gates that use the folders. |
| `directoryScopes` | The folders, as paths inside the candidate. |
| `artifactId`, `manifestPath` | The artifact of the producer rail and the published file that holds the list. |
| `reconstructionProofPath` | The published file that holds the proof of a later gate. |
| `workingDirectory`, `command`, `timeoutSeconds` | The folder, the argument list and the time limit of the command that creates the folders. |
| `inspectionExecutable` | The program that lists the content of the folders. |
| `maxEntries`, `maxBytes`, `maxFileBytes`, `maxManifestBytes` | The size limits. |

### Generated Input

An entry of `generatedInputs` names files of the candidate that a script generates from other
files.

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Optional. The value `generated-candidate-input/v1`. |
| `inputId` | The name of the entry. |
| `checkRail` | The rail of Gate 1 that checks that the generated files are current. |
| `sourceScopes` | The paths of the sources. |
| `generatedScopes` | The paths of the generated files. |

## Graph Rules

The check of a profile collects every finding before a run starts. It refuses, among others:

- two rails with the same identity, two versions of one rail id, and a duplicate selection,
  selector, adapter, decoder, artifact or field;
- a missing or an ambiguous required selection;
- an adapter whose `executable` is not `dagger`;
- gates that are missing, repeated, out of order or of the wrong rail class;
- a selection that names an unknown rail, adapter, decoder or scope provider;
- a prerequisite in a later gate, and a cycle of prerequisites inside a gate;
- an artifact that no rail declares, or that two rails produce;
- a rail of Gate 2 without a selector or without the artifacts of its suite;
- a rail of Gate 3 without an input from Gate 2;
- a clean room outside Gate 4, and a rail of Gate 4 without a teardown that always runs;
- a decoder whose result file is not declared or is optional;
- two reference rules for the same field, and an activation that names an undeclared reference
  field;
- a rail that belongs to no selection.

## Canonical Form And Digest

The digest is computed over a canonical form of the profile. The canonical form sorts the lists
that are addressed by identity and the fields that are sets. A change in the order of such a list
therefore keeps the digest. A change of a command, a selector, a runtime or an artifact changes
it.

While you write a profile, set `profileDigest` to 64 zeroes: the digit `0` 64 times, without a
prefix such as `sha256:`. Then compute the digest with the library of the installed package. In
the examples, `config/repository-certification.json` stands for the path of your profile:

```python
from pathlib import Path

from agents_remember.certification.repository_profiles import repository_profile_digest
from agents_remember.certification.repository_profiles.models import (
    RepositoryCertificationProfile,
)

path = Path("config/repository-certification.json")
profile = RepositoryCertificationProfile.model_validate_json(path.read_text(encoding="utf-8"))
print(repository_profile_digest(profile))
```

Write the printed value into `profileDigest`. Then check the file and compile the plan of every
selection:

```python
from pathlib import Path

from agents_remember.certification.models import CandidateIdentity
from agents_remember.certification.repository_profiles import (
    compile_repository_profile_plan,
    load_repository_profile,
)

root = Path("/absolute/path/to/my-repository")
admitted = load_repository_profile(
    "my-repository",
    root,
    Path("config/repository-certification.json"),
)
for selection in admitted.canonical.profile.selections:
    compile_repository_profile_plan(
        admitted.canonical,
        selection_id=selection.selectionId,
        candidate_identity=CandidateIdentity(kind="content-digest", value="0" * 64),
    )
```

The first call raises an error with the status `certification-profile-invalid` and its findings
when the profile is refused.

The profile of the Agents Remember repository is
[`mcp/certification-profile-v1.json`](https://github.com/Foxfire1st/agents-remember/blob/main/mcp/certification-profile-v1.json).
It is an example of a complete profile and belongs to that repository only. Do not copy its
commands into another repository.

## What A Run Does

A requested run takes these steps:

1. It builds the candidate in a sandbox folder: a clone of the checkout at `HEAD`, with the
   staged changes of the checkout applied. It also writes a Git bundle of the candidate's
   history.
2. It loads the profile from that candidate, checks it, and selects the purpose `closeout` with
   the requested mode.
3. It writes a manifest for the run. The manifest records, among other facts, the candidate
   tree, the hash of the profile file, the profile digest, the plan, the executor adapter, the
   result decoder and the list of published artifacts.
4. It starts the Dagger function of the executor adapter with the candidate, the bundle and the
   manifest.
5. It checks the exported files against `publishedArtifacts`, reads the result with the decoder,
   and publishes the reports under the report folder of the request. The reports of one run
   become visible together: the file `quality-report-set.json` of the report folder names the
   current set.

Each gate of a plan carries the `profileDigest` of the whole profile and a `planDigest` of its
own. The `planDigest` covers the plan of that gate and the candidate.

The run changes no branch and creates no commit.

## Writing A Profile

Advice for the author of a profile:

1. Read the repository's documented build, checks, test suites and integration tests. Ask the
   owner of the repository where a command is ambiguous. Do not derive a command from a file
   name.
2. Write one profile for the repository and compute its digest.
3. Compile every selection as shown above, and repair every finding.
