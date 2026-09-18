"""Focused behavioral check for the consolidated role-instruction corpus.

The corpus this protects is the canonical root ``skills/l-01-agent-lifecycles/`` tree: a thin
router, the shared ``core/`` blocks, one self-contained file per role, the ``operations/``
blocks, and the JSON composition manifest that routes a role to its sources. These cases assert
the properties a consumer depends on:

* every role in the registry has exactly one readable role source;
* every role source carries the agreed readable order and its machine-readable knob block;
* the manifest resolves to files that exist, for every role and every operation;
* the role registry is exactly the ten roles, and the ambient launcher is a routing condition
  rather than a role;
* the manifest shares no prose (it is a metadata plane);
* EVERY relative path the corpus cites resolves, so a consolidation cannot leave a dangling
  reference;
* a manifest that points at a missing source is reported rather than silently accepted; and
* curation is complete on every leaf: the retired optional/narrow-curation sentences are gone
  from the canonical tree and from all nine generated copies, and every source that must state
  the rule still states it.
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
    "bootstrap",
)

# The approved capsule shape, in order (developer ruling 2026-09-17; the developer-approved
# worker file `notes/briefs/worker-role-approved.md` is the exemplar). Each entry is the prefix a
# role file's H2 must begin with; the fourth and sixth carry the seat's own suffix after the dash.
REQUIRED_SECTIONS = (
    "## Inputs",
    "## Process",
    "## Outputs",
    "## What you may do",
    "## What you must not do",
    "## Stop and ",
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

# A role is self-contained: naming a sibling role file is the amalgamation the developer's ruling
# forbids, because a seat learns its duties from its own file and nowhere else. The one sanctioned
# case is hat-collapse, which genuinely requires running another seat's file: the architect may
# wear the designer hat inline. Entries elsewhere are removed rather than kept "just in case".
SANCTIONED_SIBLING_REFERENCES = {
    "architect": ("roles/designer.md",),
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

    # The registry is exactly the ten roles, and the ambient launcher is not one of them.
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


def test_every_role_source_is_a_capsule_shaped_function() -> None:
    """Each role file is the developer-approved capsule shape, and nothing else.

    The shape a role file ships in was ruled by the developer on 2026-09-17 and the worker file
    (``notes/briefs/worker-role-approved.md``, sha256 ``a07e92e1…``) is the approved exemplar:

    * frontmatter declaring the canonical skill-scoped name and a description;
    * a heading and a one-line statement of what the seat is;
    * ``Inputs`` · ``Process`` · ``Outputs`` in that order, then the seat's own may/must-not and
      stop-and-escalate sections;
    * **no ``Inherits:`` line** — a capsule composes no shared ``Core —`` block, so a role file
      that inherited one would state obligations nothing delivers;
    * **no operator-knob table** — ``harness``/``model``/``effort``/``launchArgs``/
      ``sessionCommands``/``promptKeywords`` live in settings and the seat cannot set them;
    * no sibling role file cited to learn a duty from, which is the amalgamation the same ruling
      forbids. One sanctioned case exists and is named below: the architect may wear the designer
      hat inline, which requires naming that file.
    """

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

        # The approved order, in order: every required section is present, and the positions
        # are ascending. A section may carry its own suffix after the em dash (``## Stop and
        # report — …``), and an extra seat-specific section may sit between two required ones.
        positions = []
        for heading in REQUIRED_SECTIONS:
            assert heading in text, f"{path} is missing a section beginning '{heading}'"
            positions.append(text.index(heading))
        assert positions == sorted(positions), f"{path} headings are out of the approved order"

        # A role is self-contained: naming a sibling role file is the amalgamation the ruling
        # forbids, except where the seat genuinely runs that file as a hat.
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
    """The link check catches a repository-relative anchor whose file does not exist.

    The mutation is **seeded**, not carried by the shipped corpus. The previous version of this
    case rewrote a repository-relative anchor that one role file happened to cite, so when the
    260915-CAPS-L22 cut made the corpus cite none, the case failed on a missing seed instead of
    measuring the resolver. Writing the healthy anchor in and then breaking it is the same
    measurement and it cannot be disarmed by an edit elsewhere in the tree.
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


# --------------------------------------------------------------------------------------
# Curation is complete on every leaf — the retired optional-curation doctrine
# --------------------------------------------------------------------------------------
#
# The registry of retired sentences, the registry of the retired loop-gate field pairing, and the
# readers for both live in `agents_remember_test_support.testing.curation_doctrine`; these cases are
# the assertions over the real tree. The defect they exist for is not a typo: a sentence that
# presents the memory-quality operation as a developer-request-only diagnostic, or as something a
# named scoped check may stand in for, tells a curator seat that complete curation is somebody
# else's decision. No per-file case can see it, because every individual sentence is plausible
# alone -- what has to hold is the agreement of the whole shipped corpus with the rule.
#
# The loop-gate registry is the same shape of defect in a different material: the memory-quality
# result publishes the raw checklist status as `qualityChecklistStatus` and the combined status as
# `checklistStatus`, so a carrier instructing a curator to iterate repairs until
# `checklistStatus=ready-for-closeout` names a condition the repair loop does not reach. That one is
# a fact about the shipped tool, verified against its controller rather than ruled as doctrine.
#
# WHAT THIS DOES NOT COVER (stated, not implied). Neither registry is a semantic check: a corpus
# that denied the rule, or restated the wrong gate, in fresh vocabulary the registries have never
# seen would pass. Scope is the canonical `skills/**` tree plus the nine generated copies
# `scripts/sync-skills.py` owns, and for the loop gate the reference documents named in
# `LOOP_GATE_DOCUMENTS`; the copies' byte-identity with their originals is that generator's own
# contract (`scripts/sync-skills.py --check`), while what is asserted here is that every copy
# carries the rule at all, so a stale copy cannot ship.

