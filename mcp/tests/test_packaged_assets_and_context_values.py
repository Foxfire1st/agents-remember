"""Behavioural tests for packaged-asset plumbing and coordination-context value handling.

These units were reachable from the suite only incidentally, so their interesting
arms -- the whole Windows half of the long-path normaliser, the malformed-settings
arms of the crossRepo parser, the prune-to-nothing arm of gate compaction -- had
never been observed. Each test below asserts a returned value, a file that exists
(or no longer does) on disk, or the exact error text a caller would see.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import chdir
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import provider_tools
from agents_remember.benchmarks.runner_modules.analysis import range_text
from agents_remember.controlplane.records import (
    GateAnchor,
    GateRecord,
    GateVerdict,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.install import assets
from agents_remember.kernel.coordination_context.models import CrossRepoAllowEntry
from agents_remember.kernel.coordination_context.serialize import cross_repo_entry_to_dict
from agents_remember.kernel.coordination_context.setting_values import (
    parsed_cross_repo_allow_entry,
)
from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope, RepositoryScope
from agents_remember.providers import lifecycle_service

# The Windows extended-length prefix, `\\?\`, written as a Python literal.
LONG_PATH_PREFIX = "\\\\?\\"

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _stamp(delta: timedelta) -> str:
    return (NOW + delta).isoformat()


class LongPathTests(unittest.TestCase):
    r"""``long_path`` prefixes Windows paths with the ``\\?\`` extended-length marker.

    Every Windows arm returns before it is reached on POSIX, so ``sys.platform`` is
    patched on the module under test. Windows *string* semantics (backslash
    separators, drive letters, UNC roots) come from ``PureWindowsPath`` inputs,
    because a ``PosixPath`` treats a backslash as an ordinary filename character --
    which would make the assertions fiction. ``PureWindowsPath`` has no ``resolve``,
    so it is used only on the ``resolve=False`` arms, which never call it.
    """

    def test_posix_hands_back_the_very_same_object(self) -> None:
        original = Path("sub/../file.txt")

        self.assertIs(assets.long_path(original), original)
        self.assertIs(assets.long_path(original, resolve=False), original)

    def test_windows_leaves_an_already_prefixed_path_alone(self) -> None:
        already = cast(Path, PureWindowsPath(r"\\?\C:\Users\dev\repo"))

        with mock.patch.object(assets.sys, "platform", "win32"):
            result = assets.long_path(already, resolve=False)

        self.assertIs(result, already)
        self.assertEqual(str(result), r"\\?\C:\Users\dev\repo")

    def test_windows_rewrites_a_unc_share_to_the_unc_prefix(self) -> None:
        share = cast(Path, PureWindowsPath(r"\\build-01\share\ar\worktrees"))

        with mock.patch.object(assets.sys, "platform", "win32"):
            result = assets.long_path(share, resolve=False)

        self.assertEqual(str(result), r"\\?\UNC\build-01\share\ar\worktrees")

    def test_windows_prefixes_a_plain_drive_absolute_path(self) -> None:
        drive = cast(Path, PureWindowsPath(r"C:\ar\worktrees\260731-efa-l2"))

        with mock.patch.object(assets.sys, "platform", "win32"):
            result = assets.long_path(drive, resolve=False)

        self.assertEqual(str(result), r"\\?\C:\ar\worktrees\260731-efa-l2")

    def test_windows_relative_path_is_anchored_at_cwd_but_left_uncollapsed(self) -> None:
        # resolve=False is the arm that must not touch the filesystem: the path is
        # only made absolute against the cwd, so `..` survives into the result.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "sub").mkdir()
            with chdir(work), mock.patch.object(assets.sys, "platform", "win32"):
                cwd = Path.cwd()
                unresolved = assets.long_path(Path("sub/../file.txt"), resolve=False)
                resolved = assets.long_path(Path("sub/../file.txt"), resolve=True)

        self.assertEqual(str(unresolved), LONG_PATH_PREFIX + str(cwd / "sub/../file.txt"))
        self.assertEqual(str(resolved), LONG_PATH_PREFIX + str(cwd / "file.txt"))


class CopyTraversableTreeTests(unittest.TestCase):
    """``copy_traversable_tree`` mirrors a Traversable onto disk (a ``Path`` is one)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_copies_nested_files_with_their_exact_bytes(self) -> None:
        source = self.root / "package_data"
        (source / "runtime" / "skills").mkdir(parents=True)
        (source / "empty").mkdir()
        (source / "top.txt").write_bytes(b"top\n")
        (source / "runtime" / "skills" / "skill.md").write_bytes(b"\x00\xfe\xff not utf-8\n")
        destination = self.root / "out" / "package_data"

        assets.copy_traversable_tree(source, destination)

        self.assertEqual((destination / "top.txt").read_bytes(), b"top\n")
        self.assertEqual(
            (destination / "runtime" / "skills" / "skill.md").read_bytes(),
            b"\x00\xfe\xff not utf-8\n",
        )
        self.assertTrue((destination / "empty").is_dir())
        self.assertEqual(
            sorted(child.name for child in destination.iterdir()),
            ["empty", "runtime", "top.txt"],
        )

    def test_a_second_copy_overwrites_stale_files_and_keeps_unrelated_ones(self) -> None:
        source = self.root / "package_data"
        source.mkdir()
        (source / "pinned.txt").write_bytes(b"new")
        destination = self.root / "out"
        destination.mkdir()
        (destination / "pinned.txt").write_bytes(b"stale-and-longer")
        (destination / "not-from-source.txt").write_bytes(b"kept")

        assets.copy_traversable_tree(source, destination)

        self.assertEqual((destination / "pinned.txt").read_bytes(), b"new")
        # A merge, not a mirror: nothing outside the source tree is removed.
        self.assertEqual((destination / "not-from-source.txt").read_bytes(), b"kept")

    def test_entries_that_are_neither_file_nor_directory_are_skipped(self) -> None:
        source = self.root / "package_data"
        source.mkdir()
        (source / "real.txt").write_bytes(b"real")
        (source / "dangling").symlink_to(source / "gone.txt")
        destination = self.root / "out"

        assets.copy_traversable_tree(source, destination)

        self.assertEqual((destination / "real.txt").read_bytes(), b"real")
        self.assertFalse((destination / "dangling").exists())
        self.assertFalse((destination / "dangling").is_symlink())

    def test_a_file_source_is_refused_as_a_missing_asset_root(self) -> None:
        source = self.root / "package_data.txt"
        source.write_bytes(b"not a tree")
        destination = self.root / "out"

        with self.assertRaises(RuntimeError) as ctx:
            assets.copy_traversable_tree(source, destination)

        self.assertEqual(str(ctx.exception), f"packaged asset root is missing: {source}")
        self.assertFalse(destination.exists())

    def test_a_missing_source_is_refused_before_the_destination_is_created(self) -> None:
        source = self.root / "never-installed"
        destination = self.root / "out"

        with self.assertRaises(RuntimeError) as ctx:
            assets.copy_traversable_tree(source, destination)

        self.assertEqual(str(ctx.exception), f"packaged asset root is missing: {source}")
        self.assertFalse(destination.exists())


