from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers.lifecycle import docker_runtime
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_container_health,
    docker_container_networks,
    docker_container_port,
    docker_container_state_summary,
    docker_container_uptime_seconds,
    docker_data_mount_source,
    docker_host_path_matches,
    first_port_mapping,
    mount_source_for_destination,
    parse_docker_timestamp,
)


class DockerInspectContainerTests(unittest.TestCase):
    def test_inspect_refuses_command_json_and_shape_failures(self) -> None:
        cases = (
            ({"returncode": 1, "stdout": ""}, None),
            ({"returncode": 0, "stdout": "not-json"}, None),
            ({"returncode": 0, "stdout": "{}"}, None),
            ({"returncode": 0, "stdout": "[]"}, None),
        )
        for result, expected in cases:
            with (
                self.subTest(result=result),
                mock.patch.object(docker_runtime, "run_command", return_value=result),
                mock.patch.object(docker_runtime, "docker_command", return_value="docker"),
            ):
                self.assertIs(
                    docker_runtime.docker_inspect_container(
                        "container", cwd=Path("/tmp"), timeout=3
                    ),
                    expected,
                )

    def test_inspect_returns_the_first_container_record(self) -> None:
        with (
            mock.patch.object(
                docker_runtime,
                "run_command",
                return_value={"returncode": 0, "stdout": '[{"Id": "one"}, {"Id": "two"}]'},
            ),
            mock.patch.object(docker_runtime, "docker_command", return_value="docker"),
        ):
            result = docker_runtime.docker_inspect_container(
                "container", cwd=Path("/tmp"), timeout=3
            )

        self.assertEqual(result, {"Id": "one"})


