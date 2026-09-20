"""Focused proof for pytest-plugin and support dependency discovery.

This module owns the repository's real-boundary census over the evidence-lifecycle catalog, so it
also carries the ``LOCR-R26@v1`` guard: the retained headless production-chain proof of
``LOCR-R16@v1`` is ordinary ``test_`` source, adds no governed evidence artifact, and leaves the
catalog closed over the governed inventory.

Amendment on the record (developer decision, 2026-09-16): ``LOCR-R26@v1``'s catalog-freeze clause was
amended to permit registering artifacts that ANOTHER leaf introduced. Under it the two handoff
support modules added by ``LOCR-L04`` were registered in ``mcp/tests/evidence-lifecycle.toml``, which
is what closed the inventory. The proof's own artifact delta remains exactly empty, and the freeze
still forbids the proof adding or widening anything.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality.dependency_ownership import (
    AMBIENT_ROLE_RUNNER_PATH,
    CODEX_CONFIG_PATH,
    LAYERS_CONTRACT_PATH,
    DependencyOwnershipGraph,
)
from agents_remember_test_support.testing.evidence_governance import governed_artifact_paths
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

REPOSITORY_ROOT = Path(__file__).parents[2]

PRODUCTION_PROOF_MODULE = Path("mcp/tests/test_lifecycle_owned_completion_relay.py")
"""The retained headless production-chain proof required by ``LOCR-R16@v1``."""

LIFECYCLE_CATALOG = Path("mcp/tests/evidence-lifecycle.toml")
LANE_MANIFEST = Path("mcp/tests/test-evidence-lanes.toml")

LIFECYCLE_SCHEMA = "ar-test-evidence-lifecycle/v3"
LIFECYCLE_CONTRACT_COUNT = 15
LIFECYCLE_ARTIFACT_COUNT = 65
LIFECYCLE_CATALOG_SHA256 = "73cdd2183e153af752773e8a4c6076e5e2eb7053d9e0ce5fa6538a69c907cefe"
"""``mcp/tests/evidence-lifecycle.toml`` byte-for-byte, re-pinned deliberately at every value below.

The value this line carries is the catalog after ``260915-KS``'s L28 leaf repaired the missing-consumer
registration that made the repository's own gate fail. L25 registered no artifact and no contract of its
own -- its ingest drives the shipped batch operation through ``candidate_batch_test_support`` and builds
its record trees through ``read_scope_test_support`` rather than introducing a third fixture -- so its
change is a *consumer* change only: those two support modules' consumer lists gained
``mcp/tests/test_knowledge_curator_ingest.py``. The counts stay fifteen and sixty-five, and the two
``[[contract]]`` rows named above are untouched, because a consumer registration is not an artifact.

L28 then added one more *consumer* registration to the same value: driving the new
``agents-remember knowledge-ingest`` subcommand from ``mcp/tests/test_knowledge_curator_ingest_list.py``
made that module import ``agents_remember.cli.__main__``, and the census's transitive import walk from
there reaches the portable profile runtime the node lockfile is a declared input of. The row therefore
gained that module too, and the counts still stay fifteen and sixty-five. Two re-pins inside one leaf is
the census working as designed: it derives consumers from the tree, so a new import edge is a new
declared consumer, and the declaration is what the digest pins.

The digest this value replaces was ``c25cdb1e...``, which pinned the merged catalog and was correct for
it: the omission was in the *catalog*, not in the pin. Two integration cases
(``test_repository_inputs_reach_their_supported_consumers`` and
``test_production_proof_adds_no_governed_evidence_artifact``) and the wrapper's own
``evidence_lifecycle`` gate step all read this one derivation, so the missing rows reddened all three
from L25 onward -- and ``addopts``' ``-m "not integration"`` is why the unit lane could not see it.

The earlier value is the **merged** catalog's own digest, re-measured after the master synced onto
its moved super line: fifteen contracts and sixty-five artifacts, of which the super line contributed nine
artifacts (the eve adapter/capsule/fixture rows, the two codex app-server recordings and the three
`scripts/e2e_harness/fresh_user_*` rows) and this master contributed ten (its KS knowledge-substrate support
modules). Both sides' records below are kept in file order, and the five shared rows whose `consumers` lists
differed carry the union.