class CrossRepoEntryToDictTests(unittest.TestCase):
    """``cross_repo_entry_to_dict`` emits four fixed keys plus only the set optionals."""

    def test_minimal_entry_emits_only_the_four_required_keys(self) -> None:
        entry = CrossRepoAllowEntry(repo="agents-remember", expected_branch="main")

        self.assertEqual(
            cross_repo_entry_to_dict(entry),
            {
                "repo": "agents-remember",
                "expectedBranch": "main",
                "includeCode": True,
                "includeMemory": False,
            },
        )

    def test_full_entry_emits_state_reason_code_and_memory(self) -> None:
        entry = CrossRepoAllowEntry(
            repo="ar-coordination",
            expected_branch="release",
            include_code=False,
            include_memory=True,
            state="excluded",
            reason="branch mismatch",
            code={"root": "/w/code", "branch": "feature"},
            memory={"root": "/w/memory", "branch": "main"},
        )

        self.assertEqual(
            cross_repo_entry_to_dict(entry),
            {
                "repo": "ar-coordination",
                "expectedBranch": "release",
                "includeCode": False,
                "includeMemory": True,
                "state": "excluded",
                "reason": "branch mismatch",
                "code": {"root": "/w/code", "branch": "feature"},
                "memory": {"root": "/w/memory", "branch": "main"},
            },
        )

    def test_empty_optionals_are_dropped_one_by_one(self) -> None:
        entry = CrossRepoAllowEntry(
            repo="ar",
            expected_branch="main",
            state="allowed",
            reason="",
            code={"root": "/w/code"},
            memory={},
        )

        payload = cross_repo_entry_to_dict(entry)

        self.assertEqual(
            sorted(payload),
            ["code", "expectedBranch", "includeCode", "includeMemory", "repo", "state"],
        )
        self.assertEqual(payload["state"], "allowed")
        self.assertEqual(payload["code"], {"root": "/w/code"})


