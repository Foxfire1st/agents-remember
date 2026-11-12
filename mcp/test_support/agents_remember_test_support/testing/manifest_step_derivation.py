"""Derive each composition-manifest reference step and audience from its actual pointers.

Derivation rule (P5-05):

* First-message texts are the files the compiler composes into a role's first message:
  its role file, the operation files of its manifest operation list (only the
  ``<!-- ar:roles ... -->`` passages addressed to it or to ``all``), the root file of each
  skill it lists (``SKILL.md``), and ``core/every-role.md`` when its core list names
  ``every-role`` (a paragraph opening with a ``Role, Role: `` label reaches only those
  roles; an unlabelled one reaches every such role). The other core files (authority,
  invariants, lifecycle-frame, loop, acceptance) are admitted inputs, not composed text,
  and are not counted; nor is the launcher (``core/launcher.md``), which is not a role.
  Text inside code fences is not counted.
* A pointer to file F is a Markdown link whose target resolves to F: a relative path,
  ``{{detail:F}}`` (with or without ``#anchor``), or the served
  ``skill://agents-remember/skills/l-01-agent-lifecycles/F`` form.
* The pointer's action is the opening clause of the sentence that holds the link: the
  text from the sentence start (after any every-role ``Role, Role: `` label) up to the
  last comma before the sentence's first link that sits outside parentheses and code
  spans, when that
  text starts with "Before ", "When " or "For ". The last comma, not the first, ends the
  clause so that a series ("Before diagnosing, investigating or editing source, read")
  stays whole. The first word is lower-cased. A pointer with no such clause is a
  non-clause pointer and is reported, never counted as an action.
* Derived step(F) is the set of distinct actions of all pointers to F: the first-message
  pointers plus the pointers inside the manifest's reference files (the delivered detail
  and template files), excluding F's own outbound links. Derived roles(F) are only the
  roles whose first message holds a pointer to F; an entry whose audience is wider (a
  role reaches F only through a detail file) records ``rolesReason``; its roles must then
  equal the derived roles plus the roles that reason names.
* A file with no first-message pointer is reached through detail files: its step is the
  set of actions of the pointers in the manifest's other reference files, and its roles
  are the derived roles of those reaching files (recursively).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SKILL = "l-01-agent-lifecycles"
SERVED_PREFIX = f"skill://agents-remember/skills/{SKILL}/"
LINK = re.compile(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
MARKER = re.compile(r"^<!-- ar:roles ([a-z,\-]+) -->\s*$")
ROLE_NAMES = (
    "Architect",
    "Orchestrator",
    "Manager",
    "Worker",
    "Curator",
    "Reviewer",
    "Investigator",
    "Bootstrap",
)
_NAME = "(?:" + "|".join(ROLE_NAMES) + ")"
LABEL = re.compile(rf"^({_NAME}(?:, {_NAME})*): ")
ACTION_OPENERS = ("Before ", "When ", "For ")


@dataclass(frozen=True)
class Pointer:
    """One link from a text to an l-01 file, with the action its sentence names."""

    origin: str
    line: int
    target: str
    roles: frozenset[str]
    action: str | None
    sentence: str


@dataclass
class Derivation:
    """Pointer-derived step and audience for one manifest reference."""

    key: str
    source: str
    declared_step: str | None
    declared_roles: tuple[str, ...]
    reached_from: str = "first-message"
    actions: list[str] = field(default_factory=list)
    roles: set[str] = field(default_factory=set)
    non_clause: list[Pointer] = field(default_factory=list)
    detail_roles: set[str] = field(default_factory=set)

    @property
    def declared_actions(self) -> set[str]:
        return set((self.declared_step or "").split("; ")) - {""}


def unfenced_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code blocks, numbered from 1."""
    kept: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif (
                token[0] == fence[0] and len(token) >= len(fence) and not line.strip()[len(token) :]
            ):
                fence = None
            continue
        if fence is None:
            kept.append((number, line))
    return kept