The digest was first pinned at the R16 proof's landing (5b7a84f2) as
``a9d83c375d1bfdcae7d0c46020eba41fbaf305a306fe89bb1b9b479b861c2002``. ``LOCR-R26@v1``'s
catalog-freeze clause was then amended by explicit developer decision to permit registering
artifacts that ANOTHER leaf introduced, and the two ``LOCR-L04`` handoff support modules were
registered, closing the inventory. Value ``293a187f...`` was the LOCR post-registration catalog. ``260915-KS``'s
L1-L7 leaves then registered their own knowledge-substrate support modules against the same
amended clause, and this value is the catalog after L7's ``knowledge-read-scope-cases`` support
module joined it: ten contracts and fifty-one artifacts. L7's fix round 2 then split the over-limit
integration module and added ``mcp/tests/test_knowledge_read_paths.py`` to that support module's
consumer list -- a consumer change, not a new artifact: the counts stay ten and fifty-one, and this
value is the catalog after it. ``260915-KS``'s L8 leaf then registered its own
``knowledge-diff-cases`` support module against the same amended clause -- a new artifact, and the
two new diff modules joined the read-scope support module's consumer list because they build on it --
which is what this value pins: eleven contracts and fifty-two artifacts. ``260915-KS``'s L9-L11
leaves then registered their own knowledge-substrate support modules and raised the populations to
thirteen contracts and fifty-four artifacts, which is the value L24 left in place. ``260915-KS``'s
L14 leaf added no artifact and no contract of its own -- its two detection test modules use the
existing ``diff_scope_test_support`` and ``read_scope_test_support`` fixtures rather than a third
one -- so its catalog change is a *consumer* change only: both of those support modules' consumer
lists gained ``mcp/tests/test_knowledge_detection_runs.py``, the counts stay thirteen and fifty-four,
and this value is the catalog after that consumer registration. ``260915-KS``'s L15 leaf likewise
adds no artifact and no contract of its own -- its unit module is ordinary test source and its
integration module composes the existing ``curator_coherence_test_support`` and
``test_worktree_support`` fixtures rather than introducing a third shared module -- so its change is
also a *consumer* change only: ``curator_coherence_test_support``'s consumer list gained
``mcp/tests/test_curator_review_assessment_publication.py``, the counts stay thirteen and fifty-four,
and this value is the catalog after that consumer registration. ``260915-KS``'s L19 leaf likewise
added no artifact and no contract of its own -- its two requirement-revision test modules use the
existing ``knowledge_fixture_test_support`` and ``generation_test_support`` fixtures rather than a
third one -- so its catalog change is again a *consumer* change only: both of those support modules'
consumer lists gained ``mcp/tests/test_knowledge_requirement_revisions.py`` and
``mcp/tests/test_knowledge_requirement_reference_contract.py``, the counts stay thirteen and
fifty-four, and this value is the catalog after that consumer registration. ``260915-KS``'s L18 leaf
added no artifact and no contract of its own either -- its two citation-binding test modules use the
existing ``knowledge_fixture_test_support``, ``generation_test_support`` and
``read_scope_test_support`` fixtures rather than a fourth one -- so its catalog change is likewise a
*consumer* change only: the first two of those support modules' consumer lists gained
``test_knowledge_citation_bindings.py`` and ``test_knowledge_citation_boundaries.py``, and the
ambient-role-chat e2e generator's own ``exact-source`` consumer list gained both modules because each
quotes the real corpus key that generator's run report is written about -- a path-string edge the
census derives rather than one any import creates, which is also why the repository-owned
``REPOSITORY_TEST_INPUT_CONSUMERS[AMBIENT_ROLE_RUNNER_PATH]`` entry gained the same two modules: that
constant overrides the catalog for the *selection* graph while the catalog serves the *census*, so
both derivations have to be satisfied rather than one standing in for the other. The counts stay
thirteen and fifty-four, and this value is the catalog after that consumer registration.
``260915-KS``'s L17 leaf then added further *consumers* -- its two composition modules build their
admitted candidate through the facet leaf's ``knowledge-facet-cases`` helpers rather than through a
third fixture of their own, and they read their graphs through the ``knowledge-generation-cases`` and
``knowledge-read-scope-cases`` support modules the earlier leaves registered -- so the counts stay
thirteen and fifty-four and this value is the catalog after L17's consumer registration.
The digest was first pinned at the R16 proof's landing (5b7a84f2) as
``a9d83c375d1bfdcae7d0c46020eba41fbaf305a306fe89bb1b9b479b861c2002``. ``LOCR-R26@v1``'s
catalog-freeze clause was then amended by explicit developer decision to permit registering
artifacts that ANOTHER leaf introduced, and the two ``LOCR-L04`` handoff support modules were
registered, closing the inventory. This value is the post-registration catalog. The proof's own
artifact delta remains exactly empty, so the freeze still forbids the proof adding or widening
``260915-KS``'s L12 leaf then registered its own supporting-record support module --
``mcp/tests/evidence_test_support.py``, a new contract and a new artifact -- which was what the
pre-merge value pinned: fifteen contracts and fifty-five artifacts. ``260915-KS``'s L13 leaf likewise
adds no artifact and no contract of its own -- its two authored-effect test modules use the existing
``candidate_batch_test_support``, ``knowledge_fixture_test_support`` and ``generation_test_support``
fixtures rather than a fourth one -- so its catalog change is again a *consumer* change only:
``candidate_batch_test_support``'s consumer list gained ``mcp/tests/test_knowledge_change_sets.py``
and ``mcp/tests/test_knowledge_effect_claims.py``, and ``generation_test_support``'s gained
``mcp/tests/test_knowledge_effect_claims.py``. Those consumer registrations are in the merged catalog,
so this leaf's rows are counted in the fifteen and sixty-five this pin carries. The proof's own
artifact delta remains exactly empty -- none of these rows is the proof's -- so the freeze still
forbids the proof adding or widening
anything, and any further catalog change must re-pin this digest deliberately.

