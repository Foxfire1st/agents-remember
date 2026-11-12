"""Focused behavioral check for the consolidated role-instruction corpus.
The corpus this protects is the canonical root ``skills/l-01-agent-lifecycles/`` tree: a thin
router, the shared ``core/`` blocks, one self-contained file per role, the ``operations/``
blocks, and the JSON composition manifest that routes a role to its sources. These cases assert
the properties a consumer depends on:

* every role in the registry has exactly one readable role source;
* role sources remain well-formed, self-contained instructions without operator knobs;
* the manifest resolves to files that exist, for every role and every operation;
* the role registry is exactly the eight roles, and native role routing remains separate from the
  retained ambient launcher metadata;
* the manifest shares no prose (it is a metadata plane);
* EVERY relative path the corpus cites resolves, so a consolidation cannot leave a dangling
  reference;
* a manifest that points at a missing source is reported rather than silently accepted; and
* retired instruction sentences are gone from the canonical tree and every declared package copy,
  while the compact curator role and operation preserve the current complete-quality policy.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.tasks import write_task_doc
from agents_remember_test_support.testing import task_document_instruction_contracts
from agents_remember_test_support.testing.instruction_consistency import (
    assert_public_command_reference,
    command_reference,
    misplace_command_documentation,
    public_commands,
)
from curation_statement_surface import fragment_one_surfaces
from test_task_document import _config, _doc

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"
LIFECYCLE_ROOT = SKILLS_ROOT / "l-01-agent-lifecycles"
MANIFEST_PATH = LIFECYCLE_ROOT / "composition-manifest.json"

ROLE_ORDER = (
    "architect",
    "orchestrator",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "investigator",
    "bootstrap",
)

#: The retired machine-readable block. Knobs are settings, not role-file content.
MACHINE_SECTION = "## Knobs, Tool Surface, And Dispatch Authority"

# The nine frozen operations: the architecture's eight, plus the one deliberate extension
# (`bootstrap`) authored with the bootstrap role and tested in
# test_role_capsule_admission.py.
OPERATION_KEYS = (
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
    "bootstrap",
)

BACKTICKED_PATH = re.compile(r"`([^`\n]+)`")

# A *path-shaped* backticked token: it either contains a separator, or it is a single-segment file
# name with a source-language extension. An inline code token (``AR_SPAWN_ROLE``, ``satisfied``,
# ``reviewMode=baseline``) is not a path and must never be reported as one.
PATH_TOKEN = re.compile(
    r"^[a-z0-9_][a-z0-9_.-]*(?:/[a-z0-9_][a-z0-9_.-]*)+$"
    r"|^[a-z0-9_][a-z0-9_.-]*\.(?:md|py|json|toml|ya?ml|ts|tsx|sh|cfg|txt|ini)$"
)


def cited_paths(text: str) -> list[str]:
    """Every path-shaped backticked token in a document, in order of appearance."""

    return [
        token.strip() for token in BACKTICKED_PATH.findall(text) if PATH_TOKEN.match(token.strip())
    ]


# Locations a shipped instruction may cite as a corpus path, resolved from the lifecycle root.
CORPUS_LOCATIONS = (
    "core/",
    "operations/",
    "roles/",
    "templates/",
    "criteria/",
    "reference/",
    "lenses.md",
    "composition-manifest.json",
    "SKILL.md",
)

# A role learns its duties from its own source. The Manager owns the shared
# questions-received policy; the Architect and Orchestrator refer to that home.
SANCTIONED_SIBLING_REFERENCES = {
    "architect": ("roles/manager.md",),
    # F03 one-home repair: the Manager role owns the single questions-received
    # policy; the Architect and Orchestrator carry only a referral to it.
    "orchestrator": ("roles/manager.md",),
}

# A coordination-root anchor names a file in the coordination tree this checkout does not contain
# (``system/tools.md``, ``tasks/AGENTS.md``, ``notes/reports/…``, ``runtime/…``). Its existence is
# owned by the coordinator, so the link check states that limit rather than overclaiming it.
COORDINATION_ROOT_RE = re.compile(r"^(system|tasks|notes|runtime|worktrees|memory-repos)/")

# Context-dependent symbolic names: their resident tree is task-local or route-local, so no
# repo-relative path exists to resolve. The allowance is explicit and exact rather than inferred from
# whether a path happens to resolve somewhere.
SYMBOLIC_FILE_NAMES = frozenset(
    {
        "overview.md",  # per-route onboarding pillar under the resolved memory root
        "overview.index.json",  # generated route index beside that pillar
        "series-contract.md",  # the leaf's enclosure contract under the coordination tasks tree
        "task.json",  # a JSON-primary task document under the coordination tasks tree
        "cli.py",  # the console entry point, discovered at runtime by the code owner
    }
)

# Tokens that look path-shaped but are not repository locations: a branch name (``ar/<branch>``), a
# task-artifact directory inside the coordination tree (``design/architecture.md``), and a generic
# placeholder (``section/anchor``). They are neither resolved nor reported.
NON_LOCATION_RE = re.compile(
    r"^(?:ar|feature|fix|release)/"
    r"|^(?:design|requirements|enclosures|reviews|worktrees)/"
    r"|^(?:section|path|file|row|id|node)/[a-z]+$"
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _resolve_sources(root: Path, manifest: dict) -> list[str]:
    """Resolve every source the manifest names, and report the ones that do not exist.

    This is the resolver under test: it is deliberately the only place that knows how the manifest
    references files, so a manifest entry pointing at a missing source is *reported* rather than
    silently accepted.
    """

    problems: list[str] = []
    problems += _check_core(root, manifest)
    problems += _check_operations(root, manifest)
    problems += _check_roles(root, manifest)
    problems += _check_launcher_and_references(root, manifest)
    return problems


def _check_core(root: Path, manifest: dict) -> list[str]:
    problems = []
    for block in manifest.get("core", {}).values():
        if not (root / block["source"]).is_file():
            problems.append(f"core source missing: {block['source']}")
    return problems


def _check_operations(root: Path, manifest: dict) -> list[str]:
    problems: list[str] = []
    declared = set(manifest.get("operations", {}))
    if declared != set(OPERATION_KEYS):
        problems.append(
            "manifest operation vocabulary mismatch: "
            f"declared={sorted(declared)} expected={sorted(OPERATION_KEYS)}"
        )

    known_roles = manifest.get("roles", {})
    for key, block in manifest.get("operations", {}).items():
        if not (root / block["source"]).is_file():
            problems.append(f"operation '{key}' source missing: {block['source']}")
        unknown = [role for role in block.get("applies_to_roles", []) if role not in known_roles]
        if unknown:
            problems.append(f"operation '{key}' applies to unknown roles: {unknown}")
    return problems


def _check_roles(root: Path, manifest: dict) -> list[str]:
    problems = _check_role_order(manifest)
    declared_operations = set(manifest.get("operations", {}))
    core_blocks = manifest.get("core", {})
    for role, entry in manifest.get("roles", {}).items():
        problems += _check_one_role(root, role, entry, core_blocks, declared_operations)
    return problems


def _check_role_order(manifest: dict) -> list[str]:
    role_order = manifest.get("role_order", [])
    if sorted(role_order) == sorted(manifest.get("roles", {})):
        return []
    return [
        "role_order and roles disagree: "
        f"order={sorted(role_order)} roles={sorted(manifest.get('roles', {}))}"
    ]


def _check_one_role(
    root: Path,
    role: str,
    entry: dict,
    core_blocks: dict,
    declared_operations: set[str],
) -> list[str]:
    problems: list[str] = []
    if not (root / entry["file"]).is_file():
        problems.append(f"role '{role}' file missing: {entry['file']}")
    for core_key in entry.get("core", []):
        if core_key not in core_blocks:
            problems.append(f"role '{role}' names unknown core block: {core_key}")
    for operation in entry.get("operations", []):
        if operation not in declared_operations:
            problems.append(f"role '{role}' names unknown operation: {operation}")
    for template in entry.get("templates", []):
        if not (root / "templates" / template).is_file():
            problems.append(f"role '{role}' names missing template: {template}")
    for criterion in entry.get("criteria", []):
        if not (root / "criteria" / criterion).is_file():
            problems.append(f"role '{role}' names missing criteria catalog: {criterion}")
    return problems


def _check_launcher_and_references(root: Path, manifest: dict) -> list[str]:
    problems: list[str] = []
    launcher = manifest.get("launcher", {})
    if launcher.get("is_role") is not False:
        problems.append("launcher must declare is_role=false")
    source = launcher.get("instruction_source")
    if source and not (root / source).is_file():
        problems.append(f"launcher instruction source missing: {source}")

    for key, reference in manifest.get("references", {}).items():
        target = root / reference["source"]
        if not (target.is_file() or target.is_dir()):
            problems.append(f"reference '{key}' source missing: {reference['source']}")
    return problems


def test_manifest_resolves_every_role_and_operation_source(tmp_path: Path) -> None:
    """Every source the manifest names exists, for every role and every operation."""

    assert MANIFEST_PATH.is_file(), f"composition manifest missing: {MANIFEST_PATH}"
    manifest = _manifest()

    assert _resolve_sources(LIFECYCLE_ROOT, manifest) == []

    # The registry is exactly the eight roles; ambient launch metadata remains separate from native
    # role routing and is still exercised by the existing AR launcher tests.
    assert list(manifest["role_order"]) == list(ROLE_ORDER)
    assert "launcher" not in manifest["roles"]
    conditions = {condition["id"] for condition in manifest["routing_conditions"]}
    assert conditions == {"supplied-native-role-binding", "manual-taskless-projects-role"}
    assert manifest["launcher"]["routing_condition"] == "ambient-launcher"

    # The router delegates source selection to the manifest rather than naming every role file.
    router = (LIFECYCLE_ROOT / manifest["entry_router"].split("/")[-1]).read_text(encoding="utf-8")
    assert "composition-manifest.json" in router
    assert "one applicable operation" in router

    # The manifest is routing metadata: it must not carry instruction prose.
    assert manifest_prose_lines(MANIFEST_PATH) == [], (
        f"manifest appears to copy instruction prose: {manifest_prose_lines(MANIFEST_PATH)}"
    )


def manifest_copy_indicators(text: str) -> list[str]:
    """Describe the ways a manifest value stops being routing metadata and becomes a copied payload.

    **What this predicate can and cannot do.** A consolidated manifest legitimately carries one-line
    descriptive values — role seat labels, block purposes, routing conditions — and those are the same
    *shape* as much instruction prose, so no length or punctuation heuristic separates them honestly.
    What a predicate can detect without guessing is the way copying actually happens:

    * a **multi-line** string literal (a paragraph pasted into one value);
    * a value that embeds a **fenced block** or a **heading** (a copied section);
    * a backticked corpus path **that does not resolve** (a copied citation whose target is gone).

    Everything else — "this value restates a rule from ``core/loop.md``" — is a semantic judgement and
    is **not** claimed here; the reviewer's requirement-coverage read is what covers that. This is the
    honest mechanical floor, not a proof that no prose was copied.
    """

    reasons: list[str] = []
    # A copied paragraph in JSON arrives newline-escaped, so decode the escapes before judging the
    # value; a raw newline inside a string literal is itself a structural defect worth reporting.
    decoded = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    if "\n" in decoded:
        reasons.append("multi-line value (a paragraph pasted into one value)")
    if "```" in decoded:
        reasons.append("embedded fenced block")
    if re.search(r"(?m)^\s*#{1,6}\s", decoded):
        reasons.append("embedded markdown heading")
    # A JSON value is not markdown, so its paths are not backticked: scan the value's own words.
    for raw in text.replace(",", " ").split():
        token = raw.strip("\"'()[]").split("#", 1)[0].rstrip("/").rstrip(".,:;")
        if not PATH_TOKEN.match(token) or COORDINATION_ROOT_RE.search(token):
            continue
        if token.rsplit("/", 1)[-1] in SYMBOLIC_FILE_NAMES or NON_LOCATION_RE.match(token):
            continue
        if (LIFECYCLE_ROOT / token).exists() or (REPOSITORY_ROOT / token).exists():
            continue
        if token.startswith(CORPUS_LOCATIONS):
            reasons.append(f"unresolvable corpus citation: {token}")
        elif token.startswith(("docs/", "scripts/", "mcp/", "dashboard/", "runtime/")):
            reasons.append(f"unresolvable repository citation: {token}")
    return reasons


def _iter_json_strings(text: str):
    """Yield ``(value, line_number)`` for every string value in a JSON document.

    A line-oriented scan cannot see a copied paragraph: JSON escapes the newlines, so a pasted
    paragraph is a valid single-line value and a long one may still wrap. This tokenizes the document
    instead, so every string value is judged whole and attributed to the line it starts on. The walk is
    deliberately one pass and container-agnostic — it only needs strings and their line numbers.
    """

    decoder = json.JSONDecoder()
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            value, end = decoder.raw_decode(text, index)
            yield value, text.count("\n", 0, index) + 1
            index = end
            continue
        if char in "[{":
            # Skip structural punctuation; decode nested values as they are reached.
            index += 1
            continue
        index += 1


def manifest_prose_lines(path: Path) -> list[str]:
    """Every string value of a manifest file that shows a copy indicator, with its line number."""

    text = path.read_text(encoding="utf-8")
    flagged: list[str] = []
    for value, line in _iter_json_strings(text):
        reasons = manifest_copy_indicators(value)
        if reasons:
            flagged.append(f"line {line}: {value[:70]!r}  <- {'; '.join(reasons)}")
    return flagged


def test_manifest_carries_routing_metadata_not_copied_payloads(tmp_path: Path) -> None:
    """The manifest is header-free, single-line metadata whose declared paths resolve."""

    # The real artifact passes the mechanical floor, and every value is a single line.
    assert manifest_prose_lines(MANIFEST_PATH) == []
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    for value_line in manifest_text.splitlines():
        assert value_line == value_line.rstrip(), "manifest carries trailing whitespace"
    assert '"""' not in manifest_text

    # Seeded mutations: four ways the manifest could start carrying a copied payload, written as
    # VALID JSON — a copy-paste that keeps the file parseable is the realistic case, and it is the one
    # the previous `$`-based predicate could not see.
    seeded = tmp_path / "composition-manifest.json"
    seeded_payload = {
        "authority": "skills/l-01-agent-lifecycles/SKILL.md (thin router)",
        "roles": {
            "worker": {
                # a paragraph pasted into a single value
                "source": "core/acceptance.md\nRead the brief fully, then the leaf spec it names, "
                "and implement exactly the leaf plan",
                # a copied section heading
                "purpose": "## 5 — Stop And Escalation Cases",
                # a copied fenced block
                "criteria": "```yaml\nrole: worker\ncore: common\n```",
                # a copied citation whose target does not exist
                "template": "templates/worker-brief-that-does-not-exist.md",
                # a citation that does exist must NOT be flagged (negative control)
                "realTemplate": "templates/manager-brief.md",
            }
        },
    }
    seeded.write_text(json.dumps(seeded_payload, indent=2), encoding="utf-8")
    json.loads(seeded.read_text(encoding="utf-8"))  # the seeded copy is realistic: it parses

    flagged = manifest_prose_lines(seeded)
    assert len(flagged) == 4, flagged
    assert any("multi-line value" in line for line in flagged), flagged
    assert any("embedded markdown heading" in line for line in flagged), flagged
    assert any("embedded fenced block" in line for line in flagged), flagged
    assert any("unresolvable corpus citation" in line for line in flagged), flagged

    # A healthy line of the same shape is not flagged.
    assert manifest_copy_indicators('"source": "operations/orientation.md"') == []
    assert manifest_copy_indicators('"schema": "ar-role-capsule-composition/v1"') == []
    assert (
        manifest_copy_indicators('"seat": "provider-degradation investigator; report first"') == []
    )


