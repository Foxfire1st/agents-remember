# Python Direct Diagnostic Cohort Manifest

**Manifest:** `python-direct-cohort/v1`
**Policy:** `python-direct-eligibility/v1`
**Cohort size:** 7 exact nodes
**Classifier binding:** `46333612594ed382abedebf8922aa569e70497f9cd12e6a8b7cd115177447f36`
**Expansion status:** closed; any expansion requires a separate decision

This is the first bounded production cohort for `./scripts/test-python`. It isolates existing
pure assertions from integration-heavy test modules; it does not copy production behavior,
introduce diagnostic-only assertions, or mark a directory as safe. The structural classifier is
still the sole admission authority.

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

The complete seven-node request resolves these candidate-owned files plus the canonical root
`pyproject.toml` configuration binding:

- `mcp/tests/test_python_direct_cohort.py`
- `mcp/src/agents_remember/kernel/onboarding_doc.py`
- `mcp/src/agents_remember/kernel/filesystem.py`
- `mcp/src/agents_remember/kernel/primitives/gate_policy.py`
- `mcp/src/agents_remember/kernel/primitives/gate_vocab.py`
- `mcp/src/agents_remember/kernel/primitives/identity.py`
- package `__init__.py` files encountered while resolving those imports

The manifest's candidate identity is not a mutable label. Every invocation emits the classifier's
content digest over these exact nodes, resolved closure files, policy version, and canonical
configuration. The final acceptance record separately binds the immutable Git candidate tree and
Dagger report generation, avoiding a self-referential Git hash inside this checked-in file.

## Repository population

The reproducible static population pass enumerates every top-level pytest function and class
method below `mcp/tests/test_*.py`, then calls `classify_direct_selection(candidate, (node,))` for
each exact selector. Parameterized selectors count once at their static selector boundary because
the direct policy refuses them before pytest expansion.

| Classification | Exact selectors |
| --- | ---: |
| Eligible | 7 |
| Refused: unsafe effect | 3,656 |
| Refused: unresolved dependency | 2,864 |
| Refused: parameterized target | 73 |
| Refused: unsupported collection | 15 |
| **Total** | **6,615** |

Unsafe-family observations in the same pass were: machine state 2,566; process control 804;
durability/integration 140; browser/external 112; provider/container 27; socket/service 6; and
Git/worktree 1. Mutable-global-state has a dedicated forcing sentinel even though it was not the
first refusal encountered for a current production selector.

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

`mcp/tests/test_direct_test_eligibility.py` contains one structural refusal for every closed unsafe
family. It also proves unsafe transitive helpers, unsafe imported submodules, autouse fixtures,
dynamic dependencies, and collection-time effects refuse. `mcp/tests/test_direct_test_runner.py`
proves mixed and representative unsafe requests execute zero nodes and never fall back. Those
sentinels remain ordinary Dagger-suite tests; they are not admitted cohort members.
