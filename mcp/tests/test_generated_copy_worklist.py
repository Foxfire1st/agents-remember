"""A worklist reads generated-copy declarations from its candidate, not its runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_worklist.compute import WorklistInputs, compute_worklist
from agents_remember.application.knowledge_worklist.generated_copies import generated_copy_facts
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory_quality.knowledge_validator.trees import knowledge_tree_from_directory
from knowledge_writer_test_support import build_world, commit_all, git, write
from test_sync_scripts import sync_skills_module


def test_candidate_owned_declaration_matches_and_names_divergent_copies(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    declaration = {
        "version": 1,
        "copies": [
            {
                "source": "canonical",
                "targets": [
                    {"label": "first", "path": "copy-one"},
                    {"label": "second", "path": "copy-two"},
                ],
                "ignoreNames": ["__pycache__"],
                "ignoreSuffixes": [".pyc"],
            }
        ],
    }
    write(
        world.code,
        {
            "generated-copies.json": json.dumps(declaration),
            "canonical/rule.md": "current source\n",
            "copy-one/rule.md": "current source\n",
            "copy-two/rule.md": "manual edit\n",
            "copy-one/__pycache__/ignored.pyc": b"not a declared generated payload",
            "docs/ordinary.md": "ordinary\n",
        },
    )
    commit_all(world.code, "candidate owns its copies")
    code = CodeTrees.open(world.code, base, git(world.code, "rev-parse", "HEAD^{tree}"))
    facts = generated_copy_facts(
        code,
        [
            "copy-one/rule.md",
            "copy-two/rule.md",
            "copy-one/__pycache__/ignored.pyc",
            "docs/ordinary.md",
        ],
    )
    assert facts == {
        "copy-one/rule.md": {"canonicalPath": "canonical/rule.md", "matches": True},
        "copy-two/rule.md": {"canonicalPath": "canonical/rule.md", "matches": False},
    }


@pytest.mark.parametrize("link_side", ["source", "target"])
def test_symlink_payloads_never_suppress_full_worklist(tmp_path: Path, link_side: str) -> None:
    world = build_world(tmp_path)
    declaration = (
        '{"version":1,"copies":[{"source":"canonical","targets":'
        '[{"label":"copy","path":"copy"}],"ignoreNames":[],"ignoreSuffixes":[]}]}'
    )
    write(
        world.code,
        {
            "generated-copies.json": declaration,
            "canonical/real.md": "meaningful payload\n",
            "canonical/alias.md": "before source\n",
            "copy/real.md": "meaningful payload\n",
            "copy/alias.md": "before copy\n",
        },
    )
    commit_all(world.code, "distinct base payloads")
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    write(world.code, {"canonical/alias.md": "real.md", "copy/alias.md": "real.md"})
    linked = world.code / ("canonical" if link_side == "source" else "copy") / "alias.md"
    linked.unlink()
    linked.symlink_to("real.md")
    produced = tmp_path / "generator-output"
    sync_skills_module().replace_tree(world.code / "canonical", produced)
    assert not (produced / "alias.md").is_symlink()
    expected_bytes = b"meaningful payload\n" if link_side == "source" else b"real.md"
    assert (produced / "alias.md").read_bytes() == expected_bytes
    assert (produced / "alias.md").read_bytes() != (world.code / "copy/alias.md").read_bytes()
    commit_all(world.code, "symlink candidate")
    code = CodeTrees.open(world.code, base, git(world.code, "rev-parse", "HEAD^{tree}"))
    assert code.candidate()["canonical/alias.md"] == code.candidate()["copy/alias.md"]
    tree = knowledge_tree_from_directory(world.memory)
    memory = KnowledgeSide.from_tree("memory", "memory", tree)
    document = compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))
    assert document["state"] == "complete"
    items = [one for one in document["items"] if one["facts"].get("path") == "copy/alias.md"]
    assert items
    assert all(
        one["facts"]["generatedCopy"] == {"canonicalPath": "canonical/alias.md", "matches": False}
        for one in items
    )
    assert generated_copy_facts(code, ["copy/alias.md"])["copy/alias.md"]["matches"] is False


@pytest.mark.parametrize("copy_count", [2, 12])
def test_full_worklist_suppresses_only_equal_declared_copies(
    tmp_path: Path, copy_count: int
) -> None:
    world = build_world(tmp_path)
    copy_paths = [f"projection-{number}/rule.md" for number in range(copy_count)]
    declaration = {
        "version": 1,
        "copies": [
            {
                "source": "canonical",
                "targets": [
                    {"label": f"projection-{number}", "path": f"projection-{number}"}
                    for number in range(copy_count)
                ],
                "ignoreNames": [],
                "ignoreSuffixes": [],
            }
        ],
    }
    write(
        world.code,
        {
            "generated-copies.json": json.dumps(declaration),
            "canonical/rule.md": "old source\n",
            "docs/ordinary.md": "old ordinary\n",
            **dict.fromkeys(copy_paths, "old source\n"),
        },
    )
    commit_all(world.code, "declared projection base")
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    write(
        world.code,
        {
            "canonical/rule.md": "new source\n",
            "docs/ordinary.md": "new ordinary\n",
            **dict.fromkeys(copy_paths, "new source\n"),
        },
    )
    commit_all(world.code, "equal projection candidate")
    memory = KnowledgeSide.from_tree(
        "memory", "memory", knowledge_tree_from_directory(world.memory)
    )

    def run() -> dict:
        code = CodeTrees.open(world.code, base, git(world.code, "rev-parse", "HEAD^{tree}"))
        return compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))

    def named_paths(document: dict) -> set[str]:
        return {
            path
            for item in document["items"]
            if item["kind"] in {"unexplained_file", "unexplained_hunk"}
            for path in [item["facts"]["path"], *item["facts"].get("sourceBlobs", {})]
        }

    # Negative control: without the admitted producer, equality grouping still leaves
    # all projection paths attached to an unexplained obligation.
    with mock.patch(
        "agents_remember.application.knowledge_worklist.generated_copies.generated_copy_facts",
        return_value={},
    ):
        control = run()
    assert set(copy_paths) <= named_paths(control)
    current = run()
    assert current["state"] == "complete"
    assert named_paths(current) == {"canonical/rule.md", "docs/ordinary.md"}
    write(world.code, {copy_paths[0]: "manual change\n"})
    commit_all(world.code, "one hand-edited projection")
    divergent = run()
    assert named_paths(divergent) == {"canonical/rule.md", "docs/ordinary.md", copy_paths[0]}
    items = [item for item in divergent["items"] if item["facts"].get("path") == copy_paths[0]]
    assert items
    assert all(
        item["facts"]["generatedCopy"] == {"canonicalPath": "canonical/rule.md", "matches": False}
        for item in items
    )


@pytest.mark.parametrize("representative", ["docs/a.md", "copy-one/rule.md"])
def test_grouped_generated_members_keep_each_canonical_binding(
    tmp_path: Path, representative: str
) -> None:
    world = build_world(tmp_path)
    copies = ["copy-one/rule.md", "copy-two/rule.md"]
    members = sorted({representative, *copies})
    declaration = (
        '{"version":1,"copies":[{"source":"canonical","targets":'
        '[{"label":"one","path":"copy-one"},{"label":"two","path":"copy-two"}],'
        '"ignoreNames":[],"ignoreSuffixes":[]}]}'
    )
    write(world.code, {"generated-copies.json": declaration})
    write(world.code, {"canonical/rule.md": "canonical\n"})
    write(world.code, dict.fromkeys(members, "old\n"))
    commit_all(world.code, "group base")
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    write(world.code, dict.fromkeys(members, "divergent\n"))
    commit_all(world.code, "group candidate")
    if representative == "docs/a.md":
        write(world.memory, {"onboarding/docs/a.md.md": "# Ordinary card\n"})
    tree = knowledge_tree_from_directory(world.memory)
    memory = KnowledgeSide.from_tree("memory", "memory", tree)
    code = CodeTrees.open(world.code, base, git(world.code, "rev-parse", "HEAD^{tree}"))
    document = compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))
    assert document["state"] == "complete"
    items = [one for one in document["items"] if one["kind"].startswith("unexplained_")]
    assert len(items) == 1
    facts = items[0]["facts"]
    assert facts["path"] == representative
    copy_fact = {"canonicalPath": "canonical/rule.md", "matches": False}
    expected = {path: copy_fact for path in copies}
    assert facts["generatedCopies"] == expected
    assert facts.get("generatedCopy") == expected.get(representative)
    assert facts["sourceBlobs"] == {path: code.candidate()[path] for path in members}
    assert facts["baseBlobs"] == {path: code.base()[path] for path in members}
    assert facts["onboardingTrace"]["sourceBlobs"] == facts["sourceBlobs"]
    assert facts["onboardingTrace"]["subject"] == f"onboarding:{representative}"
    assert facts["admits"] == ["onboarding_trace"]
    assert items[0]["satisfiedBy"] is None


@pytest.mark.parametrize("shape", ["invalid-json", "directory", "symlink"])
def test_absent_declaration_is_no_suppression_but_invalid_candidate_is_unreadable(
    tmp_path: Path,
    shape: str,
) -> None:
    world = build_world(tmp_path)
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    code = CodeTrees.open(world.code, base, base)
    assert generated_copy_facts(code, [".hermes/skills/rule.md"]) == {}
    if shape == "directory":
        write(world.code, {"generated-copies.json/payload": "not a declaration\n"})
    elif shape == "symlink":
        (world.code / "generated-copies.json").symlink_to("missing-declaration")
    else:
        write(world.code, {"generated-copies.json": "not json\n"})
    commit_all(world.code, "unreadable candidate declaration")
    code = CodeTrees.open(world.code, base, git(world.code, "rev-parse", "HEAD^{tree}"))
    with pytest.raises(CodeReadError, match=r"generated-copies\.json"):
        generated_copy_facts(code, [])
    memory = KnowledgeSide.from_tree(
        "memory", "memory", knowledge_tree_from_directory(world.memory)
    )
    document = compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))
    assert document["state"] == "incomplete"
    assert "generated-copies.json" in str(document["incomplete"])


@pytest.mark.parametrize("changed", [False, True])
def test_present_gitlink_declaration_is_incomplete(tmp_path: Path, changed: bool) -> None:
    world = build_world(tmp_path)
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    commit = git(world.code, "rev-parse", "HEAD")
    if changed:
        write(world.code, {"copy/rule.md": "changed copy\n"})
        git(world.code, "add", "copy/rule.md")
    git(
        world.code, "update-index", "--add", "--cacheinfo", f"160000,{commit},generated-copies.json"
    )
    candidate = git(world.code, "write-tree")
    if not changed:
        base = candidate
    entry = git(world.code, "ls-tree", candidate, "generated-copies.json")
    assert entry.startswith("160000 commit")
    code = CodeTrees.open(world.code, base, candidate)
    assert "generated-copies.json" not in code.candidate()
    tree = knowledge_tree_from_directory(world.memory)
    memory = KnowledgeSide.from_tree("memory", "memory", tree)
    document = compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))
    assert document["state"] == "incomplete"
    assert "generated-copies.json" in str(document["incomplete"])
    assert code.objects.tree_modes(candidate)["generated-copies.json"] == "160000"
    with pytest.raises(CodeReadError, match=r"generated-copies\.json.*regular file"):
        generated_copy_facts(code, [])


@pytest.mark.parametrize("nested", [False, True])
def test_present_empty_tree_declaration_is_incomplete(tmp_path: Path, nested: bool) -> None:
    world = build_world(tmp_path)

    def make_tree(rows: bytes) -> str:
        return (
            subprocess.check_output(["git", "-C", str(world.code), "mktree", "-z"], input=rows)
            .decode()
            .strip()
        )

    empty = make_tree(b"")
    if nested:
        empty = make_tree(f"040000 tree {empty}\tempty\0".encode())
    base = git(world.code, "rev-parse", "HEAD^{tree}")
    rows = subprocess.check_output(["git", "-C", str(world.code), "ls-tree", "-z", base])
    candidate = make_tree(rows + f"040000 tree {empty}\tgenerated-copies.json\0".encode())
    assert git(world.code, "ls-tree", candidate, "generated-copies.json").startswith("040000 tree")
    code = CodeTrees.open(world.code, candidate, candidate)
    assert "generated-copies.json" not in code.candidate()
    with pytest.raises(CodeReadError, match=r"generated-copies\.json.*regular file"):
        generated_copy_facts(code, [])
    memory = KnowledgeSide.from_tree(
        "memory", "memory", knowledge_tree_from_directory(world.memory)
    )
    document = compute_worklist(WorklistInputs(code, memory, memory, pairing={}, owner="leaf-one"))
    assert document["state"] == "incomplete"
    assert "generated-copies.json" in str(document["incomplete"])