def test_every_role_source_is_concise_and_well_formed() -> None:
    """Every role is a concise, parseable source with its own duties and no inherited core.

    The compact role-capsule ruling supersedes the old fixed section order and minimum-length
    convention. This check protects actual source identity and self-containment while role-specific
    contract tests cover the duties that matter.
    """

    manifest = _manifest()
    assert list(manifest["roles"]) == list(ROLE_ORDER), (
        "role registry order changed; the router, the registry table and this test must agree"
    )

    for role, entry in manifest["roles"].items():
        path = LIFECYCLE_ROOT / entry["file"]
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Frontmatter with the canonical skill-scoped name and a description.
        assert lines[0] == "---", f"{path} does not open a YAML frontmatter block"
        end = lines.index("---", 1)
        frontmatter = lines[1:end]
        assert f"name: l-01-agent-lifecycles-role-{role}" in frontmatter, (
            f"{path} frontmatter must declare the canonical name"
        )
        assert any(line.startswith("description: ") for line in frontmatter), (
            f"{path} frontmatter must declare a description"
        )
        assert any(line.startswith("# ") for line in lines[end + 1 :]), (
            f"{path} must have a readable role heading"
        )
        assert any(line.startswith("## ") for line in lines[end + 1 :]), (
            f"{path} must organize its role-specific instructions"
        )

        # The retired inheritance line and the retired machine block are gone.
        assert "**Inherits:**" not in text, (
            f"{path} still declares inherited sources; a capsule composes no shared Core block"
        )
        assert MACHINE_SECTION not in text, f"{path} still carries the operator knob block"
        # The defect is a **table row** that presents a knob as this seat's configuration. A prose
        # mention that says the knobs are settings and not the seat's to set is the corrected
        # statement the cut leaves in place, so it is not reported.
        for knob in (
            "harness",
            "model",
            "effort",
            "launchArgs",
            "sessionCommands",
            "promptKeywords",
        ):
            assert f"| {knob} " not in text, f"{path} still carries a `{knob}` knob-table row"
        assert "orchestration.rolesPerLevel" not in text, (
            f"{path} still documents its own settings override keys"
        )

        # A role is self-contained: naming a sibling role file is the amalgamation the ruling
        # forbids, except for the explicit policy referrals below.
        allowed = SANCTIONED_SIBLING_REFERENCES.get(role, ())
        for other in ROLE_ORDER:
            if other == role:
                continue
            needle = f"roles/{other}.md"
            if needle in text:
                assert needle in allowed, (
                    f"{path} references {needle}, which is not a sanctioned cross-reference"
                )