def resolve_target(root: Path, origin: str, raw: str) -> str | None:
    """The l-01-relative file a link names, or None when it leaves the skill."""
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    if raw.startswith("{{detail:"):
        path = raw[len("{{detail:") :].split("}}", 1)[0].split("#", 1)[0]
        return path
    if raw.startswith(SERVED_PREFIX):
        return raw[len(SERVED_PREFIX) :].split("#", 1)[0]
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw):
        return None
    path = raw.split("#", 1)[0]
    if not path:
        return origin
    resolved = ((root / origin).parent / path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _last_top_level_comma(text: str) -> int:
    depth = 0
    in_code = False
    last = -1
    for index, char in enumerate(text):
        if char == "`":
            in_code = not in_code
        elif in_code:
            continue
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            last = index
    return last


def sentence_start(line: str, position: int) -> int:
    """Start of the sentence holding ``position``: after the last '. ', '? ' or '! '."""
    start = 0
    for match in re.finditer(r"[.!?](?:\*\*)?\s+(?=\S)", line[:position]):
        start = match.end()
    return start


def action_of(line: str, link_start: int) -> tuple[str | None, str]:
    """The opening Before/When/For clause of the link's sentence, or None."""
    begin = sentence_start(line, link_start)
    first_link = next(
        match.start() for match in LINK.finditer(line) if begin <= match.start() <= link_start
    )
    head = line[begin:first_link]
    if begin == 0:
        head = LABEL.sub("", re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", head), count=1)
    head = head.lstrip("*_ ")
    sentence = head
    if not head.startswith(ACTION_OPENERS):
        return None, sentence
    comma = _last_top_level_comma(head)
    if comma < 0:
        return None, sentence
    clause = head[:comma].rstrip("*_ ")
    first, _, rest = clause.partition(" ")
    return f"{first.lower()} {rest}", sentence


def _role_sources(document: dict) -> dict[str, dict[str, set[str] | None]]:
    """For each first-message file, the roles it reaches (None = section-scoped)."""
    sources: dict[str, dict[str, set[str] | None]] = {}
    operations = document["operations"]
    for role, entry in document["roles"].items():
        sources.setdefault(entry["file"], {})[role] = None
        # Of the core files only every-role is composed; the others are admitted inputs.
        if "every-role" in entry.get("core", []):
            sources.setdefault(document["core"]["every-role"]["source"], {})[role] = None
        for name in entry.get("skills", []):
            sources.setdefault(document["skills"][name]["source"], {})[role] = None
        for name in entry.get("operations", []):
            sources.setdefault(operations[name]["source"], {})[role] = None
    return sources


def _line_roles(
    source: str, line: str, marker_roles: set[str] | None, audience: set[str]
) -> set[str]:
    if source == "core/every-role.md":
        label = LABEL.match(line)
        if label:
            named = {name.strip().lower() for name in label.group(1).split(",")}
            return named & audience
        return set(audience)
    if marker_roles is None:
        return set(audience)
    if "all" in marker_roles:
        return set(audience)
    return marker_roles & audience


def first_message_pointers(root: Path, document: dict) -> list[Pointer]:
    """Every link in the first-message texts, with the roles that receive it."""
    pointers: list[Pointer] = []
    for source, audience_map in sorted(_role_sources(document).items()):
        audience = set(audience_map)
        sectioned = any(
            entry["source"] == source and entry.get("audience") == "sections"
            for entry in document["operations"].values()
        )
        text = (root / source).read_text(encoding="utf-8")
        marker_roles: set[str] | None = None if not sectioned else set()
        for number, line in unfenced_lines(text):
            marker = MARKER.match(line)
            if sectioned and marker:
                marker_roles = set(marker.group(1).split(","))
                continue
            roles = _line_roles(source, line, marker_roles, audience)
            pointers.extend(_line_pointers(root, source, number, line, roles))
    return pointers


def _line_pointers(
    root: Path, source: str, number: int, line: str, roles: set[str]
) -> list[Pointer]:
    found: list[Pointer] = []
    for match in LINK.finditer(line):
        target = resolve_target(root, source, match.group(1))
        if target is None or target == source:
            continue
        action, sentence = action_of(line, match.start())
        found.append(Pointer(source, number, target, frozenset(roles), action, sentence))
    return found


def detail_pointers(root: Path, document: dict) -> list[Pointer]:
    """Links inside the manifest's reference files (roles filled in later)."""
    pointers: list[Pointer] = []
    for entry in document["references"].values():
        source = entry["source"]
        if not source.endswith(".md") or not (root / source).is_file():
            continue
        text = (root / source).read_text(encoding="utf-8")
        for number, line in unfenced_lines(text):
            pointers.extend(_line_pointers(root, source, number, line, set()))
    return pointers


def derive(root: Path, document: dict | None = None) -> dict[str, Derivation]:
    """Derive step and roles for every manifest reference that declares a step."""
    manifest: dict = (
        document
        if document is not None
        else json.loads((root / "composition-manifest.json").read_text(encoding="utf-8"))
    )
    first = first_message_pointers(root, manifest)
    details = detail_pointers(root, manifest)
    result = {
        key: Derivation(key, entry["source"], entry.get("step"), tuple(entry.get("roles", [])))
        for key, entry in manifest["references"].items()
        if "step" in entry
    }
    for item in result.values():
        _collect(item, [p for p in first if p.target == item.source])
    pending = [item for item in result.values() if not item.actions and not item.non_clause]
    for item in result.values():
        if item not in pending:
            _add_detail_actions(item, details, manifest)
    _derive_detail_reached(pending, details, manifest, result)
    for item in result.values():
        item.roles = set(ordered_roles(manifest, item.roles))
    return result


def _inbound(details: list[Pointer], source: str) -> list[Pointer]:
    return [p for p in details if p.target == source and p.origin != source]


def _add_detail_actions(item: Derivation, details: list[Pointer], document: dict) -> None:
    """Detail-file pointers add their actions to the step; roles stay first-message."""
    by_source = {entry["source"]: entry for entry in document["references"].values()}
    for pointer in _inbound(details, item.source):
        item.detail_roles.update(by_source.get(pointer.origin, {}).get("roles", []))
        if pointer.action is None:
            item.non_clause.append(pointer)
        elif pointer.action not in item.actions:
            item.actions.append(pointer.action)


def _derive_detail_reached(
    pending: list[Derivation],
    details: list[Pointer],
    document: dict,
    result: dict[str, Derivation],
) -> None:
    """A file with no first-message pointer takes the reaching detail files' roles."""
    by_source = {entry["source"]: key for key, entry in document["references"].items()}
    for _ in range(len(pending) + 1):
        for item in pending:
            hops = _inbound(details, item.source)
            item.reached_from = (
                "detail: " + ", ".join(sorted({p.origin for p in hops})) if hops else "none"
            )
            item.actions.clear()
            item.roles.clear()
            item.non_clause.clear()
            for pointer in hops:
                reaching = result.get(by_source.get(pointer.origin, ""))
                roles = reaching.roles if reaching is not None else set()
                _collect(item, [Pointer(**{**pointer.__dict__, "roles": frozenset(roles)})])


def _collect(item: Derivation, pointers: list[Pointer]) -> None:
    for pointer in pointers:
        item.roles.update(pointer.roles)
        if pointer.action is None:
            item.non_clause.append(pointer)
        elif pointer.action not in item.actions:
            item.actions.append(pointer.action)


def ordered_roles(document: dict, roles: set[str]) -> list[str]:
    return [role for role in document["role_order"] if role in roles]


def manifest_mismatches(root: Path, document: dict) -> list[str]:
    """Every entry whose declared step or roles differ from the pointer-derived ones.

    The declared step, split on "; ", must equal the derived action set. Declared roles
    must equal the derived roles in role order, unless the entry records a non-empty
    ``rolesReason``; then they must equal the derived roles plus the roles the reason names.
    """
    problems: list[str] = []
    for key, item in derive(root, document).items():
        entry = document["references"][key]
        if not item.actions:
            problems.append(f"{key}: no pointer-derived action ({item.reached_from})")
        elif item.declared_actions != set(item.actions):
            problems.append(
                f"{key}: step {sorted(item.declared_actions)} != derived {sorted(item.actions)}"
            )
        derived_roles = ordered_roles(document, item.roles)
        reason = entry.get("rolesReason")
        if reason is None:
            if list(item.declared_roles) != derived_roles:
                problems.append(f"{key}: roles {list(item.declared_roles)} != {derived_roles}")
        elif not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{key}: empty rolesReason")
        else:
            named = {
                role for role in document["role_order"] if re.search(rf"\b{role}\b", reason, re.I)
            }
            expected = ordered_roles(document, set(derived_roles) | named)
            if list(item.declared_roles) != expected:
                problems.append(f"{key}: roles {list(item.declared_roles)} != {expected} (reason)")
    return problems
