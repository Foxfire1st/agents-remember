"""Real-runtime cases for the eve capsule/worktree binding: the shipped TS verifier, executed.

The runtime half of this seam is TypeScript inside the pinned eve application, and no Python case
can observe whether it refuses a foreign binding, a tampered carrier or a sibling worktree. These
cases therefore execute the *shipped* modules with the same Node the runtime launches under, in a
real git fixture world, and read the refusal code each defect produces.

The modules under test are pure Node code — no eve import, no bundler — so this is a direct
observation of the shipped file rather than of a copy or a re-implementation. A loader hook supplies
the one thing Node's own type stripping does not: the ``./x.js`` specifier convention the eve
compiler uses for authored TypeScript.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.serving.eve_runtime_launch import (
    BINDING_REF_ENV,
    CAPSULE_DIGEST_ENV,
    CAPSULE_PATH_ENV,
    WORKSPACE_ROOT_ENV,
    resolve_node_executable,
)
from eve_capsule_test_support import FixtureWorld, build_world

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIB = REPOSITORY_ROOT / "eve_runtime" / "agent" / "lib"

pytestmark = pytest.mark.integration

_HOOK = """export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (error) {
    if (specifier.endsWith(".js")) {
      return next(`${specifier.slice(0, -3)}.ts`, context);
    }
    throw error;
  }
}
"""

_REGISTER = """import { register } from "node:module";
import { pathToFileURL } from "node:url";
register(new URL("./hook.mjs", import.meta.url), pathToFileURL("./"));
"""


@dataclass(frozen=True)
class CarrierDefect:
    """One mutated carrier plus the refusal code the runtime must produce for it."""

    label: str
    mutate: Any
    expected: str


@dataclass(frozen=True)
class LaunchDefect:
    """One degenerated launch binding plus the refusal code the runtime must produce for it."""

    label: str
    overrides: dict[str, str | None]
    expected: str


@dataclass(frozen=True)
class RuntimeProbe:
    """One executed probe: the JSON it printed, or the refusal code it exited with."""

    payload: dict[str, Any]
    stderr: str


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> FixtureWorld:
    return build_world(tmp_path_factory.mktemp("l7-runtime-world"))


@pytest.fixture(scope="module")
def node() -> str:
    """The same interpreter the runtime launches under, or a skip when this host has none."""

    try:
        return resolve_node_executable()
    except HarnessControlError as error:  # pragma: no cover - environment dependent
        pytest.skip(f"no Node >= 24 available for the runtime verifier: {error}")


def run_probe(node: str, source: str, *, env: dict[str, str], tmp_path: Path) -> RuntimeProbe:
    """Run one probe against the shipped modules and return what it printed or refused.

    The probe imports the authored modules by their real paths and carries the launch environment as
    its own environment, which is exactly how the runtime reads its binding. Node's type stripping
    needs one thing the eve compiler supplies and Node does not: a resolver that finds ``./x.ts`` for
    the ``./x.js`` specifier the authored convention writes. The hook is six lines and is written
    beside the probe so the shipped file itself is what runs.
    """

    script = tmp_path / "probe.mjs"
    script.write_text(source, encoding="utf-8")
    (tmp_path / "hook.mjs").write_text(_HOOK, encoding="utf-8")
    register = tmp_path / "register.mjs"
    register.write_text(_REGISTER, encoding="utf-8")
    completed = subprocess.run(
        [node, "--no-warnings", "--import", str(register), str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**env, "PATH": "/usr/bin:/bin"},
        timeout=180,
    )
    if completed.returncode != 0:
        return RuntimeProbe(
            payload={"refused": _refusal_code(completed.stderr)}, stderr=completed.stderr
        )
    return RuntimeProbe(payload=json.loads(completed.stdout.strip().splitlines()[-1]), stderr="")


def _refusal_code(stderr: str) -> str:
    for line in reversed(stderr.strip().splitlines()):
        if "ArCapsuleError" in line or line.startswith("Error:"):
            return line.strip()
    return stderr.strip().splitlines()[-1] if stderr.strip() else "<no output>"


def _verifier_probe(imports: str, body: str) -> str:
    return f"{imports}\n{body}\n"


VERIFY_IMPORTS = f"""import {{ loadVerifiedCapsule }} from "{LIB / "capsule.ts"}";
import {{ verifyAdmittedWorkspace }} from "{LIB / "git-workspace.ts"}";
import {{ resolve }} from "node:path";"""

ACCEPT_BODY = """const capsule = loadVerifiedCapsule(process.env);
const head = verifyAdmittedWorkspace(capsule);
console.log(JSON.stringify({
  carrierDigest: capsule.carrierDigest,
  bindingRef: capsule.identity.bindingRef,
  role: capsule.identity.role,
  workspaceRoot: resolve(capsule.workspace.root),
  workBranch: capsule.workspace.workBranch,
  head,
  instructionText: capsule.instructionText,
  instructionBlocks: capsule.instructions.length,
  taskContext: capsule.taskContextMarkdown,
  scopes: capsule.writeScopes.map((scope) => [scope.kind, resolve(scope.root, scope.path)]),
}));"""

WRITE_BODY = """const capsule = loadVerifiedCapsule(process.env);
const { admitWritePath, admitWorkspaceRead } = await import("__CAPSULE__");
const outcome = {};
for (const [label, path] of Object.entries({
  workspace: "mcp/src/agents_remember/models/scratch.json",
  report: "__REPORT__/note.md",
  sibling: "__SIBLING__/README.md",
  memory: "__MEMORY__/memory.md",
  escape: "../outside.json",
})) {
  try {
    outcome[label] = ["admitted", admitWritePath(capsule, path)];
  } catch (error) {
    outcome[label] = ["refused", error.code];
  }
}
try {
  outcome.outside_read = ["admitted", admitWorkspaceRead(capsule, "__SIBLING__/README.md")];
} catch (error) {
  outcome.outside_read = ["refused", error.code];
}
console.log(JSON.stringify(outcome));"""


def test_runtime_verifier_accepts_the_admitted_carrier_and_workspace(
    world: FixtureWorld, node: str, tmp_path: Path
) -> None:
    launch = world.bind()
    probe = run_probe(
        node, _verifier_probe(VERIFY_IMPORTS, ACCEPT_BODY), env=dict(launch.env), tmp_path=tmp_path
    )
    assert probe.payload.get("carrierDigest") == launch.digest, probe.stderr
    assert probe.payload["bindingRef"] == launch.carrier.identity.binding_ref
    assert probe.payload["role"] == "worker"
    assert probe.payload["workspaceRoot"] == str(world.code_worktree.resolve())
    assert probe.payload["workBranch"] == world.work_branch
    assert probe.payload["head"] == {"kind": "branch", "value": f"refs/heads/{world.work_branch}"}
    assert probe.payload["instructionText"] == launch.carrier.instruction_text
    assert probe.payload["instructionBlocks"] == len(launch.carrier.instructions)
    assert probe.payload["taskContext"] == launch.carrier.task_context_markdown
    assert probe.payload["scopes"] == [
        ["workspace", str(world.code_worktree.resolve())],
        ["absolute", str(world.report_root.resolve())],
    ]


def _carrier_with(
    world: FixtureWorld, tmp_path: Path, mutate: Any
) -> tuple[Path, str, dict[str, str]]:
    """A copy of the admitted carrier, mutated, with a digest that matches the mutated bytes."""

    launch = world.bind(carrier_directory=tmp_path / "base")
    payload = json.loads(launch.carrier_path.read_text(encoding="utf-8"))
    mutate(payload)
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = tmp_path / "capsule-carrier.json"
    path.write_bytes(body)
    env = {
        BINDING_REF_ENV: launch.carrier.identity.binding_ref,
        CAPSULE_PATH_ENV: str(path),
        CAPSULE_DIGEST_ENV: f"sha256:{hashlib.sha256(body).hexdigest()}",
        WORKSPACE_ROOT_ENV: str(world.code_worktree.resolve()),
    }
    return path, env[CAPSULE_DIGEST_ENV], env


def _drop_instructions(payload: dict[str, Any]) -> None:
    payload["instructions"] = []
    payload["instructionIdentities"] = []
    payload["instructionDigests"] = []


def _mismatch_instruction_counts(payload: dict[str, Any]) -> None:
    payload["instructionDigests"] = payload["instructionDigests"][:-1]


def _foreign_binding(payload: dict[str, Any]) -> None:
    payload["identity"]["bindingRef"] = "ar-binding:curator:another-task.json:curation"


def _foreign_workspace(payload: dict[str, Any]) -> None:
    """A carrier for another worktree: its own scope agrees with it, this launch's does not."""

    payload["workspace"]["root"] = "/tmp/not-the-admitted-worktree"
    for scope in payload["writeScopes"]:
        if scope["kind"] == "workspace":
            scope["root"] = "/tmp/not-the-admitted-worktree"


