"""The public documents agree with the tree, and the check that says so can fail.

``scripts/check-public-docs.py`` reads the documents a user reads and reports broken links,
missing anchors and paths, links out of ``docs/``, unreached pages, entry points that disagree
on the install steps or lack a link they owe, and identifiers of tasks, leaves, packets and
directions. This module
runs it on the checkout, which must have no finding, and proves each failure it knows by one
mutation of the checkout's own documents, held in memory. A mutation must add exactly the
finding it names: the file, the line and the target.

The check itself is offline. The command-line cases build a small Git repository in a
temporary folder, because the script lists the files that Git tracks.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts/check-public-docs.py"
HARNESS_PAGE = "docs/install/codex.md"
PICTURE = "docs/assets/a-new-picture.png"
STEP_ITEM = re.compile(r"^( {0,3}\d+\.\s+\*\*)(.+?)(\*\*)", re.MULTILINE)

Found = tuple[str, str, int, str]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_public_docs", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load scripts/check-public-docs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()


@pytest.fixture(scope="module")
def tree() -> Any:
    return CHECK.load_tree(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def baseline(tree: Any) -> set[Found]:
    return _found(tree)


def _found(tree: Any) -> set[Found]:
    return {(f.kind, f.path, f.line, f.target) for f in CHECK.check(tree)}


def _with(tree: Any, path: str, text: str) -> Any:
    """The tree with one document written or replaced."""
    return dataclasses.replace(tree, files=tree.files | {path}, texts={**tree.texts, path: text})


def _shown_by_the_front_page(tree: Any) -> Any:
    """The tree with one more picture under ``docs/``, which the front page shows."""
    held = dataclasses.replace(tree, files=tree.files | {PICTURE})
    return _appended(held, "README.md", f'<img src="{PICTURE}" alt="A picture">')[0]


def _appended(tree: Any, path: str, line: str) -> tuple[Any, int]:
    """The tree with one line added at the end of a document, and that line's number."""
    kept = tree.texts[path].rstrip("\n")
    return _with(tree, path, f"{kept}\n\n{line}\n"), len(kept.splitlines()) + 2


def _added(tree: Any, baseline: set[Found], path: str, line: str) -> tuple[set[Found], int]:
    mutated, number = _appended(tree, path, line)
    return _found(mutated) - baseline, number


def test_the_checkout_has_no_finding(tree: Any) -> None:
    assert len(tree.texts) > 40, "the check read too few documents to mean anything"
    assert [finding.render() for finding in CHECK.check(tree)] == []


def test_a_broken_relative_link_is_named(tree: Any, baseline: set[Found]) -> None:
    added, line = _added(tree, baseline, "docs/concepts.md", "[gone](no-such-page.md)")
    assert added == {("broken-link", "docs/concepts.md", line, "no-such-page.md")}


def test_an_absolute_link_to_a_missing_repository_path_is_named(
    tree: Any, baseline: set[Found]
) -> None:
    target = "https://github.com/Foxfire1st/agents-remember/blob/main/docs/no-such-page.md"
    added, line = _added(tree, baseline, "mcp/README.md", f"[gone]({target})")
    assert added == {("broken-link", "mcp/README.md", line, target)}


def test_a_missing_anchor_is_named(tree: Any, baseline: set[Found]) -> None:
    link = "getting-started.md#no-such-heading"
    added, line = _added(tree, baseline, "docs/concepts.md", f"[gone]({link})")
    assert added == {("missing-anchor", "docs/concepts.md", line, link)}


def test_a_missing_repository_path_is_named(tree: Any, baseline: set[Found]) -> None:
    added, line = _added(tree, baseline, "README.md", "Run `scripts/no-such-script.py` first.")
    assert added == {("missing-path", "README.md", line, "scripts/no-such-script.py")}


def test_a_path_that_git_ignores_or_another_check_owns_is_not_judged(
    tree: Any, baseline: set[Found]
) -> None:
    line = "Use `mcp/.venv/bin/python`, `skills/no-such-skill/` and `<memory-root>/system/x.md`."
    added, _ = _added(tree, baseline, "CONTRIBUTING.md", line)
    assert added == set()


def test_a_relative_link_out_of_docs_is_named(tree: Any, baseline: set[Found]) -> None:
    added, line = _added(tree, baseline, "docs/concepts.md", "[front page](../README.md)")
    assert added == {("outside-docs", "docs/concepts.md", line, "../README.md")}


