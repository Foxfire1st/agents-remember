"""Tests for the ``read_ar_files`` tool (slice 07).

Three layers:

* ranged-read helper unit tests (full / range / clamp / start>EOF / binary),
* controller onboarding/status + path-confinement + front-door dedup, driving a
  directly-constructed ``CoordinationContext`` so each storage mode is isolated,
* the served-ledger + ``read.packet`` emission through the ambient lifecycle, and
  the marker/refresh reset.

The route-index and storage machinery are consumed (not re-implemented) here, so
the fixtures write a real ``overview.index.json`` and real sidecar markdown.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.controllers.read_files import read_ar_files_tool
from agents_remember.errors import AuthorityError
from agents_remember.kernel import filesystem
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CrossRepoSettings,
    StorageSettings,
)
from agents_remember.mcp.config import load_config
from agents_remember.mcp.tools import read_ar_files_payload
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
    reset_ambient,
)
from agents_remember.observer.served_store import ServedStore, served_key
from test_config import settings_payload

REPO = "agents-remember"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _make_config(root: Path, code_root: Path):
    """A real ``McpRuntimeConfig`` whose coordination root is ``root`` and whose
    one repo points at ``code_root`` -- so ``observer_root`` and the reset marker
    resolve under ``root/logs/observer`` and the repo path confines correctly."""
    settings = settings_payload(root)
    settings["workspaceRoot"] = str(code_root.parent)
    settings["repositories"] = {REPO: {}}
    path = root / ".codex" / "mcp" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")
    return load_config(path)


def _build_context(
    code_root: Path,
    onboarding_root: Path,
    *,
    coordination_root: Path,
    storage_mode: str,
) -> CoordinationContext:
    """A minimal context with the fields the controller reads, isolating storage."""
    storage = StorageSettings(mode=storage_mode, default=storage_mode)
    return CoordinationContext(
        topology="external",
        code_repository_name=REPO,
        code_repository_root=code_root,
        coordination_root=coordination_root,
        memory_root=onboarding_root.parent,
        onboarding_root=onboarding_root,
        settings_path=onboarding_root / "settings.md",
        path_settings_path=None,
        task_root=coordination_root,
        temp_root=coordination_root / "temp",
        docs_root=coordination_root / "docs",
        system_root=onboarding_root.parent / "system",
        sources_path=onboarding_root.parent / "system" / "sources.md",
        tools_path=onboarding_root.parent / "system" / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=CrossRepoSettings(),
        memory_mode="external",
    )


def _write_sidecar(onboarding_root: Path, source_rel: str, body: str) -> None:
    path = onboarding_root / f"{source_rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_rel}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| path | `{source_rel}` |",
                "| doc_type | `file-level-onboarding` |",
                "| lastUpdated | 2026-06-01T00:00 |",
                "",
                body,
                "",
                "## Update History",
                "- 2026-06-01 seeded",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_overview(onboarding_root: Path, route: str, text: str) -> None:
    rel = "overview.md" if route == "" else f"{route}/overview.md"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_route_index(onboarding_root: Path, route: str, *, covered: list[str]) -> None:
    rel = "overview.index.json" if route == "" else f"{route}/overview.index.json"
    scope = "**" if route == "" else f"{route}/**"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "agents-remember-route-index",
                "route": route,
                "sourceScope": [scope],
                "coveredFiles": covered,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# ranged-read helper
# --------------------------------------------------------------------------


class RangedReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.file = self._dir / "f.txt"
        self.file.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    def test_full_read_via_read_text(self) -> None:
        self.assertEqual(filesystem.read_text(self.file), "a\nb\nc\nd\ne\n")

    def test_range_returns_inclusive_slice(self) -> None:
        self.assertEqual(filesystem.read_text_range(self.file, 2, 4), "b\nc\nd\n")

    def test_single_line_range(self) -> None:
        self.assertEqual(filesystem.read_text_range(self.file, 3, 3), "c\n")

    def test_end_clamped_to_eof(self) -> None:
        self.assertEqual(filesystem.read_text_range(self.file, 4, 99), "d\ne\n")

    def test_start_beyond_eof_returns_empty(self) -> None:
        self.assertEqual(filesystem.read_text_range(self.file, 10, 20), "")

    def test_inverted_range_returns_empty(self) -> None:
        self.assertEqual(filesystem.read_text_range(self.file, 4, 2), "")


# --------------------------------------------------------------------------
# controller: status semantics, path confinement, source independence
# --------------------------------------------------------------------------


class ControllerStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "mod.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
        (self.code / "pkg" / "other.py").write_text("# other\n", encoding="utf-8")
        (self.code / "bin.dat").write_bytes(b"\xff\xfe\x00\x01binary")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def _read(self, files, *, storage_mode: str):
        ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode=storage_mode,
        )
        return read_ar_files_tool(self.config, repo_id=REPO, files=files, _context=ctx)

    def test_found_when_sidecar_present_and_covered(self) -> None:
        _write_sidecar(self.onb, "pkg/mod.py", "Body of mod.")
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        result = self._read([{"path": "pkg/mod.py"}], storage_mode="repo-sidecar")
        file = result["files"][0]
        self.assertEqual(file["status"], "found")
        self.assertIn("Body of mod.", file["onboarding"])
        self.assertNotIn("Update History", file["onboarding"])
        self.assertEqual(file["source"], "x = 1\ny = 2\nz = 3\n")

    def test_missing_when_in_scope_but_uncovered_does_not_probe(self) -> None:
        # A sidecar exists on disk but the route index says it is NOT covered:
        # the index is authoritative, so status is missing and we never probe.
        _write_sidecar(self.onb, "pkg/mod.py", "Body that must be ignored.")
        _write_route_index(self.onb, "pkg", covered=[])  # in-scope, uncovered
        result = self._read([{"path": "pkg/mod.py"}], storage_mode="repo-sidecar")
        file = result["files"][0]
        self.assertEqual(file["status"], "missing")
        self.assertNotIn("onboarding", file)
        # source is independent of onboarding status.
        self.assertEqual(file["source"], "x = 1\ny = 2\nz = 3\n")

    def test_out_of_scope_index_defers_to_general_index_without_probe(self) -> None:
        # A nearest governing index that returns out-of-scope is skipped in
        # favor of a more-general governing index that returns absent (in-scope,
        # uncovered) -> status missing. A sidecar planted at the mirror path
        # proves the filesystem is NOT probed once the index chain decides.
        (self.code / "pkg" / "sub").mkdir(parents=True)
        (self.code / "pkg" / "sub" / "leaf.py").write_text("v = 1\n", encoding="utf-8")
        # Nearest governing index (pkg/sub) is scoped to a sibling subtree, so it
        # returns out-of-scope for leaf.py and the walk continues to pkg.
        (self.onb / "pkg" / "sub").mkdir(parents=True, exist_ok=True)
        (self.onb / "pkg" / "sub" / "overview.index.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "agents-remember-route-index",
                    "route": "pkg/sub",
                    "sourceScope": ["pkg/sub/zzz/**"],  # excludes leaf.py
                    "coveredFiles": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # More-general index (pkg) covers pkg/** but does NOT cover leaf.py.
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        # Plant a sidecar at the mirror path; if probed, status would be found.
        _write_sidecar(self.onb, "pkg/sub/leaf.py", "Must be ignored (no probe).")
        result = self._read([{"path": "pkg/sub/leaf.py"}], storage_mode="repo-sidecar")
        file = result["files"][0]
        self.assertEqual(file["status"], "missing")
        self.assertNotIn("onboarding", file)

    def test_index_absent_falls_back_to_mirror_probe(self) -> None:
        # No governing overview.index.json exists at all, so the controller
        # degrades to a direct mirror-path probe: a present sidecar resolves
        # found, an absent one resolves missing.
        (self.code / "loose.py").write_text("p = 1\n", encoding="utf-8")
        _write_sidecar(self.onb, "loose.py", "Loose body.")
        found = self._read([{"path": "loose.py"}], storage_mode="repo-sidecar")
        self.assertEqual(found["files"][0]["status"], "found")
        self.assertIn("Loose body.", found["files"][0]["onboarding"])
        (self.code / "bare.py").write_text("q = 1\n", encoding="utf-8")
        missing = self._read([{"path": "bare.py"}], storage_mode="repo-sidecar")
        self.assertEqual(missing["files"][0]["status"], "missing")

    def test_disabled_storage_mode(self) -> None:
        result = self._read([{"path": "pkg/mod.py"}], storage_mode="disabled")
        file = result["files"][0]
        self.assertEqual(file["status"], "disabled")
        self.assertEqual(file["source"], "x = 1\ny = 2\nz = 3\n")

    def test_inline_storage_mode_is_unsupported(self) -> None:
        result = self._read([{"path": "pkg/mod.py"}], storage_mode="inline")
        self.assertEqual(result["files"][0]["status"], "unsupported")

    def test_external_storage_treated_as_sidecar(self) -> None:
        # This repo's own memory is external, which also writes sidecars.
        _write_sidecar(self.onb, "pkg/mod.py", "External body.")
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        result = self._read([{"path": "pkg/mod.py"}], storage_mode="external")
        file = result["files"][0]
        self.assertEqual(file["status"], "found")
        self.assertIn("External body.", file["onboarding"])

    def test_not_requested_when_onboarding_false(self) -> None:
        _write_sidecar(self.onb, "pkg/mod.py", "Body.")
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        result = self._read(
            [{"path": "pkg/mod.py", "onboarding": False}], storage_mode="repo-sidecar"
        )
        file = result["files"][0]
        self.assertEqual(file["status"], "not_requested")
        self.assertNotIn("onboarding", file)
        self.assertEqual(file["source"], "x = 1\ny = 2\nz = 3\n")

    def test_range_request_returns_exact_slice(self) -> None:
        result = self._read(
            [{"path": "pkg/mod.py", "source": {"startLine": 2, "endLine": 3}}],
            storage_mode="disabled",
        )
        self.assertEqual(result["files"][0]["source"], "y = 2\nz = 3\n")

    def test_full_read_is_not_truncated(self) -> None:
        # A file larger than a few KB read as "full" returns its content
        # byte-for-byte -- the full path must never silently truncate.
        big = "\n".join(f"line {i:05d} " + "x" * 80 for i in range(400)) + "\n"
        self.assertGreater(len(big.encode("utf-8")), 4096)
        (self.code / "pkg" / "big.py").write_text(big, encoding="utf-8")
        result = self._read([{"path": "pkg/big.py"}], storage_mode="disabled")
        self.assertEqual(result["files"][0]["source"], big)

    def test_absent_source_omits_source(self) -> None:
        result = self._read([{"path": "pkg/ghost.py"}], storage_mode="disabled")
        self.assertNotIn("source", result["files"][0])

    def test_binary_source_is_omitted(self) -> None:
        result = self._read([{"path": "bin.dat"}], storage_mode="disabled")
        self.assertNotIn("source", result["files"][0])

    def test_inverted_range_rejected(self) -> None:
        # The controller rejects an inverted range up front (it never reaches the
        # helper, which would otherwise return "" -- a silent, confusing result).
        with self.assertRaises(AuthorityError):
            self._read(
                [{"path": "pkg/mod.py", "source": {"startLine": 5, "endLine": 2}}],
                storage_mode="disabled",
            )

    def test_zero_end_line_rejected(self) -> None:
        with self.assertRaises(AuthorityError):
            self._read(
                [{"path": "pkg/mod.py", "source": {"startLine": 1, "endLine": 0}}],
                storage_mode="disabled",
            )

    def test_path_confinement_rejects_escape(self) -> None:
        with self.assertRaises(AuthorityError):
            self._read([{"path": "../secret.txt"}], storage_mode="disabled")

    def test_absolute_path_rejected(self) -> None:
        with self.assertRaises(AuthorityError):
            self._read([{"path": "/etc/passwd"}], storage_mode="disabled")

    def test_symlink_file_escape_rejected(self) -> None:
        # A symlink inside the repo pointing at a file OUTSIDE the repo resolves
        # out of root, so confinement rejects it (not a literal ".." token).
        outside = self._dir / "outside_secret.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.code / "leak.py"
        link.symlink_to(outside)
        with self.assertRaises(AuthorityError):
            self._read([{"path": "leak.py"}], storage_mode="disabled")

    def test_symlink_dir_escape_rejected(self) -> None:
        # A symlinked directory inside the repo pointing OUTSIDE, addressed via a
        # child path, still resolves out of root and is rejected.
        outside_dir = self._dir / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "secret.py").write_text("secret = 1\n", encoding="utf-8")
        link = self.code / "linkdir"
        link.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(AuthorityError):
            self._read([{"path": "linkdir/secret.py"}], storage_mode="disabled")

    def test_mid_path_traversal_escape_rejected(self) -> None:
        # A mid-path ".." that climbs out of the repo root is rejected even when
        # it re-descends, because resolution happens before the check.
        with self.assertRaises(AuthorityError):
            self._read([{"path": "pkg/../../x"}], storage_mode="disabled")


class FrontDoorDedupTests(unittest.TestCase):
    """Auto-attach + per-lifecycle dedup of repo overview + route chain."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg" / "sub").mkdir(parents=True)
        (self.code / "pkg" / "sub" / "mod.py").write_text("a = 1\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        _write_overview(self.onb, "", "# Repo overview\nroot text\n")
        _write_overview(self.onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(self.onb, "pkg/sub", "# sub overview\nsub text\n")
        _write_sidecar(self.onb, "pkg/sub/mod.py", "Mod body.")
        _write_route_index(self.onb, "pkg/sub", covered=["pkg/sub/mod.py"])
        self.config = _make_config(self._dir, self.code)
        self.ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        reset_ambient()
        amb = AmbientLifecycle(EventStore(observer_root(self.config)), heartbeat_seconds=3600)
        amb.start(fleeting=True)
        install_ambient(amb)
        self.amb = amb

    def tearDown(self) -> None:
        reset_ambient()

    def _read(self, files, *, refresh: bool = False):
        return read_ar_files_tool(
            self.config, repo_id=REPO, files=files, refresh=refresh, _context=self.ctx
        )

    def test_first_read_attaches_overview_and_route_chain(self) -> None:
        result = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertEqual(result["repository_overview"]["path"], "overview.md")
        self.assertIn("root text", result["repository_overview"]["overview"])
        routes = result["route_overviews"]
        self.assertIn("pkg/sub", routes)
        self.assertIn("pkg", routes)

    def test_second_read_dedups_unchanged_pieces(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertNotIn("repository_overview", again)
        self.assertNotIn("route_overviews", again)

    def test_changed_overview_is_reserved(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        _write_overview(self.onb, "", "# Repo overview\nCHANGED root text\n")
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertIn("repository_overview", again)
        self.assertIn("CHANGED", again["repository_overview"]["overview"])
        # The unchanged route overviews stay deduped.
        self.assertNotIn("route_overviews", again)

    def test_refresh_forces_reserve(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        again = self._read([{"path": "pkg/sub/mod.py"}], refresh=True)
        self.assertIn("repository_overview", again)
        self.assertIn("route_overviews", again)

    def test_compact_marker_resets_served(self) -> None:
        self._read([{"path": "pkg/sub/mod.py"}])
        marker = observer_root(self.config) / "workspace" / "compact-reset.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}", encoding="utf-8")
        again = self._read([{"path": "pkg/sub/mod.py"}])
        self.assertIn("repository_overview", again)
        # Marker consumed exactly once.
        self.assertFalse(marker.exists())


class ServedLedgerAndEventTests(unittest.TestCase):
    """The on-disk served ledger and the facts-only read.packet event."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "mod.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        _write_overview(self.onb, "", "# Repo overview\nroot\n")
        _write_sidecar(self.onb, "pkg/mod.py", "Mod body.")
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        self.config = _make_config(self._dir, self.code)
        self.ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        reset_ambient()
        self.store = EventStore(observer_root(self.config))
        amb = AmbientLifecycle(self.store, heartbeat_seconds=3600)
        amb.start(fleeting=True)
        install_ambient(amb)
        self.amb = amb
        assert amb.current is not None
        self.lifecycle_id = amb.current.id

    def tearDown(self) -> None:
        reset_ambient()

    def _read(self, files):
        return read_ar_files_tool(self.config, repo_id=REPO, files=files, _context=self.ctx)

    def test_served_jsonl_records_pieces(self) -> None:
        self._read([{"path": "pkg/mod.py"}])
        served = ServedStore(observer_root(self.config))
        keys = served.served_set(self.lifecycle_id)
        self.assertIn(
            served_key("repository_overview", "overview.md", _overview_hash(self.onb, "")),
            keys,
        )

    def test_read_packet_emitted_with_facts_only(self) -> None:
        self._read([{"path": "pkg/mod.py", "source": {"startLine": 1, "endLine": 1}}])
        events = self.store.read(self.lifecycle_id)
        packets = [e for e in events if e.kind == "read.packet"]
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet.lifecycleId, self.lifecycle_id)
        self.assertEqual(packet.trust, "observed")
        self.assertEqual(packet.data["repoId"], REPO)
        files = packet.data["files"]
        # Positive allowlist: exactly the facts-only keys, nothing else.
        self.assertEqual(set(files[0]), {"path", "lines", "status", "bytes"})
        self.assertEqual(files[0]["path"], "pkg/mod.py")
        self.assertEqual(files[0]["lines"], [1, 1])
        self.assertEqual(files[0]["status"], "found")
        self.assertEqual(files[0]["bytes"], len(b"a = 1\n"))
        # CRITICAL: no source/onboarding/content keys anywhere in the event.
        serialized = packet.model_dump_json()
        self.assertNotIn("a = 1", serialized)
        self.assertNotIn("Mod body", serialized)

    def test_read_packet_full_records_full_marker(self) -> None:
        self._read([{"path": "pkg/mod.py"}])
        events = self.store.read(self.lifecycle_id)
        packet = next(e for e in events if e.kind == "read.packet")
        files = packet.data["files"]
        self.assertEqual(set(files[0]), {"path", "lines", "status", "bytes"})
        self.assertEqual(files[0]["lines"], "full")

    def test_read_packet_projects_out_stray_content_key(self) -> None:
        # A (hypothetical) caller that smuggles a content key into the entry is
        # projected to the fixed allowlist by emit_read_packet, so the content
        # never reaches Event.data -- the privacy invariant is structural.
        self.amb.emit_read_packet(
            REPO,
            [
                {
                    "path": "pkg/mod.py",
                    "lines": "full",
                    "status": "found",
                    "bytes": 6,
                    "source": "SECRET SOURCE CONTENT",
                    "onboarding": "SECRET ONBOARDING",
                }
            ],
        )
        events = self.store.read(self.lifecycle_id)
        packet = next(e for e in events if e.kind == "read.packet")
        entry = packet.data["files"][0]
        self.assertEqual(set(entry), {"path", "lines", "status", "bytes"})
        serialized = packet.model_dump_json()
        self.assertNotIn("SECRET", serialized)


# --------------------------------------------------------------------------
# integration: five-file cap + payload builder through the real fixture
# --------------------------------------------------------------------------


class FiveFileCapAndPayloadTests(unittest.TestCase):
    """End-to-end through the real resolver + payload builder + token choke point."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        memory = self._dir / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
        (memory / "system").mkdir(parents=True, exist_ok=True)
        (memory / "onboarding").mkdir(parents=True, exist_ok=True)
        (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
        self.code.mkdir(parents=True, exist_ok=True)
        (self.code / "README.md").write_text("# Fixture\nline2\n", encoding="utf-8")
        self.config = _make_config(self._dir, self.code)
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def test_six_files_rejected(self) -> None:
        files = [{"path": f"f{i}.py"} for i in range(6)]
        with self.assertRaises(AuthorityError):
            read_ar_files_payload(self.config, REPO, files)

    def test_five_files_accepted(self) -> None:
        files = [{"path": "README.md"}] + [{"path": f"f{i}.py"} for i in range(4)]
        payload = read_ar_files_payload(self.config, REPO, files)
        self.assertEqual(payload["operation"], "read_ar_files")
        self.assertEqual(len(payload["files"]), 5)
        # Token metadata is stamped by the choke point, never by the controller.
        self.assertIn("tokens", payload)
        self.assertTrue(payload["tokenCountExact"])

    def test_payload_reads_committed_source(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, [{"path": "README.md"}])
        self.assertEqual(payload["files"][0]["source"], "# Fixture\nline2\n")


# --------------------------------------------------------------------------
# test plumbing
# --------------------------------------------------------------------------


def _overview_hash(onboarding_root: Path, route: str) -> str:
    rel = "overview.md" if route == "" else f"{route}/overview.md"
    text = (onboarding_root / rel).read_text(encoding="utf-8")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