def _foreign_branch(payload: dict[str, Any]) -> None:
    payload["workspace"]["workBranch"] = "ar/not-this-branch"


def _second_workspace_scope(payload: dict[str, Any]) -> None:
    payload["writeScopes"].append(dict(payload["writeScopes"][0]))


CARRIER_DEFECTS = (
    CarrierDefect("empty instructions", _drop_instructions, "carrier-empty-instructions"),
    CarrierDefect(
        "instruction counts", _mismatch_instruction_counts, "carrier-instruction-mismatch"
    ),
    CarrierDefect("two workspace scopes", _second_workspace_scope, "carrier-scope-mismatch"),
    CarrierDefect("foreign binding", _foreign_binding, "carrier-binding-mismatch"),
    CarrierDefect("foreign workspace", _foreign_workspace, "carrier-workspace-mismatch"),
    CarrierDefect("foreign branch", _foreign_branch, "workspace-branch-mismatch"),
)


@pytest.mark.parametrize("defect", CARRIER_DEFECTS, ids=lambda item: item.label.replace(" ", "-"))
def test_runtime_verifier_refuses_each_declared_defect(
    world: FixtureWorld, node: str, tmp_path: Path, defect: CarrierDefect
) -> None:
    _, _, env = _carrier_with(world, tmp_path / defect.label.replace(" ", "-"), defect.mutate)
    probe = run_probe(
        node, _verifier_probe(VERIFY_IMPORTS, ACCEPT_BODY), env=env, tmp_path=tmp_path
    )
    assert probe.payload.get("refused") is not None, f"{defect.label} was accepted: {probe.payload}"
    assert defect.expected in probe.stderr, f"{defect.label}: {probe.stderr}"


