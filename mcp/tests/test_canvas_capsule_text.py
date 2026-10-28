"""MIK-R105 rule 12 (L104-B19): the presenter capability text through the capsule contract.

The finding was that ``canvas/review-presenter.md`` was checkout-local: an installed build could
not read it, its 1,100-word budget was asserted by a canvas test rather than enforced at the read,
and nothing recorded whether a launch delivered the canonical text or a user's addition or
replacement of it.

One case per clause of the contract:

1. the canonical text is read from the package's own data, at the registered ``package_data`` path,
   not from the checkout's source file;
2. the finite word budget and the required wording are enforced by the loader, each refusing by
   name, and a copy that is missing refuses rather than falling back to another tree;
3. the generated copy is produced from the canonical source and is byte-identical to it, so the
   checked-in copy cannot drift;
4. the launch record names which provenance applied: the canonical baseline, a user addition or a
   user replacement, with the baseline's path, revision and budget;
5. an installed layout that holds no checkout-only text at all still loads the capability, and the
   packaging declaration is what puts the copy in the wheel and the sdist.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from importlib import resources
from pathlib import Path, PurePosixPath

import pytest
from agents_remember.cli.role_instructions import (
    CAPABILITY_ADDITION,
    CAPABILITY_BASELINE,
    CAPABILITY_REPLACEMENT,
    CapabilityRefused,
    capability_copy_drift,
    compose_capability_capsule,
    load_capability_resource,
    write_generated_capability_copy,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "mcp" / "src" / "agents_remember"
CAPABILITY = "review-presenter"
PACKAGED_PATH = "package_data/canvas/review-presenter.md"
CANONICAL_PATH = "mcp/src/agents_remember/canvas/review-presenter.md"
#: The clauses the capability cannot lose without becoming a different assignment.
REQUIRED_WORDING = (
    "You are not the Reviewer",
    "Keywords:",
    "You write only in your working folder.",
    "The side conversation belongs to this environment",
)


def _installed_copy() -> Path:
    """The copy the package declares, resolved the way an installed build resolves it."""

    resolved = resources.files("agents_remember").joinpath(*PurePosixPath(PACKAGED_PATH).parts)
    assert isinstance(resolved, Path), (
        "this test needs the on-disk package layout the checkout and an installed wheel share"
    )
    return resolved


def test_the_capability_text_is_read_from_the_package_data_copy(tmp_path: Path) -> None:
    """Rule 12: the canonical text loads from package data, at its registered path."""

    loaded = load_capability_resource(CAPABILITY)
    installed = _installed_copy()

    assert loaded.packaged_path == PACKAGED_PATH
    assert loaded.canonical_path == CANONICAL_PATH
    assert installed.resolve() == (PACKAGE_ROOT / PACKAGED_PATH).resolve()
    assert installed.read_text(encoding="utf-8") == loaded.text
    # The D566 review-presenter text: 1,072 words. It carries the architect's first-reads order from
    # generation 6's measurement. L201 (MIK-R211 v5) describes the explainer's normal workspace and
    # its harness's own tools in place of the confinement and the eighteen removed tools, says where
    # each render's run folder is (renders/run-NNN/), instructs grading for the page's Sort menu,
    # and reports to the architect with the role_message call D607 proved (D602-D608).
    assert loaded.words == 1072
    assert loaded.words <= loaded.word_budget == 1100
    assert len(loaded.digest) == 64 and loaded.revision == f"sha256:{loaded.digest}"
    for clause in REQUIRED_WORDING:
        assert clause in loaded.text

    # The capsule carries the effects-off rule in whatever words its own revision uses (D566:
    # "Reduced motion and effects off show each figure's final, labelled state..."). A copy with
    # that sentence removed still loads inside the cap, and its count falls by exactly the
    # sentence's words -- the contract is the count and the budget, not one revision's phrasing.
    start = loaded.text.index("Reduced motion and effects off")
    sentence = loaded.text[start : loaded.text.index(".", start) + 1]
    draft = tmp_path / PACKAGED_PATH
    draft.parent.mkdir(parents=True)
    draft.write_text(loaded.text.replace(sentence, ""), encoding="utf-8")
    shorter = load_capability_resource(CAPABILITY, root=tmp_path)
    assert shorter.words == loaded.words - len(sentence.split())
    assert shorter.words <= shorter.word_budget == 1100


def test_the_budget_and_the_wording_are_enforced_at_the_read(tmp_path: Path) -> None:
    """Rule 12: a copy that is too long, reworded, empty or absent refuses by name."""

    packaged = tmp_path / PACKAGED_PATH
    packaged.parent.mkdir(parents=True)
    packaged.write_text("word " * 1101, encoding="utf-8")
    with pytest.raises(CapabilityRefused) as too_long:
        load_capability_resource(CAPABILITY, root=tmp_path)
    assert too_long.value.reason == "capability-too-long"
    assert "1101" in too_long.value.detail

    canonical = load_capability_resource(CAPABILITY).text
    packaged.write_text(
        canonical.replace("You write only in your working folder.", "Write wherever you like."),
        encoding="utf-8",
    )
    with pytest.raises(CapabilityRefused) as reworded:
        load_capability_resource(CAPABILITY, root=tmp_path)
    assert reworded.value.reason == "capability-wording"

    packaged.write_text(" \n", encoding="utf-8")
    with pytest.raises(CapabilityRefused) as empty:
        load_capability_resource(CAPABILITY, root=tmp_path)
    assert empty.value.reason == "capability-empty"

    packaged.unlink()
    with pytest.raises(CapabilityRefused) as absent:
        load_capability_resource(CAPABILITY, root=tmp_path)
    assert absent.value.reason == "capability-missing"
    assert "generated copy" in absent.value.detail


def test_the_installed_copy_is_the_generated_copy_of_the_canonical_source(
    tmp_path: Path,
) -> None:
    """Rule 12: the bytes ship as a copy of the source, so an edit cannot land in one place only."""

    assert capability_copy_drift(CAPABILITY, REPOSITORY_ROOT, PACKAGE_ROOT) is None
    canonical = (REPOSITORY_ROOT / CANONICAL_PATH).read_bytes()
    packaged = (PACKAGE_ROOT / PACKAGED_PATH).read_bytes()
    assert packaged == canonical

    regenerated = write_generated_capability_copy(CAPABILITY, REPOSITORY_ROOT, tmp_path)
    assert regenerated == tmp_path / PACKAGED_PATH
    assert regenerated.read_bytes() == canonical

    # A copy that no longer matches is named, so a build cannot ship a stale text quietly.
    regenerated.write_text(canonical.decode("utf-8") + "\nAn ungenerated edit.\n", encoding="utf-8")
    drift = capability_copy_drift(CAPABILITY, REPOSITORY_ROOT, tmp_path)
    assert drift is not None and "run the generator again" in drift


def test_the_launch_record_names_the_baseline_the_addition_or_the_replacement() -> None:
    """Rule 12: a reader can tell which text the seat received and where the baseline came from."""

    baseline = load_capability_resource(CAPABILITY)
    delivered, record = compose_capability_capsule(CAPABILITY, {})
    assert delivered == baseline.text
    assert record["applied"] == CAPABILITY_BASELINE
    assert record["kind"] == "compiled-capability-capsule"
    assert [block["kind"] for block in record["blocks"]] == ["baseline"]
    provenance = record["provenance"]
    assert provenance["applied"] == "baseline"
    assert provenance["baseline"]["kind"] == "canonical baseline"
    assert provenance["baseline"]["revision"] == baseline.revision
    assert provenance["baseline"]["canonicalPath"] == CANONICAL_PATH
    assert provenance["baseline"]["packagedPath"] == PACKAGED_PATH
    assert provenance["baseline"]["wordBudget"] == 1100
    assert provenance["user"] is None

    added, addition = compose_capability_capsule(
        CAPABILITY,
        {
            "source": "settings",
            "key": "orchestration.roles.review-presenter",
            "value": "Prefer the repository's own look.",
        },
    )
    assert addition["applied"] == CAPABILITY_ADDITION
    assert [block["kind"] for block in addition["blocks"]] == ["baseline", "user addition"]
    assert addition["provenance"]["user"]["kind"] == "user addition"
    assert added.startswith(baseline.text)
    assert added.rstrip().endswith("Prefer the repository's own look.")

    replaced, replacement = compose_capability_capsule(
        CAPABILITY,
        {
            "source": "settings",
            "key": "orchestration.roles.review-presenter",
            "value": "<overwrite>\nExplain only the diff, in one page.\n",
        },
    )
    assert replacement["applied"] == CAPABILITY_REPLACEMENT
    assert replacement["baselineReplaced"] is True
    assert [block["kind"] for block in replacement["blocks"]] == ["user replacement"]
    assert replacement["provenance"]["user"]["kind"] == "user replacement"
    assert not replaced.startswith(baseline.text)
    assert "Explain only the diff, in one page." in replaced


def test_an_installed_layout_without_the_checkout_text_still_loads_it(tmp_path: Path) -> None:
    """Rule 12: a clean installation has no ``canvas/review-presenter.md`` and still loads it."""

    site = tmp_path / "site" / "agents_remember"
    (site / "cli").mkdir(parents=True)
    (site / "package_data" / "canvas").mkdir(parents=True)
    for relative in ("__init__.py", "cli/__init__.py", "cli/role_instructions.py"):
        shutil.copyfile(PACKAGE_ROOT / relative, site / relative)
    shutil.copyfile(PACKAGE_ROOT / PACKAGED_PATH, site / PACKAGED_PATH)
    assert not (site / "canvas").exists(), "the installed layout must hold no checkout-only text"

    script = (
        "from agents_remember.cli.role_instructions import load_capability_resource as load;"
        "text = load('review-presenter');"
        "print(text.words, text.digest, text.packaged_path)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path / "site"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    loaded = load_capability_resource(CAPABILITY)
    assert result.stdout.strip() == f"{loaded.words} {loaded.digest} {PACKAGED_PATH}"


def test_the_packaging_declaration_ships_the_installed_copy() -> None:
    """Rule 12: the wheel and the sdist carry both copies because both patterns are declared."""

    declaration = tomllib.loads((REPOSITORY_ROOT / "mcp" / "pyproject.toml").read_text("utf-8"))
    patterns = declaration["tool"]["setuptools"]["package-data"]["agents_remember"]
    assert "package_data/**/*" in patterns
    assert "canvas/*.md" in patterns
    assert (PACKAGE_ROOT / PACKAGED_PATH).is_file()
    assert (REPOSITORY_ROOT / CANONICAL_PATH).is_file()
    loaded = load_capability_resource(CAPABILITY)
    # MIK-R211 v5 evidence 12: the instructions name role_message for the report and carry the
    # grading instruction.
    assert "Message file:" in loaded.text and '`role_message(role="architect", text=' in loaded.text
    assert "attach=[" in loaded.text
    assert "Sort menu" in loaded.text and "`define_category`" in loaded.text