class ParsedCrossRepoAllowEntryTests(unittest.TestCase):
    """``parsed_cross_repo_allow_entry`` never raises: it excludes and explains."""

    def test_valid_entry_defaults_to_code_on_and_memory_off(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"repo": "agents-remember", "expectedBranch": "main"},
            "crossRepo.allow[0]",
        )

        self.assertEqual(error, "")
        self.assertEqual(
            entry,
            CrossRepoAllowEntry(repo="agents-remember", expected_branch="main"),
        )

    def test_explicit_booleans_are_carried_through(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {
                "repo": "ar-coordination",
                "expectedBranch": "release",
                "includeCode": False,
                "includeMemory": True,
            },
            "crossRepo.allow[1]",
        )

        self.assertEqual(error, "")
        self.assertFalse(entry.include_code)
        self.assertTrue(entry.include_memory)
        self.assertEqual(entry.state, "")

    def test_backticked_and_quoted_values_are_cleaned(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"repo": "`agents-remember`", "expectedBranch": '  "main"  '},
            "crossRepo.allow[0]",
        )

        self.assertEqual(error, "")
        self.assertEqual(entry.repo, "agents-remember")
        self.assertEqual(entry.expected_branch, "main")

    def test_missing_repo_excludes_the_entry_with_a_labelled_error(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"expectedBranch": "main"},
            "crossRepo.allow[2]",
        )

        self.assertEqual(error, "crossRepo.allow[2]: repo is required")
        self.assertEqual(entry.repo, "")
        self.assertEqual(entry.expected_branch, "main")
        self.assertEqual(entry.state, "excluded")
        self.assertEqual(entry.reason, "repo is required")

    def test_blank_expected_branch_counts_as_missing(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"repo": "ar", "expectedBranch": "   "},
            "crossRepo.allow[0]",
        )

        self.assertEqual(error, "crossRepo.allow[0]: expectedBranch is required")
        self.assertEqual(entry.repo, "ar")
        self.assertEqual(entry.expected_branch, "")
        self.assertEqual(entry.state, "excluded")

    def test_both_reasons_are_joined_for_a_wholly_unusable_entry(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"repo": 7, "expectedBranch": None},
            "crossRepo.allow[3]",
        )

        self.assertEqual(
            error,
            "crossRepo.allow[3]: repo is required; expectedBranch is required",
        )
        self.assertEqual(entry.reason, "repo is required; expectedBranch is required")
        self.assertEqual(entry.state, "excluded")

    def test_a_non_boolean_flag_excludes_the_entry_and_restores_both_defaults(self) -> None:
        # The two flags are read in one expression, so the first one's value is lost
        # with the second one's error: includeCode=False does NOT survive.
        entry, error = parsed_cross_repo_allow_entry(
            {
                "repo": "ar",
                "expectedBranch": "main",
                "includeCode": False,
                "includeMemory": "yes",
            },
            "crossRepo.allow[0]",
        )

        self.assertEqual(
            error,
            "crossRepo.allow[0]: crossRepo.allow[0].includeMemory must be a boolean",
        )
        self.assertTrue(entry.include_code)
        self.assertFalse(entry.include_memory)
        self.assertEqual(entry.state, "excluded")

    def test_an_int_include_code_is_not_a_boolean(self) -> None:
        entry, error = parsed_cross_repo_allow_entry(
            {"repo": "ar", "expectedBranch": "main", "includeCode": 1},
            "crossRepo.allow[0]",
        )

        self.assertEqual(
            error,
            "crossRepo.allow[0]: crossRepo.allow[0].includeCode must be a boolean",
        )
        self.assertEqual(entry.state, "excluded")


