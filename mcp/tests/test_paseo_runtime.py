from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

from agents_remember.cli.__main__ import main
from agents_remember.cli.paseo_command import CommandResult, PaseoRuntimeFailure, run_command
from agents_remember.cli.paseo_daemon import runtime_status, stop_runtime
from agents_remember.cli.paseo_daemon_config import previous_config_path, write_provider_entries
from agents_remember.cli.paseo_plugin_files import (
    PLUGIN_ID,
    installed_plugin_path,
    plugin_source_root,
    tree_digest,
)
from agents_remember.cli.paseo_process_record import (
    ProcessFacts,
    inspect_record,
    read_process,
)
from agents_remember.cli.paseo_provision import daemon_settings, provision_runtime
from paseo_runtime_test_support import (
    OTHER_SECRET,
    PINNED,
    SECRET,
    SUPERVISOR_PID,
    FakePaseo,
    Interrupted,
    file_states,
    free,
    ok,
    provider_entries,
    runtime_settings,
    write_plugin,
)


class PaseoRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def provision(self, fake: FakePaseo, **overrides: Any) -> dict[str, Any]:
        settings = overrides.pop("settings", fake.settings)
        if "plugin_source" not in overrides:
            overrides["plugin_source"] = write_plugin(self.root, "one")
        overrides.setdefault("probe", free)
        overrides.setdefault("reader", fake.reader)
        return provision_runtime(settings, runner=fake, **overrides)

    def provision_report(self, settings: Any, *, runner: FakePaseo, reader: Any) -> dict[str, Any]:
        return self.provision(runner, settings=settings, reader=reader)

    def test_fresh_provision_then_a_repeat_that_touches_nothing(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)

        report = provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["daemon"], {"action": "started", "reasons": []})
        self.assertEqual(fake.version_at(settings.install_prefix), PINNED)
        self.assertEqual(
            json.loads((settings.install_prefix / "package.json").read_text())["dependencies"],
            {"@getpaseo/cli": PINNED},
        )
        self.assertIn("--save-exact", fake.calls[1])
        self.assertEqual(fake.calls[1][-1], f"@getpaseo/cli@{PINNED}")
        # Every setting provision owns, with when Paseo applies it, was written before the first
        # start; nothing else was.
        written = {s.path: (s.value, s.applies) for s in daemon_settings(settings)}
        self.assertEqual(
            written,
            {
                "daemon.listen": ("127.0.0.1:6831", "start"),
                "daemon.relay.enabled": (False, "live"),
                "features.webUi.enabled": (True, "start"),
                "features.dictation.enabled": (False, "start"),
                "features.voiceMode.enabled": (False, "start"),
                "pluginsEnabled": (True, "live"),
                "agents.providers": (provider_entries(SECRET), "live"),
            },
        )
        self.assertEqual(
            {path: (fake.configured(path), written[path][1]) for path in written}, written
        )
        self.assertEqual(
            {c["path"]: c["applies"] for c in report["changes"] if c["step"] == "config"},
            {path: applies for path, (_value, applies) in written.items()},
        )
        # The provider entries reached the daemon's private file; the report names their ids only.
        self.assertIn(
            {
                "step": "config",
                "action": "set",
                "path": "agents.providers",
                "providerIds": ["hermes"],
                "applies": "live",
            },
            report["changes"],
        )
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(stat.S_IMODE(fake.config_file.stat().st_mode), 0o600)
        self.assertFalse(previous_config_path(settings.home).exists())
        kinds = fake.kinds()
        self.assertLess(
            max(i for i, kind in enumerate(kinds) if kind == ("config", "set")),
            kinds.index(("daemon", "start")),
        )
        self.assertFalse(fake.speech_downloaded)
        # The packaged plugin source is what got installed, from its copy inside the home.
        copy_root = installed_plugin_path(settings.home)
        self.assertEqual(tree_digest(copy_root), tree_digest(plugin_source_root()))
        self.assertEqual(json.loads((copy_root / "paseo-plugin.json").read_text())["id"], PLUGIN_ID)
        self.assertTrue((copy_root / "index.server.ts").is_file())
        self.assertTrue((copy_root / "index.client.tsx").is_file())
        self.assertEqual(fake.plugins[PLUGIN_ID]["path"], copy_root.as_posix())
        self.assertEqual(
            [(change["step"], change["action"]) for change in report["changes"]],
            [("install", "installed")]
            + [("config", "set")] * 7
            + [("embed", "written"), ("plugin", "copied"), ("daemon", "started")]
            + [("plugin", "installed")],
        )

        before = len(fake.calls)
        files = file_states(settings.home, settings.install_prefix)
        repeat = provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)

        self.assertTrue(repeat["ok"], repeat)
        self.assertFalse(repeat["changed"])
        self.assertEqual(repeat["changes"], [])
        self.assertEqual(repeat["daemon"], {"action": "untouched", "reasons": []})
        self.assertEqual(fake.mutations(before), [])
        self.assertEqual(fake.events, [("start", PINNED)])
        # No file under the home or the prefix was written again, not even with the same bytes.
        self.assertEqual(file_states(settings.home, settings.install_prefix), files)

    def test_other_version_is_replaced_and_a_failed_install_changes_nothing(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)
        fake.install(settings.install_prefix, "0.10.2")
        fake.write_settings(daemon_settings(settings))
        fake.start()

        fake.npm_fails = True
        before = len(fake.calls)
        failed = self.provision(fake)

        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "install_failed")
        self.assertEqual(failed["error"]["step"], "install")
        self.assertIn("network request failed", failed["error"]["detail"])
        self.assertEqual(failed["changes"], [])
        self.assertEqual(fake.version_at(settings.install_prefix), "0.10.2")
        self.assertEqual(fake.running()["version"], "0.10.2")
        self.assertEqual(fake.mutations(before), [("npm", "install")])
        self.assertEqual(list(settings.install_prefix.glob(".ar-*")), [])

        # npm succeeds but what it staged does not report the pinned version: same outcome.
        fake.npm_fails = False
        fake.npm_installs = "0.11.0-beta.1"
        wrong = self.provision(fake)

        self.assertEqual((wrong["error"]["code"], wrong["changes"]), ("install_failed", []))
        self.assertEqual(fake.version_at(settings.install_prefix), "0.10.2")
        self.assertEqual(fake.running()["version"], "0.10.2")
        self.assertEqual(list(settings.install_prefix.glob(".ar-*")), [])

        fake.npm_installs = None
        report = self.provision(fake)

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            report["changes"][0],
            {"step": "daemon", "action": "stopped", "reasons": ["version"]},
        )
        self.assertEqual(
            report["changes"][1],
            {
                "step": "install",
                "action": "replaced",
                "previousVersion": "0.10.2",
                "version": PINNED,
            },
        )
        self.assertEqual(report["daemon"], {"action": "restarted", "reasons": ["version"]})
        # The old daemon was stopped while its own install was still in place.
        self.assertEqual(fake.events, [("stop", "0.10.2"), ("start", PINNED)])
        self.assertEqual(fake.running()["version"], PINNED)
        self.assertEqual(list(settings.install_prefix.glob(".ar-*")), [])

    def rerun(self, fake: FakePaseo, **changed: Any) -> tuple[dict[str, Any], list[Any]]:
        """Provision again with some settings facts changed; the report and the mutating calls."""
        probe = changed.pop("probe", free)
        source = changed.pop("plugin_source", None) or installed_plugin_path(fake.settings.home)
        block = {
            "listen": fake.settings.listen,
            "providers": fake.settings.providers,
            "embed": fake.settings.embed_payload(),
            **changed,
        }
        fake.settings = runtime_settings(self.root, **block)
        before = len(fake.calls)
        report = self.provision(fake, plugin_source=source, probe=probe)
        self.assertTrue(report["ok"], report)
        self.assertNotIn(OTHER_SECRET, json.dumps(report))
        return report, fake.mutations(before)

    def test_reload_or_untouched_follows_what_changed(self) -> None:
        fake = FakePaseo(runtime_settings(self.root))
        self.assertTrue(self.provision(fake)["ok"])

        with self.subTest("changed provider entries are reloaded from the file"):
            report, mutations = self.rerun(fake, providers=provider_entries(OTHER_SECRET))
            self.assertEqual(report["daemon"], {"action": "reloaded", "reasons": []})
            self.assertEqual(mutations, [("daemon", "reload")])
            self.assertEqual(
                fake.running()["live"]["agents.providers"], provider_entries(OTHER_SECRET)
            )
            self.assertEqual(report["changes"][0]["providerIds"], ["hermes"])

        with self.subTest("another live setting is written and applied at once"):
            fake.write("pluginsEnabled", False)
            report, mutations = self.rerun(fake)
            self.assertEqual(report["daemon"], {"action": "reloaded", "reasons": []})
            self.assertEqual(mutations, [("config", "set")])
            self.assertTrue(fake.running()["live"]["pluginsEnabled"])

        with self.subTest("the embed list reloads the plugin"):
            embed = [{"dashboardOrigin": "http://localhost:9797", "frameBaseUrl": "http://h:1"}]
            report, mutations = self.rerun(fake, embed=embed)
            self.assertEqual(report["daemon"]["action"], "reloaded")
            self.assertEqual(mutations, [("plugin", "reload")])
            self.assertEqual(json.loads(fake.plugins[PLUGIN_ID]["loaded"][1])["embed"], embed)

        with self.subTest("changed plugin content is reinstalled and reloaded"):
            source = write_plugin(self.root, "two")
            report, mutations = self.rerun(fake, plugin_source=source)
            self.assertEqual(report["daemon"]["action"], "reloaded")
            self.assertEqual(mutations, [("plugin", "reload")])
            self.assertEqual(fake.plugins[PLUGIN_ID]["loaded"][0], tree_digest(source))
            self.assertFalse((installed_plugin_path(fake.settings.home) / "node_modules").exists())

        with self.subTest("nothing changed"):
            report, mutations = self.rerun(fake)
            self.assertEqual(report["daemon"], {"action": "untouched", "reasons": []})
            self.assertEqual((report["changed"], mutations), (False, []))

    def test_a_running_daemon_restarts_for_what_only_a_start_applies(self) -> None:
        fake = FakePaseo(runtime_settings(self.root))
        self.assertTrue(self.provision(fake)["ok"])
        restart = [("daemon", "stop"), ("daemon", "start")]
        restart_with_write = [("daemon", "stop"), ("config", "set"), ("daemon", "start")]

        def held_by_the_home_daemon(_host: str, _port: int) -> OSError | None:
            in_use = OSError(errno.EADDRINUSE, "Address already in use")
            return in_use if fake.daemon is not None else None

        with self.subTest("a changed listen address"):
            report, mutations = self.rerun(fake, listen="127.0.0.1:6832")
            self.assertEqual(
                report["daemon"], {"action": "restarted", "reasons": ["setting:daemon.listen"]}
            )
            self.assertEqual(mutations, restart_with_write)
            self.assertEqual(fake.running()["started_with"]["daemon.listen"], "127.0.0.1:6832")

        with self.subTest("a host-only listen change: the port holder is this daemon itself"):
            report, mutations = self.rerun(
                fake, listen="localhost:6832", probe=held_by_the_home_daemon
            )
            self.assertEqual(
                report["daemon"], {"action": "restarted", "reasons": ["setting:daemon.listen"]}
            )
            self.assertEqual(mutations, restart_with_write)
            self.assertEqual(fake.running()["started_with"]["daemon.listen"], "localhost:6832")

        with self.subTest("a start-only feature that differs in the daemon configuration"):
            fake.write("features.dictation.enabled", True)
            report, mutations = self.rerun(fake)
            self.assertEqual(
                report["daemon"],
                {"action": "restarted", "reasons": ["setting:features.dictation.enabled"]},
            )
            self.assertEqual(mutations, restart_with_write)
            self.assertEqual(
                fake.running()["started_with"]["features.dictation"], {"enabled": False}
            )
            self.assertFalse(fake.speech_downloaded)

        with self.subTest("a daemon running another version than the prefix holds"):
            fake.running()["version"] = "0.10.2"
            report, mutations = self.rerun(fake)
            self.assertEqual(report["daemon"], {"action": "restarted", "reasons": ["version"]})
            self.assertEqual(mutations, restart)
            self.assertEqual(fake.running()["version"], PINNED)

        with self.subTest("a daemon listening elsewhere than its configuration says"):
            fake.running()["started_with"]["daemon.listen"] = "127.0.0.1:6830"
            report, mutations = self.rerun(fake)
            self.assertEqual(report["daemon"], {"action": "restarted", "reasons": ["listen"]})
            self.assertEqual(mutations, restart)
            self.assertEqual(fake.running()["started_with"]["daemon.listen"], "localhost:6832")

    def test_held_listen_port_fails_naming_it_and_leaves_nothing_running(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            settings = runtime_settings(self.root, listen=f"127.0.0.1:{port}")
            fake = FakePaseo(settings)

            report = provision_runtime(
                settings,
                runner=fake,
                reader=fake.reader,
                plugin_source=write_plugin(self.root, "one"),
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "listen_port_in_use")
        self.assertIn(f"listen port {port} ", report["error"]["message"])
        self.assertNotIn(("daemon", "start"), fake.kinds())
        self.assertEqual(fake.configured("daemon.listen"), None)
        self.assertEqual(report["daemon"]["action"], "untouched")

        # The address is taken between the check and the start: Paseo's own refusal is reported
        # the same way, and the configured port is kept.
        fake.port_held = True
        fake.lingering_supervisor = True
        raced = provision_runtime(
            settings,
            runner=fake,
            reader=fake.reader,
            plugin_source=write_plugin(self.root, "one"),
            probe=free,
        )

        self.assertEqual(raced["error"]["code"], "listen_port_in_use")
        self.assertIn("EADDRINUSE", raced["error"]["detail"])
        # A supervisor the failed start left behind is this home's own, and is stopped.
        self.assertEqual(fake.kinds()[-2:], [("daemon", "start"), ("daemon", "stop")])
        self.assertEqual((fake.signalled, fake.recorded_pid()), ([SUPERVISOR_PID], None))
        self.assertIsNone(fake.daemon)
        self.assertEqual(fake.configured("daemon.listen"), f"127.0.0.1:{port}")

        # A running daemon of this home excuses the probe only for an address it can hold itself.
        # Another host on its port, or another port, is probed and refused with nothing touched.
        fake.port_held = False
        fake.lingering_supervisor = False
        self.assertTrue(self.provision(fake)["ok"])

        def held_elsewhere(host: str, probed: int) -> OSError | None:
            own = (host, probed) == ("127.0.0.1", port)
            return None if own else OSError(errno.EADDRINUSE, "Address already in use")

        for listen in (f"127.0.0.2:{port}", "127.0.0.1:1"):
            with self.subTest(listen=listen):
                before = len(fake.calls)
                moved = runtime_settings(self.root, listen=listen)
                report = self.provision(fake, settings=moved, probe=held_elsewhere)
                self.assertEqual(report["error"]["code"], "listen_port_in_use")
                self.assertEqual((report["changes"], fake.mutations(before)), ([], []))
                self.assertEqual(report["daemon"]["action"], "untouched")
                self.assertEqual(
                    fake.running()["started_with"]["daemon.listen"], f"127.0.0.1:{port}"
                )

    def test_plugin_load_failure_keeps_the_daemon_up_and_status_says_failed(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)
        fake.plugin_error = 'Build failed with 1 error: index.server.ts:1:37: ERROR: Expected "{"'

        report = self.provision(fake)

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "plugin_load_failed")
        self.assertEqual(report["error"]["detail"], fake.plugin_error)
        self.assertEqual(report["daemon"]["action"], "started")
        # The install command failed, so no plugin change beyond the copy is claimed.
        self.assertEqual(
            [c["action"] for c in report["changes"] if c["step"] == "plugin"], ["copied"]
        )
        self.assertIsNotNone(fake.daemon)
        status = runtime_status(settings, runner=fake, reader=fake.reader)
        self.assertTrue(status["running"])
        self.assertEqual(
            status["plugin"], {"id": PLUGIN_ID, "state": "failed", "error": fake.plugin_error}
        )

        # Once the cause is gone the next pass loads the plugin again without a restart.
        fake.plugin_error = None
        recovered = self.provision(fake)

        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["daemon"]["action"], "reloaded")
        self.assertEqual(recovered["changes"], [{"step": "plugin", "action": "reloaded"}])
        self.assertEqual(
            runtime_status(settings, runner=fake, reader=fake.reader)["plugin"]["state"], "running"
        )

    def test_a_pass_interrupted_at_any_call_converges_on_the_next(self) -> None:
        target = {
            "providers": provider_entries(OTHER_SECRET),
            "embed": [{"dashboardOrigin": "http://localhost:9797", "frameBaseUrl": "http://h:1"}],
        }
        # What ran before the interrupted pass: nothing, another version, the pinned version on
        # another listen address, or the pinned runtime with only live facts about to change.
        earlier: dict[str, dict[str, Any] | None] = {
            "fresh": None,
            "other version": {"version": "0.10.2", "listen": "127.0.0.1:6830"},
            "start-only setting": {"listen": "127.0.0.1:6830"},
            "live facts only": {},
        }

        def world(name: str, before: dict[str, Any] | None) -> tuple[FakePaseo, Path]:
            root = self.root / name
            fake = FakePaseo(runtime_settings(root, **target))
            if before is not None:
                fake.install(fake.settings.install_prefix, before.get("version", PINNED))
                done = provision_runtime(
                    runtime_settings(root, **before),
                    runner=fake,
                    reader=fake.reader,
                    plugin_source=write_plugin(root, "one"),
                    probe=free,
                )
                assert done["ok"], done
            return fake, write_plugin(root, "two")

        def converged(fake: FakePaseo) -> dict[str, Any]:
            state = fake.converged()
            state["plugin"]["path"] = Path(state["plugin"]["path"]).name
            return state

        for label, before in earlier.items():
            fake, source = world(f"reference {label}", before)
            first = len(fake.calls)
            reference = provision_runtime(
                fake.settings, runner=fake, reader=fake.reader, plugin_source=source, probe=free
            )
            self.assertTrue(reference["ok"], reference)
            expected = converged(fake)
            self.assertEqual(
                expected["daemon"],
                {
                    "version": PINNED,
                    "started_with": {
                        "daemon.listen": "127.0.0.1:6831",
                        "features.webUi": {"enabled": True},
                        "features.dictation": {"enabled": False},
                        "features.voiceMode": {"enabled": False},
                    },
                    "live": {
                        "daemon.relay.enabled": False,
                        "pluginsEnabled": True,
                        "agents.providers": target["providers"],
                    },
                },
            )
            self.assertEqual(expected["plugin"]["status"], "running")
            self.assertEqual(expected["plugin"]["loaded"][0], tree_digest(source))
            self.assertEqual(
                json.loads(expected["plugin"]["loaded"][1]), {"embed": target["embed"]}
            )
            self.assertEqual((expected["installed"], expected["leftovers"]), (PINNED, []))
            for cut in range(first, len(fake.calls)):
                with self.subTest(earlier=label, cut=cut - first):
                    fake, source = world(f"{label} cut {cut}", before)
                    fake.interrupt_at = cut
                    with self.assertRaises(Interrupted):
                        provision_runtime(
                            fake.settings,
                            runner=fake,
                            reader=fake.reader,
                            plugin_source=source,
                            probe=free,
                        )
                    fake.interrupt_at = None

                    resumed = provision_runtime(
                        fake.settings,
                        runner=fake,
                        reader=fake.reader,
                        plugin_source=source,
                        probe=free,
                    )

                    self.assertTrue(resumed["ok"], resumed)
                    self.assertEqual(converged(fake), expected)
                    self.assertFalse(fake.speech_downloaded)
                    again = provision_runtime(
                        fake.settings,
                        runner=fake,
                        reader=fake.reader,
                        plugin_source=source,
                        probe=free,
                    )
                    self.assertEqual((again["ok"], again["changed"]), (True, False))

    REFUSED_ENTRIES: ClassVar[dict[str, Any]] = {
        "bad": {"extends": "nope", "env": {"KEY": OTHER_SECRET}}
    }

    def provisioned_with_foreign_keys(self) -> FakePaseo:
        """A running runtime whose daemon file also holds keys only Paseo owns."""
        fake = FakePaseo(runtime_settings(self.root))
        self.assertTrue(self.provision(fake)["ok"])
        fake.write("daemon.hostnames", ["fox.example.ts.net", "b\u00fccher.example"])
        fake.write("agents.other", {"keep": True})
        return fake

    def kept_state(self, fake: FakePaseo) -> list[str]:
        ar_home = fake.settings.home / "agents-remember"
        return sorted(path.name for path in ar_home.glob("config-*"))

    def test_provider_values_stay_in_the_daemon_file_and_a_refused_file_is_put_back(self) -> None:
        fake = self.provisioned_with_foreign_keys()
        refused = runtime_settings(self.root, providers=self.REFUSED_ENTRIES)
        foreign = (fake.configured("daemon.hostnames"), fake.configured("agents.other"))

        for daemon_runs in (True, False):
            with self.subTest(daemon_runs=daemon_runs):
                if not daemon_runs:
                    fake.kill_daemon()
                accepted = fake.config_file.read_bytes()
                before = len(fake.calls)

                report = self.provision(fake, settings=refused)

                self.assertFalse(report["ok"])
                self.assertEqual(report["error"]["code"], "provider_entries_refused")
                self.assertEqual(report["error"]["step"], "config")
                self.assertIn("the previous file was put back", report["error"]["message"])
                self.assertIn("agents.providers.bad.extends", report["error"]["detail"])
                self.assertNotIn(OTHER_SECRET, json.dumps(report))
                self.assertEqual(report["changes"], [])
                # Paseo saw the file, refused it, and the accepted file is back byte for byte.
                validated = ("daemon", "reload") if daemon_runs else ("config", "get")
                self.assertEqual(fake.kinds(before)[-1], validated)
                self.assertEqual(fake.config_file.read_bytes(), accepted)
                self.assertEqual(self.kept_state(fake), [])
                self.assertEqual(stat.S_IMODE(fake.config_file.stat().st_mode), 0o600)
                self.assertNotIn(("daemon", "start"), fake.kinds(before))
                if daemon_runs:
                    self.assertEqual(
                        fake.running()["live"]["agents.providers"], provider_entries(SECRET)
                    )

        with self.subTest("an accepted write leaves every other key, and its text, as it was"):
            self.assertTrue(self.provision(fake)["ok"])
            _report, mutations = self.rerun(fake, providers=provider_entries(OTHER_SECRET))
            self.assertEqual(mutations, [("daemon", "reload")])
            self.assertEqual(
                (fake.configured("daemon.hostnames"), fake.configured("agents.other")), foreign
            )
            self.assertIn("b\u00fccher.example".encode(), fake.config_file.read_bytes())
            self.assertEqual(self.kept_state(fake), [])

        with self.subTest("a call that fails for another reason is not reported as a refusal"):
            accepted = fake.config_file.read_bytes()
            fake.answers = {("daemon", "reload"): CommandResult(124, "", "timed out after 60 s")}
            report = self.provision(fake, settings=runtime_settings(self.root))
            fake.answers = {}
            self.assertEqual(report["error"]["code"], "paseo_command_failed")
            self.assertEqual(report["error"]["detail"], "timed out after 60 s")
            self.assertEqual((fake.config_file.read_bytes(), self.kept_state(fake)), (accepted, []))

        # A daemon file that is not a configuration object is never replaced.
        fake.config_file.write_text("[]", encoding="utf-8")
        with self.assertRaises(PaseoRuntimeFailure) as unreadable:
            write_provider_entries(fake.settings.home, self.REFUSED_ENTRIES)
        self.assertEqual(unreadable.exception.code, "daemon_config_unreadable")
        self.assertEqual(fake.config_file.read_text(encoding="utf-8"), "[]")
        self.assertEqual(self.kept_state(fake), [])

    def test_a_rollback_undoes_only_what_the_pass_changed(self) -> None:
        fake = self.provisioned_with_foreign_keys()
        home = fake.settings.home
        restored = {"step": "config", "action": "restored", "path": "agents.providers"}
        reloaded = {"step": "config", "action": "reloaded", "path": "agents.providers"}

        with self.subTest("the whole file while it is still the file the dead pass wrote"):
            accepted = fake.config_file.read_bytes()
            write_provider_entries(home, self.REFUSED_ENTRIES)
            self.assertIsNotNone(fake.invalid_config())
            self.assertEqual(
                self.kept_state(fake), ["config-previous.json", "config-written.sha256"]
            )
            # Temporary files of a killed write hold provider values; the next pass removes them.
            stale = [
                home / ".config.json.1.a.tmp",
                previous_config_path(home).with_name(".config-previous.json.1.a.tmp"),
            ]
            for path in stale:
                path.write_text(OTHER_SECRET, encoding="utf-8")

            report = self.provision(fake)

            self.assertTrue(report["ok"], report)
            self.assertEqual(
                report["changes"],
                [
                    {"step": "config", "action": "removed-leftover", "path": stale[0].as_posix()},
                    {"step": "config", "action": "removed-leftover", "path": stale[1].as_posix()},
                    {**restored, "restored": "file"},
                    reloaded,
                ],
            )
            self.assertEqual(fake.config_file.read_bytes(), accepted)
            self.assertEqual([path.exists() for path in stale], [False, False])
            self.assertEqual(self.kept_state(fake), [])

        with self.subTest(
            "only the provider entries when Paseo wrote the file after the dead pass"
        ):
            # The dead pass wrote entries Paseo accepts; a hand setting through Paseo then made
            # the running daemon load them along with the new key.
            write_provider_entries(home, provider_entries(OTHER_SECRET))
            paseo = (fake.settings.install_prefix / "node_modules/.bin/paseo").as_posix()
            hand_set = ["daemon", "config", "set", "daemon.hostnames", '["newer.example.ts.net"]']
            self.assertEqual(fake([paseo, *hand_set, "--home", home.as_posix()], 1.0).returncode, 0)
            self.assertEqual(
                fake.running()["live"]["agents.providers"], provider_entries(OTHER_SECRET)
            )
            before = len(fake.calls)

            report = self.provision(fake)

            self.assertTrue(report["ok"], report)
            self.assertEqual(report["changes"], [{**restored, "restored": "providers"}, reloaded])
            self.assertEqual(fake.mutations(before), [("daemon", "reload")])
            self.assertEqual(report["daemon"]["action"], "reloaded")
            self.assertEqual(fake.configured("daemon.hostnames"), ["newer.example.ts.net"])
            self.assertEqual(fake.configured("agents.other"), {"keep": True})
            self.assertEqual(fake.configured("agents.providers"), provider_entries(SECRET))
            # The running daemon holds the configured entries again, not the undone ones.
            self.assertEqual(fake.running()["live"]["agents.providers"], provider_entries(SECRET))
            self.assertEqual(self.kept_state(fake), [])

        with self.subTest("only the provider entries when Paseo wrote the file during a refusal"):
            fake.during = {("daemon", "reload"): lambda: fake.write("daemon.hostnames", ["x.net"])}

            report = self.provision(
                fake, settings=runtime_settings(self.root, providers=self.REFUSED_ENTRIES)
            )

            self.assertEqual(report["error"]["code"], "provider_entries_refused")
            self.assertIn("only the previous provider entries", report["error"]["message"])
            self.assertEqual(fake.configured("daemon.hostnames"), ["x.net"])
            self.assertEqual(fake.configured("agents.other"), {"keep": True})
            self.assertEqual(fake.configured("agents.providers"), provider_entries(SECRET))
            self.assertEqual((self.kept_state(fake), fake.invalid_config()), ([], None))
            self.assertEqual(fake.running()["live"]["agents.providers"], provider_entries(SECRET))

    def test_a_process_record_is_acted_on_only_when_it_names_this_homes_supervisor(self) -> None:
        foreign = 999
        stale_records: dict[str, tuple[tuple[ProcessFacts | None, ...], str]] = {
            "a dead process": ((), "the recorded process no longer exists"),
            "a live process that is not a supervisor": (
                (ProcessFacts("sleep 600", None),),
                "the recorded process is not a Paseo supervisor",
            ),
            "the supervisor of another home": (
                (ProcessFacts("Paseo Supervisor", "/another/paseo/home"),),
                "the recorded process is the supervisor of another home",
            ),
        }
        for index, (label, (alive_as, reason)) in enumerate(stale_records.items()):
            with self.subTest(record_names=label):
                # The daemon crashed; its record now names something else.
                fake = FakePaseo(runtime_settings(self.root / str(index)))
                home = fake.settings.home
                self.assertTrue(self.provision(fake)["ok"])
                fake.kill_daemon()
                fake.record(foreign, *alive_as)
                stale = {"pid": foreign, "reason": reason}
                before = len(fake.calls)

                status = runtime_status(fake.settings, runner=fake, reader=fake.reader)

                self.assertEqual((status["running"], status["staleRecord"]), (False, stale))
                self.assertEqual((len(fake.calls), fake.record_file.is_file()), (before, True))

                # Provision with a reason to restart: nothing is stopped, the daemon is started.
                moved = runtime_settings(self.root / str(index), listen="127.0.0.1:6832")
                report = self.provision(fake, settings=moved)

                self.assertTrue(report["ok"], report)
                self.assertEqual(
                    report["changes"][0],
                    {"step": "daemon", "action": "removed-stale-record", **stale},
                )
                self.assertEqual(report["daemon"], {"action": "started", "reasons": []})
                self.assertNotIn(("daemon", "stop"), fake.kinds(before))
                self.assertEqual((fake.signalled, fake.recorded_pid()), ([], SUPERVISOR_PID))
                self.assertEqual(foreign in fake.processes, bool(alive_as))

                fake.kill_daemon()
                fake.record(foreign, *alive_as)
                before = len(fake.calls)

                stopped = stop_runtime(fake.settings, runner=fake, reader=fake.reader)

                self.assertEqual(
                    stopped,
                    {
                        "ok": True,
                        "home": home.as_posix(),
                        "action": "not running",
                        "pid": None,
                        "staleRecord": stale,
                    },
                )
                self.assertEqual((fake.signalled, len(fake.calls)), ([], before))
                self.assertFalse(fake.record_file.exists())
                self.assertEqual(foreign in fake.processes, bool(alive_as))

    def test_a_recorded_process_is_read_from_proc_and_one_that_cannot_be_read_is_never_signalled(
        self,
    ) -> None:
        fake = FakePaseo(runtime_settings(self.root))
        home = fake.settings.home
        self.assertTrue(self.provision(fake)["ok"])
        fake.kill_daemon()
        foreign = 999

        with self.subTest("the real reader tells a live process, its home and a dead one apart"):
            if not Path("/proc/self").exists():
                self.skipTest("no /proc on this system")
            sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
            child = subprocess.Popen(sleeper, env={"PASEO_HOME": home.as_posix()})
            try:
                facts = read_process(child.pid)
                assert facts is not None
                self.assertEqual(facts.paseo_home, home.as_posix())
                self.assertTrue(facts.command_line.startswith(sys.executable))
                fake.record(child.pid)
                state = inspect_record(home, "stop")
                self.assertEqual((state.kind, state.pid), ("stale", child.pid))
                self.assertEqual(state.reason, "the recorded process is not a Paseo supervisor")
            finally:
                child.kill()
                child.wait()
            self.assertIsNone(read_process(child.pid))
            self.assertEqual(
                inspect_record(home, "stop").reason, "the recorded process no longer exists"
            )
            fake.record_file.unlink()
            self.assertEqual(inspect_record(home, "stop").kind, "absent")

        with self.subTest(record_names="a live process this user cannot inspect"):
            fake.record(foreign, None)
            before = len(fake.calls)
            for operation in (runtime_status, stop_runtime, self.provision_report):
                try:
                    report = operation(fake.settings, runner=fake, reader=fake.reader)
                except PaseoRuntimeFailure as failure:
                    report = {"error": failure.as_payload()}
                self.assertEqual(report["error"]["code"], "process_record_unverifiable")
                self.assertIn("999", report["error"]["message"])
            self.assertEqual((fake.signalled, len(fake.calls)), ([], before))
            self.assertTrue(fake.record_file.is_file())

    def test_status_and_stop_address_only_the_configured_home(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)

        # Nothing installed and no process record: not running, and nothing else is invoked.
        absent = runtime_status(settings, runner=fake, reader=fake.reader)
        self.assertEqual(
            absent,
            {
                "ok": True,
                "home": settings.home.as_posix(),
                "running": False,
                "version": None,
                "serverId": None,
                "listen": None,
                "plugin": None,
                "embed": None,
                "providers": None,
                "staleRecord": None,
            },
        )
        self.assertEqual(
            stop_runtime(settings, runner=fake, reader=fake.reader),
            {
                "ok": True,
                "home": settings.home.as_posix(),
                "action": "not running",
                "pid": None,
                "staleRecord": None,
            },
        )
        self.assertEqual(len(fake.calls), 2)

        self.assertTrue(self.provision(fake)["ok"])
        status = runtime_status(settings, runner=fake, reader=fake.reader)
        self.assertEqual(
            {key: status[key] for key in ("running", "version", "serverId", "listen")},
            {"running": True, "version": PINNED, "serverId": "srv_fake", "listen": settings.listen},
        )
        self.assertEqual(status["plugin"], {"id": PLUGIN_ID, "state": "running", "error": None})
        self.assertEqual(status["embed"], settings.embed_payload())
        self.assertEqual(
            status["providers"],
            [
                {"provider": "claude", "available": True, "error": None},
                {"provider": "hermes", "available": False, "error": "not installed"},
            ],
        )

        # A daemon that does not answer the plugin query is still reported as running.
        fake.plugins_unreachable = True
        self.assertEqual(
            runtime_status(settings, runner=fake, reader=fake.reader)["plugin"],
            {"id": PLUGIN_ID, "state": "unknown", "error": "Daemon is not running"},
        )
        fake.plugins_unreachable = False

        # An answer that is JSON but not the expected shape is a named failure, not a crash, and
        # nothing of the answer is echoed: a configuration read can carry provider values.
        for kind, operation in (
            (("daemon", "status"), runtime_status),
            (("daemon", "stop"), stop_runtime),
            (("daemon", "status"), self.provision_report),
            (("daemon", "config"), self.provision_report),
        ):
            with self.subTest(kind=kind, operation=operation.__name__):
                fake.answers = {kind: ok([{"env": {"KEY": OTHER_SECRET}}])}
                try:
                    report = operation(settings, runner=fake, reader=fake.reader)
                except PaseoRuntimeFailure as failure:
                    report = {"error": failure.as_payload()}
                self.assertEqual(report["error"]["code"], "paseo_invalid_response")
                self.assertIsNone(report["error"]["detail"])
                self.assertNotIn(OTHER_SECRET, json.dumps(report))
        fake.answers = {("plugin", "ls"): ok({"id": PLUGIN_ID})}
        self.assertEqual(
            runtime_status(settings, runner=fake, reader=fake.reader)["plugin"]["state"], "unknown"
        )
        fake.answers = {}
        self.assertIsNotNone(fake.daemon)

        before = len(fake.calls)
        stopped = stop_runtime(settings, runner=fake, reader=fake.reader)
        again = stop_runtime(settings, runner=fake, reader=fake.reader)

        self.assertEqual((stopped["action"], stopped["pid"]), ("stopped", 4242))
        self.assertEqual((again["action"], again["pid"]), ("not running", None))
        # The fake refuses any call that does not name the configured home or that names a host,
        # so a stop can only ever have reached this home's own process record.
        self.assertEqual(fake.kinds(before), [("daemon", "stop"), ("daemon", "stop")])
        self.assertFalse(runtime_status(settings, runner=fake, reader=fake.reader)["running"])

    def test_commands_refuse_without_a_runtime_block_and_drop_paseo_environment(self) -> None:
        settings_path = self.root / "mcp-settings.json"
        settings_path.write_text(json.dumps({"version": 1}), encoding="utf-8")

        def command(name: str) -> tuple[int, dict[str, Any]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["paseo", name, "--config", settings_path.as_posix()])
            return code, json.loads(output.getvalue())

        with patch("agents_remember.cli.paseo_command.subprocess.run") as run:
            for name in ("provision", "status", "stop"):
                with self.subTest(name):
                    code, document = command(name)
                    self.assertEqual(code, 2)
                    self.assertEqual(document["error"]["code"], "paseo_runtime_not_configured")
                    self.assertIn("no Paseo runtime configured", document["error"]["message"])
            block = {"installPrefix": (self.root / "prefix").as_posix()}
            settings_path.write_text(json.dumps({"paseoRuntime": block}), encoding="utf-8")
            code, document = command("provision")
            self.assertEqual((code, document["error"]["code"]), (2, "settings_invalid"))
            # A file that is not UTF-8 text is refused the same way, by all three commands.
            settings_path.write_bytes(b"\xff\xfe")
            for name in ("provision", "status", "stop"):
                code, document = command(name)
                self.assertEqual((code, document["error"]["code"]), (2, "settings_invalid"))
            run.assert_not_called()

        # Through the real command boundary: a prefix that holds nothing is "not running".
        block = {
            "installPrefix": (self.root / "prefix").as_posix(),
            "home": (self.root / "home").as_posix(),
            "listen": "127.0.0.1:6831",
            "version": PINNED,
            "providers": {},
            "embed": [],
        }
        settings_path.write_text(json.dumps({"paseoRuntime": block}), encoding="utf-8")
        code, document = command("stop")
        self.assertEqual((code, document["action"]), (0, "not running"))
        code, document = command("status")
        self.assertEqual((code, document["running"], document["version"]), (0, False, None))
        self.assertFalse((self.root / "home").exists())

        # Whatever fails after the settings were read ends in the error document and exit 1.
        def breaks(error: Exception) -> Any:
            def operation(_settings: Any) -> dict[str, Any]:
                raise error

            return ("", operation)

        commands = "agents_remember.cli.paseo_runtime._COMMANDS"
        for error, expected in (
            (PermissionError(13, "Permission denied"), "filesystem_error"),
            (RuntimeError("unforeseen"), "unexpected_error"),
            (PaseoRuntimeFailure("paseo_invalid_response", "status", "not an object"), None),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.dict(commands, status=breaks(error)),
            ):
                code, document = command("status")
                self.assertEqual((code, document["ok"]), (1, False))
                self.assertEqual(document["error"]["code"], expected or "paseo_invalid_response")
                self.assertEqual(set(document["error"]), {"code", "step", "message", "detail"})
        reader = "agents_remember.cli.paseo_runtime.load_paseo_runtime_settings"
        with patch(reader, side_effect=PermissionError(13, "Permission denied")):
            code, document = command("stop")
        self.assertEqual((code, document["error"]["code"]), (2, "settings_unreadable"))

        names = ("PASEO_HOME", "PASEO_HOST", "PNT_KEPT")
        script = f"import os; print([name in os.environ for name in {names!r}])"
        with patch.dict(os.environ, dict.fromkeys(names, "inherited")):
            result = run_command([sys.executable, "-c", script], 30.0)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "[False, False, True]"))


if __name__ == "__main__":
    unittest.main()
