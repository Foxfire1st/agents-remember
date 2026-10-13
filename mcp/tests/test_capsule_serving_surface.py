"""Capsule shipped-surface, protocol-exchange, and guard cases.

The shipped corpus and its manifest, the real client/server exchange over the
installed SDK, and the guards the baseline review found unpinned (F-L4-08) with
the nesting rule. Fixture worlds, the frozen vocabularies and the first two
behaviour blocks live in ``test_capsule_serving.py`` and are imported here, so
every definition still lives exactly once.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.skill_resources import (
    build_skill_catalog,
    read_served_file,
)
from agents_remember.application.skill_resources.operation import (
    skill_catalog_registry,
)
from agents_remember.mcp.registration.capsule_serving import (
    declare_skills_extension,
    declared_extensions,
    index_resource,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES as _ALL_ROLE_NAMES
from agents_remember.models.skill_resources import UnreadableSkill
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from pydantic import AnyUrl, BaseModel
from test_capsule_serving import (
    ALPHA_TASK,
    DEV_SOURCE,
    EXTENSION_ID,
    INDEX_URI,
    LIFECYCLE_SKILLS,
    MASTER_DIR,
    REPOSITORY,
    SEP_ENTRY_KEYS,
    SEP_RESOURCE_KEYS,
    SHIPPED_MANIFEST,
    SHIPPED_SKILLS,
    SKILL_ROOT_FILE,
    SKILLS_GET_METHOD,
    SKILLS_LIST_METHOD,
    ContractSpec,
    World,
    _contract_text,
    _entry_named,
    _frontmatter_of,
    _sha256_bytes,
    world,  # noqa: F401 - ``world`` is the fixture
)

from mcp import ClientSession, StdioServerParameters

# ---------------------------------------------------------------------------
# CAPS-R04 behaviour 2 and 6: the shipped surface and the real protocol exchange
# ---------------------------------------------------------------------------


def test_the_shipped_corpus_parses_and_every_served_skill_has_a_root_revision() -> None:
    """The real corpus: every skill has frontmatter, a body digest and a readable URI."""

    catalog = skill_catalog_registry()
    assert catalog.unreadable == ()
    assert len(catalog.entries) >= 14
    for entry in catalog.entries:
        root = entry.root
        assert root is not None, entry.name
        assert root.revision.startswith("sha256:")
        assert root.byte_length > 0
        assert entry.description
        assert entry.uri.endswith("/SKILL.md")
    # Every entry's frontmatter is compared against the file on disk, parsed here by
    # this case rather than read back from the entry it is checking.
    for entry in catalog.entries:
        on_disk = (Path(catalog.source_root) / entry.skill_path / SKILL_ROOT_FILE).read_text(
            encoding="utf-8"
        )
        declared = _frontmatter_of(on_disk)
        assert entry.frontmatter == declared, entry.name
        assert entry.name == declared["name"]
        assert entry.description == declared["description"]
        # `resources` is complete: every file of the skill, each exactly once.
        uris = [record.uri for record in entry.files]
        assert len(uris) == len(set(uris))
        assert entry.uri in uris
        assert all(record.revision.startswith("sha256:") for record in entry.files)


def test_the_composition_manifest_declares_the_skill_that_is_served() -> None:
    """The two planes agree on the same skill identity and origin."""

    parsed = parse_composition_manifest(SHIPPED_MANIFEST.read_bytes())
    catalog = skill_catalog_registry()
    assert catalog.origin == "agents-remember/skills"
    for name, declared in parsed.skills.items():
        entry = catalog.entry(f"agents-remember/skills#{name}")
        assert entry is not None, name
        assert declared.uri == entry.uri.rsplit("/SKILL.md", 1)[0]
        assert declared.source == "SKILL.md"
    assert set(parsed.skills).issubset(set(catalog.names()))


def test_this_servers_own_index_resource_keeps_the_agent_skills_discovery_shape() -> None:
    """What this case actually pins: *this server's* index resource, not the extension.

    ``skill://index.json`` is served as an ordinary resource in the Agent Skills
    well-known-discovery shape. That shape is not SEP-2640's enumeration result — the
    extension enumerates through the ``skills/list`` method, whose entries carry
    verbatim frontmatter and per-file digests — so the case is named for the artefact it
    asserts and the extension's own shape is pinned separately.
    """

    catalog = skill_catalog_registry()
    document = catalog.index_document()
    assert set(document) == {"$schema", "skills"}
    rows = document["skills"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        assert row["type"] == "skill-md"
        assert row["name"] and row["description"] and row["url"]
        assert str(row["url"]).startswith("skill://agents-remember/skills/")
        meta = row["_meta"]
        assert isinstance(meta, dict)
        assert meta["io.modelcontextprotocol.skills/origin"] == "agents-remember/skills"
        assert str(meta["io.modelcontextprotocol.skills/revision"]).startswith("sha256:")
    # The two shapes are different artefacts and must not be confused: this server's
    # index row carries `type`/`description`/`url`, the extension's entry carries
    # `uri`/`frontmatter`/`resources`.
    entry = catalog.entry_documents()[0]
    assert set(entry) == SEP_ENTRY_KEYS
    assert set(rows[0]) != set(entry)


def test_every_sep_2640_entry_is_complete_and_carries_verbatim_frontmatter() -> None:
    """The extension's own entry shape, checked against the tree rather than itself."""

    catalog = skill_catalog_registry()
    entries = catalog.entry_documents()
    assert len(entries) == len(catalog.entries)
    for entry in entries:
        assert set(entry) == SEP_ENTRY_KEYS
        uri = entry["uri"]
        assert isinstance(uri, str) and uri.endswith(f"/{SKILL_ROOT_FILE}")
        frontmatter = entry["frontmatter"]
        assert isinstance(frontmatter, dict)
        # Independent side: the frontmatter parsed from the file this URI addresses.
        relative = uri.split("agents-remember/skills/", 1)[1]
        on_disk = (SHIPPED_SKILLS / relative).read_text(encoding="utf-8")
        assert frontmatter == _frontmatter_of(on_disk)
        assert uri.split("/")[-2] == frontmatter["name"]
        resources = entry["resources"]
        assert isinstance(resources, list) and resources
        seen: set[str] = set()
        for record in resources:
            assert isinstance(record, dict)
            assert set(record) == SEP_RESOURCE_KEYS
            assert str(record["digest"]).startswith("sha256:")
            assert isinstance(record["size"], int) and record["size"] > 0
            record_uri = str(record["uri"])
            assert record_uri not in seen
            seen.add(record_uri)
            served = read_served_file(catalog, record_uri)
            assert len(served.content) == record["size"]
            assert _sha256_bytes(served.content) == record["digest"]
        assert uri in seen