class GateStoreCompactTests(unittest.TestCase):
    """``GateStore.compact`` prunes consumed/expired gates and reports records removed."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = GateStore(self.root)

    def _open_gate(self, gate_id: str, lifecycle_id: str | None, age: timedelta) -> GateRecord:
        return create_gate(
            "agent-question",
            gate_id=gate_id,
            now=_stamp(-age),
            anchor=GateAnchor(lifecycle_id=lifecycle_id),
        )

    def test_a_log_that_was_never_written_compacts_to_zero(self) -> None:
        self.assertEqual(self.store.compact("L-none", now=NOW), 0)
        self.assertFalse(self.store.log_path("L-none").exists())

    def test_a_live_gate_keeps_its_whole_history_and_the_file_is_untouched(self) -> None:
        opened = self._open_gate("G-live", "L1", timedelta(hours=1))
        self.store.append(opened)
        self.store.append(
            decide_gate(
                opened,
                GateVerdict(decision="approve", by="developer", via="dashboard", note="lgtm"),
                now=_stamp(-timedelta(minutes=30)),
            )
        )
        before = self.store.log_path("L1").read_bytes()

        removed = self.store.compact("L1", now=NOW)

        self.assertEqual(removed, 0)
        self.assertEqual(self.store.log_path("L1").read_bytes(), before)
        self.assertEqual([record.state for record in self.store.read("L1")], ["open", "approved"])

    def test_consumed_and_aged_gates_go_while_the_live_one_stays(self) -> None:
        live = self._open_gate("G-live", "L1", timedelta(hours=1))
        consumed = self._open_gate("G-consumed", "L1", timedelta(hours=2))
        stale = self._open_gate("G-stale", "L1", timedelta(hours=25))
        self.store.append(live)
        self.store.append(consumed)
        self.store.append(apply_gate(consumed, now=_stamp(-timedelta(hours=2))))
        self.store.append(stale)

        removed = self.store.compact("L1", now=NOW)

        # Three records leave: both snapshots of the applied gate and the aged-out open one.
        self.assertEqual(removed, 3)
        self.assertEqual([record.id for record in self.store.read("L1")], ["G-live"])
        self.assertTrue(self.store.log_path("L1").is_file())

    def test_pruning_the_last_gate_empties_the_workspace_log_without_unlinking_it(self) -> None:
        """An empty gate set is an empty FILE, not a missing one (260731-EFA-L5 R5).

        This asserted the log was deleted, and that unlink is precisely what the leaf removed:
        an appender that had already opened the log in ``"a"`` mode kept writing into an inode
        with no remaining links, so its snapshot disappeared along with the file -- no torn
        line, no exception, nothing for the caller to notice. The empty case was the most
        dangerous branch in the store rather than the dullest.

        The claim being proven is unchanged and not weakened: two snapshots went in, ``compact``
        reports removing both, and nothing survives. Only the evidence for "nothing survives"
        moves, from absence to emptiness -- and it is now checked twice over, through the
        STRICT reader (which raises rather than skipping, so an empty result cannot be a
        swallowed parse failure) and against the raw bytes.
        """
        consumed = self._open_gate("G-workspace", None, timedelta(minutes=5))
        self.store.append(consumed)
        self.store.append(apply_gate(consumed, now=_stamp(-timedelta(minutes=1))))
        self.assertTrue(self.store.log_path(None).is_file())

        removed = self.store.compact(None, now=NOW)

        self.assertEqual(removed, 2)
        self.assertTrue(self.store.log_path(None).is_file())
        self.assertEqual(self.store.log_path(None).read_bytes(), b"")
        self.assertEqual(self.store.read(None), [])


class RangeTextTests(unittest.TestCase):
    """``range_text`` renders a benchmark metric column as a value or a low-high span."""

    def test_no_values_report_not_available(self) -> None:
        self.assertEqual(range_text([]), "n/a")

    def test_values_with_nothing_numeric_report_not_available(self) -> None:
        self.assertEqual(range_text([None, "n/a", {}]), "n/a")

    def test_a_single_value_is_rendered_bare(self) -> None:
        self.assertEqual(range_text([42]), "42")

    def test_identical_values_collapse_to_one_number(self) -> None:
        self.assertEqual(range_text([3.5, 3.5, 3.5]), "3.5")

    def test_a_span_is_rendered_low_to_high_ignoring_non_numbers(self) -> None:
        self.assertEqual(range_text([9, None, 1, "missing", 4]), "1 - 9")

    def test_ints_and_floats_share_one_span(self) -> None:
        self.assertEqual(range_text([2, 7.5]), "2 - 7.5")


class _RecordingLifecycleRunner:
    """Stands in for the docker-driving lifecycle call -- the process boundary."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.actions: list[str] = []
        self.service_configs: list[lifecycle_service.ProviderLifecycleServiceConfig] = []
        self.settings_documents: list[dict[str, Any]] = []

    def __call__(
        self,
        service_config: lifecycle_service.ProviderLifecycleServiceConfig,
        request: lifecycle_service.CgcLifecycleRequest | None = None,
        *,
        action: str | None = None,
    ) -> dict[str, Any]:
        # Stands in for both entry points: GrepAI still takes a keyword action,
        # CGC now takes a positional CgcLifecycleRequest carrying it.
        resolved_action = request.action if request is not None else action
        assert resolved_action is not None
        self.actions.append(resolved_action)
        self.service_configs.append(service_config)
        settings_path = service_config.settings_path
        # The settings file must exist *while* the rebuild runs; the caller removes it after.
        self.settings_documents.append(json.loads(settings_path.read_text(encoding="utf-8")))
        return dict(self._payload)


