"""Reopened roles survive a host restart's late listing until their actual first turn."""

import asyncio
import json
import select
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from agents_remember.cli import paseo_warmup
from agents_remember.cli.paseo_status import HostStartIdentity, LiveAgents
from agents_remember.cli.paseo_warmup_state import WarmupTracker
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.observer.task_entries import task_state
from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.background_accounting import BackgroundLedger
from agents_remember.serving.paseo.paseo_plugin_files import (
    plugin_source_root,
    publish_role_agent,
    read_role_agents,
)
from agents_remember.serving.projections.host_activity import HostActivityInput
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_paseo_status import StatusTestCase as _StatusTestCase

_PUBLISHER_DRIVER = """
const [source, home] = process.argv.slice(1);
const { HostActivityPublisher } = await import(source + '/host_activity.ts');
const { readRoleAgents } = await import(source + '/role_agents.ts');
const { writeFileSync } = await import('node:fs');
const { createInterface } = await import('node:readline');
let publisher;
for await (const line of createInterface({input: process.stdin})) {
  const step = JSON.parse(line);
  if (step.boot) publisher = new HostActivityPublisher(() => readRoleAgents(home),
    publication => writeFileSync(home + '/agents-remember/host-activity.json', JSON.stringify(publication)),
    () => new Date().toISOString(), step.boot);
  if (step.agents) await publisher.reconcile({ agents: { async list() { return {
    entries: step.agents.map(agent => ({agent: {pendingPermissions: [], ...agent}})),
    pageInfo: {hasMore: false, nextCursor: null}
  }; } } });
  if (step.event) publisher.note(step.event, step.id, step.at);
  process.stdout.write(JSON.stringify({ok: true}) + '\\n');
}
"""