def _live_settings(root: Path) -> Path:
    path = root / "mcp-settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": str(root / "coord"),
                "workspaceRoot": str(root / "workspace"),
                "repositories": {REPOSITORY: {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def _server_parameters(tmp_path: Path) -> StdioServerParameters:
    """The documented argv, in a disposable coordination root, with this worktree's source."""

    settings = _live_settings(tmp_path)
    (tmp_path / "coord").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings)],
        env={**os.environ, "PYTHONPATH": str(DEV_SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
    )


async def _drive_exchange(parameters: StdioServerParameters) -> dict[str, Any]:
    """One client session against the real server: everything the client observes."""

    observed: dict[str, Any] = {}
    with open(os.devnull, "w") as devnull:
        async with stdio_client(parameters, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                observed["capabilities"] = initialized.capabilities.model_dump(
                    mode="json", exclude_none=True
                )
                listed = await session.list_resources()
                observed["resources"] = [str(item.uri) for item in listed.resources]
                observed["resourceNames"] = {str(item.uri): item.name for item in listed.resources}
                observed["resourceMeta"] = {
                    str(item.uri): item.meta or {} for item in listed.resources
                }
                index = await session.read_resource(AnyUrl(INDEX_URI))
                observed["index"] = index.contents[0].text  # type: ignore[union-attr]
                observed["indexMime"] = index.contents[0].mimeType
                for label, uri in (("body", _skill_uri()), ("guide", _guide_uri())):
                    content = (await session.read_resource(AnyUrl(uri))).contents[0]
                    observed[label] = content.text  # type: ignore[union-attr]
                    observed[f"{label}Mime"] = content.mimeType
                    observed[f"{label}Meta"] = content.meta or {}
                try:
                    await session.read_resource(
                        AnyUrl("skill://agents-remember/skills/absent/SKILL.md")
                    )
                except Exception as error:
                    observed["absent"] = type(error).__name__
                    observed["absentMessage"] = str(error)
                tools = await session.list_tools()
                observed["tools"] = [tool.name for tool in tools.tools]
                observed["extension"] = await _drive_extension_methods(session)
    return observed


async def _drive_extension_methods(session: ClientSession) -> dict[str, Any]:
    """Issue the extension's own methods over the transport the declaration rode in on.

    The SDK's client has no skills affordance, so the two requests are sent as the
    protocol messages a conforming client sends. Nothing here can be satisfied by a
    server that only declares the extension: each call either reaches a handler or
    comes back as the SDK's own "Invalid request parameters" error.
    """

    class _ExtensionRequest(BaseModel):
        method: str
        params: dict[str, Any] = {}

    class _ExtensionResult(BaseModel):
        model_config = {"extra": "allow"}

    async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await session.send_request(
                cast(Any, _ExtensionRequest(method=method, params=params)), _ExtensionResult
            )
            return {"ok": True, "result": result.model_dump(mode="json", exclude_none=True)}
        except Exception as error:
            return {"ok": False, "error": type(error).__name__, "message": str(error)}

    return {
        "list": await call(SKILLS_LIST_METHOD, {}),
        "get": await call(
            SKILLS_GET_METHOD,
            {"uri": "skill://agents-remember/skills/l-01-agent-lifecycles/SKILL.md"},
        ),
        "getAbsent": await call(
            SKILLS_GET_METHOD, {"uri": "skill://agents-remember/skills/absent/SKILL.md"}
        ),
        "unregistered": await call("skills/directory/read", {"uri": "skill://x"}),
    }


def _served_entry() -> Any:
    entry = skill_catalog_registry().entry("agents-remember/skills#l-01-agent-lifecycles")
    assert entry is not None
    return entry


def _skill_uri() -> str:
    return "skill://" + _served_entry().uri.split("skill://", 1)[1]


def _guide_uri() -> str:
    return f"{_skill_uri().rsplit('/SKILL.md', 1)[0]}/roles/worker.md"


@pytest.mark.integration
def test_a_real_client_and_server_exchange_over_the_installed_sdk(tmp_path: Path) -> None:
    """The real entry point over stdio, driven by the installed SDK's own client.

    This is the conformance exchange: the server is started as its own process with
    the documented argv, and every observation below is what the client actually
    received -- the negotiated capabilities, the resource list, an index read, one
    selected skill read, a supporting-file read, and the not-found refusal. Nothing
    here is a unit-level stub, and the request that reads a file is issued by a
    separate implementation from the one that answers it.
    """

    observed = asyncio.run(_drive_exchange(_server_parameters(tmp_path)))

    # 1. The declared extension reached the wire, and the resources primitive did too.
    assert observed["capabilities"]["extensions"] == {EXTENSION_ID: {}}
    assert "resources" in observed["capabilities"]
    # 2. Discovery enumerates resources without their bodies.
    assert INDEX_URI in observed["resources"]
    assert _skill_uri() in observed["resources"]
    assert observed["resourceNames"][_skill_uri()] == "l-01-agent-lifecycles: SKILL.md"
    assert (
        observed["resourceMeta"][_skill_uri()]["io.modelcontextprotocol.skills/origin"]
        == "agents-remember/skills"
    )
    # 3. This server's own index resource names every served skill. It is *this
    # server's* discovery surface, not the extension's enumeration result.
    index = json.loads(observed["index"])
    assert observed["indexMime"] == "application/json"
    assert {row["name"] for row in index["skills"]} == set(skill_catalog_registry().names())
    # 4. A selected body is the exact bytes on disk in this worktree.
    on_disk = (LIFECYCLE_SKILLS / "SKILL.md").read_text(encoding="utf-8")
    assert observed["body"] == on_disk
    assert observed["bodyMime"] == "text/markdown"
    assert observed["bodyMeta"]["io.modelcontextprotocol.skills/revision"] == _sha256_bytes(
        on_disk.encode("utf-8")
    )
    assert (
        observed["bodyMeta"]["io.modelcontextprotocol.skills/contentTrust"]
        == "server-supplied-data"
    )
    # 5. A relative supporting reference resolves against the skill root.
    assert observed["guide"] == (LIFECYCLE_SKILLS / "roles" / "worker.md").read_text(
        encoding="utf-8"
    )
    # 6. The unsupported read is refused rather than served as an empty success.
    assert "absent" in observed, "an unlisted skill URI must not read successfully"
    # 7. The capsule operation and the catalog reads left the served tool list
    # with MIK-R112 rule 5 (L112, 976bc39e): an agent gets its capsule at its
    # start and reads skills as files (resources/read, skills/list, skills/get
    # above). Their functions stay behind the role start; nothing on the wire
    # lists them anymore, so this exchange asserts no tool by those names.
    assert "role_capsule_compile" not in observed["tools"]
    assert "skill_catalog_list" not in observed["tools"]
    assert "skill_catalog_read" not in observed["tools"]
    # 8. The declared extension's own two methods are answered on this same transport.
    extension = observed["extension"]
    assert extension["list"]["ok"], extension["list"]
    listing = extension["list"]["result"]
    assert listing["resultType"] == "complete"
    assert listing["skills"], "a declaring server answers skills/list with its entries"
    entries = {entry["uri"]: entry for entry in listing["skills"]}
    assert _skill_uri() in entries
    entry = entries[_skill_uri()]
    assert set(entry) == SEP_ENTRY_KEYS
    assert entry["frontmatter"] == _frontmatter_of(on_disk)
    assert {record["uri"] for record in entry["resources"]} == {
        record.uri for record in _served_entry().files
    }
    assert extension["get"]["ok"], extension["get"]
    assert extension["get"]["result"]["skill"] == entry
    # An unlisted URI is the error `resources/read` uses, not an empty success.
    assert not extension["getAbsent"]["ok"]
    # A method this server does not register is still refused: the extension is a
    # bounded addition, not a blanket "answer anything" mode.
    assert not extension["unregistered"]["ok"]


@pytest.mark.integration
def test_the_server_process_never_serves_a_file_outside_a_skill_directory(
    tmp_path: Path,
) -> None:
    """A traversal URI is refused by the running server, not normalized into a read."""

    settings = _live_settings(tmp_path)
    (tmp_path / "coord").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings)],
        env={**os.environ, "PYTHONPATH": str(DEV_SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
    )

    async def probe() -> str:
        with open(os.devnull, "w") as devnull:
            async with stdio_client(parameters, errlog=devnull) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    try:
                        await session.read_resource(
                            AnyUrl(
                                "skill://agents-remember/skills/l-01-agent-lifecycles"
                                "/../../../../etc/passwd"
                            )
                        )
                    except Exception as error:
                        return type(error).__name__
        return "served"

    assert asyncio.run(probe()) != "served"


def test_the_mutation_harness_can_actually_fail() -> None:
    """A sanity check on the evidence: a wrong assertion here does raise.

    Every seeded-mutation proof in this leaf's evidence is only as good as the claim
    that its assertions can fail. This states that claim in the test module itself.
    """

    with pytest.raises(AssertionError):
        assert _sha256_bytes(b"one") == _sha256_bytes(b"another")
    with pytest.raises(KeyError):
        {"present": 1}["absent"]
    with pytest.raises(subprocess.SubprocessError):
        raise subprocess.SubprocessError("the harness's own failure path is reachable")


# ---------------------------------------------------------------------------
# The guards the baseline review found unpinned (F-L4-08), and the nesting rule
# ---------------------------------------------------------------------------


def test_a_catalog_record_that_escapes_its_skill_directory_is_refused(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """The containment proof, reached: a record resolving outside the skill refuses.

    The reviewer's seed removed ``catalog.py``'s containment guard and every case still
    passed, because the traversal case refuses earlier, at catalog lookup. This case
    builds the record the guard exists for — one whose ``relative_path`` escapes — and
    drives the read itself, so removing the guard fails here.
    """

    catalog = world.catalog
    entry = _entry_named(catalog, "shared")
    target = next(record for record in entry.files if record.relative_path == "SKILL.md")
    escaping = replace(target, relative_path="../../../../etc/passwd")
    tampered = replace(
        catalog,
        entries=tuple(
            replace(candidate, files=tuple(escaping if r is target else r for r in candidate.files))
            if candidate is entry
            else candidate
            for candidate in catalog.entries
        ),
    )
    with pytest.raises(Exception) as raised:
        read_served_file(tampered, escaping.uri)
    assert "outside its skill directory" in str(raised.value)


def test_the_index_reader_refuses_while_a_skill_cannot_be_served() -> None:
    """The wire index loader's refusal, reached through the served surface.

    The reviewer's seed removed ``require_servable`` from the index resource's loader
    and every case still passed. This case serves an index over a catalog with an
    unreadable skill and asserts the refusal, so removing that call fails here.
    """

    catalog = replace(
        skill_catalog_registry(),
        unreadable=(UnreadableSkill(skill_path="broken", reason="no usable frontmatter"),),
    )
    resource = index_resource(catalog)
    with pytest.raises(Exception) as raised:
        asyncio.run(resource.read())
    assert "cannot be served" in str(raised.value)


def test_a_skill_whose_directory_name_disagrees_with_its_name_is_recorded_not_served(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """The name/path-segment rule, reached: a mismatched pair is refused.

    SEP-2640 requires the final ``<skill-path>`` segment to equal the frontmatter
    ``name``. The reviewer's seed removed that check and every case still passed; this
    case writes the disagreement the rule forbids and asserts it is not served.
    """

    misplaced = world.corpus / "declared-name"
    misplaced.mkdir(parents=True)
    (misplaced / "SKILL.md").write_text(
        "---\nname: a-different-name\ndescription: name and directory disagree.\n---\n\n# x\n",
        encoding="utf-8",
    )
    catalog = build_skill_catalog(world.tree)
    recorded = {row.skill_path: row.reason for row in catalog.unreadable}
    assert "declared-name" in recorded
    assert "final skill-path segment" in recorded["declared-name"]
    assert "declared-name" not in catalog.names()


def test_the_extension_declaration_is_installed_once_per_server(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """The declaration idempotence guard, reached: a second install adds no second wrapper.

    The reviewer's seed removed the guard and every case still passed. This case
    registers the same server twice and asserts that the ``initialize`` options are
    built by one wrapper and declare the extension exactly once.
    """

    server = FastMCP("idempotence-probe")
    declare_skills_extension(server)
    first = server._mcp_server.create_initialization_options
    declare_skills_extension(server)
    second = server._mcp_server.create_initialization_options
    assert first is second, "a second declaration must not stack a second wrapper"
    assert declared_extensions(server) == {EXTENSION_ID: {}}


def test_altitude_admission_admits_every_role_the_document_can_carry_and_refuses_the_rest(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """The role rule is the task layer's, not a hardcoded role name.

    The reviewer's point: this leaf's document admits ``worker``, ``curator`` and
    ``reviewer``, so an implementation that hardcoded ``role == "worker"`` would pass
    every earlier case. Both sides here come from the task layer itself, driven against
    the fixture's own leaf document, so a narrowed rule fails on the first admitted role
    it omits. The document is the fixture's, not the leaf's real one, so the case runs
    wherever the worktree is checked out.
    """

    topology = TaskDocumentTopology(world.coord)
    ref = TaskDocumentRef(repository=REPOSITORY, path=ALPHA_TASK)
    admitted: list[str] = []
    refused: list[str] = []
    for role in _ALL_ROLE_NAMES:
        try:
            topology.validate_role(ref, role)
        except Exception:
            refused.append(role)
            continue
        admitted.append(role)
    assert admitted == ["worker", "curator", "reviewer"]
    # The rest are refused: four need a sprint, one needs a master, and ``bootstrap``
    # carries no structural task altitude at all -- it is the seat a workspace reaches
    # before a task document exists.
    assert refused == [
        "architect",
        "orchestrator",
        "designer",
        "strategist",
        "manager",
        "investigator",
        "bootstrap",
    ]
    # A role outside the frozen ten is refused as unsupported, not as a wrong altitude.
    with pytest.raises(Exception) as raised:
        topology.validate_role(ref, "not-a-role")
    assert "no structural task altitude" in str(raised.value)


def test_the_capsule_operation_serves_every_seat_its_document_can_carry(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """The capsule path — not only the task layer — admits every same-altitude seat.

    The reviewer's seed `G5b` narrows the rule to ``worker`` *inside* the capsule
    operation, and it survived every case: the other altitude case drives
    ``TaskDocumentTopology.validate_role`` directly, so nothing ever compiled the
    capsule for a different same-altitude seat. This case does, through the two entry
    points a consumer uses — the typed ``compile_task_capsule`` and the
    ``role_capsule_compile_tool`` response — and asserts the seat and altitude each
    reports. The mirror half proves the refusal *by name* for a role this document
    cannot carry, through the same call, so a blanket widening fails here too.
    """

    # Every seat this leaf document can carry, with an operation each may run.
    for role, operation in (
        ("worker", "implementation"),
        ("curator", "curation"),
        ("reviewer", "review"),
    ):
        outcome = world.compile(world.request(role=role, operation=operation))
        assert outcome.ok, f"{role} was refused: {outcome.explanation()}"
        assert outcome.binding is not None
        seat = outcome.binding.admitted.seat
        assert seat.role == role
        assert seat.altitude == "leaf"
        # The same seat reaches the wire response a consumer reads.
        response = world.response(world.request(role=role, operation=operation)).to_payload()
        assert response["ok"] is True, f"{role}: {response['explanation']}"
        assert response["role"] == role
        assert response["seatAltitude"] == "leaf"

    # The mirror: a role at another altitude is refused by name, through the same call.
    for role, operation in (("manager", "coordination"), ("architect", "planning")):
        refused = world.compile(world.request(role=role, operation=operation))
        assert not refused.ok, f"{role} should not be admitted at leaf altitude"
        assert refused.refusal is not None
        assert refused.refusal.status == "role-altitude-mismatch"
        assert role in refused.refusal.detail
        mirror = world.response(world.request(role=role, operation=operation)).to_payload()
        assert mirror["ok"] is False
        assert mirror["refusalStatus"] == "role-altitude-mismatch"


def test_a_nested_skill_is_published_flat_like_any_other(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    """SEP-2640 §Nested skills: nesting is permitted and publication stays flat.

    The enclosing skill's own entry lists the nested skill's files as supporting files,
    and the nested skill additionally has its own ordinary entry whose URI merely
    shares a path prefix. Nothing in either entry marks the nesting.
    """

    catalog = world.catalog
    outer = _entry_named(catalog, "shared")
    inner = _entry_named(catalog, "inner-skill")
    assert inner.skill_path == "shared/nested/inner-skill"
    assert inner.uri.startswith(f"{outer.uri.rsplit('/', 1)[0]}/nested/")
    # The enclosing entry is complete: it lists the nested skill's files too.
    outer_files = {record.relative_path for record in outer.files}
    assert "nested/inner-skill/SKILL.md" in outer_files
    assert "nested/inner-skill/support.md" in outer_files
    # The nested entry is an ordinary flat entry: its own root file, no nesting marker.
    inner_files = {record.relative_path for record in inner.files}
    assert inner_files == {"SKILL.md", "support.md"}
    assert set(inner.entry_document()) == SEP_ENTRY_KEYS
    # The nested frontmatter is verbatim, nested mapping and list included — and the
    # *entry*, which is what a client reads, carries those fields rather than a curated
    # subset. Both sides are independent: the fixture's own declared values, and the
    # file on disk parsed here.
    declared = {
        "name": "inner-skill",
        "description": "The nested skill the synthetic corpus publishes flat.",
        "metadata": {"version": "2.1.0", "author": "the fixture"},
        "license": "Apache-2.0",
        "tags": ["nested", "fixture"],
    }
    assert inner.frontmatter == declared
    assert inner.entry_document()["frontmatter"] == declared
    on_disk = (world.corpus / "shared" / "nested" / "inner-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "license: Apache-2.0" in on_disk
    # Reading the nested root file through the enclosing skill's entry is ordinary
    # reading, and it is the same bytes either way.
    nested_file = next(
        record for record in outer.files if record.relative_path == "nested/inner-skill/SKILL.md"
    )
    assert (
        read_served_file(catalog, nested_file.uri).content
        == (world.corpus / "shared" / "nested" / "inner-skill" / "SKILL.md").read_bytes()
    )


def test_old_role_compile_input_returns_the_canonical_investigator_with_one_notice(
    world: World,  # noqa: F811 - ``world`` is the fixture
) -> None:
    contract = world.master_dir / "series-contract.md"
    text = _contract_text(
        ContractSpec("", world.master_dir, world.code, "ar/master", contract),
        coordination_root=world.coord,
    )
    text = (
        text.replace("kind: leaf", "kind: series")
        .replace("task_id: SPRINT-ID", "task_id: MASTER-ID")
        .replace("task_name: SPRINT", f"task_name: {MASTER_DIR}")
        .replace("  leaf_id: \n", "")
    )
    contract.write_text(text)
    request = world.request(
        role="system-specialist",
        operation="orientation",
        task_path=f"{MASTER_DIR}/task.json",
        contract=contract,
    )
    old = world.response(request).to_payload()
    assert old["ok"], old["explanation"]
    assert old["role"] == "investigator"
    assert old["explanation"].count("Role 'system-specialist' was supplied") == 1
    new = world.response(
        world.request(
            role="investigator",
            operation="orientation",
            task_path=f"{MASTER_DIR}/task.json",
            contract=contract,
        )
    ).to_payload()
    assert old["semanticDigest"] == new["semanticDigest"]
    assert old["instructions"] == new["instructions"]
