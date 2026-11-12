"""L79's pathname-only tree facts: readable knowledge, actual scope and bounded calls."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_reader import paths
from agents_remember.application.knowledge_reader import tree_coverage as coverage
from agents_remember.application.knowledge_reader.files import FileRead
from agents_remember.application.knowledge_reader.paths import tree_listing
from agents_remember.application.knowledge_reader.selection import ReaderSelection, open_selection
from agents_remember.application.knowledge_reader.timeline import TIMELINES, record_timeline
from agents_remember.memory.knowledge_index import HistoryRow
from agents_remember.models.knowledge.continuation import decode_continuation
from test_knowledge_reader import (
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    World,
    _client,
    _commit,
    _history,
    _invariant_row,
    _realization,
    _sidecar,
    _write_json,
)

pytest_plugins = ("test_knowledge_reader",)


def test_presence_and_coverage_count_only_scoped_code_cards(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = open_selection(world.config, "demo", world.second)
    assert isinstance(opened, ReaderSelection)
    calls: list[str] = []
    memory = {
        "onboarding/own/overview.md",
        "onboarding/deep/sub/overview.md",
        "onboarding/card/a.py.md",
        "onboarding/sidecar/a.py.json",
        "onboarding/card/deleted.py.md",
        "onboarding/card/ignored.py.md",
        "onboarding/card/outside.bin.md",
    }
    paths = ["own/a.py", "card/a.py", "card/ignored.py", "card/outside.bin"]
    settings = '{"onboarding":{"storage":{"mode":"memory-repo"},"pathRules":{"include":{"fileTypes":[".py"]},"exclude":{"paths":["**/ignored.py"]}}}}'

    def memory_paths(*args: object) -> coverage.Pathnames:
        calls.append("memory")
        return coverage.Pathnames(
            files=memory, directories={"onboarding/empty", "onboarding/sidecar"}
        )

    def code_paths(*args: object) -> coverage.Pathnames:
        calls.append("code")
        return coverage.Pathnames(
            files=set(paths),
            directories={"own", "deep", "card", "sidecar", "empty", "indexed", "wide"},
            sources=set(paths),
        )

    def metadata(*args: object) -> FileRead:
        calls.append("settings")
        return FileRead("system/settings.json", "present", settings)

    monkeypatch.setattr(coverage, "_memory_paths", memory_paths)
    monkeypatch.setattr(coverage, "_git_paths", code_paths)
    monkeypatch.setattr(coverage, "read_memory_file", metadata)
    with opened:
        for scale in (1, 20):
            paths[:] = ["own/a.py", "card/a.py", "card/ignored.py", "card/outside.bin"] + [
                f"wide/{n}.py" for n in range(scale)
            ]
            monkeypatch.setattr(
                "agents_remember.application.knowledge_reader.paths._entry_counts",
                lambda *args: {"indexed": 1},
            )
            children = tree_listing(opened, ".")["children"]
            by_name = {row["name"]: row for row in children}
            assert by_name["own"]["hasOverview"] is True
            assert by_name["deep"]["hasOverview"] is False and by_name["deep"]["hasKnowledge"]
            assert by_name["card"]["coverage"] == {"state": "counted", "files": 1, "cards": 1}
            assert not by_name["sidecar"]["hasKnowledge"] and not by_name["empty"]["hasKnowledge"]
            assert by_name["indexed"]["hasKnowledge"]
            assert by_name["wide"]["coverage"] == {"state": "counted", "files": scale, "cards": 0}
            assert by_name["empty"]["onboarding"] and by_name["sidecar"]["onboarding"]
    assert calls == ["code", "memory", "settings"] * 2


def test_unreadable_scope_and_memory_do_not_invent_counts_or_false_presence(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = open_selection(world.config, "demo", world.second)
    assert isinstance(opened, ReaderSelection)
    monkeypatch.setattr(
        coverage,
        "_memory_paths",
        lambda *args: coverage.Pathnames(files={"onboarding/card/a.py.md"}),
    )
    monkeypatch.setattr(
        coverage, "read_memory_file", lambda *args: FileRead("system/settings.json", "absent")
    )
    with opened:
        children = coverage.read_tree_listing(opened, ".", {})["children"]
        children = [row for row in children if row["name"] == "card"]
        assert children[0]["hasKnowledge"]
        assert children[0]["coverage"]["state"] == "unavailable"
        assert "settings.json absent" in children[0]["coverage"]["detail"]
        assert "files" not in children[0]["coverage"]
        monkeypatch.setattr(coverage, "_memory_paths", fail_memory)
        unknown = coverage.read_tree_listing(opened, ".", {"card": 1})["children"]
        unknown = [row for row in unknown if row["name"] == "card"]
        assert "hasKnowledge" not in unknown[0] and "hasOverview" not in unknown[0]
        assert unknown[0]["coverage"]["state"] == "unavailable"
        assert "denied" in unknown[0]["coverage"]["detail"]
        monkeypatch.setattr(coverage, "_memory_paths", lambda *args: coverage.Pathnames())
        monkeypatch.setattr(opened.index, "state", replace(opened.index.state, state="partial"))
        partial = coverage.read_tree_listing(opened, ".", {"card": 0})["children"]
        card = next(row for row in partial if row["name"] == "card")
        assert "hasKnowledge" not in card  # Failed index parsing does not prove absence.
        monkeypatch.setattr(opened.index, "state", replace(opened.index.state, state="complete"))
        unavailable = replace(opened, code_tree=None, code_note="missing code pairing")
        monkeypatch.setattr(
            coverage,
            "read_memory_file",
            lambda *args: FileRead(
                "system/settings.json",
                "present",
                '{"onboarding":{"storage":{"mode":"memory-repo"}}}',
            ),
        )
        unknown = coverage.read_tree_listing(unavailable, ".", {"card": 1})["children"]
        assert "missing code pairing" in unknown[0]["coverage"]["detail"]


def fail_memory(*args: object) -> coverage.Pathnames:
    raise PermissionError("denied")


@pytest.mark.parametrize("pinned", [False, True], ids=["working-directory", "pinned-memory"])
def test_real_composed_tree_counts_and_presence_from_selected_settings(
    world: World, pinned: bool
) -> None:
    """Real Git/code paths, memory enumeration, scope settings and indexed descendants."""
    for name in [
        "own/a.py",
        "deep/sub/a.py",
        "card/a.py",
        "card/ignored.py",
        "card/outside.bin",
        "indexed/nested/a.py",
        "empty/a.py",
        "sidecar/a.py",
    ]:
        path = world.code / "probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    code_commit = _commit(world.code, "coverage code fixture")
    prose = {
        "own/overview.md": "# Own",
        "deep/sub/overview.md": "# Descendant",
        "card/a.py.md": "# Card",
        "card/deleted.py.md": "# Deleted",
        "card/ignored.py.md": "# Excluded",
        "card/outside.bin.md": "# Out of scope",
    }
    for name, text in prose.items():
        path = world.memory / "onboarding/probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name, document in {
        "sidecar/a.py.json": _sidecar("probe/sidecar/a.py", []),
        "indexed/nested/a.py.json": _sidecar(
            "probe/indexed/nested/a.py", [_realization("RLZ-C0WNT1", REVIEW_INVARIANT, "counted")]
        ),
    }.items():
        path = world.memory / "onboarding/probe" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
    (world.memory / "onboarding/probe/empty").mkdir()
    settings = world.memory / "system/settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {"fileTypes": [".py"]},
                        "exclude": {"paths": ["**/ignored.py"]},
                    },
                }
            }
        )
    )
    memory_commit = _commit(world.memory, f"coverage memory fixture\n\nCode-Commit: {code_commit}")
    opened = open_selection(world.config, "demo", memory_commit if pinned else "published")
    assert isinstance(opened, ReaderSelection)
    with opened:
        assert opened.index.state.complete, opened.index.state
        answer = tree_listing(opened, "probe")
    rows = {row["name"]: row for row in answer["children"]}
    assert answer["code"]["state"] == "listed"
    assert rows["own"]["hasOverview"] and rows["own"]["hasKnowledge"]
    assert rows["deep"]["hasOverview"] is False and rows["deep"]["hasKnowledge"]
    assert rows["card"]["coverage"] == {"state": "counted", "files": 1, "cards": 1}
    assert rows["own"]["coverage"] == {"state": "counted", "files": 1, "cards": 0}
    assert rows["empty"]["hasKnowledge"] is False
    assert rows["sidecar"]["hasKnowledge"] is False
    assert rows["indexed"]["hasKnowledge"] and rows["indexed"]["entries"] == 1
    assert rows["indexed"]["hasOverview"] is False
    assert rows["sidecar"]["onboarding"]
    if not pinned:  # Git cannot retain an empty directory; the working mirror can.
        assert rows["empty"]["onboarding"]


def test_unsearchable_mirror_root_is_unavailable_not_zero(world: World) -> None:
    opened = open_selection(world.config, "demo", "published")
    assert isinstance(opened, ReaderSelection)
    parent = world.memory / "onboarding/dashboard"
    before = parent.stat().st_mode
    parent.chmod(0)
    try:
        with opened:
            rows = tree_listing(opened, "dashboard/src")["children"]
        assert os.geteuid() != 0, "This permission witness needs the ordinary non-root test user"
        directories = [row for row in rows if row["kind"] == "dir"]
        assert directories
        for row in directories:
            assert row["coverage"]["state"] == "unavailable"
            assert "PermissionError" in row["coverage"]["detail"]
            assert "cards" not in row["coverage"] and "files" not in row["coverage"]
            assert "hasOverview" not in row and "hasKnowledge" not in row
    finally:
        parent.chmod(before)


def test_requested_mirror_symlink_cannot_enumerate_outside_memory(
    world: World, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-memory"
    outside.mkdir()
    (outside / "secret.py.md").write_text("outside memory")
    (world.memory / "onboarding/escape").symlink_to(outside, target_is_directory=True)
    opened = open_selection(world.config, "demo", "published")
    assert isinstance(opened, ReaderSelection)
    with opened:
        memory, problem = coverage._read_memory(opened, "escape")
    assert memory is None and problem is not None
    assert "escapes the repository root" in problem


def test_any_memory_commit_or_branch_is_selectable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = world.read("selections")
    assert options["default"] == "published" and options["published"]["converted"] is True
    marks = {row["commit"]: row["converted"] for row in options["commits"]}
    assert marks == {world.second: True, world.first: True, world.unconverted: False}
    old = world.read("record", commit=world.first[:10], record_id=REVIEW_INVARIANT)
    assert (old["selection"]["commit"], old["record"]["revision"]) == (world.first, 1)
    assert old["selection"]["codeSource"] == "paired-code-commit"
    unconverted = world.read("path", commit=world.unconverted, path=REVIEW_PATH)
    assert unconverted["state"] == "not-converted"
    branch = world.read("path", commit="main", path=REVIEW_PATH)
    assert branch["selection"]["commit"] == world.second
    invalid = world.read("path", commit="not-a-branch", path=REVIEW_PATH)
    assert invalid["state"] == "invalid-request"
    assert "full or abbreviated" in invalid["detail"] and "branch" in invalid["detail"]
    client = _client(world)
    for name in ("main", world.second, world.second[:10]):
        response = client.get(
            "/api/knowledge/reader/path",
            params={"repo": "demo", "commit": name, "path": REVIEW_PATH},
        )
        assert (response.status_code, response.json()["selection"]["commit"]) == (200, world.second)
    from agents_remember.application.knowledge_reader import selection  # noqa: PLC0415

    real_git = selection.run_git

    def no_nul_git(root: Path, args: list[str], *options: Any) -> Any:
        assert all("\x00" not in arg for arg in args), "malformed name reached subprocess"
        return real_git(root, args, *options)

    with monkeypatch.context() as guarded:
        guarded.setattr(selection, "run_git", no_nul_git)
        refused = client.get(
            "/api/knowledge/reader/path?repo=demo&commit=a%00b&path=dashboard/src/data/review.ts"
        )
    assert (refused.status_code, refused.json()["state"]) == (400, "invalid-request")
    assert "full or abbreviated" in refused.json()["detail"]
    assert "branch" in refused.json()["detail"]
    for n in range(41):
        _commit(world.memory, f"later memory commit {n}")
    recent = world.read("selections")["commits"]
    assert len(recent) == 40 and world.first not in {row["commit"] for row in recent}
    old = world.read("record", commit=world.first, record_id=REVIEW_INVARIANT)
    assert old["record"]["revision"] == 1
    # A commit list that cannot be read is named, never an empty list (F11).
    from agents_remember.application.knowledge_reader import selection  # noqa: PLC0415

    def failing(_repository: Path) -> list[dict[str, Any]]:
        raise selection.ReaderReadError("git log failed")

    monkeypatch.setattr(selection, "_recent_commits", failing)
    assert world.read("selections")["commitsState"]["state"] == "unavailable"


def test_path_summary_bounds_utf8_prose_and_offers_the_whole_text(world: World) -> None:

    for size in (32_768, 80_000):
        text = "é" * size
        (world.memory / "onboarding/overview.md").write_text(text, encoding="utf-8")
        opened = open_selection(world.config, "demo", None)
        assert isinstance(opened, ReaderSelection)
        with opened:
            summary = paths.path_view(opened, ".")
            prose = summary["prose"]
            assert len(prose["text"].encode("utf-8")) <= 65_536
            assert prose["fullSize"] == len(text.encode("utf-8"))
            assert prose["truncated"] is (size > 32_768)
            assert paths.path_prose(opened, ".")["text"] == text
            assert summary["deeperEntries"] > 0


def test_public_route_loads_whole_prose_by_code_path_at_the_selected_tree(world: World) -> None:
    client = _client(world)
    for size in (32_768, 80_000):
        text = "é" * size
        for code_path, memory_path in (
            (".", "onboarding/overview.md"),
            (REVIEW_PATH, f"onboarding/{REVIEW_PATH}.md"),
        ):
            (world.memory / memory_path).write_text(text, encoding="utf-8")
            params = {"repo": "demo", "commit": "published", "path": code_path}
            summary = client.get("/api/knowledge/reader/path", params=params).json()["prose"]
            assert summary["truncated"] is (size > 32_768)
            assert summary["fullSize"] == len(text.encode("utf-8"))
            response = client.get("/api/knowledge/reader/prose", params=params)
            assert response.status_code == 200
            assert response.json()["state"] == "present"
            assert response.json()["text"] == text
            assert response.json()["path"] == memory_path

    pinned = client.get(
        "/api/knowledge/reader/prose",
        params={"repo": "demo", "commit": world.second, "path": REVIEW_PATH},
    )
    assert pinned.status_code == 200
    assert pinned.json()["text"].startswith("# dashboard/src/data/review.ts")
    assert pinned.json()["selection"]["commit"] == world.second
    for path in ("../system/settings.md", "a\x00b", "a\nb"):
        refused = client.get("/api/knowledge/reader/prose", params={"repo": "demo", "path": path})
        assert (refused.status_code, refused.json()["state"]) == (400, "invalid-request")
    for path in ("system/settings.json", f"onboarding/{REVIEW_PATH}.md"):
        absent = client.get("/api/knowledge/reader/prose", params={"repo": "demo", "path": path})
        assert (absent.status_code, absent.json()["state"]) == (200, "absent")
        assert "text" not in absent.json()
    (world.memory / f"onboarding/{REVIEW_PATH}.md").write_bytes(b"\xff")
    unavailable = client.get(
        "/api/knowledge/reader/prose", params={"repo": "demo", "path": REVIEW_PATH}
    )
    assert (unavailable.status_code, unavailable.json()["state"]) == (200, "unavailable")
    assert "UnicodeDecodeError" in unavailable.json()["detail"]


def test_public_record_route_resumes_older_events_with_bound_continuations(world: World) -> None:
    client = _client(world)
    for size in (55, 125):
        rows = [_invariant_row(f"ROW-P{n:05d}", f"event {n}") for n in range(size)]
        history_paths = set()
        for n, row in enumerate(rows):
            leaf = f"260928-MIK-L{1000 + n}"
            history_path = f"knowledge/history/{leaf}-attempt-2.json"
            history_paths.add(history_path)
            _write_json(
                world.memory,
                history_path,
                {**_history(leaf, closed=False, rows=[row]), "attempt": 2},
            )
        commit = _commit(world.memory, f"paged history {size}")
        params = {"repo": "demo", "commit": commit, "id": REVIEW_INVARIANT}
        response = client.get("/api/knowledge/reader/record", params=params)
        assert response.status_code == 200
        answer = response.json()
        assert answer["selection"]["commit"] == commit
        page = answer["timeline"]
        assert len(page["events"]) == 50
        token = page["continuation"]
        decoded = decode_continuation(token)
        assert decoded is not None
        assert decoded.continuation_format == "knowledge-continuation/v2"
        assert (decoded.view, decoded.seed, decoded.position) == (
            "reader-timeline",
            {"id": REVIEW_INVARIANT},
            50,
        )
        events = list(page["events"])
        while page["continuation"]:
            response = client.get(
                "/api/knowledge/reader/record",
                params={**params, "continuation": page["continuation"]},
            )
            assert response.status_code == 200
            page = response.json()["timeline"]
            assert page["page"]["start"] == len(events)
            assert 0 < len(page["events"]) <= 50
            events.extend(page["events"])
        assert len(events) == page["page"]["total"]
        captured = [event for event in events if event.get("path") in history_paths]
        assert {event["row"] for event in captured} == {row["id"] for row in rows}
        assert len(captured) == size
        assert all(event["source"] == "history" and event["attempt"] == 2 for event in captured)
        for changed in (
            {"commit": world.first, "continuation": token},
            {"id": SIBLING_INVARIANT, "continuation": token},
            {"continuation": "not-a-continuation"},
        ):
            response = client.get("/api/knowledge/reader/record", params={**params, **changed})
            assert response.status_code == 200
            refused = response.json()["timeline"]
            assert refused["state"] == "refused"
            assert refused["code"].startswith("continuation_")
            assert refused["events"] == [] and refused["continuation"] is None
        restarted = client.get("/api/knowledge/reader/record", params=params).json()["timeline"]
        assert restarted["events"] == events[:50]


def test_timeline_pages_fifty_events_and_refuses_a_changed_tree(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_remember.application.knowledge_reader import timeline  # noqa: PLC0415

    visits = 0
    builds = 0
    real_digest = timeline.sha256_digest
    real_timeline = timeline._timeline

    def counted_digest(value: Any) -> str:
        nonlocal visits
        visits += len(value["events"])
        return real_digest(value)

    monkeypatch.setattr(timeline, "sha256_digest", counted_digest)

    def counted_timeline(*args: Any) -> dict[str, Any]:
        nonlocal builds
        builds += 1
        return real_timeline(*args)

    monkeypatch.setattr(timeline, "_timeline", counted_timeline)

    opened = open_selection(world.config, "demo", world.second)
    assert isinstance(opened, ReaderSelection)
    with opened:
        record = opened.index.record(REVIEW_INVARIANT).value
        assert record is not None
        for size in (100, 1000):
            visits = 0
            builds = 0
            history = tuple(
                HistoryRow(
                    f"ROW-{n:06d}",
                    "reader-fixture",
                    "leaf",
                    False,
                    "knowledge/history/reader-fixture-attempt-2.json",
                    REVIEW_INVARIANT,
                    "no_impact",
                    {"reason": f"event {n} " + "é🧭" * 32},
                )
                for n in range(size)
            )
            # Different input manifests represent different captured trees in the real reader.

            TIMELINES.clear()
            page = record_timeline(opened, record, (), history)
            assert len(page["events"]) == 50
            token = page["continuation"]
            decoded = decode_continuation(token)
            assert decoded is not None
            events = list(page["events"])
            pages = 1
            while page["continuation"]:
                page = record_timeline(
                    opened, record, (), history, continuation=page["continuation"]
                )
                pages += 1
                assert len(page["events"]) <= 50
                events.extend(page["events"])
            rows = [event for event in events if event["source"] == "history"]
            assert [event["row"] for event in rows] == [row.id for row in history]
            assert [event["document"] for event in rows] == [row.document for row in history]
            assert all(event["attempt"] == 2 and event["path"] == history[0].path for event in rows)
            assert visits == len(events), "a page walk must hash each event only once"
            assert builds == 1
            assert decoded.manifest_digest == real_digest({"id": record.id, "events": events})
            payload_bytes = len(json.dumps(events, ensure_ascii=False).encode("utf-8"))
            assert TIMELINES.weight >= payload_bytes, "cache weight must price retained bytes"
            print(
                f"timeline events={len(events)} pages={pages} builds={builds} "
                f"hash_visits={visits} retained_bytes={TIMELINES.weight}"
            )
            _assert_timeline_reclamation(opened, record, events, monkeypatch)
            _assert_timeline_changed_tree(world, record, history, token)


def _assert_timeline_changed_tree(world: World, record: Any, history: Any, token: str) -> None:
    other = open_selection(world.config, "demo", world.first)
    assert isinstance(other, ReaderSelection)
    with other:
        refused = record_timeline(other, record, (), history, continuation=token)
        assert refused["state"] == "refused"
        assert refused["code"] == "continuation_binding_mismatch"
        assert refused["events"] == []


def _assert_timeline_reclamation(
    opened: ReaderSelection,
    record: Any,
    events: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents_remember.application.knowledge_reader import timeline  # noqa: PLC0415
    from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo  # noqa: PLC0415

    budget = TIMELINES.weight * 2
    cache = BoundedMemo(budget, weigh=timeline.timeline_weight)
    built = 0

    def materialized(*args: Any) -> dict[str, Any]:
        nonlocal built
        built += 1
        return {"sources": {"record": {"state": "read"}}, "events": events}

    with monkeypatch.context() as bounded:
        bounded.setattr(timeline, "TIMELINES", cache)
        bounded.setattr(timeline, "_timeline", materialized)
        for revision in ("a" * 40, "b" * 40, "c" * 40):
            page = record_timeline(replace(opened, revision=revision), record, (), ())
            assert page["events"] == events[:50]
            assert 0 < cache.weight <= budget
        assert built == 3 and len(cache) == 2
        # The oldest entry is reclaimed. The most recent hit costs no rebuild and returned
        # dictionaries cannot mutate the immutable manifest or invalidate its digest.
        latest = replace(opened, revision="c" * 40)
        page["events"][0]["subject"] = "caller mutation"
        assert record_timeline(latest, record, (), ())["events"] == events[:50]
        assert built == 3
        record_timeline(replace(opened, revision="a" * 40), record, (), ())
        assert built == 4 and len(cache) == 2 and cache.weight <= budget
        cache.clear()
        assert len(cache) == cache.weight == 0
        # An individually over-budget timeline remains readable, but is never retained.
        bounded.setattr(timeline, "TIMELINES", BoundedMemo(1, weigh=timeline.timeline_weight))
        page = record_timeline(opened, record, (), ())
        unretained = list(page["events"])
        while page["continuation"]:
            page = record_timeline(opened, record, (), (), continuation=page["continuation"])
            unretained.extend(page["events"])
        assert unretained == events
        assert timeline.TIMELINES.weight == 0 and len(timeline.TIMELINES) == 0