from agents_remember_test_support.testing.curation_doctrine import (
    CURATION_COMPLETENESS_STATEMENTS,
    CURATION_DOCTRINE_SURFACES,
    GENERATED_SKILL_COPIES,
    RETIRED_CURATION_STATEMENTS,
    RETIRED_LOOP_GATE_FIELD_PAIRING,
    doctrine_files,
    gates_the_retired_loop_gate_pairing,
    missing_completeness_statements,
    missing_loop_gate_statements,
    normalize_statement,
    retired_curation_findings,
    retired_loop_gate_findings,
    retired_statement_findings,
)


class CurationIsCompleteOnEveryLeafTests:
    """The shipped corpus states the rule, and no copy still ships a sentence that denied it."""

    def test_no_shipped_surface_still_carries_a_retired_curation_statement(self) -> None:
        findings = retired_curation_findings(REPOSITORY_ROOT)
        assert findings == [], (
            "a shipped instruction surface still presents curation as optional or narrow:\n  "
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
        assert len(canonical) > len(CURATION_COMPLETENESS_STATEMENTS), (
            "the canonical scan examined no more surfaces than the declared statements"
        )
        for copy_root in GENERATED_SKILL_COPIES:
            assert doctrine_files(REPOSITORY_ROOT, copy_root), f"{copy_root} produced no surfaces"
        assert set(CURATION_DOCTRINE_SURFACES) == {"skills", *GENERATED_SKILL_COPIES}

        # The loop-gate census ranges over those same ten surfaces plus the reference documents
        # outside the skill tree that state the loop, so its coverage boundary is asserted rather
        # than assumed: a document that dropped a corrected field name is reported here, and one
        # that moved away is reported as missing, instead of silently leaving the census.
        gate_documents = missing_loop_gate_statements(REPOSITORY_ROOT)
        assert gate_documents == [], (
            "declared loop-gate documents no longer state the corrected gate:\n  "
            + "\n  ".join(gate_documents)
        )

    def test_every_canonical_source_states_the_complete_curation_rule(self) -> None:
        missing: list[str] = []
        for relative, statements in CURATION_COMPLETENESS_STATEMENTS.items():
            path = REPOSITORY_ROOT / relative
            assert path.is_file(), f"declared curation-doctrine source is missing: {relative}"
            reading = normalize_statement(path.read_text(encoding="utf-8"))
            missing.extend(
                f"{relative} -> {statement}"
                for statement in statements
                if normalize_statement(statement) not in reading
            )
        assert missing == [], (
            "a canonical source that must state complete curation no longer does:\n  "
            + "\n  ".join(missing)
        )

    def test_every_generated_copy_carries_the_rule_its_canonical_original_states(self) -> None:
        """A stale copy is a real defect: a seat on that harness reads the old sentence."""

        missing: list[str] = []
        for copy_root in GENERATED_SKILL_COPIES:
            root = REPOSITORY_ROOT / copy_root
            assert root.is_dir(), f"generated skill copy is missing: {copy_root}"
            missing.extend(missing_completeness_statements(root, copy_root))
        assert missing == [], (
            "generated skill copies do not carry the complete-curation rule:\n  "
            + "\n  ".join(missing)
        )


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
        for retired in RETIRED_CURATION_STATEMENTS:
            reading = normalize_statement(retired.statement)
            for source in retired.sources:
                relative = source.removeprefix("skills/")
                shipped = normalize_statement(
                    (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
                )
                assert retired_statement_findings(reading, relative), (
                    f"{source} would not report the retired statement it shipped: {retired.probe!r}"
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
        mutated = original.replace("qualityChecklistStatus", RETIRED_LOOP_GATE_FIELD_PAIRING, 1)
        assert mutated != original, "seed site not found in the staged role file"
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

        fragment_one = RETIRED_CURATION_STATEMENTS[0]
        assert retired_statement_findings(
            normalize_statement(fragment_one.statement),
            "l-01-agent-lifecycles/templates/curator-brief.md",
        ), "the curator brief must be a declared surface for fragment 1"
        assert not retired_statement_findings(
            normalize_statement(fragment_one.statement), "c-02-memory-quality-control/SKILL.md"
        ), "an undeclared surface must not be reported for a statement it never shipped"
