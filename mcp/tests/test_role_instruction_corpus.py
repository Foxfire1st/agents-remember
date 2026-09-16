"""Focused behavioral check for the consolidated role-instruction corpus.

The corpus this protects is the canonical root ``skills/l-01-agent-lifecycles/`` tree: a thin
router, the shared ``core/`` blocks, one self-contained file per role, the ``operations/``
blocks, and the JSON composition manifest that routes a role to its sources. These cases assert
the properties a consumer depends on:

* every role in the registry has exactly one readable role source;
* every role source carries the agreed readable order and its machine-readable knob block;
* the manifest resolves to files that exist, for every role and every operation;
* the role registry is exactly the nine roles, and the ambient launcher is a routing condition
  rather than a tenth role;
* the manifest shares no prose (it is a metadata plane);
* EVERY relative path the corpus cites resolves, so a consolidation cannot leave a dangling
  reference; and
* a manifest that points at a missing source is reported rather than silently accepted.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"
LIFECYCLE_ROOT = SKILLS_ROOT / "l-01-agent-lifecycles"
MANIFEST_PATH = LIFECYCLE_ROOT / "composition-manifest.json"

ROLE_ORDER = (
    "architect",
    "orchestrator",
    "designer",
    "strategist",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "system-specialist",
)

REQUIRED_SECTIONS = (
    "## 1 — Purpose And Authority",
    "## 2 — Required Inputs",
    "## 3 — Normal Workflow",
    "## 4 — Permitted Writes And Actions",
    "## 5 — Stop And Escalation Cases",
    "## 6 — Completion And Handoff",
)

MACHINE_SECTION = "## Knobs, Tool Surface, And Dispatch Authority"

# The eight frozen operations from the architecture's operation vocabulary.
OPERATION_KEYS = (
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
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

# A role may name a sibling role file only for a sanctioned reason. The architecture requires two:
# the architect may wear the designer hat, and a coordinating seat names the seat it dispatches.
# The allowlist is exact, not speculative: an entry that no path in the suite currently exercises is
# removed rather than kept "just in case", because a stale allowance silently weakens the check.
SANCTIONED_SIBLING_REFERENCES = {
    "architect": ("roles/designer.md",),
    "orchestrator": ("roles/strategist.md", "roles/designer.md"),
    "strategist": ("roles/manager.md",),
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

    # The registry is exactly nine roles, and the ambient launcher is not one of them.
    assert list(manifest["role_order"]) == list(ROLE_ORDER)
    assert "launcher" not in manifest["roles"]
    conditions = {condition["id"] for condition in manifest["routing_conditions"]}
    assert "ambient-launcher" in conditions

    # Every role must be reachable from the router's own registry.
    router = (LIFECYCLE_ROOT / manifest["entry_router"].split("/")[-1]).read_text(encoding="utf-8")
    for role in ROLE_ORDER:
        assert f"`roles/{role}.md`" in router, f"router does not register `roles/{role}.md`"

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
                "realTemplate": "templates/worker-brief.md",
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


def test_every_role_source_carries_the_readable_order_and_knob_block() -> None:
    """Each role file exists, parses as the agreed shape, and declares its own sources."""

    manifest = _manifest()
    assert list(manifest["roles"]) == list(ROLE_ORDER), (
        "role registry order changed; the router, the registry table and this test must agree"
    )

    for role, entry in manifest["roles"].items():
        path = LIFECYCLE_ROOT / entry["file"]
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # The file must be a real document, not a stub.
        assert len(lines) > 40, f"{path} is too short to be a role source"

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

        # Every role states the shared sources it composes with.
        assert "**Inherits:**" in text, f"{path} does not declare its inherited sources"
        for core_key in entry["core"]:
            source = manifest["core"][core_key]["source"]
            assert f"`{source}`" in text, f"{path} does not declare inherited source {source}"

        # The agreed readable order, in order.
        positions = []
        for heading in REQUIRED_SECTIONS:
            assert heading in text, f"{path} is missing '{heading}'"
            positions.append(text.index(heading))
        assert positions == sorted(positions), f"{path} headings are out of the agreed order"

        # The machine-readable knob block, after the readable order.
        assert MACHINE_SECTION in text, f"{path} is missing '{MACHINE_SECTION}'"
        assert text.index(MACHINE_SECTION) > positions[-1], (
            f"{path} must place its knob block after the handoff section"
        )
        for knob in ("harness", "model", "effort", "dispatch", "tools"):
            assert f"| {knob}" in text, f"{path} knob table is missing the `{knob}` row"
        assert "orchestration.rolesPerLevel" in text, (
            f"{path} must state its settings override keys"
        )

        # A role is self-contained: it may name a sibling role file only for a sanctioned reason —
        # wearing that hat, or dispatching that seat — never to learn its own duties.
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
      against the skills root for a cross-skill reference such as ``../w-02-light-task-workflow/…``.
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
    cannot contain. Everything else is reported, so the check is real for the class it claims.
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
                if token.rsplit("/", 1)[-1] in SYMBOLIC_FILE_NAMES:
                    continue
                if NON_LOCATION_RE.match(token):
                    continue
                roots = [lifecycle_root, path.parent, skills_root, REPOSITORY_ROOT]
                roots += [lifecycle_root / directory for directory in context_dirs]
                if not any((root / token).exists() for root in roots):
                    unresolved.append(f"{path.relative_to(skills_root)} → {token}")
    return unresolved


def test_link_check_reports_a_repo_relative_anchor_pointed_at_nothing(tmp_path: Path) -> None:
    """The link check catches a repository-relative anchor whose file does not exist."""

    staged = tmp_path / "skills"
    shutil.copytree(SKILLS_ROOT, staged)
    staged_root = staged / "l-01-agent-lifecycles"
    healthy = staged_root / "roles" / "reviewer.md"
    original = healthy.read_text(encoding="utf-8")

    # The healthy corpus resolves.
    assert unresolved_references(staged_root) == []

    # Seed one mutation: point a repository-relative anchor at a file that does not exist.
    mutated = original.replace(
        "mcp/src/agents_remember/controlplane/gate_policy.py",
        "controlplane/gate_policy.py",
        1,
    )
    assert mutated != original, "seed anchor not found in the staged role file"
    healthy.write_text(mutated, encoding="utf-8")
    problems = unresolved_references(staged_root)
    assert any("controlplane/gate_policy.py" in problem for problem in problems), problems

    # A coordination-root anchor is skipped by design, not reported as a false alarm.
    healthy.write_text(original + "\nSee `system/tools.md`.\n", encoding="utf-8")
    assert unresolved_references(staged_root) == []