class _HostPublisher:
    """One real publisher per boot, with only the native listing and events injected."""

    def __init__(self, root, home):
        source = root / "plugin-source"
        shutil.copytree(plugin_source_root() / "server", source)
        self.process = subprocess.Popen(
            [
                "node",
                "--experimental-strip-types",
                "--input-type=module",
                "-e",
                _PUBLISHER_DRIVER,
                source.as_uri(),
                str(home),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def step(self, **step):
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(step) + "\n")
        self.process.stdin.flush()
        assert select.select([self.process.stdout], [], [], HANG_GUARD_SECONDS)[0]
        assert json.loads(self.process.stdout.readline()) == {"ok": True}

    def close(self):
        try:
            _, stderr = self.process.communicate(timeout=HANG_GUARD_SECONDS)
            assert self.process.returncode == 0, stderr
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.communicate()


class TestRoleRevivalRegistration(_StatusTestCase):
    def test_warmup_driver_after_the_new_boots_listing(self):
        self._restart_then_reopen("listing-first")

    def test_warmup_driver_before_the_new_boots_listing(self):
        self._restart_then_reopen("registration-first")

    def test_revive_before_the_new_boots_listing(self):
        self._restart_then_reopen("revive")

    def _restart_then_reopen(self, order):
        request = self.launched("worker", status="idle")
        agent = self.agent_of(request)
        agent_id = agent["id"]
        assert self.config.paseo_runtime is not None
        home = self.config.paseo_runtime.home
        expected = read_role_agents(home)[agent_id]
        publisher = _HostPublisher(self.root, home)
        self.addCleanup(publisher.close)
        publication = home / "agents-remember/host-activity.json"
        publisher.step(boot="B1", agents=[{"id": agent_id, "status": "idle"}])
        publisher.step(event="agent.closed", id=agent_id)
        assert json.loads(publication.read_text())["agents"][agent_id]["state"] == "ended"
        agent["status"] = "closed"  # The persisted session after the old host shut down.
        publisher.step(boot="B2")
        assert json.loads(publication.read_text()) == {
            "bootId": "B2",
            "complete": False,
            "agents": {},
            "ignoredEvents": 0,
        }
        if order == "listing-first":
            publisher.step(agents=[{"id": agent_id, "status": "closed"}])
        resume = self.runtime._agent_resume
        resumes = []

        def listing_during_resume(payload):
            resumes.append(payload["agentId"])
            # Registration has completed; the host has not yet loaded the session.
            assert agent_id in read_role_agents(home)
            if order != "listing-first":
                publisher.step(agents=[{"id": agent_id, "status": "closed"}])
            assert json.loads(publication.read_text())["agents"][agent_id] == {"state": "ended"}
            # A different launch races the in-flight resume and its stale close.
            publish_role_agent(home, "concurrent-launch", expected)
            assert agent_id in read_role_agents(home)
            return resume(payload)

        with patch.object(self.runtime, "_agent_resume", side_effect=listing_during_resume):
            if order == "revive":
                assert self.revive(request)[0] == 200
            else:
                self._drive_restart_warmup(request, agent_id)
        assert resumes == [agent_id]
        assert agent["status"] == "idle"
        publish_role_agent(home, "after-warmup-launch", expected)
        assert agent_id in read_role_agents(home)
        now = datetime.now(UTC)
        # A message to the open session starts a turn without another resume.
        publisher.step(event="agent.turn_started", id=agent_id, at=now.isoformat())
        published = json.loads(publication.read_text())
        assert published["ignoredEvents"] == 0
        assert published["agents"][agent_id]["state"] == "in-turn"
        intake = HostActivityInput(
            lambda _: {"publication": {"bootId": "B2"}}, supervisor_armed=lambda: True
        )
        snapshot = intake.read(self.config)
        assert snapshot.known
        state = task_state("inProgress", [row.activity for row in snapshot.agents], host_known=True)
        assert state.activity == "works"
        self._assert_end_reclaimers(publisher, home, agent_id, expected)

    @staticmethod
    def _assert_end_reclaimers(publisher, home, agent_id, expected):
        # Later native ends reclaim the registration, even after successful reopening.
        now = datetime.now(UTC)
        for event in ("agent.closed", "agent.archived"):
            publisher.step(event=event, id=agent_id, at=(now + timedelta(seconds=1)).isoformat())
            publish_role_agent(home, "next-launch", expected)
            assert agent_id not in read_role_agents(home)
            publish_role_agent(home, agent_id, expected)
            now += timedelta(seconds=2)
        publisher.step(boot="B3", agents=[{"id": agent_id, "status": "closed"}])
        publish_role_agent(home, "new-boot-launch", expected)
        assert agent_id not in read_role_agents(home)

    def _drive_restart_warmup(self, request, agent_id):
        selection = RoleSelection.model_validate(self.receipt(request)["selection"])
        tracker = WarmupTracker()
        live = LiveAgents(
            agent_ids=frozenset({agent_id}),
            server_id="B2",
            start_identity=HostStartIdentity(4242, "777", "test-os-boot"),
        )
        runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                launcher_catalog_prepare=None,
                config=self.config,
                background_ledger=BackgroundLedger(),
            ),
        )
        with (
            patch.object(paseo_warmup, "read_live_agents", return_value=live),
            patch.object(paseo_warmup, "tree_selections", return_value=[selection]),
            patch.object(paseo_warmup, "bridge_call", side_effect=self.runtime),
            patch.object(serving, "_warmup_tracker", tracker),
            patch.object(serving, "read_available_memory", return_value=64 * 1024**3),
        ):
            admitted = paseo_warmup.check_host_once(self.config, tracker=tracker)
            assert admitted is not None, tracker.intake_detail
            run, claim = admitted
            assert (
                run.role_receipts[agent_id]["report"]["path"]
                == self.receipt(request)["report"]["path"]
            )
            asyncio.run(serving._drive_warmup_run(runtime, run, claim, {}))
            assert run.loaded_ids == [agent_id]
            assert not run.failed
            assert claim.lease.closed
            assert tracker.run is None
        assert self.commands() == ["agent-resume"]