``260915-KS-L20`` (``ar/260915-ks-l20``) re-pinned it once more, and adds no governed artifact.
L20 delivers the five query views and the managed external projection; its two new modules are
ordinary ``test_`` source (``test_knowledge_views_and_projection.py`` in the unit lane and
``test_knowledge_projection_vault_safety.py`` in the integration lane), so the inventory stays
closed at 14 contracts / 64 artifacts and the counts are unchanged. The digest moves for the one
reason the L19 precedent records: the integration lane's acceptance case builds its dataset through
the registered ``shared-support`` fixture ``mcp/tests/read_scope_test_support.py``, whose
``consumer_scope = "exact"`` requires its ``consumers`` list to equal the source-derived consumer
set, so the new module was appended there -- and a ``consumers`` addition necessarily shifts every
line below it. Both directions were re-measured on the delivered candidate: the validator prints
``evidence-lifecycle: PASS (64 governed artifacts)`` and this pin equals
``sha256sum mcp/tests/evidence-lifecycle.toml``.

The same change repaired the lane manifest: ``mcp/tests/test_atomic_series_chain_pair_order.py`` was
committed by ``260915-CAPS-L25`` (``f0313143``) with no row in ``mcp/tests/test-evidence-lanes.toml``,
so ``load_lane_manifest`` raised ``test files without an explicit lane`` -- 288 test modules against
287 rows. That took the manifest out of service for its consumers without failing an ordinary
``pytest mcp/tests`` run, because it is loaded by the cadence plugin and ``code_quality/check.py``
rather than by the default selection. The missing row was added in the unit-regression lane, where
its neighbours sit; no lane was widened and no module moved between lanes.

**Second deliberate re-pin (260915-CAPS-L16, 2026-09-16).** The first re-pin left the pin behind
the file: at the source-line convergence merge ``23cc7a72`` the catalog was already
``bb567a25f30b9e3bdbd48a9dd1d2641a6a240763f545ddeca1a4b7932e98cb15`` over **4 contracts / 50
artifacts** while this pin still read ``293a187f…`` / 45, because the merge carried the landed
260831-LOCR line's governed artifacts in without a re-pin, and ``260915-CAPS-L13`` then moved the
file again (``9ea1d207…``, 4/50). That stale pin was the single pre-existing integration failure
(D10) inherited by every candidate cut from the master tip, and it is nobody's finding.