def _provider_config(tmp: Path, provider_ids: tuple[str, ...]) -> McpRuntimeConfig:
    coordination_root = tmp / "coord"
    workspace_root = tmp / "ws"
    return McpRuntimeConfig(
        config_path=tmp / "authority.json",
        coordination_root=coordination_root,
        workspace_root=workspace_root,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={
            "repo": RepositoryScope(
                repo_id="repo",
                path=workspace_root / "repo",
                memory_root=workspace_root / "repo-memory",
            )
        },
        providers={
            provider_id: ProviderScope(
                provider_id=provider_id,
                runtime_root=coordination_root / "providers" / "runners" / provider_id,
                log_root=coordination_root / "logs" / "providers" / provider_id,
                instance_id="i1",
            )
            for provider_id in provider_ids
        },
    )


class ProviderInvalidateIndexesTests(unittest.TestCase):
    """``_provider_invalidate_indexes`` is the destructive full-rebuild fan-out.

    Only the two lifecycle entry points are patched -- they shell out to docker.
    The dispatch, the per-provider action names, the temp settings file and the
    ``ok`` fold are the real code under test.
    """

    def _invalidate(
        self,
        provider_ids: tuple[str, ...],
        *,
        grepai: _RecordingLifecycleRunner,
        cgc: _RecordingLifecycleRunner,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(lifecycle_service, "run_grepai_lifecycle", grepai),
            mock.patch.object(lifecycle_service, "run_cgc_lifecycle", cgc),
        ):
            config = _provider_config(Path(tmp), provider_ids)
            return provider_tools._provider_invalidate_indexes(config, dry_run=dry_run)

    def test_both_providers_rebuild_with_their_own_destructive_action(self) -> None:
        grepai = _RecordingLifecycleRunner({"ok": True, "provider": "grepai"})
        cgc = _RecordingLifecycleRunner({"ok": True, "provider": "cgc"})

        result = self._invalidate(
            ("grepai-memory", "codegraphcontext-code"),
            grepai=grepai,
            cgc=cgc,
        )

        self.assertEqual(grepai.actions, ["refresh"])
        self.assertEqual(cgc.actions, ["refresh-all"])
        self.assertEqual(
            result,
            {
                "ok": True,
                "operation": "provider_watchers",
                "action": "invalidate-indexes",
                "steps": [
                    {"ok": True, "provider": "grepai", "operation": "provider_watchers"},
                    {"ok": True, "provider": "cgc", "operation": "provider_watchers"},
                ],
            },
        )

    def test_the_rebuild_runs_against_a_generated_settings_file_that_is_then_removed(self) -> None:
        grepai = _RecordingLifecycleRunner({"ok": True})
        cgc = _RecordingLifecycleRunner({"ok": True})

        self._invalidate(("grepai-memory", "codegraphcontext-code"), grepai=grepai, cgc=cgc)

        providers = grepai.settings_documents[0]["contextProviders"]["providers"]
        self.assertEqual(sorted(providers), ["codegraphcontext-code", "grepai-memory"])
        self.assertTrue(grepai.settings_documents[0]["contextProviders"]["enabled"])
        for runner in (grepai, cgc):
            service_config = runner.service_configs[0]
            self.assertTrue(service_config.dry_run)
            self.assertFalse(service_config.settings_path.exists())

    def test_dry_run_false_reaches_the_lifecycle_call(self) -> None:
        grepai = _RecordingLifecycleRunner({"ok": True})
        cgc = _RecordingLifecycleRunner({"ok": True})

        self._invalidate(("grepai-memory",), grepai=grepai, cgc=cgc, dry_run=False)

        self.assertFalse(grepai.service_configs[0].dry_run)

    def test_only_the_enabled_provider_is_rebuilt(self) -> None:
        grepai = _RecordingLifecycleRunner({"ok": True})
        cgc = _RecordingLifecycleRunner({"ok": True, "provider": "cgc"})

        result = self._invalidate(("codegraphcontext-code",), grepai=grepai, cgc=cgc)

        self.assertEqual(grepai.actions, [])
        self.assertEqual(cgc.actions, ["refresh-all"])
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["steps"],
            [{"ok": True, "provider": "cgc", "operation": "provider_watchers"}],
        )

    def test_with_no_providers_enabled_nothing_runs_and_the_result_is_not_ok(self) -> None:
        grepai = _RecordingLifecycleRunner({"ok": True})
        cgc = _RecordingLifecycleRunner({"ok": True})

        result = self._invalidate((), grepai=grepai, cgc=cgc)

        self.assertEqual(grepai.actions, [])
        self.assertEqual(cgc.actions, [])
        self.assertEqual(
            result,
            {
                "ok": False,
                "operation": "provider_watchers",
                "action": "invalidate-indexes",
                "steps": [],
            },
        )

    def test_a_failing_step_sinks_the_result_without_skipping_the_other_provider(self) -> None:
        grepai = _RecordingLifecycleRunner({"error": "docker unavailable"})
        cgc = _RecordingLifecycleRunner({"ok": True, "provider": "cgc"})

        result = self._invalidate(
            ("grepai-memory", "codegraphcontext-code"),
            grepai=grepai,
            cgc=cgc,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(cgc.actions, ["refresh-all"])
        self.assertEqual(
            result["steps"],
            [
                {"ok": False, "error": "docker unavailable", "operation": "provider_watchers"},
                {"ok": True, "provider": "cgc", "operation": "provider_watchers"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
