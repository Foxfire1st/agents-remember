"""Retained setup guards and real benchmark preparation without installed skill copies.

Each test here stands on a defect a reviewer found statically in the round-2 candidate: a sandbox
builder reading a key its producer does not return, an enforcing qualification rail calling a script
this leaf deleted, the setup skills still assigning onboarding, and a same-request retry preparing a
cold root twice. The first three are checked as structure, the fourth as behaviour in
``test_mik_r196_l188_preparation_order.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkCase,
    BenchmarkPreparation,
)
from agents_remember.benchmarks.runner_modules.workspace import prepare_case
from agents_remember.kernel.primitives.runtime_config import config_from_mapping
from agents_remember.mcp.registration.capsule_serving import register_capsule_and_skill_tools
from mcp.server.fastmcp import FastMCP

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPOSITORY_ROOT / "mcp/certification-profile-v1.json"


def test_the_sandbox_builder_reads_the_key_its_producer_returns() -> None:
    """The pair moves together: the settings owner names the key, the builder reads that name."""

    producer = (REPOSITORY_ROOT / "scripts/pnt_sandbox/settings_expectation.py").read_text(
        encoding="utf-8"
    )
    builder = (REPOSITORY_ROOT / "scripts/pnt_sandbox/builder.py").read_text(encoding="utf-8")
    assert '"preparedSettingsPath"' in producer, "the producer's own key is preparedSettingsPath"
    assert "renderedSettingsPath" not in builder, (
        "the builder reads a key the producer does not return; the real build raises KeyError"
    )
    assert "preparedSettingsPath" in builder


def test_every_qualification_rail_runs_a_script_the_repository_holds() -> None:
    """An enforcing rail that names a deleted script cannot run, and must not be certified."""

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    missing: list[str] = []
    for rail in profile["rails"]:
        for argument in rail.get("execution", {}).get("command", []):
            names_a_script = re.fullmatch(r"scripts/[\w./-]+\.py", str(argument))
            if names_a_script and not (REPOSITORY_ROOT / argument).is_file():
                missing.append(f"{rail['identity']['railId']} -> {argument}")
    assert missing == [], f"rails run scripts the repository does not hold: {missing}"


def test_the_setup_skills_assign_no_onboarding() -> None:
    """MIK-R196 rules 4-5: setup initialises the memory root; the foundation is a regular task."""

    canonical = REPOSITORY_ROOT / "skills/c-13-install-and-onboard/SKILL.md"
    packaged = (
        REPOSITORY_ROOT
        / "mcp/src/agents_remember/package_data/runtime/skills/c-13-install-and-onboard/SKILL.md"
    )
    for path in (canonical, packaged):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPOSITORY_ROOT)
        assert "Setup authors no onboarding" in text, relative
        assert "Scaffold onboarding" not in text, (
            f"{relative} still hands onboarding authorship to setup"
        )
        assert "citation fixer here" in text, f"{relative} must keep the fixer out of setup"


def test_benchmark_preparation_discovers_package_skills_without_installing_copies(
    tmp_path: Path,
) -> None:
    """Real local clones, scaffold and registration; resource reads use the shipped package."""

    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(origin), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    (origin / "fixture.txt").write_text("pinned fixture\n", encoding="utf-8")
    git("add", "fixture.txt")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-qm",
        "fixture",
    )
    commit = git("rev-parse", "HEAD")
    benchmarks = tmp_path / "benchmarks"
    shutil.copytree(REPOSITORY_ROOT / "benchmarks/templates", benchmarks / "templates")
    case = BenchmarkCase(
        path=benchmarks / "case.json",
        data={
            "id": "fixture",
            "repository": {"name": "fixture", "url": str(origin), "commit": commit},
            "memoryRepository": {"name": "ar-fixture", "url": str(origin), "commit": commit},
            "workspace": {"fixturePath": "workspaces/fixture"},
        },
    )
    preparation = BenchmarkPreparation(
        benchmarks_root=benchmarks, dry_run=False, allowed_provider_ids=()
    )
    # The same operation must remain usable on a previously prepared case, too.
    prepare_case(preparation, case)
    prepare_case(preparation, case)
    root = benchmarks / "workspaces/fixture"
    memory = root / "with-memory"
    for path in (
        root / "source-only/repos/fixture",
        memory / "repos/fixture",
        memory / "ar-coordination/memory-repos/ar-fixture",
    ):
        assert (path / "fixture.txt").read_text(encoding="utf-8") == "pinned fixture\n"
        actual = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual == commit
    assert (memory / "ar-coordination/AGENTS.md").is_file()
    assert not (root / "source-only/.codex").exists()
    assert not list(root.rglob("skills")), "preparation must not install any skill tree"
    registration = tomllib.loads((memory / ".codex" / "config.toml").read_text(encoding="utf-8"))
    server_entry = registration["mcp_servers"]["agents_remember_benchmark"]
    assert server_entry["args"][:3] == ["-m", "agents_remember.mcp", "--config"]
    settings_path = Path(server_entry["args"][3])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    config = config_from_mapping(settings, settings_path)
    assert config.repositories["fixture"].path == memory / "repos/fixture"
    assert config.coordination_root == memory / "ar-coordination"
    assert config.providers == {}
    server = FastMCP("benchmark package resources")
    register_capsule_and_skill_tools(server, config)

    async def read_resources() -> None:
        listed = {str(resource.uri) for resource in await server.list_resources()}
        assert "skill://index.json" in listed
        index_contents = list(await server.read_resource("skill://index.json"))
        index = json.loads(str(index_contents[0].content))
        assert "c-13-install-and-onboard" in {row["name"] for row in index["skills"]}
        for relative in (
            "c-13-install-and-onboard/SKILL.md",
            "l-01-agent-lifecycles/roles/worker.md",
        ):
            uri = f"skill://agents-remember/skills/{relative}"
            assert uri in listed
            contents = list(await server.read_resource(uri))
            packaged = (
                REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills" / relative
            )
            assert contents[0].content == packaged.read_text(encoding="utf-8")

    asyncio.run(read_resources())