The value below is re-derived at ``8997e184`` -- the tip this leaf lands -- where the catalog is
``812211e9…`` over **4 contracts / 51 artifacts**: ``260915-CAPS-L7``'s landing added the
fifty-first governed artifact row. The population moved 45 -> 50 (the merged LOCR line) -> 51
(L7), and each of those states was measured rather than assumed. It is pinned here because a leaf
that changes the catalog last must pin the value at its own tip; a value correct for someone
else's base re-reds the moment this one lands. The proof's own artifact delta is still exactly
empty: this leaf registered nothing and added no consumer.

**Third deliberate re-pin (260915-CAPS-L15, 2026-09-17).** ``260915-CAPS-L15`` added **consumer
rows only** — the three governed artifacts its acceptance module reaches through the shared test
support it imports — so the populations stayed at **4 contracts / 51 artifacts** and the catalog
became ``3342a249…`` at that leaf's tip. Nothing was registered, no row was removed and no
artifact's identity moved: only the consumer proofs of ``curator_coherence_test_support.py``, the
Node ``package-lock.json`` fixture and the Codex ``model_page`` recording gained the new importer,
which is the same shape L7 used when its own module became a consumer.

**Fourth deliberate re-pin (260915-CAPS-L14, 2026-09-17) — the merged value.** ``260915-CAPS-L14``
added the fresh-user acceptance harness under ``scripts/e2e_harness/`` — a declared permanent
evidence-support root — so three governed artifacts entered the inventory and the population moved
51 -> **54**. L15 landed first, so this value is re-derived against the **merged** artifact set:
L14's three registered rows **plus** L15's consumer rows, over **4 contracts / 54 artifacts**, and
the catalog is ``5e938c85…`` — which is neither leaf's own figure (L14 measured ``3c7f184e…``
before L15 landed, L15 measured ``3342a249…`` before L14's rows existed). Unlike the first two
re-pins this one registers *new* artifacts rather than consumers of existing ones: each row
declares its exact source-derived consumers and an executable ``node:`` replacement, and
``mcp/tests/test_fresh_user_harness.py`` is the module that answers for all three (it is the
literal-path consumer, the shape proof for the fixtures, and the guard that a step which cannot run
is recorded ``blocked`` and never ``completed``).

**Fifth deliberate re-pin (260915-CAPS-L17, 2026-09-17) — its own branch's merged value.** ``260915-CAPS-L17``
added one real test module (``mcp/tests/test_eve_effort_runtime.py``) which begins the shipped
runtime and therefore consumes three already-governed artifacts — the eve adapter and capsule
shared-support modules and the portable Node lockfile fixture — so the census requires its path on
those three rows. **No artifact was registered and no contract changed**: the population stays at
**4 contracts / 51 artifacts**, and the only new bytes are three consumer entries on top of L15's.
L15 landed first, so this value is re-derived against the **merged** catalog rather than carried
from either leaf's base: ``563582a0…``, which is neither L15's ``3342a249…`` nor the
``22ce7027…`` this leaf measured before L15 landed.

**Sixth deliberate re-pin (260915-CAPS-L17, 2026-09-17) — the merged value at landing.** `260915-CAPS-L17` added **consumer
rows only** (its new runtime module reaches three already-governed artifacts), and L14 landed first, so this value is
re-derived against the **merged** catalog: L14's fifty-fourth artifact row plus this leaf's three consumer entries,
over **4 contracts / 54 artifacts**, giving `e3651d6f…`. Neither L14's `5e938c85…` nor this leaf's pre-sync
`563582a0…` (measured at 51 artifacts before L14 landed) is correct at this tip.