def test_manifest_reports_a_missing_source_instead_of_accepting_it(tmp_path: Path) -> None:
    """A manifest entry pointing at a missing source is reported, not silently accepted."""

    staged = tmp_path / "l-01-agent-lifecycles"
    shutil.copytree(LIFECYCLE_ROOT, staged)
    manifest = _manifest()

    # (a) a missing role file
    broken = copy.deepcopy(manifest)
    broken["roles"]["worker"]["file"] = "roles/worker-renamed.md"
    problems = _resolve_sources(staged, broken)
    assert any("role 'worker' file missing" in problem for problem in problems), problems

    # (b) a missing operation source
    broken = copy.deepcopy(manifest)
    broken["operations"]["review"]["source"] = "operations/review-moved.md"
    problems = _resolve_sources(staged, broken)
    assert any("operation 'review' source missing" in problem for problem in problems), problems

    # (c) a role naming an operation that is not in the frozen vocabulary
    broken = copy.deepcopy(manifest)
    broken["roles"]["curator"]["operations"] = ["orientation", "curation", "ceremony"]
    problems = _resolve_sources(staged, broken)
    assert any("unknown operation: ceremony" in problem for problem in problems), problems

    # (d) a role naming a core block that does not exist
    broken = copy.deepcopy(manifest)
    broken["roles"]["manager"]["core"] = ["authority", "tradition"]
    problems = _resolve_sources(staged, broken)
    assert any("unknown core block: tradition" in problem for problem in problems), problems

    # (e) a role naming a criteria catalog that does not exist
    broken = copy.deepcopy(manifest)
    broken["roles"]["reviewer"]["criteria"] = ["code-seam.md", "vibes.md"]
    problems = _resolve_sources(staged, broken)
    assert any("missing criteria catalog: vibes.md" in problem for problem in problems), problems

    # (f) the healthy staged copy still resolves, so the negatives above are not false alarms
    assert _resolve_sources(staged, manifest) == []


def test_every_relative_path_the_corpus_cites_resolves() -> None:
    """No shipped instruction may cite a corpus, repository, or sibling-skill path that is missing.

    Three citing forms are resolved, and each one is checked for real:

    * **corpus-relative** — ``core/authority.md``, ``operations/review.md``: resolved against the
      lifecycle root.
    * **file-relative** — ``../core/loop.md``: resolved against the citing file's own directory, and
      against the skills root for a reference such as ``../core/every-role.md``.
    * **repository-relative** — ``mcp/src/agents_remember/controlplane/gate_policy.py``: resolved
      against the repository root.

    A **coordination-root** anchor (``system/tools.md``, ``tasks/AGENTS.md``, ``notes/reports/…``,
    ``runtime/…``) is deliberately **not** checked: those files live in the coordination tree this
    checkout does not contain, so their existence is owned by the coordinator rather than by this
    corpus. The previous version of this test claimed to resolve that class while skipping it; this
    docstring now states the limit instead of overclaiming it.
    """

    assert unresolved_references(LIFECYCLE_ROOT) == [], (
        "unresolved relative references:\n" + "\n".join(unresolved_references(LIFECYCLE_ROOT))
    )


