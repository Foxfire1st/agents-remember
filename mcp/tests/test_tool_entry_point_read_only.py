"""The read-only mark's sweep cases: no advertised read writes the world.

Split text-identical out of test_tool_entry_point_sweep.py under MIK-R196 rule 10."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from typing import Any

import anyio
from agents_remember.mcp.read_only import (
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    UnknownReadOnlyMark,
    read_only_hint,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from tool_entry_point_sweep_support import MASTER, REPO, EntryPointWorld

# Paths a read-marked tool may touch without failing the rule-7 case: the
# tool log and its detail files (MIK-R108), and the derived knowledge index
# under runtime/knowledge-index/ (MIK-R74) -- and nothing else. The exemption
# names the index directory exactly: any other runtime/ write (provider
# state, temp files promoted sideways) fails the case. Coordination census
# paths are relative to the coordination root.
_READ_SWEEP_IGNORED_PREFIXES = ("logs/", "runtime/knowledge-index/")


_READ_SWEEP_IGNORED_SEGMENTS = ("/logs/",)


def _read_sweep_kept(census: dict[str, str]) -> dict[str, str]:
    """One coordination census without the log and index paths."""

    return {
        path: digest
        for path, digest in census.items()
        if not path.startswith(_READ_SWEEP_IGNORED_PREFIXES)
        and not any(segment in path for segment in _READ_SWEEP_IGNORED_SEGMENTS)
    }


class ReadOnlyMarkTests(unittest.TestCase):
    """MIK-R109 rule 7: a tool that only reads says so, and only reads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.addClassCleanup(cls.world.close)
        cls.world.build()
        # The fixture author's old delegation-policy file (coord/system/)
        # is an upgrade-pending layout the new base refuses to read beside
        # a user file; the read sweep addresses no delegation policy, so
        # the world presents the new layout and no call can trip the
        # upgrade collision mid-sweep.
        old_system = cls.world.coord / "system" / "settings.json"
        old_system.unlink(missing_ok=True)

    def test_every_registered_tool_stands_in_exactly_one_part_of_the_list(self) -> None:
        self.assertEqual(
            frozenset(PUBLIC_TOOLS),
            READ_ONLY_TOOLS | MUTATING_TOOLS,
            "a registered tool stands in neither part of the read-only list",
        )
        self.assertEqual(
            frozenset(),
            READ_ONLY_TOOLS & MUTATING_TOOLS,
            "a tool stands in both parts of the read-only list",
        )
        with self.assertRaises(UnknownReadOnlyMark):
            read_only_hint("no-such-tool")
        # L94 rule 2: the direct-execution grant writes the grant record
        # only, so it stands with the mutating tools, not the reads.
        self.assertFalse(read_only_hint("direct_execution_grant"))

    def test_every_advertised_mark_matches_the_one_list(self) -> None:
        by_name = {tool.name: tool for tool in anyio.run(self.world.server.list_tools)}
        self.assertEqual(set(by_name), set(PUBLIC_TOOLS))
        for tool in sorted(PUBLIC_TOOLS):
            with self.subTest(tool=tool):
                annotations = by_name[tool].annotations
                self.assertIsNotNone(annotations, f"{tool} is advertised unmarked")
                assert annotations is not None
                self.assertEqual(
                    tool in READ_ONLY_TOOLS,
                    annotations.readOnlyHint,
                    f"{tool} advertises readOnlyHint={annotations.readOnlyHint}",
                )

    def test_a_tool_marked_as_a_read_changes_no_file_of_the_world(self) -> None:
        # The derived index and the logs do not count (MIK-R74, MIK-R108);
        # every other file of the world must not move under a marked read --
        # worktree files, Git metadata, and every coordination file outside
        # the log and the index directory. Each call must answer (a refusal
        # inside the envelope still counts); a call that raises instead of
        # answering proves nothing about writes and fails the case.
        arguments = self.world.benign()
        variants = self._read_variants()
        for tool in sorted(READ_ONLY_TOOLS):
            cases = [arguments[tool], *variants.get(tool, [])]
            for index, args in enumerate(cases):
                with self.subTest(tool=tool, case=index):
                    before_code = self.world.repository_state(self.world.code)
                    before_memory = self.world.repository_state(self.world.memory)
                    before_coord = _read_sweep_kept(self.world.coordination_census())
                    outcome, payload = self.world.call(tool, args)
                    self.assertEqual(outcome, "RETURNED", f"{tool} raised: {payload}")
                    self.assertIsInstance(payload, dict, tool)
                    self.assertEqual(before_code, self.world.repository_state(self.world.code))
                    self.assertEqual(before_memory, self.world.repository_state(self.world.memory))
                    self.assertEqual(
                        before_coord,
                        _read_sweep_kept(self.world.coordination_census()),
                        f"{tool} changed a file of its world outside the log and index",
                    )

    def _read_variants(self) -> dict[str, list[dict[str, Any]]]:
        """Non-default invocations of the marked reads, still read-only.

        Legacy-argument refusals, unknown views, and absent contracts all
        answer inside the envelope (RETURNED): the sweep watches each for
        writes too, so a refusal path cannot hide a mutation.
        """

        absent_contract = (
            self.world.coord / "tasks" / REPO / MASTER / "enclosures" / "absent"
        ).as_posix()
        return {
            "read_ar_files": [
                {"repo_id": REPO, "files": [{"path": "not-covered.md"}]},
                {"repo_id": REPO, "files": ["not-covered.md"], "refresh": True},
                {"repo_id": REPO, "files": ["not-covered.md"], "refresh": False},
            ],
            "knowledge_read": [
                {
                    "memoryRoot": (self.world.coord / "temp" / "absent-memory").as_posix(),
                    "view": "no-such-view",
                },
            ],
            "knowledge_integrity_check": [
                {"memoryRoot": (self.world.coord / "temp" / "absent-memory").as_posix()},
            ],
            "worktree_status": [
                {
                    "repo_id": REPO,
                    "contract_path": self.world.contract.as_posix(),
                    "leaf_id": "no-such-leaf",
                },
            ],
            "worktree_closeout_preview": [{"contract_path": absent_contract}],
        }

    def test_the_sweep_catches_a_git_metadata_write(self) -> None:
        # Control: a write to Git metadata under a marked read must fail
        # the comparison. The write is injected (no registered read writes
        # .git/); the monitor must see it.
        arguments = self.world.benign()
        before = self.world.repository_state(self.world.code)
        probe = self.world.code / ".git" / "sweep-control-probe"
        probe.write_text("control\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, self.world.repository_state(self.world.code))
        finally:
            probe.unlink(missing_ok=True)
        self.assertEqual(before, self.world.repository_state(self.world.code))
        self.assertIn("server_info", arguments)

    def test_the_sweep_catches_a_non_index_runtime_write(self) -> None:
        # Control: a write to runtime/ outside the derived-index directory
        # must fail the comparison -- the exemption names the index
        # directory exactly, not all of runtime/.
        before = _read_sweep_kept(self.world.coordination_census())
        probe_dir = self.world.coord / "runtime" / "sweep-control"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / "probe.json"
        probe.write_text("{}\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, _read_sweep_kept(self.world.coordination_census()))
        finally:
            probe.unlink(missing_ok=True)
            probe_dir.rmdir()

    def test_the_sweep_catches_an_ordinary_write(self) -> None:
        # Control: an ordinary worktree write must fail the comparison.
        before = self.world.repository_state(self.world.code)
        probe = self.world.code / "sweep-control-probe.md"
        probe.write_text("control\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, self.world.repository_state(self.world.code))
        finally:
            probe.unlink(missing_ok=True)

    def test_a_freshness_fetch_writes_only_under_a_false_mark(self) -> None:
        # context_packet admits include_freshness, which fetches the
        # configured remotes and writes FETCH_HEAD: the registered call
        # must perform the write, and the advertised mark must be false.
        self.assertFalse(read_only_hint("context_packet"))
        remote = self.world.coord / "sweep-remote.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(remote)],
            check=True,
            capture_output=True,
        )
        self.addCleanup(shutil.rmtree, remote, True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "remote",
                "add",
                "sweep-remote",
                str(remote),
            ],
            check=True,
            capture_output=True,
        )
        # The remote must advertise a ref: fetching an empty remote writes
        # nothing, so seed it with the world's own HEAD first.
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "push",
                "-q",
                "sweep-remote",
                "HEAD:refs/heads/seed",
            ],
            check=True,
            capture_output=True,
        )
        # The fetch only runs for a branch with an upstream: track the
        # seeded ref from the checked-out branch.
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "branch",
                "--set-upstream-to=sweep-remote/seed",
            ],
            check=True,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(self.world.code), "remote", "remove", "sweep-remote"],
            check=False,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(self.world.code), "branch", "--unset-upstream"],
            check=False,
            capture_output=True,
        )
        fetch_head = self.world.code / ".git" / "FETCH_HEAD"
        if fetch_head.exists():
            fetch_head.unlink()
        outcome, payload = self.world.call(
            "context_packet", {"repo_id": REPO, "include_freshness": True}
        )
        self.assertEqual(outcome, "RETURNED", payload)
        self.assertTrue(fetch_head.is_file(), payload)