**Seventh deliberate re-pin (260915-CAPS-L9, 2026-09-17) — this leaf's own branch value.** ``260915-CAPS-L9`` added
**consumer rows only** — two of them, for the one governed artifact its installer surface reaches:
``mcp/tests/fixtures/repository_profiles/node/package-lock.json``. Measured at this leaf's own base the
populations were unchanged at **4 contracts / 51 artifacts** and the catalog stood at ``8764ea1f…``. One row is the
leaf's new module (``test_capsule_experiment_install.py``), which reads the pinned application's committed lockfile
through the installer it drives; the other is ``test_install_runtime.py``, which became a consumer of the same
artifact because the module it imports now reaches the lockfile — the same propagation L7, L14 and L15 recorded when
their own modules became consumers. Nothing was registered, no row was removed and no artifact's identity moved.
**This figure and the one below are historical**: each was correct at the first-sync tip it was measured at, and
neither is correct at the merged tip.

**Eighth deliberate re-pin (260915-CAPS-L9, 2026-09-17) — the first merged value.** ``260915-CAPS-L14`` landed while
this leaf was in flight, so this value was re-derived after ``worktree_sync`` against the **merged** catalog: L14's
three registered rows plus this leaf's two consumer entries, over **4 contracts / 54 artifacts**, giving
``dca9c2f9…``. Neither L14's ``5e938c85…`` nor this leaf's pre-sync ``8764ea1f…`` was correct at that tip.

**Ninth deliberate re-pin (260915-CAPS-L9, 2026-09-17) — the merged value at this leaf's landing.** ``260915-CAPS-L17``
then landed as well, so the value is re-derived a second time against the **merged** catalog: L14's three registered
rows, L17's three consumer entries and this leaf's own two consumer entries, over **4 contracts / 54 artifacts**,
giving ``31c6983d…``. Neither L17's landing figure ``e3651d6f…``, nor the first merged value ``dca9c2f9…``, nor this
leaf's pre-sync ``8764ea1f…`` is correct here. The measured delta against L17's landed catalog is exactly this leaf's
two added consumer paths and nothing else, which is why the value could not be taken from either leaf's figure.