def unresolved_references(lifecycle_root: Path) -> list[str]:
    """Every relative path the corpus cites that resolves under none of its roots.

    Resolution roots, in order: the lifecycle root, the citing file's own directory, the skills root,
    the repository root, and any corpus directory the citing line names (so the
    "consolidated into `core/`: `authority.md`, `invariants.md`, …" form resolves). A
    **coordination-root** anchor and a **context-dependent symbolic name** are skipped and listed in
    `SYMBOLIC_FILE_NAMES` — their resident tree is task-local or route-local, which this checkout
    cannot contain. Bootstrap also names the bare `memory.md` ledger explicitly at the memory
    repository root; that contextual citation is external, not a checkout-relative path.
    Everything else is reported.
    """

    skills_root = lifecycle_root.parent
    unresolved: list[str] = []
    for path in sorted(lifecycle_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        # Track the nearest corpus directory named anywhere in the file — not only on the current
        # line — so a bare file name in a following table or list resolves against the directory the
        # prose named (for example `criteria/` above the reviewer's catalog-binding table).
        context_dirs: list[str] = []
        for line in text.splitlines():
            named = [
                directory
                for directory in CORPUS_LOCATIONS
                if directory.endswith("/") and f"`{directory}" in line
            ]
            context_dirs = named or context_dirs
            for candidate in cited_paths(line):
                token = candidate.strip()
                if any(marker in token for marker in ("<", ">", "{", "}", " ", "*", "|", "…")):
                    continue
                token = token.split("#", 1)[0].rstrip("/")
                if not token or COORDINATION_ROOT_RE.search(token):
                    continue
                # Bootstrap explicitly locates this derived ledger in the resolved memory tree.
                # A path prefix or a different location claim must still resolve normally.
                if token.rsplit("/", 1)[-1] in SYMBOLIC_FILE_NAMES or (
                    path.relative_to(lifecycle_root).as_posix() == "operations/bootstrap.md"
                    and token == "memory.md"
                    and "at the memory repository root" in line
                ):
                    continue
                if NON_LOCATION_RE.match(token):
                    continue
                roots = [lifecycle_root, path.parent, skills_root, REPOSITORY_ROOT]
                roots += [lifecycle_root / directory for directory in context_dirs]
                if not any((root / token).exists() for root in roots):
                    unresolved.append(f"{path.relative_to(skills_root)} → {token}")
    return unresolved


def test_the_curator_hand_off_list_contract_has_one_home_its_consequences_declared() -> None:
    """The shape the producers emit for the curator exists exactly once, and its seats name it.

    The rule this protects is the hand-off contract, not a file name. A requirement-shaped item the
    builder or the reviewer produces must reach the curator as data in one agreed shape; when the
    contract has no home, or a seat that emits or consumes the list does not name it, each leaf
    re-invents the shape and the curator ingests a different document every time. The consequences
    asserted here are the four that make it a contract rather than formatting -- the field ownership
    split, the JSON shape, the co-resolution rule and the no-paraphrase rule -- plus the registry
    wiring, because an unregistered contract is one no seat is routed to.
    """

    relative = "templates/curator-handoff-list.md"
    contract = LIFECYCLE_ROOT / relative
    assert contract.is_file(), f"the curator hand-off list contract is missing: {relative}"

    text = contract.read_text(encoding="utf-8")
    for section in (
        "## The entry",
        "## Shape",
        "## Rule 1 — co-resolution",
        "## Rule 2 — carry producer data verbatim",
    ):
        assert section in text, f"{relative} no longer carries '{section}'"
    # F12: every in-scope copy of the hand-off contract requires the current onboarding row
    # and narrows counted sidecar edits to references.
    ingestion_relative = "templates/curator-handoff-ingestion.md"
    ingestion = LIFECYCLE_ROOT / ingestion_relative
    assert ingestion.is_file(), f"the curator ingestion detail is missing: {ingestion_relative}"
    copies = [ingestion] + [
        REPOSITORY_ROOT / root / "l-01-agent-lifecycles" / ingestion_relative
        for root in GENERATED_SKILL_COPIES
    ]
    for copy_path in copies:
        copy_text = " ".join(copy_path.read_text(encoding="utf-8").split())
        assert "its current `onboarding:<path>` row" in copy_text, copy_path
        assert "a counted change of its card alone never answers" in copy_text, copy_path
        assert "its sidecar's `references` beyond mechanical anchor fields" in copy_text, copy_path
    # Card/overview row schema and current-source binding live in the hand-off contract;
    # card-authoring guidance points at the same current dispositions.
    card_detail = "reference/curator/card-formats-and-citations.md"
    for surface in ("skills", *GENERATED_SKILL_COPIES):
        lifecycle = REPOSITORY_ROOT / surface / "l-01-agent-lifecycles"
        card_text = " ".join((lifecycle / card_detail).read_text(encoding="utf-8").split())
        assert "subject `onboarding:<path>` or `onboarding:<route>/overview`" in card_text
        assert (
            "Use `updated` with a counted card change, or `no_impact` with a reason." in card_text
        )
        handoff_text = " ".join(
            (lifecycle / ingestion_relative).read_text(encoding="utf-8").split()
        )
        for clause in (
            "`onboarding:<source path>` or `onboarding:<route>/overview`",
            "Its disposition is `updated` when the card or overview was brought up to date",
            "the writer refuses an `updated` row without one",
            "or `no_impact` when the reviewed file needs no change",
            "with each file's blob at the code candidate (`sources`)",
            "When a covered file changes again after the row was written, the item reopens: name the row again.",
        ):
            assert clause in handoff_text, (surface, clause)
            with pytest.raises(AssertionError, match=re.escape(clause)):
                assert clause in handoff_text.replace(clause, ""), clause
    assert "target: []" in text, f"{relative} no longer encodes a ruling that applies nowhere"
    assert unresolved_references(LIFECYCLE_ROOT) == [], "the contract cites a path that is gone"

    # Every seat on either side of the interface declares the contract it hands over or ingests.
    roles = _manifest()["roles"]
    for role in ("worker", "reviewer", "orchestrator", "curator"):
        assert "curator-handoff-list.md" in roles[role]["templates"], (
            f"role '{role}' emits or consumes the curator hand-off list but does not declare it"
        )
        role_text = (LIFECYCLE_ROOT / roles[role]["file"]).read_text(encoding="utf-8")
        # Q-P5-b split: producers name the list contract; the Curator names the ingestion
        # sections it consumes (and declares that template too).
        contract = (
            "templates/curator-handoff-ingestion.md"
            if role == "curator"
            else "templates/curator-handoff-list.md"
        )
        assert contract in role_text, (
            f"{roles[role]['file']} does not name the hand-off list contract it depends on"
        )
    assert "curator-handoff-ingestion.md" in roles["curator"]["templates"]


def test_link_check_reports_a_repo_relative_anchor_pointed_at_nothing(tmp_path: Path) -> None:
    """The link check catches a repository-relative anchor whose file does not exist.

    Seed a healthy backticked reference, then break it. This check exercises backticked paths,
    not Markdown link syntax. Missing paths must fail in shared and role files; a deleted target
    must also fail.
    """

    staged = tmp_path / "skills"
    shutil.copytree(SKILLS_ROOT, staged)
    staged_root = staged / "l-01-agent-lifecycles"
    healthy = staged_root / "roles" / "reviewer.md"
    original = healthy.read_text(encoding="utf-8")

    # The healthy corpus resolves.
    assert unresolved_references(staged_root) == []

    anchor = "mcp/src/agents_remember/controlplane/gate_policy.py"
    assert (REPOSITORY_ROOT / anchor).is_file(), "the healthy anchor must be a real file"
    healthy.write_text(original + f"\nSee `{anchor}`.\n", encoding="utf-8")
    assert unresolved_references(staged_root) == [], (
        "a real repository-relative anchor must resolve"
    )

    # Seed one mutation: the same anchor, no longer a resolvable repository-relative path.
    healthy.write_text(original + "\nSee `controlplane/gate_policy.py`.\n", encoding="utf-8")
    problems = unresolved_references(staged_root)
    assert any("controlplane/gate_policy.py" in problem for problem in problems), problems

    # A coordination-root anchor is skipped by design, not reported as a false alarm.
    healthy.write_text(original + "\nSee `system/tools.md`.\n", encoding="utf-8")
    assert unresolved_references(staged_root) == []

    # The ledger citation is external only when Bootstrap locates the bare name in memory.
    bootstrap = staged_root / "operations/bootstrap.md"
    bootstrap_text = bootstrap.read_text(encoding="utf-8")
    location = "at the memory repository root"
    assert "`memory.md`" in bootstrap_text and location in bootstrap_text
    bootstrap.write_text(bootstrap_text.replace(location, "beside this instruction file"))
    assert any("bootstrap.md → memory.md" in item for item in unresolved_references(staged_root))
    bootstrap.write_text(bootstrap_text.replace("`memory.md`", "`missing/memory.md`"))
    assert any(
        "bootstrap.md → missing/memory.md" in item for item in unresolved_references(staged_root)
    )
    bootstrap.write_text(bootstrap_text, encoding="utf-8")
    assert unresolved_references(staged_root) == []

    # Missing backticked paths in shared instructions have no exception.
    shared = staged_root / "core/acceptance.md"
    shared_text = shared.read_text(encoding="utf-8")
    anchor = "roles/investigator.md"
    assert (staged_root / anchor).is_file()
    shared.write_text(shared_text + f"\nSee `{anchor}`.\n", encoding="utf-8")
    assert unresolved_references(staged_root) == []
    missing = "roles/investigator-missing.md"
    shared.write_text(shared_text + f"\nSee `{missing}`.\n", encoding="utf-8")
    assert any(missing in item for item in unresolved_references(staged_root))
    shared.write_text(shared_text + f"\nSee `{anchor}`.\n", encoding="utf-8")
    replacement = staged_root / anchor
    replacement_text = replacement.read_text(encoding="utf-8")
    replacement.unlink()
    assert any(anchor in item for item in unresolved_references(staged_root))
    replacement.write_text(replacement_text, encoding="utf-8")
    assert unresolved_references(staged_root) == []
    shared.write_text(shared_text, encoding="utf-8")
    healthy.write_text(original + f"\nSee `{missing}`.\n", encoding="utf-8")
    assert any(missing in item for item in unresolved_references(staged_root))
    healthy.write_text(original, encoding="utf-8")
    assert unresolved_references(staged_root) == []


# --------------------------------------------------------------------------------------
# Complete curation policy and retired instruction wording
# --------------------------------------------------------------------------------------
#
# The registry of retired sentences, the registry of the retired loop-gate field pairing, and the
# readers for both live in `agents_remember_test_support.testing.curation_doctrine`; these cases are
# the assertions over the real tree. Retired coordination defaults and curation deferrals are
# rejected on the exact surfaces that shipped them. Complete curation always includes the full
# memory-quality operation; a named diagnostic never substitutes for it.
#
# The loop-gate registry is the same shape of defect in a different material: the memory-quality
# result publishes the raw checklist status as `qualityChecklistStatus` and the combined status as
# `checklistStatus`, so a carrier instructing a curator to iterate repairs until
# `checklistStatus=ready-for-closeout` names a condition the repair loop does not reach. That one is
# a fact about the shipped tool, verified against its controller rather than ruled as doctrine.
#
# WHAT THIS DOES NOT COVER (stated, not implied). Neither registry is a semantic check: a corpus
# that denied the rule, or restated the wrong gate, in fresh vocabulary the registries have never
# seen would pass. Scope is the canonical `skills/**` tree plus its declared package copies
# `scripts/sync-skills.py` owns, and for the loop gate the reference documents named in
# `LOOP_GATE_DOCUMENTS`; the copies' byte-identity with their originals is that generator's own
# contract (`scripts/sync-skills.py --check`), while what is asserted here is that every copy
# carries the rule at all, so a stale copy cannot ship.

from agents_remember_test_support.testing.curation_doctrine import (
    CURATION_DOCTRINE_SURFACES,
    CURATION_POLICY_STATEMENTS,
    GENERATED_SKILL_COPIES,
    RETIRED_CURATION_STATEMENTS,
    RETIRED_LOOP_GATE_FIELD_PAIRING,
    doctrine_files,
    gates_the_retired_loop_gate_pairing,
    missing_curation_policy_statements,
    missing_loop_gate_statements,
    normalize_statement,
    retired_curation_findings,
    retired_loop_gate_findings,
    retired_statement_findings,
)


class CurationPolicyTests:
    """Current curation policy and retired instruction wording on their declared surfaces."""

    def test_no_shipped_surface_still_carries_a_retired_curation_statement(self) -> None:
        findings = retired_curation_findings(REPOSITORY_ROOT)
        assert findings == [], (
            "a shipped instruction surface still carries retired wording:\n  "
            + "\n  ".join(findings)
        )

        # The second, independent registry: the loop gate's field names are facts about the shipped
        # tool, so a carrier that gates the repair loop on the combined status field with the raw
        # status value sends its seat back to repair work that is already finished.
        gate_findings = retired_loop_gate_findings(REPOSITORY_ROOT)
        assert gate_findings == [], (
            "a shipped instruction surface still gates the repair loop on the wrong field:\n  "
            + "\n  ".join(gate_findings)
        )

    def test_the_census_ranges_over_the_canonical_tree_and_every_generated_copy(self) -> None:
        """A census that quietly examined nothing is the one way this check can lie."""

        canonical = doctrine_files(REPOSITORY_ROOT, "skills")
        assert len(canonical) > len(CURATION_POLICY_STATEMENTS), (
            "the retired-wording scan examined no more surfaces than the current policy set"
        )
        for copy_root in GENERATED_SKILL_COPIES:
            assert doctrine_files(REPOSITORY_ROOT, copy_root), f"{copy_root} produced no surfaces"
        assert set(CURATION_DOCTRINE_SURFACES) == {"skills", *GENERATED_SKILL_COPIES}

        # The loop-gate census ranges over those same declared surfaces plus the reference documents
        # outside the skill tree that state the loop, so its coverage boundary is asserted rather
        # than assumed: a document that dropped a corrected field name is reported here, and one
        # that moved away is reported as missing, instead of silently leaving the census.
        gate_documents = missing_loop_gate_statements(REPOSITORY_ROOT)
        assert gate_documents == [], (
            "declared loop-gate documents no longer state the corrected gate:\n  "
            + "\n  ".join(gate_documents)
        )

    def test_each_compact_curation_source_states_its_complete_quality_policy(self) -> None:
        missing: list[str] = []
        for relative, statements in CURATION_POLICY_STATEMENTS.items():
            path = REPOSITORY_ROOT / relative
            assert path.is_file(), f"declared curation-doctrine source is missing: {relative}"
            reading = normalize_statement(path.read_text(encoding="utf-8"))
            missing.extend(
                f"{relative} -> {statement}"
                for statement in statements
                if normalize_statement(statement) not in reading
            )
        assert missing == [], (
            "a compact curation source is missing its current complete-quality policy:\n  "
            + "\n  ".join(missing)
        )

    def test_every_generated_copy_carries_the_policy_its_canonical_original_states(
        self, tmp_path: Path
    ) -> None:
        """A stale package copy serves the old policy to its reader."""

        missing: list[str] = []
        for copy_root in GENERATED_SKILL_COPIES:
            root = REPOSITORY_ROOT / copy_root
            assert root.is_dir(), f"generated skill copy is missing: {copy_root}"
            missing.extend(missing_curation_policy_statements(root, copy_root))
        assert missing == [], (
            "generated skill copies do not carry the complete-curation policy:\n  "
            + "\n  ".join(missing)
        )

        # Seed from the actual canonical bodies; removing any required clause must be visible.
        for relative in CURATION_POLICY_STATEMENTS:
            target = tmp_path / relative.removeprefix("skills/")
            target.parent.mkdir(parents=True, exist_ok=True)
            original = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            target.write_text(original, encoding="utf-8")
        assert missing_curation_policy_statements(tmp_path, "seeded") == []
        for relative, statements in CURATION_POLICY_STATEMENTS.items():
            target = tmp_path / relative.removeprefix("skills/")
            original = target.read_text(encoding="utf-8")
            normalized = normalize_statement(original)
            for statement in statements:
                clause = normalize_statement(statement)
                assert clause in normalized, (relative, statement)
                mutant = normalized.replace(clause, "")
                assert mutant != normalized
                target.write_text(mutant, encoding="utf-8")
                assert f"seeded: {relative}" in missing_curation_policy_statements(
                    tmp_path, "seeded"
                ), (relative, statement)
            target.write_text(original, encoding="utf-8")
        assert missing_curation_policy_statements(tmp_path, "seeded") == []
        role_path = tmp_path / "l-01-agent-lifecycles/roles/curator.md"
        original_role = role_path.read_text(encoding="utf-8")
        for old, new in (
            ("Before running curation checks, read", "Read"),
            ("#run-the-complete-curation-checks", "#normal-workflow"),
            ("Before validating coherence, read", "Read"),
            ("#authority-gates", "#normal-workflow"),
        ):
            mutant = original_role.replace(old, new)
            assert mutant != original_role, old
            role_path.write_text(mutant, encoding="utf-8")
            assert "seeded: skills/l-01-agent-lifecycles/roles/curator.md" in (
                missing_curation_policy_statements(tmp_path, "seeded")
            )
        role_path.write_text(original_role, encoding="utf-8")
        assert missing_curation_policy_statements(tmp_path, "seeded") == []


class CurationGuardTeethTests:
    """The guard can fail. A guard that cannot fail is not evidence.

    The shipped side is READ from the corpus rather than pasted here, so the two halves cannot
    agree with each other by construction; only the seed is synthetic. Each retired statement is
    asserted to be reported on the surface that shipped it, and the shipped corpus is asserted
    clean first -- which is what makes "re-inserting it fails" a measurement over this tree
    rather than an assertion about a fragment that never matched anything.
    """

    def test_reinserting_each_retired_statement_is_detected_on_its_own_surface(
        self, tmp_path: Path
    ) -> None:
        """For every registered sentence: the seed is reported, and the SHIPPED text is clean.

        The shipped side is read from the corpus, so no side of this pair is a restatement of
        the other. A registry row whose seed is not reported is a row that guards nothing, which
        is the failure this case exists to make impossible.
        """

        assert retired_curation_findings(REPOSITORY_ROOT) == [], (
            "the corpus must read clean before a seed is meaningful"
        )
        authorized_absent_sources = frozenset(
            {
                "skills/l-01-agent-lifecycles/roles/designer.md",
                "skills/l-01-agent-lifecycles/roles/strategist.md",
                "skills/l-01-agent-lifecycles/templates/worker-brief.md",
            }
        )
        for source in authorized_absent_sources:
            assert not (REPOSITORY_ROOT / source).exists(), source
        for retired in RETIRED_CURATION_STATEMENTS:
            reading = normalize_statement(retired.statement)
            for source in retired.sources:
                relative = source.removeprefix("skills/")
                # Every historical seed still exercises its original source-confined reader.
                assert retired_statement_findings(reading, relative), (
                    f"{source} would not report the retired statement it shipped: {retired.probe!r}"
                )
                if source in authorized_absent_sources:
                    assert not (REPOSITORY_ROOT / source).exists(), source
                else:
                    shipped = normalize_statement(
                        (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
                    )
                    assert not retired_statement_findings(shipped, relative), (
                        f"{source} still reads as carrying the retired statement: {retired.probe!r}"
                    )

        # The loop-gate pairing is retired as a *field pairing*, not as a sentence: the carriers
        # phrase the gate differently from each other, so the pairing is what has to be absent. The
        # shipped half is the corpus and the seed is written into a staged copy of it, so again
        # neither half is a restatement of the other.
        assert retired_loop_gate_findings(REPOSITORY_ROOT) == [], (
            "the corpus must read clean of the retired loop-gate pairing before a seed is meaningful"
        )
        staged = tmp_path / "skills"
        shutil.copytree(SKILLS_ROOT, staged)
        seeded = staged / "l-01-agent-lifecycles" / "roles" / "curator.md"
        original = seeded.read_text(encoding="utf-8")
        mutated = f"{original}\n\n{RETIRED_LOOP_GATE_FIELD_PAIRING}\n"
        assert mutated != original
        # Both sides of the matcher, on two texts: the corrected carrier must not read as the
        # pairing while the one-token seed must, so the reader is not vacuously reporting.
        assert not gates_the_retired_loop_gate_pairing(original), (
            "the corrected carrier text reads as the retired pairing"
        )
        assert gates_the_retired_loop_gate_pairing(mutated), (
            "the seeded carrier text does not read as the retired pairing"
        )
        seeded.write_text(mutated, encoding="utf-8")
        gate_findings = retired_loop_gate_findings(tmp_path)
        assert any(
            finding.startswith("skills/l-01-agent-lifecycles/roles/curator.md:")
            for finding in gate_findings
        ), gate_findings

    def test_one_seeded_old_sentence_is_observed_to_fail_the_guard(self) -> None:
        """The seed is applied to the REAL file's text, and the guard's reader must red it."""

        relative = "l-01-agent-lifecycles/roles/curator.md"
        shipped = (REPOSITORY_ROOT / "skills" / relative).read_text(encoding="utf-8")
        assert retired_statement_findings(normalize_statement(shipped), relative) == []

        seed = (
            "\n- **a narrow `memory_quality_check`** or **`curator_coherence`** only on an "
            "explicit developer request\n  for a named affected check or curator certification.\n"
        )
        findings = retired_statement_findings(normalize_statement(shipped + seed), relative)
        assert len(findings) == 1, findings
        assert "fragment 3" in findings[0], findings

        # The seed is removed again and the same reader is green: the guard reports the
        # difference, not a constant.
        assert retired_statement_findings(normalize_statement(shipped), relative) == []

    def test_the_preserved_developer_request_doctrine_does_not_trip_the_guard(self) -> None:
        """Full code quality and full tests stay developer-requested and must read clean.

        The replacement sentences KEEP this clause deliberately, so a guard that fired on the
        phrase would fire on the shipped answer rather than on the defect.
        """

        preserved = (
            "They do not automatically run code-quality checks or full test suites, and full "
            "code quality, full tests, and independent review run only after an explicit "
            "developer request."
        )
        for retired in RETIRED_CURATION_STATEMENTS:
            for source in retired.sources:
                assert not retired_statement_findings(
                    normalize_statement(preserved), source.removeprefix("skills/")
                ), f"{retired.probe!r} fires on doctrine this ruling preserves"

    def test_a_statement_is_reported_only_on_a_surface_that_shipped_it(self) -> None:
        """The registry is a census of surfaces, not a single-file check."""

        statement, declared, undeclared = fragment_one_surfaces()
        assert retired_statement_findings(normalize_statement(statement), declared), (
            "the curator brief must be a declared surface for fragment 1"
        )
        assert not retired_statement_findings(normalize_statement(statement), undeclared), (
            "an undeclared surface must not be reported for a statement it never shipped"
        )


def test_new_series_guidance_requires_json_primary_master_before_dependencies() -> None:
    task_document_instruction_contracts.assert_contract(REPOSITORY_ROOT, GENERATED_SKILL_COPIES)


def test_native_new_master_then_indexed_leaf_dependencies(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = tmp_path / "tasks" / "agents-remember" / "legacy"

    def call(operation, target_slug=None, **fields):
        target = TaskDocTarget(repo_id="agents-remember", task_name=root.name, slug=target_slug)
        return task_doc_tool(config, target, operation=operation, edit=TaskDocEdit(fields=fields))

    base = dict(repo="agents-remember", createdAt="2026-01-01T00:00")
    write_task_doc(
        root, _doc(**base, id="A", slug="01_a", title="A", kind="subTask", master="task.md")
    )
    (root / "task.md").write_text("# Task: Legacy Markdown master\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(TaskDocError, match="master"):
        call("set_field", "01_a", waitsFor=["B"])
    assert {path: path.read_bytes() for path in before} == before
    root = root.with_name("series")
    call("create", **base, id="series", slug="series", title="Series", kind="master")
    call("create", **base, id="A", slug="01_a", title="A", kind="subTask", master="task.md")
    call("create", **base, id="B", slug="02_b", title="B", kind="subTask", master="task.md")
    rows = json.loads((root / "task.json").read_bytes())["subTasks"]
    assert [row["number"] for row in rows] == ["A", "B"]
    assert call("set_field", "01_a", waitsFor=["B"])["ok"]
    assert json.loads((root / "01_a.json").read_bytes())["waitsFor"] == ["B"]
    assert "## Order" in (root / "task.md").read_text(encoding="utf-8")


def _require_clauses(text: str, clauses: tuple[str, ...]) -> None:
    for clause in clauses:
        assert clause in text, clause


def _architect_passages(text: str) -> str:
    return " ".join(
        " ".join(
            re.findall(r"(?s)<!-- ar:roles architect -->\s*(.*?)(?=<!-- ar:roles |\Z)", text)
        ).split()
    )


def _require_originating_leaf_home(text: str) -> None:
    """Keep the Architect action and the complete all-role owning rule together."""
    pointer = (
        "Before assigning an issue to another open leaf, follow "
        "[Complete the plan and preserve its boundaries]"
        "(#complete-the-plan-and-preserve-its-boundaries) for the originating-leaf default "
        "and charter exception."
    )
    assert pointer in _architect_passages(text)
    common = "\n".join(re.findall(r"(?s)<!-- ar:roles all -->\s*(.*?)(?=<!-- ar:roles |\Z)", text))
    section = re.search(
        r"(?ms)^## Complete the plan and preserve its boundaries\n(.*?)(?=^## |\Z)", common
    )
    assert section is not None, "originating-leaf rule needs its common owner"
    assert (
        "Keep issues in their originating leaf unless another open leaf's charter already covers them."
        in " ".join(section[1].split())
    )


def _assert_originating_leaf_home_controls(text: str) -> None:
    _require_originating_leaf_home(text)
    for old, new in (
        ("Before assigning an issue to another open leaf", "Read"),
        ("#complete-the-plan-and-preserve-its-boundaries)", "#cross-packet-alignment-sweep)"),
        ("Keep issues in their originating leaf", ""),
        ("unless another open leaf's charter already covers them.", ""),
        ("## Complete the plan and preserve its boundaries", "## Unrelated action"),
        ("<!-- ar:roles all -->", "<!-- ar:roles curator -->"),
    ):
        mutant = text.replace(old, new)
        assert mutant != text, old
        with pytest.raises(AssertionError):
            _require_originating_leaf_home(mutant)


def test_packet_alignment_and_advisory_review_keep_independent_preparation() -> None:
    """Inherited12/13/19/22/23: omissions or a move to common prose lose the duty."""
    required = {
        "operations/planning.md": (
            "## Cross-packet alignment sweep",
            "Before releasing a new packet or revision into a master with other open leaves:",
            "Check counts, paths, leaf identifiers, order and premises.",
            "in both directions",
            "reader other than the drafter",
            'before the new leaf\'s intent-review question, "go, or review first?"',
            "Repeat it before the go for a packet revised during review.",
            "draft and register the older packet's amendment and update its leaf task document",
            "including a result with no findings",
            "Before assigning an issue to another open leaf, follow "
            "[Complete the plan and preserve its boundaries]"
            "(#complete-the-plan-and-preserve-its-boundaries) for the originating-leaf default "
            "and charter exception.",
            "every knowledge answer through its last page",
            "the code they leave unattributed",
            "Put that list in the leaf task document's Prepared context before any seat starts.",
            "a row kind, disposition, writer field or gate",
            "how its own curation completes on the installed build",
            "Prove the new behavior only on fixtures and scratch copies.",
            "tell the developer that day: the hours, the item",
            "Plan-review and super-exit have no startable Reviewer seat",
        ),
        "operations/review.md": (
            "## Advisory independent reviews",
            "exception to normal routing",
            "the Architect commissions it and carries the findings to the Reviewer.",
            "It holds no leaf seat",
            "Provide the freeze, packets, rulings, design source and Worker evidence supplied to the Reviewer.",
            "own output folder",
            "clean copy for anything it runs",
            "and ports.",
            "No advisory reviewer reads the Reviewer's or Curator's files",
            "another advisory reviewer's folders",
            "Each reports only to the Architect.",
            "Name nothing the Architect or developer has already noticed",
            "An advisory report changes no verdict by itself",
            "one questions file",
            "Tell the Manager once",
            "rule on each",
            "An extra round for these findings is the Architect's grant.",
            "The Architect's pre-landing check confirms that every advisory finding has a ruling.",
            "sufficient seat permissions",
            "Before responding to an advisory reviewer's permission prompt, read "
            "[Formal permissions](orientation.md#formal-permissions) for the once-only "
            "developer notice and existing parent route.",
        ),
    }
    planning = (LIFECYCLE_ROOT / "operations/planning.md").read_text()
    _assert_originating_leaf_home_controls(planning)
    # P5-07 OP-138: the contradiction-to-developer rule has one home, the all-role rule of
    # "Complete the plan and preserve its boundaries"; the Architect-only sweep no longer
    # restates it.
    one_home = (
        "Send a requirement contradiction, a premise that changes a requirement's promise, scope "
        "expansion or a high-impact unresolved decision to the developer before affected work "
        "proceeds."
    )
    assert planning.count(one_home) == 1
    assert "goes to the developer before either leaf proceeds" not in planning
    assert one_home not in _architect_passages(planning)
    for source, clauses in required.items():
        text = (LIFECYCLE_ROOT / source).read_text()
        selected = _architect_passages(text)
        _require_clauses(selected, clauses)
        for clause in clauses:
            mutant = selected.replace(clause, "")
            with pytest.raises(AssertionError, match=re.escape(clause)):
                _require_clauses(mutant, clauses)
        # The same prose in the all-roles passage cannot satisfy Architect-only ownership.
        moved = text.replace("<!-- ar:roles architect -->", "<!-- ar:roles all -->")
        assert not _architect_passages(moved)
    formal_clauses = (
        "The Architect answers permission prompts that are only formalities itself, "
        "but only as far as its own seat's permissions reach.",
        "It never approves a whole class of prompts in advance (no blanket approver) "
        "without the developer's word.",
        "If its mode is default or auto-review, or its own answer to a prompt is refused, "
        "the developer decides:",
        "name the waiting prompt to him once, and use no way around it.",
        "Keep required upward notices on the existing parent chain only where a parent exists.",
        "A parentless Architect speaks to the developer in its own chat.",
        "Child permission notices retain their source-specific route.",
    )

    def require_formal_permission_home(text: str) -> None:
        blocks = re.findall(r"(?s)<!-- ar:roles architect -->\s*(.*?)(?=<!-- ar:roles |\Z)", text)
        sections = [
            match[1]
            for block in blocks
            if (match := re.search(r"(?ms)^## Formal permissions\n(.*?)(?=^## |\Z)", block))
        ]
        assert len(sections) == 1, "Formal permissions needs its Architect-owned home"
        _require_clauses(" ".join(sections[0].split()), formal_clauses)

    orientation = (LIFECYCLE_ROOT / "operations/orientation.md").read_text()
    require_formal_permission_home(orientation)
    for clause in formal_clauses:
        mutant = orientation.replace(clause, "", 1)
        assert mutant != orientation, clause
        with pytest.raises(AssertionError):
            require_formal_permission_home(mutant)
    with pytest.raises(AssertionError):
        require_formal_permission_home(
            orientation.replace("<!-- ar:roles architect -->", "<!-- ar:roles all -->")
        )
    with pytest.raises(AssertionError):
        require_formal_permission_home(
            orientation.replace("## Formal permissions\n", "## Unrelated permissions\n")
        )

    architect = (LIFECYCLE_ROOT / "roles/architect.md").read_text()
    for heading, target in (
        ("Establish the work", "operations/planning.md"),
        ("Delegate and decide", "operations/review.md"),
    ):
        section = architect.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0]
        pointer = next(line for line in section.splitlines() if target in line)
        assert len(pointer.split()) <= 25, (heading, pointer)
    coordination = (LIFECYCLE_ROOT / "operations/coordination.md").read_text()
    manager = " ".join(
        " ".join(
            re.findall(r"(?s)<!-- ar:roles manager -->\s*(.*?)(?=<!-- ar:roles |\Z)", coordination)
        ).split()
    )
    _require_clauses(
        manager,
        (
            "For integration or master-exit",
            "acceptance and delivery-proof leaf",
            "If no such leaf is named, ask the Architect.",
            "adding candidate identity and attempt records",
            "as your first `role_message` on `agents-remember-task` to the returned agent identifier.",
            "For a pending handover, give the sender the occasion to retry; relay no contents and ask no developer confirmation of that occasion.",
        ),
    )
    # Advice does not recreate the superseded cold-read sweep requirement.
    alignment = _architect_passages((LIFECYCLE_ROOT / "operations/planning.md").read_text())
    assert "drafted, cold-read and registered" not in alignment


def _require_worklist_fields_and_knowledge_command_owners() -> None:
    detail = (LIFECYCLE_ROOT / "reference/curator/worklist.md").read_text()
    worklist, gate = detail.split("`knowledgeGate`", 1)
    for field in ("state", "path", "digest", "itemCount", "itemsByKind", "incomplete"):
        assert f"| `{field}` |" in worklist, field
    for field in (
        "state",
        "owner",
        "worklistState",
        "worklistDigest",
        "worklistPath",
        "findingCount",
        "openItemCount",
        "incompleteCount",
        "violationCount",
        "reportOnlyCount",
    ):
        assert f"| `{field}` |" in gate, field
    assert "an absent block is not a passing judgment or an empty result" in detail
    reference = command_reference(REPOSITORY_ROOT)
    for column in ("What it does", "Who and when", "Example"):
        assert column in reference
    for tool in (name for name in PUBLIC_TOOLS if name.startswith("knowledge_")):
        assert f"| `{tool}` |" in reference
    assert_public_command_reference(reference)
    for role in ROLE_ORDER:
        text = (LIFECYCLE_ROOT / f"roles/{role}.md").read_text()
        assert "{{detail:reference/knowledge/commands.md}}" in text, role
    assert not (SKILLS_ROOT / "c-02-memory-quality-control").exists()


def _require_dependency_owner(text: str) -> None:
    section = re.search(r"(?ms)^## Check packet dependencies\n(.*?)(?=^## |\Z)", text)
    assert section is not None, "the Closeout dependency owner is missing"
    _require_clauses(
        " ".join(section[1].split()),
        (
            "The Manager checks packet dependencies before starting a leaf and before integrating it; "
            "the product does not perform that check.",
        ),
    )


def test_knowledge_gate_and_worklist_explanations_cover_actual_landing_steps() -> None:
    """Inherited2/4/5 retain exact-tree/refusal meaning and field owners after c-02 retirement."""
    closeout = " ".join((LIFECYCLE_ROOT / "operations/closeout.md").read_text().split())
    for clause in (
        "exact candidate code and memory trees",
        "Leaf integration judges the closeout pair again",
        "master's memory commit against the parent line's memory tip",
        "master's net code diff",
        "every non-abandoned leaf landed",
        "A checkpoint proves the chain already landed.",
        "Direct landing judges its exact candidate pair before publication.",
        "`knowledge-gate-refused`",
        "`direct-landing-knowledge-gate-refused`",
        "Send the findings to the Curator",
        "Bypass no gate.",
    ):
        assert clause in closeout, clause
        assert clause not in closeout.replace(clause, "", 1)
    brief_routes = (
        "Before starting or integrating a leaf, read [the Manager\N{RIGHT SINGLE QUOTATION MARK}s dependency check]"
        "(../roles/manager.md#start-leaf-agents-and-preserve-their-work) for prerequisite packets "
        "and leaf-stage start authority.",
        "Before handling a landing-gate refusal, read [Authorized Closeout]"
        "(../operations/closeout.md#repair-a-gate-refusal-through-its-owner) for the gate-finding route.",
    )
    dependency_clauses = (
        "Check packet dependencies before starting a leaf and before integrating it; "
        "the product does not perform that check.",
    )
    gate_clauses = (
        "A gate refusal prevents publication and names the findings.",
        "Closeout and integration report `knowledge-gate-refused`; "
        "direct landing reports `direct-landing-knowledge-gate-refused`.",
        "Send the findings to the Curator for repair, then retry the owning route on the corrected pair.",
        "Bypass no gate.",
    )

    def require_brief_routes(text: str) -> None:
        section = re.search(r"(?ms)^### Check dependencies\n(.*?)(?=^### |\Z)", text)
        assert section is not None, "the Manager brief needs its dependency action"
        _require_clauses(" ".join(section[1].split()), brief_routes)

    def require_dependency_home(text: str) -> None:
        section = re.search(
            r"(?ms)^## Start leaf agents and preserve their work\n(.*?)(?=^## |\Z)", text
        )
        assert section is not None, "the Manager dependency home is missing"
        _require_clauses(" ".join(section[1].split()), dependency_clauses)

    def require_gate_home(text: str) -> None:
        section = re.search(
            r"(?ms)^## Repair a gate refusal through its owner\n(.*?)(?=^## |\Z)", text
        )
        assert section is not None, "the closeout refusal home is missing"
        _require_clauses(" ".join(section[1].split()), gate_clauses)

    brief = (LIFECYCLE_ROOT / "templates/manager-brief.md").read_text()
    manager = (LIFECYCLE_ROOT / "roles/manager.md").read_text()
    closeout_source = (LIFECYCLE_ROOT / "operations/closeout.md").read_text()
    for text, clauses, require_home in (
        (brief, brief_routes, require_brief_routes),
        (manager, dependency_clauses, require_dependency_home),
        (closeout_source, gate_clauses, require_gate_home),
    ):
        require_home(text)
        for clause in clauses:
            mutant = text.replace(clause, "", 1)
            assert mutant != text, clause
            with pytest.raises(AssertionError):
                require_home(mutant)
        # The same rule after an unrelated heading cannot stand in for its action home.
        heading = {
            require_brief_routes: "### Check dependencies",
            require_dependency_home: "## Start leaf agents and preserve their work",
            require_gate_home: "## Repair a gate refusal through its owner",
        }[require_home]
        with pytest.raises(AssertionError):
            require_home(text.replace(heading, heading.split(" ", 1)[0] + " Unrelated action", 1))
    _require_worklist_fields_and_knowledge_command_owners()


@pytest.mark.parametrize("command", sorted(public_commands()))
def test_each_public_command_requires_its_documentation_owner(command: str) -> None:
    reference = command_reference(REPOSITORY_ROOT)
    assert_public_command_reference(reference)
    with pytest.raises(AssertionError):
        assert_public_command_reference(misplace_command_documentation(reference, command))


def test_root_instructions_name_no_retired_job_lens_or_phase() -> None:
    """L84-CUR-04: lenses.md is gone and l-01 defines no job lens or reframe-research phase.

    The source checkout's root AGENTS.md therefore states no step that depends on either.
    """

    retired = ("reframe-research", "as a *lens*")
    lifecycle = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(LIFECYCLE_ROOT.rglob("*.md"))
    )
    assert not (LIFECYCLE_ROOT / "lenses.md").exists()
    assert [term for term in retired if term in lifecycle] == []
    root = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert [term for term in retired if term in root] == []


@pytest.mark.parametrize(
    ("role", "clause"),
    [
        (
            "manager",
            "When the resolved memory layer holds `system/task-publication.md`, follow it: claiming "
            "a leaf assigns its issue and sets In progress before the start; after the landing and "
            "both pushes, close the issue. Without that file, publish nothing.",
        ),
        (
            "architect",
            "When the resolved memory layer holds `system/task-publication.md`, follow it: "
            "registering a leaf creates its open linked issue. Without that file, publish nothing.",
        ),
    ],
)
def test_role_names_its_publication_moments(role: str, clause: str) -> None:
    """L84-CUR-07: the publication clause names the role's moments (Architect ruling b2508984).

    The clause is the role's one pointer to task-publication.md; the generic
    "When publishing task work" sentence does not return in either copy.
    """

    packaged = REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills"
    for root in (SKILLS_ROOT, packaged):
        text = (root / "l-01-agent-lifecycles" / "roles" / f"{role}.md").read_text(encoding="utf-8")
        assert text.count(clause) == 1
        assert text.count("system/task-publication.md") == 1
        assert "When publishing task work" not in text
