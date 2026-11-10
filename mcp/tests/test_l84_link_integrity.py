"""L84 P5-27: every delivered l-01 link, anchor and skill URI resolves, with mutations.

Both the source tree and its package copy are scanned. Checked: relative links, same-file
``(#anchor)`` links, ``<...>`` angle-bracket targets, ``{{detail:...}}`` links and every
``skill://`` URI (in a link or in prose) against the origin the product serves. A link
must stay inside the delivered skills tree, so a package link resolves inside the
package. Anchors use GitHub's slug (``gfm_anchor``); headings inside code fences are not
anchors.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from agents_remember.application.skill_resources.provider import SHIPPED_SKILL_ORIGIN
from agents_remember_test_support.testing.curator_contracts import gfm_anchor
from agents_remember_test_support.testing.manifest_step_derivation import unfenced_lines

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
PACKAGED = (
    REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills/l-01-agent-lifecycles"
)
SERVED = f"skill://{SHIPPED_SKILL_ORIGIN}/"

LINK = re.compile(r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
SKILL_URI = re.compile(r"skill://[^\s)>\]\"'`,;]*[^\s)>\]\"'`,;.]")
HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
WEB = ("http://", "https://", "mailto:")
# Template scaffolds whose angle-bracket targets are fill-in placeholders, by file.
PLACEHOLDERS = {
    "templates/repo-overview.md": {"<relative overview path>", "<relative card path>"},
    "templates/route-local-overview.md": {
        "<relative path to the parent overview>",
        "<relative card path>",
        "<relative child overview path>",
    },
    "templates/file-card.md": {"<relative path to the governing overview>"},
}

Overrides = dict[str, str | None]


class _Tree:
    """One delivered l-01 tree, optionally with files replaced, added or removed."""

    def __init__(self, root: Path, overrides: Overrides | None = None) -> None:
        self.root = root
        self.skills = root.parent
        self.overrides = {(root / rel).resolve(): text for rel, text in (overrides or {}).items()}
        self._anchors: dict[Path, set[str]] = {}

    def text(self, path: Path) -> str | None:
        path = path.resolve()
        if path in self.overrides:
            return self.overrides[path]
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def files(self) -> list[Path]:
        found = {path.resolve() for path in self.root.rglob("*.md")}
        found |= {path for path in self.overrides if path.suffix == ".md"}
        return sorted(path for path in found if self.text(path) is not None)

    def anchors(self, path: Path) -> set[str]:
        if path not in self._anchors:
            seen: dict[str, int] = {}
            for _, line in unfenced_lines(self.text(path) or ""):
                match = HEADING.match(line)
                if match:
                    slug = gfm_anchor(match.group(1))
                    count = seen.get(slug, 0)
                    seen[slug] = count + 1
                    self._anchors.setdefault(path, set()).add(
                        slug if count == 0 else f"{slug}-{count}"
                    )
            self._anchors.setdefault(path, set())
        return self._anchors[path]


def _resolve(tree: _Tree, origin: Path, raw: str) -> tuple[Path | None, str | None, str]:
    """(target file, anchor, problem) for one link target."""
    target = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
    if target.startswith("{{detail:"):
        inner, closing, rest = target[len("{{detail:") :].partition("}}")
        if not closing:
            return None, None, "unclosed detail link"
        path, _, anchor = (inner + rest).partition("#")
        return (tree.root / path).resolve(), anchor or None, ""
    if target.startswith("skill://"):
        if not target.startswith(SERVED):
            return None, None, "unserved skill URI origin"
        path, _, anchor = target[len(SERVED) :].partition("#")
        return (tree.skills / path).resolve(), anchor or None, ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None, None, "unknown link scheme"
    path, _, anchor = target.partition("#")
    resolved = (origin.parent / path).resolve() if path else origin
    return resolved, anchor or None, ""


def _check(tree: _Tree, origin: Path, raw: str, problems: list[str]) -> None:
    where = f"{origin.relative_to(tree.root.resolve())}: {raw}"
    target, anchor, problem = _resolve(tree, origin, raw)
    if target is None:
        problems.append(f"{where}: {problem}")
        return
    if not target.is_relative_to(tree.skills.resolve()):
        problems.append(f"{where}: leaves the delivered skills tree")
    elif tree.text(target) is None:
        problems.append(f"{where}: missing target")
    elif anchor and target.suffix != ".md":
        problems.append(f"{where}: anchor into a non-Markdown file")
    elif anchor and anchor not in tree.anchors(target):
        problems.append(f"{where}: missing anchor")


def _scan(root: Path, overrides: Overrides | None = None) -> list[str]:
    tree = _Tree(root, overrides)
    problems: list[str] = []
    for origin in tree.files():
        text = tree.text(origin) or ""
        relative = origin.relative_to(root.resolve()).as_posix()
        for match in LINK.finditer(text):
            raw = match.group(1).strip()
            if raw.startswith(WEB) or raw.startswith("skill://"):
                continue
            if raw.startswith("<") and raw in PLACEHOLDERS.get(relative, set()):
                continue
            _check(tree, origin, raw, problems)
        for uri in SKILL_URI.findall(text):
            _check(tree, origin, uri, problems)
    return problems


@pytest.mark.parametrize("root", [SOURCE, PACKAGED], ids=["source", "package"])
def test_every_delivered_link_and_anchor_resolves(root: Path) -> None:
    assert root.is_dir(), root
    assert _scan(root) == []


def test_gfm_anchor_keeps_one_hyphen_per_space() -> None:
    assert gfm_anchor("OM-2 — Independent fact-check") == "om-2--independent-fact-check"
    assert gfm_anchor("## Handoff / exit") == "handoff--exit"
    assert gfm_anchor("The Writer's input sections") == "the-writers-input-sections"
    assert gfm_anchor("Fix (`reviewMode=fix-verification`)") == "fix-reviewmodefix-verification"


def _first(pattern: str) -> tuple[str, str, re.Match[str]]:
    """The first source file and match of ``pattern`` (so mutants follow text edits)."""
    for path in sorted(SOURCE.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(pattern, text)
        if match:
            return path.relative_to(SOURCE).as_posix(), text, match
    raise AssertionError(f"no source link matches {pattern}")


def _swap(pattern: str, replacement: Callable[[re.Match[str]], str]) -> Overrides:
    relative, text, match = _first(pattern)
    return {relative: text[: match.start()] + replacement(match) + text[match.end() :]}


def _appended(relative: str, addition: str) -> Overrides:
    return {relative: (SOURCE / relative).read_text(encoding="utf-8") + addition}


PROBE_HEADINGS = "# Probe\n\n## OM-2 — Independent probe\n\n```text\n## Fenced heading\n```\n\n"
MUTANTS: dict[str, Callable[[], Overrides]] = {
    "broken same-file anchor": lambda: _swap(r"\]\(#[^)]+\)", lambda _: "](#no-such-section)"),
    "broken relative path": lambda: _swap(
        r"\]\((\.\./[^)#{]+\.md)\)", lambda m: m.group(0).replace(".md)", "-absent.md)")
    ),
    "broken relative anchor": lambda: _swap(
        r"\]\((\.\./[^)#{]+\.md)#[^)]+\)", lambda m: f"]({m.group(1)}#no-such-section)"
    ),
    "broken detail anchor": lambda: _swap(
        r"\]\(\{\{detail:[^}]+\}\}#[^)]+\)", lambda m: m.group(0).split("#")[0] + "#absent)"
    ),
    "broken detail path": lambda: _swap(
        r"\]\(\{\{detail:[^}]+\}\}", lambda m: m.group(0).replace("}}", "-absent.md}}")
    ),
    "broken angle-bracket path": lambda: _appended(
        "roles/worker.md", "\n[x](<../reference/absent file.md>)\n"
    ),
    "broken angle-bracket anchor": lambda: _appended(
        "roles/worker.md", "\n[x](<../core/every-role.md#no such section>)\n"
    ),
    "placeholder outside its template": lambda: _appended(
        "roles/worker.md", "\n[<file card>](<relative card path>)\n"
    ),
    "collapsed-hyphen anchor": lambda: {
        "probe.md": PROBE_HEADINGS + "[a](#om-2-independent-probe)\n"
    },
    "heading inside a code fence": lambda: {"probe.md": PROBE_HEADINGS + "[a](#fenced-heading)\n"},
    "unserved skill URI": lambda: _appended(
        "roles/worker.md", "\nSee skill://l-01-agent-lifecycles/roles/worker.md.\n"
    ),
    "skill URI missing target": lambda: _appended(
        "roles/worker.md", f"\n[x]({SERVED}l-01-agent-lifecycles/roles/absent.md)\n"
    ),
    "skill URI missing anchor": lambda: _appended(
        "roles/worker.md", f"\n[x]({SERVED}l-01-agent-lifecycles/core/every-role.md#absent)\n"
    ),
    "link leaving the skills tree": lambda: _appended(
        "core/lifecycle-frame.md", "\n[tools](../../../docs/reference/mcp-tools.md)\n"
    ),
}


def test_link_mutations_fail_and_the_gfm_anchor_passes() -> None:
    baseline = set(_scan(SOURCE))
    probe: Overrides = {"probe.md": PROBE_HEADINGS + "[a](#om-2--independent-probe)\n"}
    assert set(_scan(SOURCE, probe)) == baseline, "the GitHub-form anchor must resolve"
    for name, mutant in MUTANTS.items():
        assert set(_scan(SOURCE, mutant())) - baseline, name
    # In the package root, a skill:// target resolves in the package, not in the source.
    relative = "reference/knowledge/use.md"
    assert (PACKAGED / relative).is_file()
    added = set(_scan(PACKAGED, {relative: None})) - set(_scan(PACKAGED))
    assert any(f": {SERVED}l-01-agent-lifecycles/{relative}" in item for item in added), added


def test_acceptance_does_not_point_at_its_own_approval_definition() -> None:
    """AC2.1: the acceptance place must not link its own definition back to itself."""
    acceptance = SOURCE / "core/acceptance.md"
    text = acceptance.read_text(encoding="utf-8")
    targets = [match.group(1) for match in LINK.finditer(text)]
    assert not any(target.endswith("every-role.md#requirements-and-limits") for target in targets)


SKILL_ROOTS = (REPOSITORY_ROOT / "skills", PACKAGED.parent)


def _fragment_problems(skill: Path, overrides: Overrides | None = None) -> list[str]:
    """Every link with a ``#`` fragment in one skill: its file exists and holds the section."""
    tree = _Tree(skill, overrides)
    problems: list[str] = []
    for origin in tree.files():
        text = tree.text(origin) or ""
        raws = [match.group(1).strip() for match in LINK.finditer(text)]
        raws += [uri for uri in SKILL_URI.findall(text) if uri not in raws]
        for raw in raws:
            if raw.startswith(WEB) or "#" not in raw:
                continue
            _check(tree, origin, raw, problems)
    return [f"{skill.name}/{problem}" for problem in problems]


@pytest.mark.parametrize("skills", SKILL_ROOTS, ids=["source", "package"])
def test_every_fragment_link_in_every_skill_resolves(skills: Path) -> None:
    """L84-CUR-06: a section link from any skill, not only l-01, names an existing section."""
    roots = sorted(path for path in skills.iterdir() if (path / "SKILL.md").is_file())
    assert "l-01-agent-lifecycles" in {root.name for root in roots}
    assert [problem for root in roots for problem in _fragment_problems(root)] == []
