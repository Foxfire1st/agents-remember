from __future__ import annotations

from agents_remember.serving.harness_launch import ResolvedLaunch
from test_spawn_agent_session import (
    _DAEMON_PACKAGE_ROOT,
    SpawnKnobApplicationTests,
    _FakePaster,
    _ObservedPaster,
    _runner_config,
)


class SpawnKnobApplicationTests1(SpawnKnobApplicationTests):
    def test_native_launch_selection_rides_the_runner_config_with_no_session_command(self) -> None:
        self._write_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "max",
                    }
                }
            }
        )
        paster = _ObservedPaster(capture="Fable 5 with max effort · Claude Max\n◉ max · /effort\n")
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "worker"},
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("claude", "claude-fable-5", "max", self.tmp),
        )
        # The runner validates dynamically before its adapter emits the native flags; spawn never
        # pastes a model/effort command or a task brief.
        self.assertEqual(paster.calls, [])
        self.assertNotIn("sessionCommands", payload)
        self.assertNotIn("sessionCommandsDelivered", payload)

    def test_ultracode_is_not_synthesized_into_a_session_command(self) -> None:
        self._write_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "ultracode",
                    }
                }
            }
        )
        paster = _ObservedPaster(
            capture="Fable 5 with ultracode effort · Claude Max\n◉ ultracode · /effort\n"
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"}, paster=paster)
        self.assertEqual(payload["status"], "spawned-unbriefed")
        # Dynamic launch validation rejects this stale value when the runner starts. The spawn seam
        # carries it honestly and never turns it into a composer/session paste.
        self.assertEqual(_runner_config(self.host).argv, ("claude",))
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                "AR_SPAWN_ROLE": "worker",
                "AR_SPAWN_MODEL": "claude-fable-5",
                "AR_SPAWN_EFFORT": "ultracode",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )
        self.assertEqual(paster.calls, [])
        self.assertEqual(_runner_config(self.host).session_commands, ())
        self.assertNotIn("sessionCommands", payload)
        self.assertNotIn("sessionCommandsDelivered", payload)

    def test_unknown_effort_is_carried_to_dynamic_runner_validation(self) -> None:
        self._write_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "turbo",
                    }
                }
            }
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(
            _runner_config(self.host).resolved_launch,
            ResolvedLaunch("claude", "claude-fable-5", "turbo", self.tmp),
        )
        self.assertEqual(_runner_config(self.host).session_commands, ())

    def test_codex_builtin_harness_receives_structured_launch_selection(self) -> None:
        self._write_settings(
            {"roles": {"worker": {"harness": "codex", "model": "gpt-5.6-sol", "effort": "xhigh"}}}
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("codex",))
        self.assertEqual(
            runner.resolved_launch,
            ResolvedLaunch("codex", "gpt-5.6-sol", "xhigh", self.tmp),
        )
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                "AR_SPAWN_ROLE": "worker",
                "AR_SPAWN_MODEL": "gpt-5.6-sol",
                "AR_SPAWN_EFFORT": "xhigh",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )

    def test_worker_manager_curator_tiers_resolve_and_record_without_brief_log(self) -> None:
        tiers = {
            "worker": ("gpt-5.6-sol", "xhigh"),
            "manager": ("gpt-5.6-terra", "medium"),
            "curator": ("gpt-5.6-luna", "medium"),
        }
        self._write_settings(
            {
                "roles": {
                    role: {"harness": "codex", "model": model, "effort": effort}
                    for role, (model, effort) in tiers.items()
                }
            }
        )
        for role, (model, effort) in tiers.items():
            paster = _FakePaster()
            payload = self._spawn(
                session_id=f"{role}-tier",
                env={"AR_SPAWN_ROLE": role},
                paster=paster,
            )
            self.assertEqual(payload["status"], "spawned-unbriefed")
            self.assertEqual(payload["resolvedModel"], model)
            self.assertEqual(payload["resolvedEffort"], effort)
            self.assertNotIn("sessionLogEntryId", payload)
            self.assertNotIn("sessionLogPath", payload)
            runner = _runner_config(self.host, -1)
            self.assertEqual(runner.argv, ("codex",))
            self.assertEqual(
                runner.resolved_launch,
                ResolvedLaunch("codex", model, effort, self.tmp),
            )
            row = self.catalog.get(f"{role}-tier")
            assert row is not None
            self.assertEqual(row.resolved_model, model)
            self.assertEqual(row.resolved_effort, effort)
            self.assertIsNone(row.session_log_entry_id)
            self.assertIsNone(row.session_log_path)

    def test_spawn_never_attempts_a_brief_or_binds_a_session_log(self) -> None:
        self._write_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "codex",
                        "model": "gpt-5.6-sol",
                        "effort": "xhigh",
                    }
                }
            }
        )
        paster = _FakePaster(delivered=True, submitted=False, capture="failure pane")
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"}, paster=paster)

        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(paster.calls, [])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertIsNone(row.session_log_path)
        self.assertNotIn("deliveryCapture", payload)

    def test_launch_args_ride_the_argv_verbatim_and_are_recorded(self) -> None:
        self._write_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "max",
                        "launchArgs": ["--dangerously-skip-permissions"],
                    }
                }
            }
        )
        payload = self._spawn(env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(
            _runner_config(self.host).argv,
            ("claude", "--dangerously-skip-permissions"),
        )
        self.assertEqual(
            _runner_config(self.host).resolved_launch,
            ResolvedLaunch("claude", "claude-fable-5", "max", self.tmp),
        )
        self.assertEqual(payload["launchArgs"], ["--dangerously-skip-permissions"])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.launch_args, ("--dangerously-skip-permissions",))

    def test_prompt_keywords_are_recorded_but_withheld_from_spawn(self) -> None:
        # The original acceptance case: strategist as effort:max + promptKeywords:["ultracode"]
        # dispatches claude with --effort max and the keyword riding the paste.
        self._write_settings(
            {
                "roles": {
                    "strategist": {
                        "harness": "claude",
                        "model": "claude-fable-5",
                        "effort": "max",
                        "promptKeywords": ["ultracode"],
                    }
                }
            }
        )
        paster = _ObservedPaster(capture="Fable 5 with max effort · Claude Max\n◉ max · /effort\n")
        payload = self._spawn(
            env={"AR_SPAWN_ROLE": "strategist"},
            paster=paster,
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(_runner_config(self.host).argv, ("claude",))
        self.assertEqual(paster.calls, [])
        self.assertEqual(payload["promptKeywords"], ["ultracode"])
        row = self.catalog.get("worker-1")
        assert row is not None
        self.assertEqual(row.prompt_keywords, ("ultracode",))
