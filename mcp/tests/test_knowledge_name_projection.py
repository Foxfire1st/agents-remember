"""``knowledge_read`` names records instead of carrying them (MIK-R109 rule 2).

Through the registered payload builder, on hermetic converted trees: every
row of every view holds the text ID, kind, computed state and location and
no record text; each location opens the record's file as written; a caller
without a memory worktree gets absolute paths with the commit beside the
answer. Paging, counts and short-form blocks stay. List views write their
list as a report and answer counts, first entries and the location.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.knowledge_names import (
    NameResolver,
    project_knowledge_body,
)
from agents_remember.mcp.tools.knowledge import (
    ReadToolRequest,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from knowledge_index_test_support import (
    FAMILY,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    commit_all,
    init_repository,
    write_review_tree,
)


def _tree(root: Path) -> Path:
    memory = root / "memory"
    init_repository(memory)
    write_review_tree(memory)
    commit_all(memory)
    return memory


def _head(memory: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(memory), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


class KnowledgeNameProjectionTests(unittest.TestCase):
    maxDiff = None

    def test_invariant_rows_name_but_do_not_carry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory = _tree(root)
            out = knowledge_read_payload(
                ReadToolRequest(
                    memory_root=str(memory),
                    view="invariant",
                    invariant_revision_id=text_uuid("revision", f"{REVIEW_INVARIANT}@1"),
                    limit=5,
                ),
                coordination_root=str(root / "coord"),
            )
            TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
            self.assertEqual(out["state"], "view", out)
            rows = out["payload"]["rows"]
            self.assertTrue(rows, "the invariant view names no record")
            (member,) = [row for row in rows if row["id"] == REVIEW_INVARIANT]
            self.assertEqual(member["id"], REVIEW_INVARIANT)
            self.assertEqual(set(member), {"id", "kind", "state", "location"}, member)
            location = member["location"]
            found = Path(location["file"])
            self.assertTrue(found.is_file(), location)
            self.assertIn(REVIEW_INVARIANT, found.read_text(encoding="utf-8"))
            self.assertEqual(out["memoryCommit"], _head(memory))
            whole = json.dumps(out)
            self.assertNotIn("A comparison shows unchanged realizations", whole)
            (realization,) = [row for row in rows if row["id"] == "RLZ-RVW001"]
            self.assertIsNotNone(realization["location"], realization)
            assert realization["location"] is not None
            sidecar = Path(realization["location"]["file"])
            self.assertTrue(sidecar.is_file(), realization)
            self.assertIn("RLZ-RVW001", sidecar.read_text(encoding="utf-8"))

    def test_family_rows_locate_members_in_their_own_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory = _tree(root)
            out = knowledge_read_payload(
                ReadToolRequest(
                    memory_root=str(memory),
                    view="family",
                    family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
                    limit=10,
                ),
                coordination_root=str(root / "coord"),
            )
            TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
            self.assertEqual(out["state"], "view", out)
            rows = out["payload"]["rows"]
            self.assertTrue(rows, "the family view names no record")
            for row in rows:
                with self.subTest(row=row.get("id")):
                    self.assertEqual(
                        set(row) - {"role", "memberOf"},
                        {"id", "kind", "state", "location"},
                        row,
                    )
                    self.assertIn(
                        row["kind"],
                        {
                            "invariant",
                            "realization",
                            "family",
                            "decision",
                            "proof",
                            "family_header_reference",
                        },
                        row,
                    )
                    self.assertIsNotNone(row["state"], row)
                    if row.get("role") == "member":
                        # A membership names its member record: the kind is
                        # the member's own (an invariant, never "member"),
                        # the ID is the member's own, the family rides
                        # beside it.
                        self.assertEqual(row["kind"], "invariant", row)
                        self.assertTrue(
                            row["id"].startswith("INV-"),
                            row,
                        )
                        self.assertTrue(
                            str(row["memberOf"]).startswith("FAM-"),
                            row,
                        )
                    if row["location"] is not None:
                        found = Path(row["location"]["file"])
                        self.assertTrue(found.is_file(), row)
            whole = json.dumps(out)
            self.assertNotIn("Family context is read from the comparison", whole)

    def test_non_admitted_views_are_refused_unknown_view(self) -> None:
        # The authorized closure is three views (source_context, invariant,
        # family): the former work-through names are refused, not answered.
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory = _tree(root)
            for view in ("review_matrix", "curation_queue"):
                with self.subTest(view=view):
                    out = knowledge_read_payload(
                        ReadToolRequest(memory_root=str(memory), view=view, limit=5),
                        coordination_root=str(root / "coord"),
                    )
                    TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
                    self.assertEqual(out["state"], "refused", out)
                    self.assertEqual(out["refusalCode"], "unknown_view", out)
                    self.assertNotIn("report", out, out)

    def test_refusals_pass_through_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            plain = root / "plain"
            plain.mkdir()
            out = knowledge_read_payload(
                ReadToolRequest(memory_root=str(plain), view="invariant"),
                coordination_root=str(root / "coord"),
            )
            self.assertEqual(out["state"], "refused", out)
            self.assertEqual(out["refusalCode"], "legacy-format", out)
            self.assertNotIn("report", out, out)

    def test_projection_drops_record_text_but_keeps_short_forms(self) -> None:
        body = {
            "state": "view",
            "currentness": {
                "codeTree": None,
                "counts": {"current": 1},
                "invariants": [
                    {
                        "id": "INV-KEEP",
                        "state": "current",
                        "entries": [
                            {
                                "id": "RLZ-KEEP",
                                "kind": "realization",
                                "path": "src/a.py",
                                "state": "current",
                                "blobDigest": "0" * 64,
                            }
                        ],
                    },
                    {"id": "INV-DROPPED", "state": "stale", "entries": []},
                ],
                "families": [],
            },
            "payload": {
                "rows": [
                    {
                        "kind": "member",
                        "id": "INV-KEEP",
                        "state": "current",
                        "statement": "/record text must not travel",
                    }
                ]
            },
        }

        class _Resolver(NameResolver):
            def __init__(self) -> None:
                self.memory_commit = None

            def text_id(self, _value: str) -> str | None:  # type: ignore[override]
                return None

            def record_file(self, _value: str) -> str | None:  # type: ignore[override]
                return None

            def entry_file(self, _value: str) -> str | None:  # type: ignore[override]
                return None

        projected = project_knowledge_body(body, _Resolver())  # type: ignore[arg-type]
        whole = json.dumps(projected)
        self.assertNotIn("record text must not travel", whole)
        self.assertNotIn("blobDigest", whole)
        self.assertNotIn("INV-DROPPED", whole)
        (row,) = projected["payload"]["rows"]
        self.assertEqual(row["id"], "INV-KEEP")
        self.assertEqual(
            projected["currentness"]["invariants"],
            [
                {
                    "id": "INV-KEEP",
                    "state": "current",
                    "entries": [],
                }
            ],
        )

    def test_header_reference_names_id_state_and_location(self) -> None:
        body = {
            "state": "view",
            "currentness": {"codeTree": None, "counts": {}, "invariants": []},
            "payload": {"rows": []},
            "page": {
                "headerReference": {
                    "kind": "family_header_reference",
                    "family": "FAM-KEEP",
                }
            },
        }

        class _Resolver(NameResolver):
            def __init__(self) -> None:
                self.memory_commit = None

            def text_id(self, _value: str) -> str | None:  # type: ignore[override]
                return None

            def record_file(self, _value: str) -> str | None:  # type: ignore[override]
                return None

            def entry_file(self, _value: str) -> str | None:  # type: ignore[override]
                return None

        projected = project_knowledge_body(body, _Resolver())  # type: ignore[arg-type]
        reference = projected["page"]["headerReference"]
        # The consumer text ID and the state ride beside the family: a
        # header reference is a named pair, never a bare pointer.
        self.assertEqual(reference["id"], "FAM-KEEP", reference)
        self.assertEqual(reference["family"], "FAM-KEEP", reference)
        self.assertIn("state", reference, reference)
        self.assertIn("location", reference, reference)

    def test_header_reference_projects_family_kind_and_state_values(self) -> None:
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            _project_reference,
        )

        states = {"FAM-KEEP": "stale"}
        reference = _project_reference(
            {"kind": "family_header_reference", "family": "FAM-KEEP"},
            None,  # type: ignore[arg-type]
            states,
        )
        # A header reference names a family: its kind is the family's,
        # and its state is the evaluated value, not a key presence.
        self.assertEqual(reference["kind"], "family", reference)
        self.assertEqual(reference["id"], "FAM-KEEP", reference)
        self.assertEqual(reference["state"], "stale", reference)
        failed = _project_reference(
            {"kind": "family_header_reference", "family": "FAM-NEW"},
            None,  # type: ignore[arg-type]
            {},
            "unverifiable",
        )
        self.assertEqual(failed["kind"], "family", failed)
        self.assertEqual(failed["state"], "unverifiable", failed)
        unmeasured = _project_reference(
            {"kind": "family_header_reference", "family": "FAM-NEW"},
            None,  # type: ignore[arg-type]
            {},
        )
        # Null names an unmeasured family, never a current one.
        self.assertIsNone(unmeasured["state"], unmeasured)

    def test_structural_row_kinds_name_records_with_endpoint_roles(self) -> None:
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            _project_row,
        )

        # A structural row names its record (MIK-R109 rule 2) with the
        # endpoint riding the role beside it: shared IDs stay distinct
        # endpoints, so (kind, id, role) is unique wherever (kind, id)
        # was. No structural kind reaches a projected row.
        cases = (
            ({"kind": "member"}, "invariant", "member"),
            ({"kind": "member_reference"}, "invariant", "reference"),
            ({"kind": "family_header"}, "family", "header"),
            ({"kind": "advertised_family"}, "family", "advertised"),
            ({"kind": "chain_family"}, "family", "chain"),
            ({"kind": "family_header_reference"}, "family", "reference"),
        )
        for row, expected_kind, expected_role in cases:
            with self.subTest(row=row):
                projected = _project_row(
                    dict(row),
                    None,  # type: ignore[arg-type]
                    {},
                    None,
                )
                self.assertEqual(projected["kind"], expected_kind, projected)
                self.assertEqual(projected.get("role"), expected_role, projected)

    def test_family_state_values_and_failure_fallback(self) -> None:
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            _project_family,
        )

        stale = _project_family(
            {"id": "FAM-OLD"},
            None,  # type: ignore[arg-type]
            {"FAM-OLD": "stale"},
        )
        self.assertEqual(stale["kind"], "family", stale)
        self.assertEqual(stale["state"], "stale", stale)
        failed = _project_family(
            {"id": "FAM-NEW"},
            None,  # type: ignore[arg-type]
            {},
            "unverifiable",
        )
        self.assertEqual(failed["state"], "unverifiable", failed)
        unmeasured = _project_family(
            {"id": "FAM-NEW"},
            None,  # type: ignore[arg-type]
            {},
        )
        self.assertIsNone(unmeasured["state"], unmeasured)

    def test_scope_continuations_deliver_names_not_prose(self) -> None:
        # MIK-R109 round-1 F10: an accepted scope continuation carries
        # prose-bearing items, not rows. Distinctive sentinels in the
        # invariant statement, the family guarantee and the realization
        # rationale must reach no delivered page -- first or continued --
        # through the registered payload builder.
        from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
            file_sidecar_path,
            record_path,
        )

        sentinels = (
            "Sentinel quasar invariant prose QZX-1",
            "Sentinel quasar family prose QZX-2",
            "Sentinel quasar realization prose QZX-3",
        )
        # Three padded fields (~14K chars each, under the 20K record
        # bound) total past the 8K-token page threshold, so the scope walk
        # must mint a continuation page.
        padding = " padding" * 1800
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory = root / "memory"
            init_repository(memory)
            write_review_tree(memory)
            invariant_file = memory / record_path(
                "invariant", REVIEW_INVARIANT, "unchanged-realization-context"
            )
            document = json.loads(invariant_file.read_text(encoding="utf-8"))
            document["statement"] = f"{sentinels[0]}.{padding}"
            invariant_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
            family_file = next((memory / "knowledge" / "families").glob("*.json"))
            family = json.loads(family_file.read_text(encoding="utf-8"))
            family["guarantee"] = f"{sentinels[1]}.{padding}"
            family_file.write_text(json.dumps(family, indent=2), encoding="utf-8")
            sidecar_file = memory / file_sidecar_path(REVIEW_PATH)
            sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
            (realization,) = [entry for entry in sidecar["realizes"] if entry["id"] == "RLZ-RVW001"]
            realization["rationale"] = f"{sentinels[2]}.{padding}"
            sidecar_file.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
            commit_all(memory)
            pages: list[dict[str, Any]] = []
            request = ReadToolRequest(
                memory_root=str(memory),
                view="source_context",
                source_path=REVIEW_PATH,
            )
            out = knowledge_read_payload(request, coordination_root=str(root / "coord"))
            while True:
                TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
                self.assertEqual(out["state"], "page", out)
                pages.append(out)
                continuation = out.get("continuation")
                if continuation is None:
                    break
                out = knowledge_read_payload(
                    ReadToolRequest(
                        memory_root=str(memory),
                        view="source_context",
                        source_path=REVIEW_PATH,
                        continuation=continuation,
                    ),
                    coordination_root=str(root / "coord"),
                )
            self.assertGreater(len(pages), 1, "the walk never reached a continuation page")
            for page in pages:
                with self.subTest(continuation=bool(page.get("continuation"))):
                    self.assertNotIn("items", page.get("payload", {}), page)
                    whole = json.dumps(page)
                    for sentinel in sentinels:
                        self.assertNotIn(sentinel, whole)

    def test_registered_leaf_walk_delivers_names_not_prose(self) -> None:
        # MIK-R109 round-3 F10: the registered mirror of the scope control
        # above, on the leaf route. The same sentinel-bearing tree is
        # walked through the registered handler: every leaf continuation
        # is honored, the skimmed names of each participant (invariant,
        # family, realization) are delivered, and no sentinel prose
        # reaches any page. Leaf continuations are response="leaf"; the
        # accepted scope-token walk is the sibling test below.
        from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
            file_sidecar_path,
            record_path,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        sentinels = (
            "Sentinel quasar invariant prose QZX-1",
            "Sentinel quasar family prose QZX-2",
            "Sentinel quasar realization prose QZX-3",
        )
        padding = " padding" * 1800
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = world.memory
        write_review_tree(memory)
        invariant_file = memory / record_path(
            "invariant", REVIEW_INVARIANT, "unchanged-realization-context"
        )
        document = json.loads(invariant_file.read_text(encoding="utf-8"))
        document["statement"] = f"{sentinels[0]}.{padding}"
        invariant_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
        family_file = next((memory / "knowledge" / "families").glob("*.json"))
        family = json.loads(family_file.read_text(encoding="utf-8"))
        family_id = family["id"]
        family["guarantee"] = f"{sentinels[1]}.{padding}"
        family_file.write_text(json.dumps(family, indent=2), encoding="utf-8")
        sidecar_file = memory / file_sidecar_path(REVIEW_PATH)
        sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
        (realization,) = [entry for entry in sidecar["realizes"] if entry["id"] == "RLZ-RVW001"]
        realization["rationale"] = f"{sentinels[2]}.{padding}"
        sidecar_file.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        commit_all(memory)
        pages: list[dict[str, Any]] = []
        outcome, out = world.call(
            "knowledge_read",
            {
                "memoryRoot": str(memory),
                "view": "source_context",
                "sourcePath": REVIEW_PATH,
            },
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert isinstance(out, dict)
        while True:
            TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
            self.assertEqual(out["state"], "page", out)
            pages.append(out)
            continuation = out.get("continuation")
            if continuation is None:
                break
            outcome, out = world.call(
                "knowledge_read",
                {
                    "memoryRoot": str(memory),
                    "view": "source_context",
                    "sourcePath": REVIEW_PATH,
                    "continuation": continuation,
                },
            )
            self.assertEqual(outcome, "RETURNED", out)
            assert isinstance(out, dict)
        self.assertGreater(len(pages), 1, "the walk never reached a continuation page")
        names = {
            str(row.get("id")) for page in pages for row in page.get("payload", {}).get("rows", [])
        }
        for participant in (REVIEW_INVARIANT, family_id, "RLZ-RVW001"):
            self.assertIn(participant, names, names)
        for page in pages:
            with self.subTest(continuation=bool(page.get("continuation"))):
                self.assertNotIn("items", page.get("payload", {}), page)
                whole = json.dumps(page)
                for sentinel in sentinels:
                    self.assertNotIn(sentinel, whole)

    def _prepare_scope_walk(
        self,
    ) -> tuple[Any, str, str, tuple[str, str, str], tuple[str, str, str]]:
        """A scope-token walk's fixture: world, view, token, sentinels, names.

        The review tree carries one distinctive sentinel in its invariant
        statement, its family guarantee and one realization rationale, so
        the walk below can prove none reaches a page.
        """

        from agents_remember.application.knowledge_paging.scope_pages import (  # noqa: PLC0415
            PreparedScope,
            ScopePageRequest,
            TreeBinding,
            prepare_scope,
            scope_view,
        )
        from agents_remember.application.knowledge_read import (  # noqa: PLC0415
            open_read_context,
        )
        from agents_remember.application.published_intent import (  # noqa: PLC0415
            select_knowledge_dataset,
        )
        from agents_remember.mcp.tools.knowledge import (  # noqa: PLC0415
            INDEX_REPOSITORY_ID,
        )
        from agents_remember.models.knowledge.read import PathSeed  # noqa: PLC0415
        from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
            file_sidecar_path,
            record_path,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        sentinels = (
            "Sentinel scope invariant prose SC-1",
            "Sentinel scope family prose SC-2",
            "Sentinel scope realization prose SC-3",
        )
        padding = " padding" * 1800
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = world.memory
        write_review_tree(memory)
        invariant_file = memory / record_path(
            "invariant", REVIEW_INVARIANT, "unchanged-realization-context"
        )
        document = json.loads(invariant_file.read_text(encoding="utf-8"))
        document["statement"] = f"{sentinels[0]}.{padding}"
        invariant_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
        family_file = next((memory / "knowledge" / "families").glob("*.json"))
        family = json.loads(family_file.read_text(encoding="utf-8"))
        family_id = family["id"]
        family["guarantee"] = f"{sentinels[1]}.{padding}"
        family_file.write_text(json.dumps(family, indent=2), encoding="utf-8")
        sidecar_file = memory / file_sidecar_path(REVIEW_PATH)
        sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
        (realization,) = [entry for entry in sidecar["realizes"] if entry["id"] == "RLZ-RVW001"]
        realization["rationale"] = f"{sentinels[2]}.{padding}"
        sidecar_file.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        commit_all(memory)
        selected = select_knowledge_dataset(memory, coordination_root=world.coord)
        assert selected.memory_tree is not None
        context = open_read_context(selected.database_path, INDEX_REPOSITORY_ID)
        seed = PathSeed(path=REVIEW_PATH)
        prepared = prepare_scope(
            ScopePageRequest(
                database_path=selected.database_path,
                context=context,
                seed=seed,
                tree=TreeBinding(
                    tree_id=selected.memory_tree.tree_key,
                    index_state=selected.memory_tree.index_state,
                ),
            )
        )
        assert isinstance(prepared, PreparedScope), prepared
        return (
            world,
            scope_view(seed),
            prepared.deferred()["continuation"],
            sentinels,
            (REVIEW_INVARIANT, family_id, "RLZ-RVW001"),
        )

    def test_registered_scope_token_walk_delivers_names_not_prose(self) -> None:
        # MIK-R109 round-4 F10: the accepted scope-token walk. A scope
        # continuation (response="scope", minted for an identity seed of
        # the published-intent route) resumes through the registered
        # handler: every page carries projected rows, never items, and
        # the invariant/family/realization sentinel prose reaches no
        # page while participant names are delivered.
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world, view, token, sentinels, participants = self._prepare_scope_walk()
        assert isinstance(world, EntryPointWorld)
        pages: list[dict[str, Any]] = []
        outcome, out = world.call(
            "knowledge_read",
            {"memoryRoot": str(world.memory), "view": view, "continuation": token},
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert isinstance(out, dict)
        while True:
            TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
            self.assertEqual(out["state"], "page", out)
            pages.append(out)
            continuation = out.get("continuation")
            if continuation is None:
                break
            outcome, out = world.call(
                "knowledge_read",
                {
                    "memoryRoot": str(world.memory),
                    "view": view,
                    "continuation": continuation,
                },
            )
            self.assertEqual(outcome, "RETURNED", out)
            assert isinstance(out, dict)
        self.assertGreater(len(pages), 0, "the scope walk delivered no page")
        names = {
            str(row.get("id")) for page in pages for row in page.get("payload", {}).get("rows", [])
        }
        for participant in participants:
            self.assertIn(participant, names, names)
        for page in pages:
            with self.subTest(continuation=bool(page.get("continuation"))):
                self.assertNotIn("items", page.get("payload", {}), page)
                whole = json.dumps(page)
                for sentinel in sentinels:
                    self.assertNotIn(sentinel, whole)


class RegisteredDeclarationTests(unittest.TestCase):
    """Declarations open through registered reads: relative roots, dirty states, moves.

    MIK-R109 round-3 F12: locations bind the selected snapshot. A caller
    on its own worktree joins the answered memoryRoot with relative row
    files and opens the actual declaration; untracked files dirty the
    checkout; a checkout that moves between selection and projection is
    refused instead of mixing rows.
    """

    def _converted(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        memory = tmp / "memory"
        init_repository(memory)
        write_review_tree(memory)
        commit_all(memory)
        return memory

    def test_relative_declaration_opens_from_the_answered_root(self) -> None:
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = self._converted()
        outcome, out = world.call(
            "knowledge_read", {"memoryRoot": str(memory), "view": "invariant"}
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert isinstance(out, dict)
        TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
        rows = {str(row["id"]): row for row in out["payload"]["rows"] if isinstance(row, dict)}
        row = rows["RLZ-RVW001"]
        location = row["location"]
        self.assertFalse(Path(str(location["file"])).is_absolute(), row)
        found = Path(str(out["memoryTree"]["memoryRoot"])) / str(location["file"])
        self.assertTrue(found.is_file(), row)
        text = found.read_text(encoding="utf-8")
        self.assertIn("RLZ-RVW001", text)
        # The location names the declaration line, not a passing mention:
        # the line it names carries the entry's own id field.
        line = int(location["line"])
        self.assertIn('"RLZ-RVW001"', text.splitlines()[line - 1], row)

    def test_declaration_wins_over_an_earlier_history_mention(self) -> None:
        from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
            file_sidecar_path,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = self._converted()
        sidecar_file = memory / file_sidecar_path(REVIEW_PATH)
        sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
        # A prose mention of the entry before its declaration: the
        # rationale of the first realization names the second entry.
        realizes = sidecar["realizes"]
        self.assertGreaterEqual(len(realizes), 2, sidecar)
        target = realizes[1]["id"]
        realizes[0]["rationale"] = f"Earlier {target} mention; see below."
        sidecar_file.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        commit_all(memory, "history mention precedes the declaration")
        outcome, out = world.call(
            "knowledge_read", {"memoryRoot": str(memory), "view": "invariant"}
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert isinstance(out, dict)
        rows = {str(row["id"]): row for row in out["payload"]["rows"] if isinstance(row, dict)}
        row = rows[target]
        location = row["location"]
        found = Path(str(out["memoryTree"]["memoryRoot"])) / str(location["file"])
        lines = found.read_text(encoding="utf-8").splitlines()
        mention = next(index for index, text in enumerate(lines, start=1) if target in text)
        declared = next(
            index
            for index, text in enumerate(lines, start=1)
            if f'"{target}"' in text and '"id"' in text
        )
        self.assertGreater(declared, mention, row)
        self.assertEqual(int(location["line"]), declared, row)

    def test_untracked_files_dirty_the_checkout(self) -> None:
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = self._converted()
        outcome, clean = world.call(
            "knowledge_read", {"memoryRoot": str(memory), "view": "invariant"}
        )
        self.assertEqual(outcome, "RETURNED", clean)
        assert isinstance(clean, dict)
        self.assertFalse(clean.get("memoryDirty"), clean)
        (memory / "knowledge" / "untracked.json").write_text(
            json.dumps({"schema": "ar-invariant/v1", "id": "INV-UNTRACKED"}),
            encoding="utf-8",
        )
        outcome, dirty = world.call(
            "knowledge_read", {"memoryRoot": str(memory), "view": "invariant"}
        )
        self.assertEqual(outcome, "RETURNED", dirty)
        assert isinstance(dirty, dict)
        self.assertTrue(dirty.get("memoryDirty"), dirty)

    def test_resumed_walk_after_a_checkout_move_is_refused(self) -> None:
        from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
            record_path,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = self._converted()
        # Pad one statement past the page threshold so the first answer
        # mints a continuation the move can invalidate.
        invariant_file = memory / record_path(
            "invariant", REVIEW_INVARIANT, "unchanged-realization-context"
        )
        document = json.loads(invariant_file.read_text(encoding="utf-8"))
        document["statement"] = f"Padded statement.{' padding' * 1800}"
        invariant_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
        commit_all(memory, "padded statement")
        outcome, first = world.call(
            "knowledge_read", {"memoryRoot": str(memory), "view": "invariant"}
        )
        self.assertEqual(outcome, "RETURNED", first)
        assert isinstance(first, dict)
        self.assertIn(first["state"], ("page", "view"), first)
        continuation = first.get("continuation")
        self.assertIsNotNone(continuation, first)
        minted_tree = first.get("page", {}).get("memoryTreeId")
        (memory / "knowledge" / "moved.json").write_text(
            json.dumps({"schema": "ar-invariant/v1", "id": "INV-MOVED"}),
            encoding="utf-8",
        )
        commit_all(memory, "a move between selection and projection")
        outcome, out = world.call(
            "knowledge_read",
            {
                "memoryRoot": str(memory),
                "view": "invariant",
                "continuation": continuation,
            },
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert isinstance(out, dict)
        self.assertEqual(out["state"], "refused", out)
        # A walk resumed after the checkout moved refuses instead of
        # mixing epochs: the binding names the minted tree and the
        # selected tree, and the caller starts a new read.
        self.assertEqual(out["refusalCode"], "continuation_binding_mismatch", out)
        self.assertIn("changed since this walk began", out["refusalDetail"], out)
        self.assertIn(str(minted_tree), out["refusalDetail"], out)

    def test_move_between_selection_and_projection_is_refused(self) -> None:
        from agents_remember.application.knowledge_names import (  # noqa: PLC0415
            SelectionProvenance,
            _memory_commit,
        )
        from agents_remember.application.published_intent import (  # noqa: PLC0415
            select_knowledge_dataset,
        )
        from agents_remember.mcp.tools.knowledge import (  # noqa: PLC0415
            ProjectionNaming,
            _projected_body,
        )
        from tool_entry_point_world import EntryPointWorld  # noqa: PLC0415

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        memory = self._converted()
        selected = select_knowledge_dataset(memory, coordination_root=world.coord)
        assert selected.memory_tree is not None
        first = _memory_commit(memory)
        assert first is not None
        provenance = SelectionProvenance(commit=first, dirty=False)
        (memory / "knowledge" / "moved.json").write_text(
            json.dumps({"schema": "ar-invariant/v1", "id": "INV-MOVED"}),
            encoding="utf-8",
        )
        commit_all(memory, "a move between selection and projection")
        request = ReadToolRequest(memory_root=str(memory), view="invariant")
        out = _projected_body({}, request, selected, naming=ProjectionNaming(provenance=provenance))
        self.assertEqual(out["state"], "refused", out)
        self.assertEqual(out["refusalCode"], "snapshot_unavailable", out)
        self.assertIn(first, out["refusalDetail"], out)
        self.assertIn(str(_memory_commit(memory)), out["refusalDetail"], out)


if __name__ == "__main__":
    unittest.main()