LAUNCH_DEFECTS = (
    LaunchDefect("unbound launch", {"capsule": None}, "unbound-launch"),
    LaunchDefect(
        "missing carrier", {"capsule_path": "/tmp/absent-carrier.json"}, "carrier-missing"
    ),
    LaunchDefect("partial binding", {"digest": None}, "partial-binding"),
    LaunchDefect("digest mismatch", {"digest": "sha256:" + "0" * 64}, "carrier-digest-mismatch"),
)


@pytest.mark.parametrize("defect", LAUNCH_DEFECTS, ids=lambda item: item.label.replace(" ", "-"))
def test_runtime_verifier_refuses_an_unusable_launch_binding(
    world: FixtureWorld, node: str, tmp_path: Path, defect: LaunchDefect
) -> None:
    launch = world.bind(carrier_directory=tmp_path / "base")
    env: dict[str, str] = dict(launch.env)
    env_overrides = defect.overrides
    label = defect.label
    expected = defect.expected
    if "capsule" in env_overrides:
        for name in (BINDING_REF_ENV, CAPSULE_PATH_ENV, CAPSULE_DIGEST_ENV, WORKSPACE_ROOT_ENV):
            env.pop(name, None)
    else:
        for key, value in env_overrides.items():
            if key == "capsule":
                continue
            name = {"capsule_path": CAPSULE_PATH_ENV, "digest": CAPSULE_DIGEST_ENV}[key]
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
    probe = run_probe(
        node, _verifier_probe(VERIFY_IMPORTS, ACCEPT_BODY), env=env, tmp_path=tmp_path
    )
    assert probe.payload.get("refused") is not None, f"{label} was accepted: {probe.payload}"
    assert expected in probe.stderr, f"{label}: {probe.stderr}"


def test_runtime_write_admission_refuses_a_sibling_worktree_and_the_memory_surface(
    world: FixtureWorld, node: str, tmp_path: Path
) -> None:
    launch = world.bind(carrier_directory=tmp_path / "base")
    body = (
        WRITE_BODY.replace("__CAPSULE__", str(LIB / "capsule.ts"))
        .replace("__REPORT__", str(world.report_root.resolve()))
        .replace("__SIBLING__", str(world.sibling_worktree.resolve()))
        .replace("__MEMORY__", str(world.memory_worktree.resolve()))
    )
    probe = run_probe(
        node,
        _verifier_probe(VERIFY_IMPORTS, body),
        env=dict(launch.env),
        tmp_path=tmp_path,
    )
    outcome = probe.payload
    assert outcome["workspace"][0] == "admitted", probe.stderr
    assert outcome["report"][0] == "admitted", probe.stderr
    assert outcome["sibling"] == ["refused", "write-scope-refused"], probe.stderr
    assert outcome["memory"] == ["refused", "write-scope-refused"], probe.stderr
    assert outcome["escape"] == ["refused", "write-scope-refused"], probe.stderr
    assert outcome["outside_read"] == ["refused", "path-escape"], probe.stderr
