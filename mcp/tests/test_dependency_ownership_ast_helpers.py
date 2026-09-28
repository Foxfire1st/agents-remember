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
LIFECYCLE_CONTRACT_COUNT = 16
LIFECYCLE_ARTIFACT_COUNT = 66
LIFECYCLE_CATALOG_SHA256 = "1c682524157cb12075319069b03410b39053317946a61847c51a603c146a68b2"
"""``mcp/tests/evidence-lifecycle.toml`` byte-for-byte, re-pinned deliberately at every value below.

**Thirty-second deliberate re-pin (260921-ICR-L57, at base ``ae2fd5c8``, 2026-09-28) -- the consumer
rows the census derived for eight landed case modules and three file-size splits, and three earlier
row additions that were never re-pinned.** ``260921-ICR-L57`` is the master's exit-gate repair leaf
and **owns no ICR requirement**; it registers no artifact and no contract. At its base the oracle
reported **five** rows, each ``unsupported=[]``: ``mcp/tests/curator_coherence_test_support.py``
(2 missing), ``mcp/tests/fixtures/repository_profiles/node/package-lock.json`` (7),
``mcp/tests/snapshot_lifecycle_test_support.py`` (3), ``mcp/tests/diff_scope_test_support.py`` (6) and
``mcp/tests/read_scope_test_support.py`` (5). The missing paths are case modules that landed without
their rows: ``test_review_assessment_history.py``, ``test_review_assessment_history_repairs.py``,
``test_review_recorded_knowledge.py``, ``test_review_unchanged_knowledge.py``,
``test_curator_candidate_progression.py``, ``test_curator_family_retention.py``,
``test_curator_scope.py`` and ``test_knowledge_review_attributed_source_content.py``. The same leaf
split three case modules that had crossed the **1200**-line hard limit, each by moving one cohesive
section into a new ordinary unit module that imports the sibling's helpers:
``test_knowledge_diff_scope.py`` (1384 lines, 22 cases) into itself (790, 13) and
``test_knowledge_diff_attribution.py`` (626, 9); ``test_knowledge_review_source_endpoints.py``
(1515, 20) into itself (1093, 14) and ``test_knowledge_review_attribution_precedence.py`` (450, 6);
``test_knowledge_review_surface.py`` (1475, 31) into itself (1056, 22) and
``test_knowledge_review_resolution_and_route.py`` (470, 9). The census then named the three new
modules on the two rows their siblings consume, ``diff_scope_test_support.py`` and
``read_scope_test_support.py``, raising those two findings to 9 and 8 missing paths. Every missing
path was added to its row beside the sibling it was split from or the family it belongs to (the
curator modules after ``test_curator_realization_authoring.py``), otherwise at the row's end, and
each new module's lane row was added to ``mcp/tests/test-evidence-lanes.toml`` in the
unit-regression lane beside its sibling. Separately, the bytes had already left the Thirty-first pin
without a re-pin: ``55c62237``, ``9b2f775f`` and ``eda94732`` each added consumer paths (one, one and
two) and left the file at ``aaf23d7890387ad6f069a5206c10717091b03360f12928b011dca1b83794459c``. The
drift was masked because the inventory-closure assertion above the byte check failed first. **Nothing
was registered, no row was removed and no artifact's identity moved**, so the population stays at
**sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``49b1e98bdf4adead6a36038ecfb182646ab6de324d0a4e4f3c3ac81a28372e83`` (the Thirty-first) to
``1c682524157cb12075319069b03410b39053317946a61847c51a603c146a68b2``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Thirty-first deliberate re-pin (260921-ICR-L32, at base ``71a17079``, 2026-09-24) -- one module split
across the file-size rail, and the TWO consumer rows the census derived for the new half.**
``260921-ICR-L32`` is the master's developer-approved pre-R25 repair leaf and **owns no ICR
requirement**: it closes six must-close findings in leaves that already landed, and it registers no
artifact and no contract of its own. Its own item here is D54: the module
``mcp/tests/test_curator_family_authoring.py`` had reached **1320** lines, past the **1200**-line File
Size Budget hard limit -- a breach of the standing condition that the census must not gain a new
offender -- so the nine run-level cases its own fourth section held moved into the new ordinary unit
module ``mcp/tests/test_curator_ingest_write_and_retention.py``, which imports the sibling's authoring
and read-back helpers rather than duplicating them. The nine are the external-source manifest and its
two refusals, the identity a refused entry does not spend, the two replays, the changed no-family
basis, the dry run, and the command line. The two modules collect the same **twenty** cases the one
module collected before (**eleven** and **nine**), and they measure **818** and **578** lines. The
census derived the delta exactly, and the finding named **two** rows, each reporting
``missing=['mcp/tests/test_curator_ingest_write_and_retention.py'], unsupported=[]``:
``mcp/tests/fixtures/repository_profiles/node/package-lock.json`` and
``mcp/tests/snapshot_lifecycle_test_support.py``, the two rows its sibling
``mcp/tests/test_curator_family_authoring.py`` already consumes. Both ``consumer_scope = "exact"``
rows gained that one path, in the position each row already lists the sibling case module, and the
module's own lane row
was added to ``mcp/tests/test-evidence-lanes.toml`` in the unit-regression lane beside its sibling.
**Nothing was registered, no row was removed and no artifact's identity moved**, so the population
stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``cbe71c9ad9b6d2851220069bc10d8e80b2eaca5b60bccda56a91758bce05bf7a`` (the Thirtieth) to
``49b1e98bdf4adead6a36038ecfb182646ab6de324d0a4e4f3c3ac81a28372e83``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Thirtieth deliberate re-pin (260921-ICR-L27, at base ``06ed70cf``, 2026-09-24) -- the delivered
knowledge-bootstrap procedure's three consumer rows.** ``260921-ICR-L27`` (requirement ``ICR-R27@v1``)
registers no artifact and no contract of its own: its seven cases live in the new ordinary unit module
``mcp/tests/test_knowledge_bootstrap_procedure.py``, which drives the shipped skill catalog's own list
and read entry points, the shipped umbrella command line's own parser, the launch compiler, and — for
the seat gate — the dashboard's own open route over the world
``mcp/tests/test_capsule_launch_wiring.py`` keeps real. The census derived the delta exactly, in two
waves as the module grew: ``mcp/tests/fixtures/repository_profiles/node/package-lock.json`` reported
``missing=['mcp/tests/test_knowledge_bootstrap_procedure.py'], unsupported=[]`` first, and the
seat-level cases' import of the shipped launch world then added
``mcp/tests/curator_coherence_test_support.py`` and
``mcp/tests/fixtures/codex_app_server_model_page.json`` with the same one-path finding. All three rows
gained that one path each, and the module's lane row was added to
``mcp/tests/test-evidence-lanes.toml`` in the unit-regression lane beside its sibling. **Nothing was
registered, no row was removed and no artifact's identity moved**, so the population stays at
**sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``88bd67ebed540594438b9256a752e59479ae29d095596f6c352a6f474ad3b42e`` (this leaf's round-1 value) to
``cbe71c9ad9b6d2851220069bc10d8e80b2eaca5b60bccda56a91758bce05bf7a``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact
delta remains exactly empty.

**Twenty-ninth deliberate re-pin (260921-ICR-L29, at base ``0d7910f9``, 2026-09-24) -- the taskless
bootstrap case module's one consumer row.** ``260921-ICR-L29`` (requirement ``ICR-R29@v1``)
registers no artifact and no contract of its own: its nineteen cases live in the new ordinary unit
module ``mcp/tests/test_knowledge_bootstrap.py``, which drives the shipped
``agents-remember knowledge-bootstrap`` subcommand over a real coordination world (a real code
checkout, a real external memory repository and a real MCP settings document) and reads every claim
back through the read route's own owners. The census derived the delta exactly, and the finding was
one row: ``mcp/tests/fixtures/repository_profiles/node/package-lock.json`` reported
``missing=['mcp/tests/test_knowledge_bootstrap.py'], unsupported=[]``, so that row gained that one
path, and the module's lane row was added to ``mcp/tests/test-evidence-lanes.toml`` in the
unit-regression lane beside its sibling. **Nothing was registered, no row was removed and no
artifact's identity moved**, so the population stays at **sixteen contracts / sixty-six artifacts**,
and the catalog is re-pinned from
``97ae9cbefd756a6def6e8048baa2066beef2c06300f73eb93b4ab23b4ebf6879`` (the Twenty-eighth) to
``f3dd258c161eee057edee9bdb7b7a4874220c8a4201c04a516dc146c14526589``, measured with ``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved
candidate. The proof's own artifact delta remains exactly empty.

**Twenty-eighth deliberate re-pin (260921-ICR-L31's fix round 1, at base ``4c000b11``, 2026-09-23) --
one further case module's two consumer rows.** Fix round 1 moved the family-population cases
(the canonical memberless-successor ambiguity, the recorded family with an empty roster, the
owner-measured history sentence and the measured-zero contrast) into a new
``mcp/tests/test_review_family_context_population.py`` rather than growing the case module past the
soft rail, importing its enclosure and helpers from the sibling case module. The census derived the
delta exactly: ``mcp/tests/diff_scope_test_support.py`` and
``mcp/tests/read_scope_test_support.py`` each reported
``missing=['mcp/tests/test_review_family_context_population.py'], unsupported=[]``, so each gained
that one path, and the module's lane row was added to ``mcp/tests/test-evidence-lanes.toml``.
**Nothing was registered, no row was removed and no artifact's identity moved**, so the population
stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``b1c38ed84fb847bc76ddd0497319cfad7059d35f3fdf78cd425169d4f49cbd48`` (the Twenty-seventh) to
``23dd7c0f85b50585e8968122f60f50e87e15bfbfa241b28b77b7c71b2a41e252``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Twenty-seventh deliberate re-pin (260921-ICR-L31, at base ``4c000b11``, 2026-09-23) -- one new case
module's two consumer rows.** ``260921-ICR-L31`` (requirement ``ICR-R31@v1``) registers no artifact and
no contract of its own: its eighteen comparison-bound family-context cases live in the ordinary unit
module ``mcp/tests/test_review_family_context.py``, which builds the leaf enclosure the review leaves
use through the existing endpoint fixture and authors every family revision, membership and moved
member through the public store operations rather than constructing a payload. It is therefore a
source-derived consumer of **two** rows, each derived from the census's own finding --
``missing=['mcp/tests/test_review_family_context.py']`` with ``unsupported=[]`` on both:
``mcp/tests/diff_scope_test_support.py`` (the two snapshots, their recorded identities and the two real
trees the enclosure is built over) and ``mcp/tests/read_scope_test_support.py`` (the authorship
envelope and the recorded family/membership topology the composition reads). Both
``consumer_scope = "exact"`` rows gained that one path each, and the module's own lane row was added
to ``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered module at
collection. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``caf1b9ee2b0a0b82356ee65339e77328e6f632d1848a9ba05fc4a0fc88831f78`` (the Twenty-sixth) to
``b1c38ed84fb847bc76ddd0497319cfad7059d35f3fdf78cd425169d4f49cbd48``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Twenty-sixth deliberate re-pin (260921-ICR-L22's fix round 3, at base ``e605822e``, 2026-09-23) --
the read-side cases' own module, and the rail that forced it.** Fix round 3's H1 case pushed
``mcp/tests/test_review_sync_rebinding.py`` to 1263 lines, which would have made it a **new** offender in
the whole-tree ≥1200 census (26 → 27) -- the exact condition this master's ruling refuses. The fix is
extraction at the seam the production owners already have: the five *read-side* cases (what the shipped
``read_knowledge_review`` renders about a sync that moved its inputs, plus the movement validator's own
refusals) moved into a new ``mcp/tests/test_review_sync_movement_read.py`` (356 lines), which imports the
enclosure fixture from its sibling ``mcp/tests/test_review_sync_rebinding.py`` (now 942 lines) rather than
duplicating it -- the same sibling-fixture pattern ``test_review_sync_binding``'s own consumers already
use. The census derived the delta exactly: the new module is a consumer of these four rows, each reporting
``missing=['mcp/tests/test_review_sync_movement_read.py']`` with ``unsupported=[]``, so each gained that one
path, and its lane row was added to ``mcp/tests/test-evidence-lanes.toml`` in the integration lane beside
its sibling. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c`` (the Twenty-fifth) to
``d07c2f9d456b0f658228c91aecb2a1f3da8d13e2b6575b6b40b0b0d2ca165f6b``, measured with ``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate.
The proof's own artifact delta remains exactly empty.

**Twenty-fifth deliberate re-pin (260921-ICR-L22's fix round 1, at base ``e605822e``, 2026-09-23) --
the module split's own closure change.** The F5 ruling moved this leaf's four managed-sync rebinding
cases, their fixture and their module-level helpers out of ``mcp/tests/test_worktree_sync.py`` into a
new ``mcp/tests/test_review_sync_rebinding.py``, so the case module that already owned the
managed-sync evidence is back in the soft band and the whole-tree hard-rail census returns to its base
count. The census derived the delta exactly: ``mcp/tests/test_sync_parked_candidate.py`` and
``mcp/tests/test_worktree_sync.py`` are **no longer** consumers of the endpoint and scope support rows
(their only route to them was the case module's own import, which moved), and the new module is a
consumer of four rows -- ``mcp/tests/fixtures/repository_profiles/node/package-lock.json``,
``mcp/tests/merge_case_test_support.py``, ``mcp/tests/diff_scope_test_support.py`` and
``mcp/tests/read_scope_test_support.py`` -- reporting ``missing=['mcp/tests/test_review_sync_rebinding.py']``
with ``unsupported=['mcp/tests/test_sync_parked_candidate.py', 'mcp/tests/test_worktree_sync.py']`` on the
three that had carried them. Each row was corrected to exactly the derived set: three lost the two
paths, ``merge_case_test_support.py`` kept both and gained the new one. The new module's lane row was
added to ``mcp/tests/test-evidence-lanes.toml`` in the integration lane beside its sibling. **Nothing was
registered, no row was removed and no artifact's identity moved**, so the population stays at
**sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``8ce61c57b8660011b1cb81f0c2f21bd493ce3e2295359228d8804f0b8192b9d1`` (the Twenty-fourth) to
``7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c``, measured with ``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate.
The proof's own artifact delta remains exactly empty.

**Twenty-fourth deliberate re-pin (260921-ICR-L22, at base ``e605822e``, 2026-09-23) -- the managed-sync
case module's three newly reached consumer rows.** ``260921-ICR-L22`` (requirement ``ICR-R22@v1``)
registers no artifact and no contract of its own: its three managed-sync rebinding cases live in the
module the packet's own anchor names, ``mcp/tests/test_worktree_sync.py``, which already owns the
managed merge/conflict production-operation evidence. What moved is that module's **consumer
closure**: the new cases build the leaf enclosure the review leaves use, so the module now imports the
shared endpoint fixture and the authorship envelope, and the census derives three further consumers
from that -- ``mcp/tests/fixtures/repository_profiles/node/package-lock.json``,
``mcp/tests/diff_scope_test_support.py`` and ``mcp/tests/read_scope_test_support.py`` -- each reporting
``missing=['mcp/tests/test_sync_parked_candidate.py', 'mcp/tests/test_worktree_sync.py']`` with
``unsupported=[]``. ``test_sync_parked_candidate.py`` reaches the same rows transitively because it
already imports ``SyncFixture`` from the case module, which is a fact of the existing tree rather than
of this leaf. All three ``consumer_scope = "exact"`` rows gained those two paths, in the position each
row already lists the sibling case modules. **Nothing was registered, no row was removed, no artifact's
identity moved and no lane row changed**, so the population stays at **sixteen contracts / sixty-six
artifacts**, and the catalog is re-pinned from
``81a518b567b654375bd1ea4e5af5395cefe3a3464563308c17f31278e276134c`` (the Twenty-third) to
``8ce61c57b8660011b1cb81f0c2f21bd493ce3e2295359228d8804f0b8192b9d1``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Twenty-third deliberate re-pin (260921-ICR-L12, at base ``870701b4``, 2026-09-22) -- one new case
module's three consumer rows.** ``260921-ICR-L12`` (requirement ``ICR-R12@v1``) registers no artifact
and no contract of its own: its six committed-leaf cases live in the ordinary unit module
``mcp/tests/test_historical_committed_leaf_review.py``, which builds on the existing
``mcp/tests/test_knowledge_review_source_endpoints.py`` enclosure fixture -- freezing a real
comparison through R11's freeze owner, removing the real worktree group, serving the same request
through the production HTTP composition in this process and in a **fresh interpreter**, and driving
the entry, subject, expansion and refusal routes -- rather than introducing a second enclosure or a
second support module. It is therefore a source-derived consumer of **three** rows, each derived from
the census's own finding -- ``missing=['mcp/tests/test_historical_committed_leaf_review.py']`` with
``unsupported=[]`` on all three: ``mcp/tests/diff_scope_test_support.py`` (the two snapshots and the
two real trees the enclosure is built over), ``mcp/tests/read_scope_test_support.py`` (the repository,
the recorded anchors and the tracked paths) and the Node ``package-lock.json`` fixture, which the
census's own propagation rule reaches through the support modules those rows already name. All three
``consumer_scope = "exact"`` rows gained that one path each, and the module's own lane row was added
to ``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered module at
collection. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``4d874fb54c627549db098177caa477040e09ca63c7ae2cba2246bd1390a338e0`` (the Twenty-second) to
``4ab067e360c7807c3051ad71058c09058225f1e9155e7111da6e95c73cac0258``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Twenty-second deliberate re-pin (260921-ICR-L26, at base ``2edad477``, 2026-09-22) -- one new case
module's four consumer rows.** ``260921-ICR-L26`` (requirement ``ICR-R26@v1``) registers no artifact
and no contract of its own: its ten subject-and-comparison-isolation cases live in the ordinary unit
module ``mcp/tests/test_knowledge_review_subject_isolation.py``, which builds one real leaf enclosure
with an external memory half through the existing endpoint fixture, produces every supplied record
class through the operation that owns it -- four assessments through the curator-coherence
publication, a detection run holding two signals, an evidence claim and a verification observation
through the application evidence writer, and an authored open question through the admitted candidate
batch -- and then drives the **production composition** (``cli.dashboard.serving_collaborators``), once
before and once after a source movement that makes the same assessment a previous generation's
record. It is therefore a source-derived consumer of **four** rows, each derived from the census's own
finding -- ``missing=['mcp/tests/test_knowledge_review_subject_isolation.py']`` with ``unsupported=[]``
on all four: ``mcp/tests/curator_coherence_test_support.py`` (the recorded task topology the
publication binds), ``mcp/tests/fixtures/repository_profiles/node/package-lock.json``,
``mcp/tests/diff_scope_test_support.py`` (the two snapshots, their recorded identities and the two
real trees the enclosure is built over) and ``mcp/tests/read_scope_test_support.py`` (the authorship
envelope and the recorded family/membership topology the classification's recorded relationships are
read from). All four ``consumer_scope = "exact"`` rows gained that one path each, and the module's own
lane row was added to ``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an
unregistered module at collection. **Nothing was registered, no row was removed and no artifact's
identity moved**, so the population stays at **sixteen contracts / sixty-six artifacts**, and the
catalog is re-pinned from ``b4d4a7f9e9500ba10f763dacdc916c0b77054113fc388f3096869fbedeef92cd`` (the
Twenty-first) to ``4d874fb54c627549db098177caa477040e09ca63c7ae2cba2246bd1390a338e0``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact
delta remains exactly empty.

**Twenty-first deliberate re-pin (260921-ICR-L10, at base ``dcf35a0e``, 2026-09-22) -- one new case
module's three consumer rows.** ``260921-ICR-L10`` (requirement ``ICR-R10@v1``) registers no artifact
and no contract of its own: its seven bounded-pagination cases live in the ordinary unit module
``mcp/tests/test_review_bounded_pagination.py``, which builds its two dataset sizes by adding
realization claims to the shared diff fixture's candidate through the public store operation, authors
enough review-matrix records to page that collection through the admitted candidate batch, and drives
the real composition and the real HTTP transport rather than a constructed payload. It is therefore a
source-derived consumer of **three** rows, each derived from the census's own finding --
``missing=['mcp/tests/test_review_bounded_pagination.py']`` with ``unsupported=[]`` on all three:
``mcp/tests/diff_scope_test_support.py`` (the two snapshots and the two real trees the added
realizations extend), ``mcp/tests/read_scope_test_support.py`` (the authorship envelope and the
recorded topology the candidate is copied from) and ``mcp/tests/candidate_batch_test_support.py``
(the admitted destination and the resolved context the matrix's own records are written through).
All three ``consumer_scope = "exact"`` rows gained that one path each, and the module's own lane row
was added to ``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered
module at collection. **Nothing was registered, no row was removed and no artifact's identity
moved**, so the population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is
re-pinned from ``d2d6dc6ad331b6913b54262786be68f63158bb5b0ba91ab9351706ab1806748d`` (the Twentieth) to
``b4d4a7f9e9500ba10f763dacdc916c0b77054113fc388f3096869fbedeef92cd``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Twentieth deliberate re-pin (260921-ICR-L8's second fix round, at base ``02957762``,
2026-09-22) -- a third case module's two consumer rows.** The second fix round added the authored-line
cases the ruling requires (a relationship recorded at an intermediate descendant, a split whose
successors each record one, the split-only sentence, and a membership re-recorded onto an unselected
successor family revision). They live in the ordinary unit module
``mcp/tests/test_knowledge_review_relationship_line.py``, which authors its fixtures through the same
public store operations and the same existing endpoint enclosure, importing the shared builders from
its sibling case modules rather than duplicating them. It is therefore a source-derived consumer of
the same **two** rows and both ``consumer_scope = "exact"`` rows gained that one path each, derived
from the census's own finding --
``missing=['mcp/tests/test_knowledge_review_relationship_line.py']`` with ``unsupported=[]`` on both.
Its lane row was added to ``mcp/tests/test-evidence-lanes.toml``. **Nothing was registered, no row was
removed and no artifact's identity moved**, so the population stays at **sixteen contracts /
sixty-six artifacts**, and the catalog is re-pinned from
``b1ed783810951a0af6bc142507f18b4f612bcc52835dd37ac6bf68a9fac0ea42`` (the Nineteenth) to
``d2d6dc6ad331b6913b54262786be68f63158bb5b0ba91ab9351706ab1806748d``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Nineteenth deliberate re-pin (260921-ICR-L8's fix round, at base ``02957762``, 2026-09-22) --
one further case module's two consumer rows.** The leaf's fix round added the reach cases the ruling
requires (a member identity's relationship read at its own established head, a non-unique head stated
unresolved with its reason, a re-recorded citation the withdrawn rows must name rather than deny, and
member-wise family pairing). They live in the ordinary unit module
``mcp/tests/test_knowledge_review_relationship_reach.py``, which authors its fixtures through the same
public store operations and the same existing endpoint enclosure, importing the shared builders from
the sibling case module rather than duplicating them. It is therefore a source-derived consumer of the
same **two** rows as its sibling and both ``consumer_scope = "exact"`` rows gained that one path each,
derived from the census's own finding --
``missing=['mcp/tests/test_knowledge_review_relationship_reach.py']`` with ``unsupported=[]`` on both.
Its lane row was added to ``mcp/tests/test-evidence-lanes.toml``. **Nothing was registered, no row was
removed and no artifact's identity moved**, so the population stays at **sixteen contracts /
sixty-six artifacts**, and the catalog is re-pinned from
``c676b0a07480beeaf39e0093bddf15e30db4f4c9b3e0014095a96cd214548dbc`` (the leaf's first measurement) to
``b1ed783810951a0af6bc142507f18b4f612bcc52835dd37ac6bf68a9fac0ea42``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Eighteenth deliberate re-pin (260921-ICR-L8, at base ``02957762``, 2026-09-22) -- one new case
module's two consumer rows.** ``260921-ICR-L8`` (requirement ``ICR-R08@v1``) registers no artifact
and no contract of its own: its twelve relationship-movement cases live in the ordinary unit module
``mcp/tests/test_knowledge_review_relationship_movement.py``, which curates the shared diff fixture's
two datasets through the public store operations inside the existing
``mcp/tests/test_knowledge_review_source_endpoints.py`` enclosure -- rather than introducing a second
enclosure or a second two-snapshot topology. It is therefore a source-derived consumer of **two**
rows, each derived from the census's own finding --
``missing=['mcp/tests/test_knowledge_review_relationship_movement.py']`` with ``unsupported=[]`` on
both: ``mcp/tests/diff_scope_test_support.py`` (the candidate's own transitions, its
``_claim_row_digest`` removal helper and the two real trees the enclosure is built over) and
``mcp/tests/read_scope_test_support.py`` (the repository, the recorded paths and the family topology
the moved associations are read from). Both ``consumer_scope = "exact"`` rows gained that one path
each, and the module's own lane row was added to ``mcp/tests/test-evidence-lanes.toml``, which pins
no digest and refuses an unregistered module at collection. **Nothing was registered, no row was
removed and no artifact's identity moved**, so the population stays at **sixteen contracts /
sixty-six artifacts**, and the catalog is re-pinned from
``8d60a34cf56a9740e234823658312c8b6f5ae4e958344fe8ba2ea152b8ff6891`` (L9's measurement above) to
``c676b0a07480beeaf39e0093bddf15e30db4f4c9b3e0014095a96cd214548dbc``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Seventeenth deliberate re-pin (260921-ICR-L9, at base ``f141d164``, 2026-09-22) -- one new case
module's two consumer rows.** ``260921-ICR-L9`` (requirement ``ICR-R09@v1``) registers no artifact and
no contract of its own: its ten catalogue cases live in the ordinary unit module
``mcp/tests/test_review_subject_catalogue.py``, which builds its retired/added/unselectable
populations through the public store operations over copies of the shared diff fixture's datasets
beside its two real Git trees rather than introducing a second topology. It is therefore a
source-derived consumer of **two** rows, each derived from the census's own finding --
``missing=['mcp/tests/test_review_subject_catalogue.py']`` with ``unsupported=[]`` on both:
``mcp/tests/diff_scope_test_support.py`` (the two snapshots and the two real trees the populations
are copied from) and ``mcp/tests/read_scope_test_support.py`` (the authorship and the
applicability/conditions/exclusions constants the authored rows are built with). Both
``consumer_scope = "exact"`` rows gained that one path each. The module's own lane row was added to
``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered module at
collection. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``3e9105c2116422debaa01c295294fc0c714288f701ee5902e6552884b64a52d8`` (L14's measurement above) to
``8d60a34cf56a9740e234823658312c8b6f5ae4e958344fe8ba2ea152b8ff6891``, measured with ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Sixteenth deliberate re-pin (260921-ICR-L14, synced onto the landed ICR-L3 re-pin, 2026-09-21) -- one
new case module's four consumer rows, merged with the Fifteenth's two.** ``260921-ICR-L14`` (requirement
``ICR-R14@v1``) registers no artifact and no contract of its own: its eight record-collection cases live in
the ordinary unit module ``mcp/tests/test_knowledge_review_evidence_channels.py``, which builds on the
existing ``mcp/tests/test_knowledge_review_source_endpoints.py`` enclosure fixture and the existing
``mcp/tests/curator_coherence_test_support.py`` publication helper rather than introducing a second
fixture of either. It is therefore a source-derived consumer of **four** rows, each derived from the
census's own finding -- ``missing=['mcp/tests/test_knowledge_review_evidence_channels.py']`` with
``unsupported=[]`` on every one: ``mcp/tests/curator_coherence_test_support.py`` (the topology and
publication helpers the assessment channel is produced through), ``mcp/tests/diff_scope_test_support.py``
(the two real trees and the candidate bytes the enclosure is built over),
``mcp/tests/read_scope_test_support.py`` (the repository, the anchors and the datasets the comparison is
between) and the Node ``package-lock.json`` fixture, which the census's own propagation rule reaches
through the support modules those rows already name. The module's own lane row was added to
``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered module at
collection. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**. Because this branch was synced onto the
landed Fifteenth re-pin (``dc6e3808…``), the merged catalog carries both leaves' consumer rows, so the pin
is re-measured on the merged candidate rather than copied from either side: ``sha256sum
mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta remains
exactly empty.

**Fifteenth deliberate re-pin (260921-ICR-L3, at base ``d80a0513``, 2026-09-21) -- one new case
module's two consumer rows.** ``260921-ICR-L3`` (requirement ``ICR-R03@v1``) registers no artifact and
no contract of its own: its twenty source-content cases live in the ordinary unit module
``mcp/tests/test_knowledge_review_source_content.py``, which extends the existing
``mcp/tests/test_knowledge_review_source_endpoints.py`` enclosure fixture (adding the content classes
the packet names to its worktree) rather than introducing a second enclosure. It is therefore a
source-derived consumer of both ``mcp/tests/diff_scope_test_support.py`` (the fixture's two real trees
and its ``_git`` observation helper) and ``mcp/tests/read_scope_test_support.py`` (the repository, the
tracked paths whose bytes the cases read back), and both ``consumer_scope = "exact"`` rows gained that
one path each -- derived from the census's own finding,
``missing=['mcp/tests/test_knowledge_review_source_content.py']`` with ``unsupported=[]`` on both rows.
The module's own lane row was added to ``mcp/tests/test-evidence-lanes.toml``, which pins no digest and
refuses an unregistered module at collection. **Nothing was registered, no row was removed and no
artifact's identity moved**, so the population stays at **sixteen contracts / sixty-six artifacts**,
and the catalog is re-pinned from ``aedb2636844faf41ca63b7099e6c62bcaf888720e7e7dd78e203e3a1c9e061ff``
(L11's measurement above) to ``dc6e380867302d7e2fff89f8c36549c85528ed4dafa28b4510981b573f09739b``,
measured with ``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own
artifact delta remains exactly empty.

**Fourteenth deliberate re-pin (260921-ICR-L11, at base ``9043a82e``, 2026-09-21) -- one new case
module's two consumer rows.** ``260921-ICR-L11`` (requirement ``ICR-R11@v1``) registers no artifact and
no contract of its own: its nine durable-comparison-generation cases live in the ordinary unit module
``mcp/tests/test_knowledge_review_comparison_generation.py``, which builds on the existing
``mcp/tests/test_knowledge_review_source_endpoints.py`` enclosure fixture rather than introducing a
second one -- so it is a source-derived consumer of both ``mcp/tests/diff_scope_test_support.py`` (the
candidate content its cases read back out of the retained tree and the two real trees the enclosure is
built over) and ``mcp/tests/read_scope_test_support.py`` (the repository, the recorded anchors and the
datasets the comparison is between). Both ``consumer_scope = "exact"`` rows gained that one path each,
derived from the census's own finding -- ``missing=['mcp/tests/test_knowledge_review_comparison_generation.py']``
with ``unsupported=[]`` on both rows. The module's own lane row was added to
``mcp/tests/test-evidence-lanes.toml``, which pins no digest and refuses an unregistered module at
collection. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts**, and the catalog is re-pinned from
``7920a0f9f6134d3849e60685ad8a9424cdc95e8209631a03b889cf7d79e05281`` (L6's measurement above) to
``aedb2636844faf41ca63b7099e6c62bcaf888720e7e7dd78e203e3a1c9e061ff``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact delta
remains exactly empty.

**Thirteenth deliberate re-pin (260921-ICR-L6, at the sync onto ``71a4433e``, 2026-09-21) -- the union
of two consumer repairs, re-measured on the merged file.** ``260921-ICR-L6`` drives the real review
composition over two real read-scope snapshots, so its case module consumes
``mcp/tests/read_scope_test_support.py``, whose ``consumer_scope = "exact"`` requires its
``consumers`` list to equal the source-derived consumer set:
``mcp/tests/test_knowledge_review_one_sided_statements.py`` is the one path this leaf added there,
beside the ``mcp/tests/test_read_ar_files.py`` entry L18's consequence repair below had already
landed for the L19 import. The two are kept as a *set* and not concatenated: the census compares
consumer sets, so a second copy of one path would pass silently, which is why the resolved row names
each path exactly once. **Nothing was registered, no row was removed and no artifact's identity
moved**, so the population stays at **sixteen contracts / sixty-six artifacts**, and the catalog --
L18's landed bytes plus this leaf's one path -- is re-pinned from
``3f91773d5533139aa24df0e75ddafcecbc68cd9797b7663084f8aca567176c57`` (L18's own measurement on
``71a4433e``) to ``7920a0f9f6134d3849e60685ad8a9424cdc95e8209631a03b889cf7d79e05281``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the resolved candidate. The proof's own artifact
delta remains exactly empty.

**Consequence repair (260921-ICR-L18, 2026-09-21) -- attributed to the `260921-ICR-L19` landing, and NOT
ICR-R17/R18 work.** The sync that landed ``260921-ICR-L19`` rewrote ``mcp/tests/test_read_ar_files.py``
and added ``from read_scope_test_support import (build_read_scope_fixture, ...)`` to it, which makes that
module a source-derived consumer of the pre-existing ``consumer_scope = "exact"`` row
``mcp/tests/read_scope_test_support.py`` -- a row L19's landing did not update, so
``test_repository_inputs_reach_their_supported_consumers`` reported
``missing=['mcp/tests/test_read_ar_files.py']`` on the merged tip. The entry was derived from the census
itself (``RepositoryDependencyFacts.observed_test_consumers``: 14 derived paths against 13 declared, with
``unsupported=[]``) and appended, which closes the proof with **no artifact registered, no row added, no
identity moved** and the population unchanged at **sixteen contracts / sixty-six artifacts**. Two
pre-existing facts of that row are deliberately untouched: it lists ``mcp/tests/test_knowledge_curator_ingest.py``
twice (tolerated because the proof compares sets), and its list is otherwise sorted, so the new entry sits
last. The catalog is re-pinned to
``3f91773d5533139aa24df0e75ddafcecbc68cd9797b7663084f8aca567176c57``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the delivered candidate.

``260921-ICR-L18`` (requirement ``ICR-R18@v1``) registered no artifact and no contract of its own: its
cases live in two ordinary unit modules -- ``mcp/tests/test_knowledge_ingest_comparison_generation.py``
(the successful journey) and ``mcp/tests/test_knowledge_ingest_failure_windows.py`` (every way a
placement refuses or loses a leg, extracted there so neither module approaches the file-size rail) --
which import the existing ingest-list fixture module and ``snapshot_lifecycle_test_support`` rather than
introducing a third support module. Its catalog change is therefore a *consumer* change only, and
exactly two ``consumer_scope = "exact"`` rows gained both paths: ``mcp/tests/snapshot_lifecycle_test_support.py``
(it builds the real datasets the cases publish and rebase) and the Node ``package-lock.json`` fixture,
which the census's own propagation rule reaches through the support module those rows already name.
**Nothing was registered, no row was removed and no artifact's identity moved**, so the population stays
at **sixteen contracts / sixty-six artifacts** and the catalog was re-pinned to
``4fb2bc3f2da65134e428631f0e964f4f712e6655f4816934e2a93a4cb8e32046``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on this leaf's own candidate **before** the consequence
repair recorded above (that value is historical; the pin in force is ``3f91773d...``). The proof's own
artifact delta remains exactly empty.

``260921-ICR-L1`` (requirement ``ICR-R01@v1``) registered no artifact and no contract of its own -- its
source-endpoint cases build on the existing ``diff_scope_test_support`` and ``read_scope_test_support``
fixtures rather than introducing a third one -- so its catalog change is a *consumer* change only: both
of those support modules' consumer lists gained
``mcp/tests/test_knowledge_review_source_endpoints.py``. The counts stay sixteen and sixty-six, and the
digest this value replaces was ``f0cb5fec...``.

``260921-ICR-L20`` (requirement ``ICR-R20@v1``) registered no artifact and no contract of its own: its
ordinary-publication cases live in one new ordinary unit module,
``mcp/tests/test_knowledge_ingest_publication_route.py``, which drives the shipped
``agents-remember knowledge-ingest`` entry point over a production-shaped enclosure (a real external
memory repository with a real memory worktree) and imports the existing ingest-list fixture module
rather than introducing a second support module. Its catalog change is therefore a *consumer* change
only, and exactly two ``consumer_scope = "exact"`` rows gained the path:
``mcp/tests/snapshot_lifecycle_test_support.py``, which the new module reaches through the fixture
module it imports, and the Node ``package-lock.json`` fixture, which the census's own propagation rule
reaches through the CLI import chain those rows already name -- the same two rows L18's own module
moved. **Nothing was registered, no row was removed and no artifact's identity moved**, so the
population stays at **sixteen contracts / sixty-six artifacts** and the catalog is re-pinned to
``aedb2636844faf41ca63b7099e6c62bcaf888720e7e7dd78e203e3a1c9e061ff``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the delivered candidate. The digest this value
replaces was ``3f91773d…``. The proof's own artifact delta remains exactly empty.

``260918-TSIP-L10`` registered the module this leaf's own red base exposed as ungoverned:
``mcp/tests/tool_refusal_census_support.py``, introduced by ``260918-TSIP-L6`` (the refusal-census leaf
of this master) and carrying no catalog row, so ``load_evidence_inventory``'s coverage finding read
*"governed evidence has no lifecycle metadata: ['mcp/tests/tool_refusal_census_support.py']"* and the
two integration cases below -- ``test_repository_inputs_reach_their_supported_consumers`` and
``test_production_proof_adds_no_governed_evidence_artifact`` -- were red from L6's landing onward.
Neither the unit lane nor any leaf's measurement could see it: ``addopts`` deselects integration, and
the master's frozen-tip check is the combined lane. **A lane nobody runs cannot be reviewed**, which
is why this registration is a leaf's work rather than a note. The registration follows the sibling
support-module pattern exactly (``migration_census_test_support.py``): a new ``[[contract]]`` row
(``tool-refusal-census-cases``) whose ``evidence_node`` is the census's own partition case, and one
``[[artifact]]`` row of ``kind = "shared-support"``, ``category = "unit-regression"`` (both consumers
are in that lane), ``fidelity = "local-composition"`` (the census drives production entry points over
a disposable coordination root through a real MCP client session) and
``replacement_contract = "contract:tool-refusal-census-cases"``. The consumer list is not invented:
it is ``RepositoryDependencyFacts.observed_test_consumers`` for that path --
``mcp/tests/test_tool_response_conformance.py`` and ``mcp/tests/test_response_address_binding.py``,
the second through its ``from test_tool_refusal_conformance import refusal_axes`` import.
**Registering the module moved one more list**: the census's import walk from that path reaches
``agents_remember.models.tools.public_roster`` and, through it, the portable profile runtime whose
input is the node lockfile, so ``mcp/tests/fixtures/repository_profiles/node/package-lock.json``
gained those same two consumers -- a consumer change, not a new artifact, exactly as the sibling
leaves' registrations each moved theirs. Contract count **15 -> 16**, artifact count **65 -> 66**;
the digest this value replaces was ``6ec7eb0d…``.

**Re-derived at the sync.** This master then merged onto the advanced sprint line (``756c47b3``),
whose own catalog value read fifteen contracts and sixty-five artifacts at ``825abfd6…``. Both
lines changed this file and git merged it cleanly, so **neither side's pin described the merged
bytes**: the merged catalog carries this master's ``tool-refusal-census-cases`` contract and its
shared-support artifact *and* the sibling line's consumer re-registrations, and its digest is
neither side's. The three values were re-derived from the merged file itself
(``sha256sum mcp/tests/evidence-lifecycle.toml``) rather than chosen from a side. The counts happen
to equal this line's because the sibling line's changes were all consumer lists; the digest does
not.

The value this line carried before is the catalog after ``260915-KS``'s L28 leaf repaired the missing-consumer
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
**Eleventh deliberate re-pin (260915-KS-L40, 2026-09-21) -- consumer rows only.** ``260915-KS-L40``
closes CYCLE-02's remainder at the public boundary: the sync response carries the merge engine's own
conflict diagnosis, and one authored decision settles it. Its acceptance case is added to the
existing integration module ``mcp/tests/test_worktree_sync.py`` rather than to a new one -- the
integration lane sits at its exact ceiling of 400 collected cases -- and that module now builds one
scenario at an older recorded schema generation, so it consumes the registered ``shared-support``
fixture ``mcp/tests/generation_test_support.py`` whose ``consumer_scope = "exact"`` requires its
``consumers`` list to equal the source-derived consumer set. Two paths were appended there: the
module itself and ``mcp/tests/test_sync_parked_candidate.py``, which imports it and is therefore a
consumer by the census's own propagation rule. **Nothing was registered, no row was removed and no
artifact's identity moved**, so the population stays at **fifteen contracts / sixty-five artifacts**
and the catalog is re-pinned to
``c499cbcc266088f35bd6fdde655f00579431591f02bba5c4859b72f767f65f06``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the delivered candidate. The proof's own artifact
delta remains exactly empty.
**Twelfth deliberate re-pin (series sync of 260915-KS onto 260713, 2026-09-21) -- the merged union.**
The master was synced onto its super line and both sides had moved this catalog: this master's own
L40 consumer rows and the super line's ``260918-TSIP-L10`` registration
(``tool-refusal-census-cases`` with its ``shared-support`` artifact) both belong on the merged file.
The populations are therefore the super line's **sixteen contracts / sixty-six artifacts**, and the
bytes are neither side's own: re-pinned to
``f0cb5fec700019923c5028d4e2547e70cecab97582870f128e70e4f8f960a1e4``, measured with
``sha256sum mcp/tests/evidence-lifecycle.toml`` on the merged candidate. Neither side's pin was
correct for the union, which is why this value had to be measured rather than inherited. The proof's
own artifact delta remains exactly empty.
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
