# Python Direct Diagnostic Cohort Manifest

**Manifest:** `python-direct-cohort/v2`
**Policy:** `python-direct-eligibility/v2`
**Cohort size:** 7 exact nodes
**Classifier binding:** `7c8643c943ef126b1eeac33cc12fba1dbf061fffced0895cce806fa147d95c4c`
**Expansion status:** closed; any expansion requires a separate decision

This is the bounded production cohort for `./scripts/test-python`. It isolates existing pure
assertions from integration-heavy test modules; it does not copy production behavior, introduce
diagnostic-only assertions, or mark a directory as safe. The strict manifest plus the canonical
classifier are the sole admission authority. There is no generic whole-repository dependency
analyzer: this cohort is a reviewed, content-addressed audit whose exact dependency/effect facts
fail closed on drift.

## Exact nodes and rationale

| Exact node | Existing assertion preserved | Why it represents the first cohort |
| --- | --- | --- |
| `mcp/tests/test_python_direct_cohort.py::test_stable_provider_id_never_returns_empty` | Provider names normalize deterministically and whitespace falls back to `repo`. | Pure production transform plus a local pytest fixture and helper chain. |
| `mcp/tests/test_python_direct_cohort.py::test_known_gate_kind_passes_through` | A configured known gate kind retains its literal identity. | Positive typed-vocabulary path. |
| `mcp/tests/test_python_direct_cohort.py::test_unknown_gate_kind_is_refused` | An unknown gate kind fails loudly. | Pure negative validation at a policy boundary. |
| `mcp/tests/test_python_direct_cohort.py::test_known_decision_role_passes_through` | A configured known decision role retains its literal identity. | Positive delegation-vocabulary path. |
| `mcp/tests/test_python_direct_cohort.py::test_unknown_decision_role_is_refused_by_name` | A misspelled decision role fails with its input named. | Pure negative validation with stable error meaning. |
| `mcp/tests/test_python_direct_cohort.py::test_normalize_route_root_forms` | All documented root forms normalize to `.`. | Looping pure string transform near onboarding-route policy. |
| `mcp/tests/test_python_direct_cohort.py::test_normalize_route_strips_slashes_and_backticks` | A quoted directory route normalizes to its repository-relative form. | Ordinary pure path-label transform without filesystem access. |

## Resolved dependency closure

The complete seven-node audit seals these candidate-owned files plus the canonical root
`pyproject.toml` and `mcp/tests/evidence-lifecycle.toml` configuration bindings:

- `mcp/tests/test_python_direct_cohort.py`
- `mcp/src/agents_remember/__init__.py`
- `mcp/src/agents_remember/kernel/__init__.py`
- `mcp/src/agents_remember/kernel/onboarding_doc.py`
- `mcp/src/agents_remember/kernel/filesystem.py`
- `mcp/src/agents_remember/kernel/primitives/gate_policy.py`
- `mcp/src/agents_remember/kernel/primitives/gate_vocab.py`
- `mcp/src/agents_remember/kernel/primitives/identity.py`

For each file the manifest records the exact SHA-256, audited symbols, candidate-local imports,
whether its relevant effects are fully known, and any protected effect families. Each node records
its exact symbol closure. The classifier verifies those declarations, fingerprints, node shapes,
and fixture/autouse membership before execution. Updating a hash is a policy change that requires
review of the same closure facts; no command can auto-refresh or bypass it.

The manifest's candidate identity is therefore not a mutable label. Every invocation emits a
content digest over the manifest, requested nodes, audited files, policy version, and canonical
configuration. The final acceptance record separately binds the immutable Git candidate tree and
Dagger report generation, avoiding a self-referential Git hash inside this checked-in file.

## Repository population

The final policy is explicit rather than inferred. Exactly seven manifest nodes are admitted. Any
other selector is `not-in-cohort` (or `mixed-selection` when combined with a member) before pytest
or repository-wide analysis starts.

| Classification | Exact selectors |
| --- | ---: |
| Manifest members | 7 |
| Non-members | all other selectors, refused without speculative analysis |

The retired Candidate-A prototype had statically enumerated 6,615 selectors and admitted the same
seven while building a generic analyzer for the rest. Those counts remain historical rollout
evidence, not a reason to retain that analyzer. The final policy's safety boundary is the small
sealed cohort; expansion requires an explicit audit and decision.

This very small admitted population is intentional. It demonstrates useful fast feedback and the
closed eligibility model without weakening classification to inflate an adoption percentage.

## Canonical invocation

```text
./scripts/test-python \
  mcp/tests/test_python_direct_cohort.py::test_stable_provider_id_never_returns_empty \
  mcp/tests/test_python_direct_cohort.py::test_known_gate_kind_passes_through \
  mcp/tests/test_python_direct_cohort.py::test_unknown_gate_kind_is_refused \
  mcp/tests/test_python_direct_cohort.py::test_known_decision_role_passes_through \
  mcp/tests/test_python_direct_cohort.py::test_unknown_decision_role_is_refused_by_name \
  mcp/tests/test_python_direct_cohort.py::test_normalize_route_root_forms \
  mcp/tests/test_python_direct_cohort.py::test_normalize_route_strips_slashes_and_backticks
```

The command output is the raw direct-route evidence: it contains the exact ordered nodes, node
outcomes, candidate binding, route timestamps, phase durations, and non-certifying altitude. The
same candidate's Dagger `pytest-phases.json` supplies the certifying-route node/outcome and phase
record for parity analysis. Dagger may collect the larger acceptance population; parity filters
the seven exact manifest nodes and discloses that full-suite context rather than presenting it as
an exact-only Dagger run.

## Unsafe sentinels

`mcp/tests/test_direct_test_eligibility.py` contains one manifest-level refusal for every closed
unsafe family. It also proves transitive unsafe closure, unresolved local-import declarations,
unknown effects, unaudited autouse fixtures, non-members, mixed requests, and content/configuration
drift refuse. `mcp/tests/test_direct_test_runner.py` proves every refused request executes zero
nodes and never falls back. Those sentinels remain ordinary Dagger-suite tests; they are not
admitted cohort members.