**Tenth deliberate re-pin (260915-CAPS-L21, 2026-09-17) — consumer rows only.** ``260915-CAPS-L21`` added one real
test module, ``mcp/tests/test_citation_migrate_registration.py``, which drives the MCP tool registration through the
shared test support and therefore consumes the portable Node lockfile fixture: the census requires its path on that
row. **Nothing was registered, no row was removed and no artifact's identity moved**, so the populations stay at
**4 contracts / 54 artifacts** and the only new bytes are one consumer entry. The value is re-derived at this leaf's
own tip (L9 landed first and its ninth re-pin is the base), giving ``0bf0a2be…``, and the measured delta against
L9's landed catalog is exactly that one consumer path and nothing else. The proof's own artifact delta remains
exactly empty.
``260915-KS``'s L12 leaf then registered its own supporting-record support module --
``mcp/tests/evidence_test_support.py``, a new contract and a new artifact -- which is what that
line's own value pinned as fourteen contracts and fifty-five artifacts on its own branch. **This is
the pin in force, and it is the merge's**: at the merge of the two lines the measured merged
population is **fifteen contracts and sixty-five artifacts** -- this master's ten
knowledge-substrate rows plus the ias line's nine rows, on top of the forty-five rows both sides
already carried -- with this master's consumer additions retained alongside the ias line's. Five rows
both sides touched carry the union of both sides' consumer entries rather than either side's list:
``mcp/tests/curator_coherence_test_support.py`` (45 + 47 -> 48),
``mcp/tests/lifecycle_enclosure_test_support.py`` (2 + 3 -> 3),
``mcp/tests/fixtures/repository_profiles/node/package-lock.json`` (22 + 103 -> 103),
``scripts/e2e_harness/reporting.py`` (4 + 6 -> 6) and ``scripts/e2e_harness/run.py`` (5 + 3 -> 5,
the one case where the union is simply this master's list because the ias side's three entries are
all inside it). The proof's own artifact delta remains exactly empty -- none of these rows is the
proof's -- so the freeze still forbids the proof adding or widening anything, and any further
catalog change must re-pin this digest deliberately.
``260915-KS``'s L16 leaf likewise added no artifact and no contract of its own -- its three test modules
use the existing ``read_scope_test_support`` and ``diff_scope_test_support`` fixtures rather than a
third one -- so its catalog change is again a *consumer* change only: both of those support modules'
consumer lists gained the modules that build on them (``read_scope_test_support`` gained
``mcp/tests/test_knowledge_registered_scope.py`` and
``mcp/tests/test_knowledge_family_integrity_pipeline.py``, ``diff_scope_test_support`` gained
``mcp/tests/test_knowledge_family_integrity_pipeline.py``), so that registration moved no count: they were
thirteen and fifty-four on this leaf's own pre-sync base and are **fourteen and sixty-four** on the merged base it
landed against, because L12's supporting-record artifact and the super line's nine arrived in between. This value is
the catalog after L16's consumer registration **on that merged base**, re-measured on the merged file rather than
inherited from the leaf's own base. The proof's own artifact delta remains
exactly empty -- none of these rows is the proof's -- so the freeze still forbids the proof adding or
widening anything, and any further catalog change must re-pin this digest deliberately.
"""

REJECTED_STANDALONE_IDENTITY = "lifecycle-owned-completion-relay-production-chain"
"""The standalone contract identity ``LOCR-R26@v1`` explicitly rejects: no artifact references it."""


@pytest.mark.integration
def test_repository_inputs_reach_their_supported_consumers() -> None:
    # The graph validates the complete artifact catalog as part of this one real census.
    graph = DependencyOwnershipGraph(Path(__file__).parents[2])
    empty = graph.resolve([CODEX_CONFIG_PATH])
    assert empty.complete and not empty.tests
    assert [reason.detail for reason in empty.input_decisions] == [
        "verified-repository-input-no-consumers"
    ]
    assert not graph.resolve([Path("unknown") / "settings" / "unowned.toml"]).complete
    for source, consumer in (
        (AMBIENT_ROLE_RUNNER_PATH, Path("mcp/tests/test_agents_remember_quality.py")),
        (LAYERS_CONTRACT_PATH, Path("mcp/tests/test_layering.py")),
    ):
        impact = graph.resolve([source])
        assert impact.complete
        assert not impact.global_invalidation
        assert consumer in impact.tests
        assert any(
            reason.kind.value == "declared-consumer" for reason in impact.reasons_for(consumer)
        )


@pytest.mark.integration
def test_production_proof_adds_no_governed_evidence_artifact() -> None:
    """The R16 production-chain proof is ordinary test source and adds no governed artifact.

    ``LOCR-R26@v1``: the proof's controlled adapter frames, ports, and helper objects stay Python
    source inside the one module the lane manifest already selects, so the proof's own
    governed-artifact delta is exactly empty, the catalog fully closes over the governed inventory,
    and ``mcp/tests/evidence-lifecycle.toml`` keeps its pinned bytes. Five distinct escapes redden
    here: a proof fixture or support module landing unregistered in the governed tree, a catalog row
    deleted so a governed path loses its metadata, a standalone ``[[contract]]`` row or any other
    catalog edit, the proof module renamed out of ``test_`` source, and a dropped lane row.
    """

    catalog = _load(LIFECYCLE_CATALOG)
    _assert_the_proof_is_ordinary_test_source()
    _assert_the_proof_is_selected_by_the_lane_manifest()
    _assert_the_governed_inventory_is_closed(catalog)
    _assert_the_catalog_kept_its_bytes_and_identities(catalog)


def _assert_the_proof_is_ordinary_test_source() -> None:
    module = REPOSITORY_ROOT / PRODUCTION_PROOF_MODULE
    assert module.is_file(), f"{PRODUCTION_PROOF_MODULE.as_posix()} is the retained R16 proof"
    assert module.name.startswith("test_"), (
        f"{PRODUCTION_PROOF_MODULE.as_posix()} must stay ordinary test source: a non-``test_`` "
        "name moves it into the governed inventory, where no lifecycle row is permitted for it"
    )
    governed = governed_artifact_paths(REPOSITORY_ROOT)
    assert PRODUCTION_PROOF_MODULE.as_posix() not in governed, (
        "the proof's controlled adapter frames, ports, and fakes are module-local test source and "
        f"must stay outside the governed inventory, but {PRODUCTION_PROOF_MODULE.as_posix()} was "
        "discovered by governed_artifact_paths"
    )


def _assert_the_proof_is_selected_by_the_lane_manifest() -> None:
    membership = _lane_membership()
    owning = sorted(
        category
        for category, paths in membership.items()
        if PRODUCTION_PROOF_MODULE.as_posix() in paths
    )
    assert len(owning) == 1, (
        f"{PRODUCTION_PROOF_MODULE.as_posix()} must stay in exactly one evidence lane so the "
        f"existing test and quality selection keeps running the exact R16 node; found {owning}"
    )


def _assert_the_governed_inventory_is_closed(catalog: Mapping[str, object]) -> None:
    """Every governed path carries lifecycle metadata, and the catalog itself validates whole."""

    governed = _governed_artifact_paths(catalog)
    cataloged = _cataloged_paths(catalog)
    assert governed == cataloged, (
        "the governed-artifact inventory must stay closed over the lifecycle catalog, so the R16 "
        "production proof adds no unregistered path and no row points outside it; "
        f"unregistered={sorted(governed - cataloged)}, stale={sorted(cataloged - governed)}. A "
        "proof fixture or support module that escaped the test module reddens here: return the "
        "input to module-local test source instead of registering a file to force green."
    )
    try:
        load_evidence_inventory(REPOSITORY_ROOT)
    except EvidenceLifecycleError as error:
        raise AssertionError(
            f"the evidence-lifecycle catalog does not validate: {error}"
        ) from error


def _assert_the_catalog_kept_its_bytes_and_identities(catalog: Mapping[str, object]) -> None:
    assert catalog["schema_version"] == LIFECYCLE_SCHEMA, catalog["schema_version"]
    declared, referenced = _artifact_identities(catalog)
    assert REJECTED_STANDALONE_IDENTITY not in declared, REJECTED_STANDALONE_IDENTITY
    assert declared == referenced, sorted(declared - referenced)
    digest = hashlib.sha256((REPOSITORY_ROOT / LIFECYCLE_CATALOG).read_bytes()).hexdigest()
    contracts = _tables(catalog, "contract")
    artifacts = _tables(catalog, "artifact")
    assert (
        digest,
        len(contracts),
        len(artifacts),
    ) == (
        LIFECYCLE_CATALOG_SHA256,
        LIFECYCLE_CONTRACT_COUNT,
        LIFECYCLE_ARTIFACT_COUNT,
    ), "mcp/tests/evidence-lifecycle.toml must stay at its pinned bytes and populations"


def _load(relative: Path) -> Mapping[str, object]:
    with (REPOSITORY_ROOT / relative).open("rb") as stream:
        return tomllib.load(stream)


def _governed_artifact_paths(catalog: Mapping[str, object]) -> set[str]:
    threshold = catalog["large_fixture_bytes"]
    assert isinstance(threshold, int) and not isinstance(threshold, bool), threshold
    return governed_artifact_paths(REPOSITORY_ROOT, large_fixture_bytes=threshold)


def _cataloged_paths(catalog: Mapping[str, object]) -> set[str]:
    return {str(item["path"]) for item in _tables(catalog, "artifact")}


def _artifact_identities(catalog: Mapping[str, object]) -> tuple[set[str], set[str]]:
    declared = {str(item["id"]) for item in _tables(catalog, "contract")}
    referenced = {
        str(item["replacement_contract"]).removeprefix("contract:")
        for item in _tables(catalog, "artifact")
        if str(item["replacement_contract"]).startswith("contract:")
    }
    return declared, referenced


def _tables(catalog: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    rows = catalog[key]
    assert isinstance(rows, list), f"{key} must be a list"
    return [row for row in rows if isinstance(row, Mapping)]


def _lane_membership() -> dict[str, list[str]]:
    files = _load(LANE_MANIFEST)["files"]
    assert isinstance(files, Mapping), files
    return {str(category): [str(path) for path in paths] for category, paths in files.items()}


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
