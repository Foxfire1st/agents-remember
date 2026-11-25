"""The L109 largest-case producers and the fixtures they pin.

Each producer rebuilds one largest answer from a hermetic fixture under
``fixtures/l109_tool_answers``; the production tests that call them live in
test_tool_answer_table.py. Moved text-identical out of that module so no
test module carries the whole table (MIK-R196 rule 10)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.memory.knowledge_index.tree import directory_key
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from tool_entry_point_world import EntryPointWorld

# L109's largest cases are operation-produced, not recorded: the producers
# below rebuild each largest answer from a hermetic fixture and pin it
# byte-for-byte against its file (L109_REGEN=1 rewrites the files).
# Only volatile bytes are sanitized: the scratch prefixes read /world
# (the entry-point world) and /gen (other fixture roots), day folders read
# ar-tools-2000-01-01, and detail timestamps read zero. Git commits use
# fixed author/committer dates, so tree and commit identities are stable
# and stay in the pin. Every other byte is the produced answer.
_L109_FIXED_GIT_ENV = {
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_AUTHOR_NAME": "l109 fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "l109 fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def _l109_git(root: Path, *args: str) -> str:
    import subprocess  # noqa: PLC0415

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        **_L109_FIXED_GIT_ENV,
    }
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _l109_commit_all(root: Path, message: str) -> str:
    _l109_git(root, "add", "-A")
    _l109_git(root, "commit", "-q", "-m", message)
    return _l109_git(root, "rev-parse", "HEAD")


def _sanitize_l109_answer(answer: dict[str, Any], prefixes: dict[str, str]) -> str:
    import re  # noqa: PLC0415

    raw = json.dumps(answer, indent=1, sort_keys=True) + chr(10)
    for prefix in sorted(prefixes, key=len, reverse=True):
        raw = raw.replace(prefix, prefixes[prefix])
    raw = re.sub(r"ar-tools-\d{4}-\d{2}-\d{2}", "ar-tools-2000-01-01", raw)
    raw = re.sub(r"(?<=/)(\d{12})(?=-)", "000000000000", raw)
    # The tool-log detail file carries a random 8-hex suffix per call; the
    # pin names the stable stem (day, tool) and zeroes the random part.
    raw = re.sub(
        r"-(read_ar_files|knowledge_read|knowledge_diff|context_packet|read-ar-files|knowledge-read|knowledge-diff|context-packet)-[0-9a-f]{8}\.json",
        lambda match: f"-{match.group(1)}-00000000.json",
        raw,
    )
    return raw


def _write_l109_largest_pair(code: Path, memory: Path, onb: Path, index: int, rel: str) -> str:
    """One governed file with its card, sidecar and invariant in the largest case."""

    import hashlib  # noqa: PLC0415

    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )

    inv = derived_record_id("invariant", f"l109-largest-{index}")
    rlz = f"RLZ-{index:06d}"
    blob = _l109_git(code, "hash-object", rel)
    digest = "sha256:" + hashlib.sha256((code / rel).read_bytes()).hexdigest()
    anchor = {"locator": {"kind": "file"}, "blob": blob, "content": digest}
    card = onb / f"{rel}.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(f"# Mod {index}\n\n## Records\n\n- holds [1]\n", encoding="utf-8")
    (onb / f"{rel}.json").write_text(
        canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": rel,
                "references": {"1": {"targets": [{"kind": "code", "anchor": anchor}]}},
                "realizes": [
                    {
                        "id": rlz,
                        "invariant": inv,
                        "anchor": anchor,
                        "role": "primary-authority",
                        "rationale": f"Mod {index} lands both halves.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    inv_path = memory / "knowledge" / "invariants" / f"{inv}-mod-{index}.json"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        canonical_text(
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Mod {index} lands as one pair.",
                "applicability": f"Every mod {index}.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-largest-{index}",
                },
            }
        ),
        encoding="utf-8",
    )
    return inv


def _assert_l109_compact_reply(answer: dict[str, Any], files: list[str], memory: Path) -> None:
    assert answer.get("ok") is True and answer.get("state") == "named", answer
    assert "cut" not in answer and answer.get("overflow") is not True, answer
    assert count_response_tokens(answer) <= bound_for("read_ar_files"), answer
    assert answer.get("memoryRoot") == memory.as_posix(), answer
    rows = answer.get("files")
    assert isinstance(rows, list) and len(rows) == len(files) == 20, answer
    for source, row in zip(files, rows, strict=True):
        assert row == {
            "path": source,
            "exists": True,
            "cardStatus": "found",
            "brief": {"path": f"briefs/{source}.md", "freshness": "missing"},
        }, row
    notice = answer["briefsLeftOut"]
    assert "under memoryRoot with your own file tool" in notice, answer
    assert "next write covering the file or the whole folder" in notice, answer


def _produce_l109_read_ar_files_largest() -> str:
    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )
    from agents_remember.worktrees.worktree_contract import (  # noqa: PLC0415
        load_contract,
    )
    from test_read_ar_files import (  # noqa: PLC0415
        _write_overview,
        _write_route_index,
    )

    world = EntryPointWorld()
    world.build()
    try:
        # Twenty governed paths at typical package depths exercise the compact
        # answer before any brief has been saved.
        contract = load_contract(world.contract)
        code, memory = contract.code_worktree, contract.memory_worktree
        assert memory is not None, contract
        for name in ("user.email", "user.name"):
            _l109_git(code, "config", name, "l109-fixture@example.invalid")
            _l109_git(memory, "config", name, "l109-fixture@example.invalid")
        package = (
            "pkg/controlplane/lifecycle/series_worktree_reconciliation/requirement_attempt_evidence"
        )
        files = [f"{package}/mod{index}.py" for index in range(10)] + [
            f"{package}/sub/deep{index}.py" for index in range(10)
        ]
        for index, rel in enumerate(files):
            path = code / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"def mod{index}():{chr(10)}    return {index}{chr(10)}",
                encoding="utf-8",
            )
        onb = memory / "onboarding"
        (memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge" / "layout.json").write_text(
            canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        _write_overview(onb, "", "# Repo overview\nroot text\n")
        _write_overview(onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(onb, f"{package}/sub", "# sub overview\nsub text\n")
        members = []
        for index, rel in enumerate(files):
            members.append(_write_l109_largest_pair(code, memory, onb, index, rel))
        fam = derived_record_id("family", "l109-largest-family")
        (memory / "knowledge" / "families").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge" / "families" / f"{fam}-largest.json").write_text(
            canonical_text(
                {
                    "schema": "ar-family/v1",
                    "id": fam,
                    "revision": 1,
                    "status": "accepted",
                    "title": "Largest",
                    "guarantee": "Twenty governed files land together.",
                    "members": members,
                    "routes": ["pkg"],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260928-MIK",
                        "leaf": "260928-MIK-L109",
                        "legacyId": "legacy-largest-family",
                    },
                }
            ),
            encoding="utf-8",
        )
        _write_route_index(onb, "pkg", covered=list(files))
        _l109_commit_all(code, "twenty governed files")
        _l109_commit_all(memory, "twenty governing cards")
        task_context = {
            "task_document_ref": {"repository": "repo-a", "path": "m/1_leaf.json"},
            "contract_path": world.contract.as_posix(),
        }
        tree = directory_key(memory)
        memory_files = world.census(memory)
        assert _l109_git(memory, "status", "--porcelain") == ""
        assert not (memory / "briefs").exists()
        outcome, out = world.call(
            "read_ar_files",
            {"repo_id": "repo-a", "files": list(files), "task_context": task_context},
        )
        assert outcome == "RETURNED", out
        assert isinstance(out, dict)
        _assert_l109_compact_reply(out, files, memory)
        assert directory_key(memory) == tree
        assert world.census(memory) == memory_files
        assert _l109_git(memory, "status", "--porcelain") == ""
        assert not (memory / "briefs").exists()
        return _sanitize_l109_answer(out, {world.coord.as_posix(): "/world"})
    finally:
        world.close()


def _produce_l109_knowledge_read_largest() -> str:
    import test_knowledge_paging as paging  # noqa: PLC0415
    from knowledge_index_test_support import (  # noqa: PLC0415
        init_repository,
    )

    tmp = Path(tempfile.mkdtemp())
    mem = tmp / "memory"
    init_repository(mem)
    paging.write_family_tree(mem, paging._statements(18, words=20), directory=paging._DEEP)
    _l109_commit_all(mem, "deep family")
    world = EntryPointWorld()
    world.build()
    try:
        outcome, first = world.call(
            "knowledge_read",
            {
                "memoryRoot": str(mem),
                "view": "source_context",
                "sourcePath": paging._member_path(1, paging._DEEP),
            },
        )
        assert outcome == "RETURNED", first
        assert isinstance(first, dict)
        biggest = first
        biggest_tokens = count_response_tokens(first)
        continuation = first.get("continuation")
        while continuation is not None:
            outcome, response = world.call(
                "knowledge_read",
                {
                    "memoryRoot": str(mem),
                    "view": "source_context",
                    "sourcePath": paging._member_path(1, paging._DEEP),
                    "continuation": continuation,
                },
            )
            assert outcome == "RETURNED", response
            assert isinstance(response, dict)
            measured = count_response_tokens(response)
            if measured > biggest_tokens:
                biggest, biggest_tokens = response, measured
            continuation = response.get("continuation")
        return _sanitize_l109_answer(
            biggest, {world.coord.as_posix(): "/world", tmp.as_posix(): "/gen"}
        )
    finally:
        world.close()


def _produce_l109_knowledge_diff_largest() -> str:
    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )
    from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
        LAYOUT_MARKER_PATH,
        record_path,
    )
    from knowledge_index_test_support import (  # noqa: PLC0415
        init_repository,
        write_document,
    )

    tmp = Path(tempfile.mkdtemp())
    mem = tmp / "memory"
    init_repository(mem)
    write_document(mem, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "1"})
    for index in range(50):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        write_document(
            mem,
            record_path("invariant", inv, f"rec-{index}"),
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Record {index} holds.",
                "applicability": "Always.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-diff-{index}",
                },
            },
        )
    _l109_commit_all(mem, "base fifty")
    for index in range(35):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        path = mem / record_path("invariant", inv, f"rec-{index}")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["statement"] = f"Record {index} holds, changed."
        path.write_text(canonical_text(document), encoding="utf-8")
    for index in range(50, 80):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        write_document(
            mem,
            record_path("invariant", inv, f"rec-{index}"),
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Record {index} holds, added.",
                "applicability": "Always.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-diff-{index}",
                },
            },
        )
    for index in range(40, 45):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        (mem / record_path("invariant", inv, f"rec-{index}")).unlink()
    _l109_commit_all(mem, "change one hundred")
    world = EntryPointWorld()
    world.build()
    try:
        outcome, out = world.call(
            "knowledge_diff",
            {
                "memoryRoot": str(mem),
                "beforeRevision": "HEAD~1",
                "afterRevision": "HEAD",
            },
        )
        assert outcome == "RETURNED", out
        assert isinstance(out, dict)
        return _sanitize_l109_answer(
            out, {world.coord.as_posix(): "/world", tmp.as_posix(): "/gen"}
        )
    finally:
        world.close()
