"""Served-build preflight unit tests (L15-R4, gate-repair coverage)."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.tasks.serving_preflight import (
    TOPOLOGY_SERVING_VERSION_FLOOR,
    TaskDocument,
    TopologyServingBuildError,
    _below_floor,
    _installed_distribution,
    _is_editable_install,
    _path_is_within,
    require_serving_topology_schema,
)


class ServingFloorTests(unittest.TestCase):
    def test_proven_pre_floor_releases_are_below_the_floor(self) -> None:
        self.assertTrue(_below_floor("3.0.0rc7"))
        self.assertTrue(_below_floor("2.9.0"))

    def test_floor_and_newer_versions_are_not_below(self) -> None:
        self.assertFalse(_below_floor(TOPOLOGY_SERVING_VERSION_FLOOR))
        self.assertFalse(_below_floor("3.0.0rc9"))
        self.assertFalse(_below_floor("3.0.0"))
        self.assertFalse(_below_floor("3.1.0"))

    def test_dev_post_and_local_builds_are_not_provably_stale(self) -> None:
        self.assertFalse(_below_floor("3.0.0rc8.dev0+gabc123"))
        self.assertFalse(_below_floor("3.0.0rc7.dev1"))
        self.assertFalse(_below_floor("3.0.0rc7.post1"))
        self.assertFalse(_below_floor("3.0.0rc7+local.1"))
        self.assertFalse(_below_floor("not-a-version"))

    def test_preflight_passes_when_no_distribution_is_installed(self) -> None:
        with mock.patch(
            "agents_remember.tasks.serving_preflight._installed_distribution",
            return_value=None,
        ):
            require_serving_topology_schema()  # must not raise

    def test_preflight_refuses_when_the_model_lacks_the_topology_fields(self) -> None:
        with (
            mock.patch.object(TaskDocument, "model_fields", {}),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIn("lacks topology field(s)", str(raised.exception))
        self.assertIn("upgrade", str(raised.exception))

    def test_installed_distribution_returns_none_when_the_package_is_not_installed(
        self,
    ) -> None:
        with mock.patch(
            "agents_remember.tasks.serving_preflight.metadata.distribution",
            side_effect=metadata.PackageNotFoundError,
        ):
            self.assertIsNone(_installed_distribution())

    def test_editable_or_source_distributions_pass_the_preflight(self) -> None:
        with mock.patch(
            "agents_remember.tasks.serving_preflight._is_editable_install",
            return_value=True,
        ):
            require_serving_topology_schema()  # must not raise

    def test_preflight_refuses_a_non_editable_pre_floor_build(self) -> None:
        dist = _as_dist(_FakeDist(None, "unused", version="3.0.0rc7"))
        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=dist,
            ),
            mock.patch(
                "agents_remember.tasks.serving_preflight._is_editable_install",
                return_value=False,
            ),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIn("3.0.0rc7", str(raised.exception))
        self.assertIn("upgrade", str(raised.exception))


class _FakeDist:
    """A minimal stand-in for ``importlib.metadata.Distribution``.

    ``_path`` is a real directory (or ``None``) so the pth-marker and containment
    branches run against real filesystem shapes; ``read_text`` is injected.
    """

    def __init__(
        self,
        path: Path | None,
        read_text: str | Exception | Callable[[str], str],
        *,
        version: str = "3.0.0rc8",
        fail_after_first_version_read: bool = False,
    ) -> None:
        self._path = path
        self._read_text = read_text
        self._version = version
        self._fail_after_first_version_read = fail_after_first_version_read
        self.version_reads = 0

    @property
    def version(self) -> str:
        self.version_reads += 1
        if self._fail_after_first_version_read and self.version_reads > 1:
            raise OSError("version metadata changed after first read")
        return self._version

    def read_text(self, filename: str) -> str:
        if isinstance(self._read_text, Exception):
            raise self._read_text
        if callable(self._read_text):
            return self._read_text(filename)
        return self._read_text


def _as_dist(dist: _FakeDist) -> metadata.Distribution:
    """Tests fake the installed distribution; cast to the protocol for type-checking."""

    return cast(metadata.Distribution, dist)


class EditableInstallDetectionTests(unittest.TestCase):
    """RAIL 2/3: force every branch of ``_is_editable_install`` with fake dists.

    The container installs the package editable, so the wheel/egg-info branches
    never execute in the live environment; these tests drive them directly.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.root / "src"
        self.source_root.mkdir()
        self.site = self.root / "site-packages"
        self.site.mkdir()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch(
            "agents_remember.tasks.serving_preflight._PACKAGE_SOURCE_ROOT",
            self.source_root,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dist_info(self) -> Path:
        dist_info = self.site / "agents_remember_mcp.dist-info"
        dist_info.mkdir()
        return dist_info

    def _fake(
        self,
        path: Path | None,
        read_text: str | Exception | Callable[[str], str],
        *,
        version: str = "3.0.0rc8",
        fail_after_first_version_read: bool = False,
    ):
        if path is not None and path.is_dir():
            metadata_name = "PKG-INFO" if path.name.endswith(".egg-info") else "METADATA"
            metadata_path = path / metadata_name
            if not metadata_path.exists():
                metadata_path.write_text(
                    f"Metadata-Version: 2.1\nName: agents-remember-mcp\nVersion: {version}\n",
                    encoding="utf-8",
                )
        return _as_dist(
            _FakeDist(
                path,
                read_text,
                version=version,
                fail_after_first_version_read=fail_after_first_version_read,
            )
        )

    def _direct_url(self, payload: object) -> Path:
        info_dir = self._dist_info()
        (info_dir / "direct_url.json").write_text(json.dumps(payload), encoding="utf-8")
        return info_dir

    def test_egg_info_inside_the_source_root_proves_the_checkout(self) -> None:
        egg = self.source_root / "agents_remember_mcp.egg-info"
        egg.mkdir()
        self.assertTrue(_is_editable_install(self._fake(egg, "unused")))

    def test_editable_direct_url_proves_the_checkout(self) -> None:
        dist = self._fake(
            self._direct_url({"url": "file:///x", "dir_info": {"editable": True}}),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        self.assertTrue(_is_editable_install(dist))

    def test_exact_metadata_file_bypasses_the_error_suppressing_distribution_adapter(self) -> None:
        seen: list[str] = []

        def read_direct_url(filename: str) -> str:
            seen.append(filename)
            return json.dumps({"url": "file:///x", "dir_info": {"editable": True}})

        dist = self._fake(
            self._direct_url({"url": "file:///x", "dir_info": {"editable": True}}),
            read_direct_url,
        )
        self.assertTrue(_is_editable_install(dist))
        self.assertEqual(seen, [])

    def test_non_editable_direct_url_without_pth_is_not_editable(self) -> None:
        dist = self._fake(
            self._direct_url({"dir_info": {"editable": False}}),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        self.assertFalse(_is_editable_install(dist))

    def test_dir_info_not_a_dict_falls_through_to_the_pth_check(self) -> None:
        dist = self._fake(
            self._direct_url({"dir_info": "x"}),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        self.assertFalse(_is_editable_install(dist))

    def test_invalid_direct_url_json_falls_through_to_the_pth_check(self) -> None:
        info_dir = self._dist_info()
        (info_dir / "direct_url.json").write_text("not-json", encoding="utf-8")
        dist = self._fake(
            info_dir,
            AssertionError("PathDistribution adapter must not own the read"),
        )
        self.assertFalse(_is_editable_install(dist))

    def test_read_text_failure_is_not_treated_as_absent_metadata(self) -> None:
        dist = self._fake(
            self._dist_info(),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        with (
            mock.patch.object(Path, "read_text", side_effect=OSError("metadata unreadable")),
            self.assertRaisesRegex(OSError, "metadata unreadable"),
        ):
            _is_editable_install(dist)

    def test_editable_pth_marker_proves_the_checkout(self) -> None:
        (self.site / "__editable__.agents_remember_mcp.pth").write_text("", encoding="utf-8")
        dist = self._fake(
            self._dist_info(),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        self.assertTrue(_is_editable_install(dist))

    def test_none_metadata_dir_is_not_editable(self) -> None:
        self.assertFalse(_is_editable_install(self._fake(None, "x")))

    def test_metadata_path_that_is_a_file_is_not_editable(self) -> None:
        marker = self.site / "not-a-dir"
        marker.write_text("x", encoding="utf-8")
        self.assertFalse(
            _is_editable_install(
                self._fake(
                    marker,
                    AssertionError("a file metadata path must stop before adapter reads"),
                )
            )
        )

    def test_path_is_within_reports_outside_paths(self) -> None:
        self.assertTrue(_path_is_within(self.source_root, self.source_root / "egg-info"))
        outside = self.root / "outside" / "dist-info"
        self.assertFalse(_path_is_within(self.source_root, outside))

    def test_preflight_refuses_a_real_non_editable_pre_floor_distribution(self) -> None:
        # A real non-editable wheel shape (no direct_url editable, no pth, outside
        # the source root) with a pre-floor version reaches the version-floor raise
        # through the REAL _is_editable_install, not a mock.
        dist = self._fake(
            self._direct_url({"dir_info": {"editable": False}}),
            AssertionError("PathDistribution adapter must not own the read"),
            version="3.0.0rc7",
        )
        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=dist,
            ),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIn("3.0.0rc7", str(raised.exception))

    def test_preflight_passes_a_real_non_editable_current_build(self) -> None:
        # A non-editable wheel at/above the version floor is not provably stale:
        # the preflight exits cleanly (branch 81 -> exit), no refusal.
        dist = self._fake(
            self._direct_url({"dir_info": {"editable": False}}),
            AssertionError("PathDistribution adapter must not own the read"),
            version="3.0.0rc8",
        )
        with mock.patch(
            "agents_remember.tasks.serving_preflight._installed_distribution",
            return_value=dist,
        ):
            require_serving_topology_schema()  # must not raise

    def test_expected_serving_proof_failures_share_typed_recovery_guidance(self) -> None:
        dist_info = self._dist_info()
        dist = self._fake(
            dist_info,
            AssertionError("PathDistribution adapter must not own the read"),
        )
        cases = (
            (
                mock.patch(
                    "agents_remember.tasks.serving_preflight.metadata.distribution",
                    side_effect=OSError("distribution unreadable"),
                ),
                mock.patch.object(Path, "read_text"),
            ),
            (
                mock.patch(
                    "agents_remember.tasks.serving_preflight._installed_distribution",
                    return_value=dist,
                ),
                mock.patch.object(Path, "read_text", side_effect=OSError("metadata unreadable")),
            ),
            (
                mock.patch(
                    "agents_remember.tasks.serving_preflight._installed_distribution",
                    return_value=dist,
                ),
                mock.patch.object(Path, "stat", side_effect=OSError("metadata stat unreadable")),
            ),
            (
                mock.patch(
                    "agents_remember.tasks.serving_preflight._installed_distribution",
                    return_value=dist,
                ),
                mock.patch.object(Path, "iterdir", side_effect=OSError("iteration unreadable")),
            ),
        )
        for index, (distribution_patch, filesystem_patch) in enumerate(cases):
            with (
                self.subTest(case=index),
                distribution_patch,
                filesystem_patch,
                self.assertRaises(TopologyServingBuildError) as raised,
            ):
                require_serving_topology_schema()
            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("serving-build-unverifiable", str(raised.exception))
            self.assertIn("execution-topology-migration.md", str(raised.exception))

    def test_version_metadata_is_snapshotted_once_before_the_floor_decision(self) -> None:
        info_dir = self._direct_url({"dir_info": {"editable": False}})
        metadata_path = info_dir / "METADATA"
        metadata_path.write_text(
            "Metadata-Version: 2.1\nName: agents-remember-mcp\nVersion: 3.0.0rc7\n",
            encoding="utf-8",
        )
        raw_dist = _FakeDist(
            info_dir,
            AssertionError("PathDistribution adapter must not own the read"),
            version="3.0.0rc7",
            fail_after_first_version_read=True,
        )
        original_read_text = Path.read_text
        version_reads = 0

        def read_version_once(path: Path, *args, **kwargs):
            nonlocal version_reads
            if path == metadata_path:
                version_reads += 1
                if version_reads > 1:
                    raise OSError("version metadata changed after first read")
            return original_read_text(path, *args, **kwargs)

        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=_as_dist(raw_dist),
            ),
            mock.patch.object(Path, "read_text", new=read_version_once),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIn("3.0.0rc7", str(raised.exception))
        self.assertEqual(version_reads, 1)
        self.assertEqual(raw_dist.version_reads, 0)

    def test_path_distribution_version_permission_failure_is_a_typed_refusal(self) -> None:
        info_dir = self._direct_url({"dir_info": {"editable": False}})
        (info_dir / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: agents-remember-mcp\nVersion: 3.0.0rc8\n",
            encoding="utf-8",
        )
        dist = metadata.PathDistribution(info_dir)
        original_read_text = Path.read_text

        def denied_metadata(path: Path, *args, **kwargs):
            if path == info_dir / "METADATA":
                raise PermissionError("version metadata unreadable")
            return original_read_text(path, *args, **kwargs)

        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=dist,
            ),
            mock.patch.object(Path, "read_text", new=denied_metadata),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertIn("serving-build-unverifiable", str(raised.exception))

    def test_path_resolution_failure_is_a_typed_preflight_refusal(self) -> None:
        dist = self._fake(
            self._direct_url({"dir_info": {"editable": False}}),
            AssertionError("PathDistribution adapter must not own the read"),
        )
        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=dist,
            ),
            mock.patch.object(Path, "resolve", side_effect=OSError("resolve unreadable")),
            self.assertRaises(TopologyServingBuildError) as raised,
        ):
            require_serving_topology_schema()
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertIn("repair", str(raised.exception))

    def test_programmer_failure_is_not_mislabeled_as_an_upgrade_problem(self) -> None:
        dist = self._fake(None, AssertionError("programmer defect"))
        with (
            mock.patch(
                "agents_remember.tasks.serving_preflight._installed_distribution",
                return_value=dist,
            ),
            self.assertRaisesRegex(AssertionError, "programmer defect"),
        ):
            require_serving_topology_schema()
