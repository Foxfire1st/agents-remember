"""R1-05: the correct-write hand-write sentence is pinned in every carrier."""

import json
from pathlib import Path

from agents_remember_test_support.testing.curator_contracts import gfm_anchor
from agents_remember_test_support.testing.manifest_step_derivation import manifest_mismatches

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
PACKAGED_LIFECYCLE = (
    REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills/l-01-agent-lifecycles"
)

# T1 ruling 1: the whole sentence, directly after "...before applying a stop.", once.
SENTENCE = (
    "If no admitted route works, record the refusal, tell the owner, then make that write "
    "openly by hand and log it; never keep a blanket ban on writing it by hand."
)
PLACED = "before applying a stop. " + SENTENCE

CARRIERS = (
    LIFECYCLE / "core/authority.md",
    LIFECYCLE / "core/every-role.md",
    PACKAGED_LIFECYCLE / "core/authority.md",
    PACKAGED_LIFECYCLE / "core/every-role.md",
    REPOSITORY_ROOT / "mcp/src/agents_remember/cli/role_launch_preparation.py",
    REPOSITORY_ROOT / "docs/reference/settings-json.md",
)


def _flowed(text: str) -> str:
    """The text with line wrapping undone (settings-json.md wraps its prose)."""
    return " ".join(text.split())


def test_r1_05_sentence_is_pinned_once_in_every_carrier() -> None:
    for carrier in CARRIERS:
        text = _flowed(carrier.read_text(encoding="utf-8"))
        assert text.count(SENTENCE) == 1, carrier
        assert text.count(PLACED) == 1, carrier
    # Mutation: removing either half of the sentence from a carrier fails the pin.
    first_half, second_half = SENTENCE.split("; ", 1)
    text = _flowed(CARRIERS[0].read_text(encoding="utf-8"))
    for removed in (first_half + "; ", " " + second_half, SENTENCE):
        mutant = text.replace(removed, "", 1)
        assert mutant != text and (mutant.count(SENTENCE), mutant.count(PLACED)) != (1, 1)


def test_core_unit_sources_do_not_link_their_own_digest_place() -> None:
    document = json.loads((LIFECYCLE / "core/digest-map.json").read_text(encoding="utf-8"))
    places = {place["id"]: place for place in document["digest"]["places"]}
    for unit in document["units"]:
        place_id = unit.get("digestPlace")
        source = unit.get("file")
        if not place_id or not source or not source.startswith("core/"):
            continue
        place = places[place_id]
        own_anchor = f"every-role.md#{gfm_anchor(place['heading'])}"
        text = (LIFECYCLE / source).read_text(encoding="utf-8")
        start, end = unit["lines"]
        block = "\n".join(text.splitlines()[start - 1 : end])
        assert own_anchor not in block, (unit["id"], own_anchor)


def test_manifest_steps_name_the_pointer_derived_actions() -> None:
    """P5-05: every declared step and audience equals the one derived from its pointers.

    The derivation rule is in ``manifest_step_derivation``: an action is the opening
    "Before/When/For ..." clause of a first-message sentence that links the file.
    """
    for root in (LIFECYCLE, PACKAGED_LIFECYCLE):
        document = json.loads((root / "composition-manifest.json").read_text(encoding="utf-8"))
        references = document["references"]
        # Every step_detail entry declares a step (the product refuses one without);
        # every entry that declares a step is compared, so none is skipped.
        assert all("step" in entry for entry in references.values() if entry.get("step_detail"))
        assert manifest_mismatches(root, document) == []
        assert "refused push" not in references["refused-write"]["description"].lower()
        for entry in references.values():
            assert not entry.get("description", "").startswith("Named-step"), entry.get("source")
    # Mutation: arbitrary text in a step no literal names, or one invented role, fails.
    document = json.loads((LIFECYCLE / "composition-manifest.json").read_text(encoding="utf-8"))
    for key, field, value in (
        ("curator/memory-handover", "step", "before doing anything at all"),
        ("curator/card-formats-and-citations", "step", "before escalating a refused push"),
        ("curator/worklist", "roles", ["curator", "reviewer"]),
    ):
        mutant = json.loads(json.dumps(document))
        mutant["references"][key][field] = value
        assert any(
            problem.startswith(key + ":") for problem in manifest_mismatches(LIFECYCLE, mutant)
        )


def test_source_homes_carry_the_architect_wording() -> None:
    invariants = (LIFECYCLE / "core/invariants.md").read_text(encoding="utf-8")
    assert "A guard is a check that refuses, holds back or switches off an operation." in invariants
    assert "A guard needs an observed recurring cause" not in invariants
    authority = (LIFECYCLE / "core/authority.md").read_text(encoding="utf-8")
    assert (
        "In a sandbox run's report, name the sandbox's host, plug-in and build versions"
        in authority
    )
    assert "Some harnesses create files in the folder they run in" in authority
    assert "Some harnesses create files there" not in authority


# P5-16 moved these out of reference/sandbox-actors.md; their pins follow to the homes.
MOVED_SANDBOX_RULES = (
    "Run a harness command only in the supplied scratch roots, never with a worktree as its "
    "working folder. Some harnesses create files in the folder they run in; remove any that "
    "appear by literal path.",
    "In a sandbox run's report, name the sandbox's host, plug-in and build versions; treat a "
    "difference from the developer's installation as a finding.",
)


def test_moved_sandbox_rules_hold_once_in_each_home() -> None:
    sandbox = (LIFECYCLE / "reference/sandbox-actors.md").read_text(encoding="utf-8")
    for home in ("core/authority.md", "core/every-role.md"):
        text = (LIFECYCLE / home).read_text(encoding="utf-8")
        for rule in MOVED_SANDBOX_RULES:
            assert text.count(rule) == 1, (home, rule)
            assert rule not in sandbox


def test_reviewer_repair_and_continue_rule_is_pinned() -> None:
    text = (LIFECYCLE / "roles/reviewer.md").read_text(encoding="utf-8")
    assert (
        "Repair faults in your work or instruments, including mistaken records, "
        "wrong evidence anchors and misread candidate identities, and continue."
    ) in text
    assert "Correct input faults through the same tool; name repairs in the verdict." in text
    # P5-18 removed the Reviewer's own refused-write pointer; every-role.md:75 carries it.
    assert (
        "Product guards, read/check/validation/sync refusals and commit gates keep their stops."
    ) in text


def test_r1_05_carriers_also_name_the_same_tool_and_input_class() -> None:
    for carrier in CARRIERS:
        text = carrier.read_text(encoding="utf-8")
        assert "same tool and input class" in text or "same-tool/input-class" in text, carrier
