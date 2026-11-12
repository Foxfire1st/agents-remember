"""The agent-eval recorder plugin records a native seat's helper rows and its ended turn.

The plugin runs in Node against the pinned Paseo hook shape: ``agent.turn.after`` carries an
empty timeline, so the recorder reads the ended turn through ``agents.ref(id).timeline.refetch``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.host_events import merge_host_events
from agent_evals.adapters.models import NativeCapture
from agent_evals.adapters.native_trials import host_window
from test_agent_eval_adapters import _TrialSources

#: The Node driver: it loads the recorder plugin, feeds it the pinned host's hook shapes and a
#: refetch reply, and keeps the observed window open.
RECORDER_DRIVER = r"""
const [sourcePath, homePath, projectPath] = process.argv.slice(2)
const { default: contribute } = await import(sourcePath)
process.env.PASEO_HOME = homePath
let opening, ending, listener, ready
const gate = new Promise(resolve => { ready = resolve })
const server = {
  addHook(name, callback) {
    if (name === 'agent.open.before') opening = callback
    if (name === 'agent.turn.after') ending = callback
    return () => {}
  }
}
const cleanup = contribute(server)
const pending = opening({ request: { agentId: 'owner', cwd: projectPath } }, { paseo: {
  observeEvents(names, options) {
    if (names.join(',') !== 'agent.provider_subagents.update' || options.notifications !== false) throw Error('wrong native subscription')
    return { ready: gate, subscribe(value) { listener = value }, release: async () => {} }
  }
}})
ready()
await pending
listener.snapshot()
const native = { type: 'agent.provider_subagents.update', payload: { kind: 'upsert', subagent: {
  id: 'helper', parentAgentId: 'owner', status: 'running', createdAt: new Date().toISOString()
}} }
listener.update(native)
listener.update({ type: native.type, payload: { kind: 'remove', parentAgentId: 'owner', subagentId: 'helper' } })
// Paseo 0.15.4's hook shape: an empty timeline; the recorder reads the turn through refetch.
const refetched = { entries: [
  { item: { type: 'user_message', text: 'Begin' }, turnId: 'actual-turn' },
  { item: { type: 'assistant_message', text: 'Complete' }, turnId: 'actual-turn' },
] }
await ending({ agent: { id: 'owner' }, turnId: 'actual-turn', outcome: { kind: 'completed' }, timeline: [] }, { paseo: {
  agents: { ref(id) {
    if (id !== 'owner') throw Error('wrong agent')
    return { timeline: { async refetch(options) {
      if (options.direction !== 'tail' || options.limit !== 0) throw Error('not the whole held timeline')
      return refetched
    } } }
  } }
}})
// Keep this as a live observed window: cleanup would correctly mark it stopped.
"""


def test_native_recorder_records_the_host_turn_and_its_window(tmp_path: Path) -> None:
    """The recorder plugin, run in Node against the pinned host's hook shape, writes the helper
    rows and the ended turn read through refetch; a recorded subscription gap opens the window."""

    node = shutil.which("node")
    assert node is not None
    source = (
        Path(__file__).resolve().parents[2]
        / "scripts/agent_evals/adapters/recorder_plugin/index.server.ts"
    )
    home = tmp_path / "native-recorder"
    project = home / "project"
    project.mkdir(parents=True)
    output = home / "agent-eval"
    output.mkdir()
    (output / "recorder-settings.json").write_text(json.dumps({"projectRoot": str(project)}))
    done = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--input-type=module",
            "-",
            source.as_uri(),
            str(home),
            str(project),
        ],
        input=RECORDER_DRIVER,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    rows = [json.loads(line) for line in (output / "host-events.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and rows[0]["native"]["payload"]["subagent"]["id"] == "helper"
    sources = _TrialSources(
        project, home, home / "settings", "build", {}, (output / "host-events.jsonl",)
    )
    observed = host_window(sources, "owner")
    assert observed is not None and observed.complete
    native = NativeCapture("codex", "actual-native-session", (), (), (), ())
    merged = merge_host_events(
        native, owner_id="owner", host_event_paths=sources.host_event_paths, window=observed
    )
    assert not merged.capture.unknowns
    assert merged.capture.raw_records == tuple(rows)
    assert merged.capture.events[-1].payload["turnId"] == "actual-turn"
    assert merged.capture.events[-1].payload["timeline"] == [
        {"type": "user_message", "text": "Begin", "turnId": "actual-turn"},
        {"type": "assistant_message", "text": "Complete", "turnId": "actual-turn"},
    ]
    marker = output / "owner.window.json"
    value = json.loads(marker.read_text())
    value["errors"] = ["actual subscription gap"]
    marker.write_text(json.dumps(value))
    gap = host_window(sources, "owner")
    assert gap is not None and not gap.complete