class ParseDockerTimestampTests(unittest.TestCase):
    def test_nanosecond_zulu_timestamp_truncates_to_microseconds_utc(self) -> None:
        parsed = parse_docker_timestamp("2026-05-19T05:18:07.123456789Z")

        assert parsed is not None
        self.assertEqual(parsed.tzinfo, UTC)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 5)
        self.assertEqual(parsed.day, 19)
        self.assertEqual(parsed.hour, 5)
        self.assertEqual(parsed.minute, 18)
        self.assertEqual(parsed.second, 7)
        # Nanosecond precision is truncated to the first 6 fractional digits.
        self.assertEqual(parsed.microsecond, 123456)

    def test_offset_timestamp_is_normalized_to_utc(self) -> None:
        parsed = parse_docker_timestamp("2026-05-19T07:18:07.500000+02:00")

        assert parsed is not None
        self.assertEqual(parsed.tzinfo, UTC)
        self.assertEqual(parsed.hour, 5)
        self.assertEqual(parsed.minute, 18)
        self.assertEqual(parsed.microsecond, 500000)

    def test_naive_timestamp_is_assumed_utc(self) -> None:
        parsed = parse_docker_timestamp("2026-05-19T05:18:07")

        assert parsed is not None
        self.assertEqual(parsed.tzinfo, UTC)

    def test_short_fraction_is_padded_to_microseconds(self) -> None:
        parsed = parse_docker_timestamp("2026-05-19T05:18:07.5Z")

        assert parsed is not None
        self.assertEqual(parsed.microsecond, 500000)

    def test_zero_value_timestamp_returns_none(self) -> None:
        # Docker reports a not-yet-started container as the Go zero time.
        self.assertIsNone(parse_docker_timestamp("0001-01-01T00:00:00Z"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(parse_docker_timestamp(""))

    def test_unparseable_value_returns_none(self) -> None:
        self.assertIsNone(parse_docker_timestamp("not-a-timestamp"))


class DockerContainerUptimeTests(unittest.TestCase):
    def test_uptime_is_elapsed_seconds_since_start(self) -> None:
        started = datetime.now(UTC) - timedelta(seconds=120)

        uptime = docker_container_uptime_seconds(started)

        assert uptime is not None
        self.assertGreaterEqual(uptime, 119)
        self.assertLessEqual(uptime, 125)

    def test_future_start_clamps_to_zero(self) -> None:
        started = datetime.now(UTC) + timedelta(seconds=60)

        self.assertEqual(docker_container_uptime_seconds(started), 0)

    def test_none_start_returns_none(self) -> None:
        self.assertIsNone(docker_container_uptime_seconds(None))


class DockerContainerHealthTests(unittest.TestCase):
    def test_health_status_is_extracted(self) -> None:
        self.assertEqual(
            docker_container_health({"Health": {"Status": "healthy"}}),
            "healthy",
        )

    def test_missing_health_section_returns_none(self) -> None:
        self.assertIsNone(docker_container_health({"Running": True}))

    def test_health_without_status_returns_none(self) -> None:
        self.assertIsNone(docker_container_health({"Health": {}}))

    def test_non_mapping_health_returns_none(self) -> None:
        self.assertIsNone(docker_container_health({"Health": "starting"}))


class DockerContainerStateSummaryTests(unittest.TestCase):
    def test_running_container_summary_includes_uptime_and_health(self) -> None:
        started = (datetime.now(UTC) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        inspect = {
            "State": {
                "Status": "running",
                "Running": True,
                "StartedAt": started,
                "Health": {"Status": "healthy"},
            }
        }

        summary = docker_container_state_summary(inspect)

        self.assertEqual(summary["containerState"], "running")
        self.assertTrue(summary["running"])
        self.assertEqual(summary["health"], "healthy")
        self.assertIsInstance(summary["uptimeSeconds"], int)
        assert isinstance(summary["uptimeSeconds"], int)
        self.assertGreaterEqual(summary["uptimeSeconds"], 80)

    def test_stopped_container_has_no_uptime(self) -> None:
        inspect = {
            "State": {
                "Status": "exited",
                "Running": False,
                "StartedAt": "2026-05-19T05:18:07.000000Z",
            }
        }

        summary = docker_container_state_summary(inspect)

        self.assertEqual(summary["containerState"], "exited")
        self.assertFalse(summary["running"])
        self.assertIsNone(summary["uptimeSeconds"])
        self.assertIsNone(summary["health"])

    def test_missing_inspect_data_reports_missing_state(self) -> None:
        summary = docker_container_state_summary(None)

        self.assertEqual(
            summary,
            {
                "containerState": "missing",
                "running": False,
                "startedAt": None,
                "uptimeSeconds": None,
                "health": None,
            },
        )


class MountSourceTests(unittest.TestCase):
    def test_mount_source_matches_requested_destination(self) -> None:
        mount = {"Destination": "/data", "Source": "/host/data"}

        self.assertEqual(mount_source_for_destination(mount, "/data"), "/host/data")

    def test_mount_source_ignores_other_destinations(self) -> None:
        mount = {"Destination": "/other", "Source": "/host/data"}

        self.assertIsNone(mount_source_for_destination(mount, "/data"))

    def test_mount_source_handles_non_dict_mount(self) -> None:
        self.assertIsNone(mount_source_for_destination("not-a-mount", "/data"))

    def test_mount_source_missing_source_returns_none(self) -> None:
        self.assertIsNone(mount_source_for_destination({"Destination": "/data"}, "/data"))

    def test_data_mount_source_scans_mounts_for_destination(self) -> None:
        inspect = {
            "Mounts": [
                {"Destination": "/logs", "Source": "/host/logs"},
                {"Destination": "/data", "Source": "/host/data"},
            ]
        }

        self.assertEqual(docker_data_mount_source(inspect, "/data"), "/host/data")

    def test_data_mount_source_returns_none_when_absent(self) -> None:
        inspect = {"Mounts": [{"Destination": "/logs", "Source": "/host/logs"}]}

        self.assertIsNone(docker_data_mount_source(inspect, "/data"))

    def test_data_mount_source_handles_missing_inspect(self) -> None:
        self.assertIsNone(docker_data_mount_source(None))

    def test_data_mount_source_handles_non_list_mounts(self) -> None:
        self.assertIsNone(docker_data_mount_source({"Mounts": "broken"}))


class DockerHostPathMatchesTests(unittest.TestCase):
    def test_resolved_path_matches_actual(self) -> None:
        target = Path(__file__).resolve()

        self.assertTrue(docker_host_path_matches(target.as_posix(), target))

    def test_trailing_slash_is_ignored(self) -> None:
        target = Path(__file__).resolve().parent

        self.assertTrue(docker_host_path_matches(f"{target.as_posix()}/", target))

    def test_docker_desktop_mount_prefix_is_rewritten_to_drive(self) -> None:
        # Docker Desktop on Windows exposes host C:\Users\me as
        # /run/desktop/mnt/host/c/Users/me; the normalizer maps it back so a
        # Windows-style PureWindowsPath comparison would match.
        self.assertTrue(
            docker_host_path_matches(
                "/run/desktop/mnt/host/c/Users/me",
                cast(Path, _StubResolvePath("c:/Users/me")),
            )
        )

    def test_empty_actual_path_never_matches(self) -> None:
        self.assertFalse(docker_host_path_matches(None, Path(__file__).resolve()))
        self.assertFalse(docker_host_path_matches("", Path(__file__).resolve()))

    def test_divergent_paths_do_not_match(self) -> None:
        target = Path(__file__).resolve()

        self.assertFalse(docker_host_path_matches("/totally/other/path", target))


class _StubResolvePath:
    """A Path-like object whose resolve() yields a fixed value.

    docker_host_path_matches calls expected.resolve(); a real Windows path
    cannot be resolved on POSIX, so this stub returns a stable string to
    exercise the /run/desktop/mnt/host drive-letter rewrite branch.
    """

    def __init__(self, resolved: str) -> None:
        self._resolved = resolved

    def resolve(self) -> str:
        return self._resolved

    def __str__(self) -> str:  # pragma: no cover - defensive
        return self._resolved


class PortMappingTests(unittest.TestCase):
    def test_first_port_mapping_returns_first_entry(self) -> None:
        mapping = [{"HostIp": "0.0.0.0", "HostPort": "6379"}]

        self.assertEqual(first_port_mapping(mapping), {"HostIp": "0.0.0.0", "HostPort": "6379"})

    def test_first_port_mapping_empty_list_returns_none(self) -> None:
        self.assertIsNone(first_port_mapping([]))

    def test_first_port_mapping_non_list_returns_none(self) -> None:
        self.assertIsNone(first_port_mapping(None))

    def test_container_port_resolves_host_ip_and_port(self) -> None:
        inspect = {
            "NetworkSettings": {
                "Ports": {
                    "6379/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6380"}],
                }
            }
        }

        self.assertEqual(docker_container_port(inspect, 6379), ("127.0.0.1", 6380))

    def test_container_port_defaults_host_ip_when_blank(self) -> None:
        inspect = {
            "NetworkSettings": {
                "Ports": {
                    "6379/tcp": [{"HostIp": "", "HostPort": "6380"}],
                }
            }
        }

        self.assertEqual(docker_container_port(inspect, 6379), ("127.0.0.1", 6380))

    def test_container_port_missing_mapping_returns_none(self) -> None:
        inspect = {"NetworkSettings": {"Ports": {}}}

        self.assertIsNone(docker_container_port(inspect, 6379))

    def test_container_port_missing_host_port_returns_none(self) -> None:
        inspect = {
            "NetworkSettings": {
                "Ports": {
                    "6379/tcp": [{"HostIp": "127.0.0.1"}],
                }
            }
        }

        self.assertIsNone(docker_container_port(inspect, 6379))


class DockerContainerNetworksTests(unittest.TestCase):
    def test_network_names_are_collected_into_a_set(self) -> None:
        inspect = {
            "NetworkSettings": {
                "Networks": {
                    "ar-cgc-code": {"NetworkID": "abc"},
                    "bridge": {"NetworkID": "def"},
                }
            }
        }

        self.assertEqual(
            docker_container_networks(inspect),
            {"ar-cgc-code", "bridge"},
        )

    def test_missing_inspect_returns_empty_set(self) -> None:
        self.assertEqual(docker_container_networks(None), set())

    def test_non_dict_networks_returns_empty_set(self) -> None:
        inspect = {"NetworkSettings": {"Networks": "broken"}}

        self.assertEqual(docker_container_networks(inspect), set())


if __name__ == "__main__":
    unittest.main()