def test_an_unreached_page_is_named(tree: Any, baseline: set[Found]) -> None:
    lonely = _with(tree, "docs/guides/unlinked.md", "# Unlinked\n")
    assert _found(lonely) - baseline == {
        ("unreached", "docs/README.md", 1, "docs/guides/unlinked.md")
    }


def test_a_page_behind_two_index_pages_is_named(tree: Any, baseline: set[Found]) -> None:
    chain = _with(tree, "docs/guides/deep.md", "# Deep\n\n[deeper](deeper.md)\n")
    chain = _with(chain, "docs/guides/deeper.md", "# Deeper\n")
    chain, _ = _appended(chain, "docs/guides/README.md", "[deep](deep.md)")
    assert _found(chain) - baseline == {("unreached", "docs/README.md", 1, "docs/guides/deeper.md")}


def test_a_picture_that_the_front_page_shows_is_reached(tree: Any, baseline: set[Found]) -> None:
    assert _found(_shown_by_the_front_page(tree)) - baseline == set()


def test_a_page_that_only_the_front_page_links_is_named(tree: Any, baseline: set[Found]) -> None:
    lonely = _with(_shown_by_the_front_page(tree), "docs/guides/unlinked.md", "# Unlinked\n")
    lonely, _ = _appended(lonely, "README.md", "[unlinked](docs/guides/unlinked.md)")
    assert _found(lonely) - baseline == {
        ("unreached", "docs/README.md", 1, "docs/guides/unlinked.md")
    }


def test_a_picture_that_no_document_shows_is_named(tree: Any, baseline: set[Found]) -> None:
    shown = _shown_by_the_front_page(tree)
    lonely = dataclasses.replace(shown, files=shown.files | {"docs/assets/unshown.png"})
    assert _found(lonely) - baseline == {
        ("unreached", "docs/README.md", 1, "docs/assets/unshown.png")
    }


def _retitled(tree: Any, position: int, title: str) -> tuple[Any, int, str]:
    """A harness page with one install step retitled; the step's line and its true title."""
    text = tree.texts[HARNESS_PAGE]
    step = list(STEP_ITEM.finditer(text))[position]
    changed = text[: step.start(2)] + title + text[step.end(2) :]
    return _with(tree, HARNESS_PAGE, changed), text.count("\n", 0, step.start()) + 1, step.group(2)


def test_a_differing_step_title_is_named(tree: Any, baseline: set[Found]) -> None:
    mutated, line, _ = _retitled(tree, 1, "Do something else")
    assert _found(mutated) - baseline == {
        ("install-steps", HARNESS_PAGE, line, "Do something else")
    }


def test_a_missing_step_is_named(tree: Any, baseline: set[Found]) -> None:
    text = tree.texts[HARNESS_PAGE]
    steps = list(STEP_ITEM.finditer(text))
    last = steps[-1]
    end = text.find("\n", last.start())
    mutated = _with(tree, HARNESS_PAGE, text[: last.start()] + text[end + 1 :])
    added = _found(mutated) - baseline
    assert {(kind, path, target) for kind, path, _, target in added} == {
        ("install-steps", HARNESS_PAGE, f"{len(steps) - 1} steps")
    }


def test_a_differing_command_line_is_named(tree: Any, baseline: set[Found]) -> None:
    text = tree.texts[HARNESS_PAGE]
    first = STEP_ITEM.search(text)
    assert first is not None
    end = text.find("\n", first.start())
    block = "\n\n   ```bash\n   agents-remember no-such-command\n   ```\n"
    mutated = _with(tree, HARNESS_PAGE, text[:end] + block + text[end:])
    line = text.count("\n", 0, end) + 4
    assert _found(mutated) - baseline == {
        ("install-steps", HARNESS_PAGE, line, "agents-remember no-such-command")
    }


def test_an_identifier_is_named_on_every_page(tree: Any, baseline: set[Found]) -> None:
    planned = ("MIK-R97", "L97", "D154", "260928-MIK-L97", "260928_some-task-folder")
    shown_by_the_product = ("R22.3-unresolved-target", "pnt-sandbox", "ar-pnt")
    for identifier in (*planned, *shown_by_the_product):
        added, line = _added(tree, baseline, "docs/concepts.md", f"This came with {identifier}.")
        assert added == {("identifier", "docs/concepts.md", line, identifier)}, identifier
    marked = "# Old design\n\n**Status:** History. This page records a design of 2026.\n\nL97.\n"
    added = _found(_with(tree, "docs/design/old.md", marked)) - baseline
    assert ("identifier", "docs/design/old.md", 5, "L97") in added


def test_a_front_page_without_a_link_to_the_roadmap_is_named(
    tree: Any, baseline: set[Found]
) -> None:
    text = tree.texts["README.md"].replace("](ROADMAP.md)", "](docs/README.md)")
    added = _found(_with(tree, "README.md", text)) - baseline
    assert added == {("entry-link", "README.md", 1, "ROADMAP.md")}


def test_an_entry_point_that_drops_an_owed_link_is_named(tree: Any, baseline: set[Found]) -> None:
    text = tree.texts[HARNESS_PAGE].replace("getting-started.md", "FAQ.md")
    added = _found(_with(tree, HARNESS_PAGE, text)) - baseline
    assert ("entry-link", HARNESS_PAGE, 1, "docs/getting-started.md") in added
    assert {kind for kind, _, _, _ in added} <= {"entry-link", "missing-anchor"}


def test_a_relative_link_in_the_package_description_is_named(
    tree: Any, baseline: set[Found]
) -> None:
    added, line = _added(tree, baseline, "mcp/README.md", "[front page](../README.md)")
    assert added == {("entry-link", "mcp/README.md", line, "../README.md")}


def test_a_marker_with_a_reason_allows_its_line_and_no_other(
    tree: Any, baseline: set[Found]
) -> None:
    named = "`scripts/no-such-script.py`"
    reason = "<!-- docs-check: path: the example names a script a user writes -->"
    added, _ = _added(tree, baseline, "README.md", f"{named} {reason}")
    assert added == set()
    fenced = f"{reason}\n```bash\npython3 scripts/no-such-script.py\n```\n\n{named}"
    added, line = _added(tree, baseline, "README.md", fenced)
    assert added == {("missing-path", "README.md", line + 5, "scripts/no-such-script.py")}


def test_a_marker_without_a_reason_or_without_a_use_is_named(
    tree: Any, baseline: set[Found]
) -> None:
    added, line = _added(tree, baseline, "README.md", "MIK-R97 <!-- docs-check: identifier: -->")
    assert added == {("marker", "README.md", line, "identifier")}
    added, line = _added(tree, baseline, "README.md", "Text. <!-- docs-check: path: unused -->")
    assert added == {("marker", "README.md", line, "path")}
    added, line = _added(tree, baseline, "README.md", "Text. <!-- docs-check: link: why -->")
    assert added == {("marker", "README.md", line, "link")}


def _repository(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "--all"], cwd=root, check=True)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--root", str(root)]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_the_script_names_file_line_and_target_and_fails(tmp_path: Path) -> None:
    _repository(tmp_path, {"docs/README.md": "# Documentation\n\n[gone](missing.md)\n"})
    done = _run(tmp_path)
    assert done.returncode == 1
    assert "docs/README.md:3: broken-link: missing.md" in done.stdout


def test_a_named_path_behind_a_symbolic_link_does_not_stop_the_check(tmp_path: Path) -> None:
    text = "# Documentation\n\nRun `mcp/.venv/bin/python -m pytest`.\n"
    _repository(tmp_path, {"docs/README.md": text, "mcp/x.py": "", ".gitignore": ".venv/\n"})
    (tmp_path / "mcp/.venv/bin").mkdir(parents=True)
    (tmp_path / "mcp/.venv/bin/python").symlink_to(sys.executable)
    done = _run(tmp_path)
    assert "documents," in done.stdout, done.stdout + done.stderr
    assert "missing-path" not in done.stdout


def test_a_file_that_git_does_not_track_is_not_read(tmp_path: Path) -> None:
    _repository(tmp_path, {"docs/README.md": "# Documentation\n"})
    (tmp_path / "docs/draft.md").write_text("# Draft\n\n[gone](missing.md)\n", encoding="utf-8")
    done = _run(tmp_path)
    assert "check-public-docs: 1 documents," in done.stdout, done.stdout + done.stderr
    assert "docs/draft.md" not in done.stdout


def test_the_script_passes_on_this_checkout() -> None:
    done = _run(REPOSITORY_ROOT)
    assert done.returncode == 0, done.stdout
    assert done.stdout.strip().endswith("0 findings")
