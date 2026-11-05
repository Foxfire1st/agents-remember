"""Portable native Git sides and leaf authority declared by admitted D641 cases."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_write_route import load_leaf_contract
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.application.memory_tools import CitationOperationScope, citation_fix_tool
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    prepare_enclosure_publication,
    publish_enclosure_location,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.worktree_contract import (
    contract_publication_text,
    parse_contract_text,
)

from .adapters.execution import HERMES_ACTOR, LaunchProfile
from .artifacts import child_path, read_object, sha256_file, write_object
from .fixtures import CaseBundle, TrialFixture
from .hermes_profiles import hermes_auxiliary_tasks, hermes_route
from .hermes_runtime import HermesRuntimeBinding

_DESTINATION_TIMEOUT = 60
_DESTINATION_PROBE = r"""
import contextlib,hashlib,io,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
source,home=map(Path,sys.argv[1:3]);home=home.resolve()
provider,model,task=sys.argv[3:6]
blocked=[]
metadata_children=0
private=[Path(os.environ[key]).resolve() for key in
 ('HOME','HERMES_HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','TMPDIR')
 if os.environ.get(key)]
real=source.parent
def guard(event,args):
    global metadata_children
    # The installed Fireworks profile reads the native install-stamp commit.
    # Admit that exact local query, not arbitrary credential commands or an
    # installer: parent socket guards do not constrain spawned children.
    if (event=='subprocess.Popen' and args[1]==['git','rev-parse','HEAD']
            and args[2] is not None and Path(args[2]).resolve()==source.resolve()):
        metadata_children+=1;return
    if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','subprocess.Popen'):
        blocked.append(event);raise PermissionError('destination probe forbids network and children')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(args[0])).absolute()
        if path in [real/name for name in ('config.yaml','.env','auth.json','auth.lock')]:
            blocked.append('real-home-read');raise PermissionError('private home only')
        mode,flags=args[1:3]
        writing=isinstance(mode,str) and any(c in mode for c in 'wax+')
        writing|=isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT))
        if writing and not any(path.resolve().is_relative_to(root) for root in private):
            blocked.append('outside-private-write');raise PermissionError('private writes only')
sys.addaudithook(guard);sys.path.insert(0,str(source))
result={'status':'native-resolution-failed','resolvedHost':None}
# Native diagnostics can contain credentials. Keep them only in memory; emit
# this whitelist, never the runtime dict, URL, config, stderr or exception text.
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        from hermes_constants import get_hermes_home,get_default_hermes_root
        from hermes_cli.config import get_config_path,get_env_path
        from hermes_cli.env_loader import load_hermes_dotenv
        if get_hermes_home().resolve()!=home or get_default_hermes_root().resolve()!=home:
            raise ValueError('native private-home binding differs')
        if get_config_path()!=home/'config.yaml' or get_env_path()!=home/'.env':
            raise ValueError('native private config paths differ')
        # Exactly ACP entry._load_env, without a project dotenv or explicit
        # credential/URL argument that would select a different resolver rung.
        load_hermes_dotenv(hermes_home=get_hermes_home())
        from hermes_cli.runtime_provider import resolve_runtime_provider
        if task=='main':
            runtime=resolve_runtime_provider(requested=provider,target_model=model)
            result={'status':'resolved','resolvedHost':urlsplit(runtime['base_url']).hostname}
        else:
            from agent import auxiliary_client as auxiliary
            observed=[]
            def observe(frame,event,value):
                if (event=='call' and frame.f_code.co_name=='get_text_auxiliary_client'
                        and Path(frame.f_code.co_filename)==source/'agent/auxiliary_client.py'):
                    observed.append(frame.f_locals.get('task'))
            sys.setprofile(observe)
            try:
                with auxiliary.aux_probe_mode():
                    client,selected=auxiliary.get_text_auxiliary_client(task=task)
            finally:sys.setprofile(None)
            if client is None:raise ValueError('task client unavailable')
            host=urlsplit(str(client.base_url)).hostname
            config=auxiliary._get_auxiliary_task_config(task)
            body=auxiliary._get_task_extra_body(task)
            kwargs=auxiliary._build_call_kwargs(provider,selected,[],extra_body=body,
                                                task=task,base_url=str(client.base_url))
            allowed=('none','minimal','low','medium','high','xhigh','max','ultra')
            effort=kwargs.get('reasoning_effort')
            configured=config.get('reasoning_effort')
            result={'status':'resolved','resolvedHost':host,
                    'taskSelectionObserved':task if observed==[task] else None,
                    'configuredReasoningEffort':configured if configured in allowed else None,
                    'outgoingEffort':effort if effort in allowed else None,
                    'outgoingFields':sorted(kwargs),
                    'taskInvocationObserved':False,
                    'auxiliarySourceSha256':hashlib.sha256((source/'agent/auxiliary_client.py').read_bytes()).hexdigest()}

except Exception as error:
    # Fail closed at the diagnostic boundary; no exception is treated as a
    # usable route and no alternative provider is attempted.
    result={'status':'native-resolution-failed','resolvedHost':None,'errorType':type(error).__name__}
if blocked:
    result={'status':'scope-guard-refused','resolvedHost':None,'blockedEvents':sorted(set(blocked))}
result['metadataChildCalls']=metadata_children
print(json.dumps(result))
"""


def resolve_hermes_destination(
    runtime: HermesRuntimeBinding,
    environment: dict[str, str],
    *,
    receipt_path: Path,
    profile: LaunchProfile = HERMES_ACTOR,
) -> dict[str, Any]:
    """Bind each actor to its declared provider host with the selected seed."""
    return _probe_destination(
        runtime, environment, {**hermes_route(profile), "task": "main"}, receipt_path
    )


def resolve_hermes_auxiliary(
    runtime: HermesRuntimeBinding,
    environment: dict[str, str],
    *,
    receipt_root: Path,
) -> list[dict[str, Any]]:
    """Resolve each named task independently; resolution is not invocation."""
    document = read_object(Path(environment["HERMES_HOME"]) / "config.yaml")
    results = []
    for route in hermes_auxiliary_tasks(document):
        result = _probe_destination(
            runtime, environment, route, receipt_root / (route["task"] + ".json")
        )
        result["reachability"] = "unobserved; preflight resolution is not a task dispatch"
        if route["task"] == "mcp":
            result["configHasMcpServers"] = bool(document.get("mcp_servers"))
        write_object(receipt_root / (route["task"] + ".json"), result)
        results.append(result)
    return results


def _probe_destination(
    runtime: HermesRuntimeBinding,
    environment: dict[str, str],
    route: dict[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    provider, model, expected_host = route["provider"], route["model"], route["expectedHost"]
    argv = [
        str(runtime.python),
        "-I",
        "-B",
        "-c",
        _DESTINATION_PROBE,
        str(runtime.source_root),
        environment["HERMES_HOME"],
        provider,
        model,
        route["task"],
    ]
    result: dict[str, Any] = {
        "phase": "pre-actor",
        "task": route["task"],
        "resolver": (
            "hermes_cli.runtime_provider.resolve_runtime_provider"
            if route["task"] == "main"
            else "agent.auxiliary_client.get_text_auxiliary_client"
        ),
        "expectedHost": expected_host,
        "provider": provider,
        "model": model,
        "resolvedHost": None,
        "limitSeconds": _DESTINATION_TIMEOUT,
        "status": "native-resolution-failed",
    }
    try:
        done = subprocess.run(
            argv,
            cwd=environment["HERMES_HOME"],
            env={**environment, **runtime.environment},
            capture_output=True,
            text=True,
            timeout=_DESTINATION_TIMEOUT,
            check=False,
        )
        if not done.returncode:
            observed = json.loads(done.stdout)
            if not isinstance(observed, dict):
                raise ValueError("native destination diagnostic is not an object")
            result.update(
                {
                    key: observed[key]
                    for key in (
                        "status",
                        "resolvedHost",
                        "errorType",
                        "blockedEvents",
                        "metadataChildCalls",
                        "taskSelectionObserved",
                        "configuredReasoningEffort",
                        "outgoingEffort",
                        "outgoingFields",
                        "taskInvocationObserved",
                        "auxiliarySourceSha256",
                    )
                    if key in observed
                }
            )
        else:
            result["nativeExitCode"] = done.returncode
    except subprocess.TimeoutExpired:
        result["status"] = "native-resolution-timeout"
    except (OSError, ValueError) as error:
        result["errorType"] = type(error).__name__
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    host_matches = result["resolvedHost"] == expected_host
    task_matches = route["task"] == "main" or result.get("taskSelectionObserved") == route["task"]
    matched = result["status"] == "resolved" and host_matches and task_matches
    if route["task"] != "main":
        result["taskBinding"] = (
            "native task-selected resolution; actual dispatch not observed"
            if task_matches
            else "asserted by config, not observed"
        )
    if result["status"] == "resolved":
        result["status"] = (
            "pass"
            if matched
            else "host-mismatch"
            if not host_matches
            else "task-binding-unavailable"
        )
    result["matches"] = matched
    write_object(receipt_path, result)
    if not matched:
        raise AuthorityError(
            "binding-unavailable: Hermes provider destination "
            f"{result['status']}; resolved_host={result['resolvedHost']}; "
            f"expected_host={expected_host}; limit={_DESTINATION_TIMEOUT}s; "
            f"elapsed={result['elapsedSeconds']}s; receipt={receipt_path}"
        )
    return result


def _git(root: Path, environment: dict[str, str], *arguments: str) -> str:
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise AuthorityError("D641 native fixture Git setup failed: " + done.stderr.strip())
    return done.stdout.strip()


def import_native_sides(
    case: CaseBundle, fixture: TrialFixture, environment: dict[str, str]
) -> dict[str, Any] | None:
    metadata = case.manifest.get("setup", {}).get("nativeWorklist")
    if metadata is None:
        return None
    if (
        metadata.get("schema") != "ar-curator-eval-native-worklist/v1"
        or metadata.get("authority") != "D641"
    ):
        raise AuthorityError("Unsupported native worklist fixture binding")
    result = {}
    for name in ("code", "memory"):
        side = metadata[name]
        root = child_path(fixture.actor_root, side["repository"]).resolve()
        bundle = child_path(fixture.actor_root, side["bundle"])
        declared = [
            item for item in case.manifest["actorInputs"] if item["destination"] == side["bundle"]
        ]
        if len(declared) != 1:
            raise AuthorityError("Native Git bundle is not a declared actor input")
        source = next(item for item in case.files if item.path == declared[0]["source"])
        if sha256_file(bundle) != source.sha256:
            raise AuthorityError("Native Git bundle bytes differ")
        _git(
            root,
            environment,
            "fetch",
            "--no-write-fetch-head",
            str(bundle),
            side["bundleRef"] + ":refs/heads/fixture-source",
        )
        for key in ("base", "candidate"):
            if re.fullmatch("[0-9a-f]{40}", side[key]) is None:
                raise AuthorityError("Native Git side is not an exact object")
            kind = "commit" if key == "base" else "tree"
            _git(root, environment, "cat-file", "-e", side[key] + "^{" + kind + "}")
        tree = _git(root, environment, "rev-parse", side["candidate"] + "^{tree}")
        _git(root, environment, "diff", "--exit-code", side["candidate"], "--")
        result[name] = {
            "base": side["base"],
            "candidate": side["candidate"],
            "candidateTree": tree,
            "bundleSha256": source.sha256,
        }
    return {
        "schema": metadata["schema"],
        "sides": result,
        "expectedWorklistDigest": metadata["expectedWorklistDigest"],
    }


def place_native_leaf_repositories(
    case: CaseBundle, fixture: TrialFixture
) -> tuple[Path, Path, Path] | None:
    """Place the explicitly declared leaf pair at the native enclosure topology."""
    metadata = case.manifest.get("setup", {}).get("nativeLeaf")
    if metadata is None:
        return None
    if metadata.get("memoryTopology") != "distinct-official-linked-leaf":
        raise AuthorityError(
            "Native leaf requires an explicit distinct official/linked-leaf binding"
        )
    leaf_id = metadata.get("leafId", "")
    if re.fullmatch("[A-Za-z0-9-]+", leaf_id) is None:
        raise AuthorityError("Invalid native leaf identity")
    project = fixture.actor_root
    group = project / "coordination/worktrees/code" / (leaf_id.lower() + "-ar")
    group.mkdir(parents=True, exist_ok=False)
    roots = []
    for name in ("code", "memory"):
        alias = project / "projects" / name
        root = group / name
        if alias.exists():
            alias.rename(root)
        else:
            root.mkdir()
        alias.symlink_to(root, target_is_directory=True)
        roots.append(root)
    official = project / "coordination/memory-repos/ar-code"
    official.parent.mkdir(parents=True, exist_ok=True)
    official.mkdir()
    return roots[0], roots[1], project / "projects/memory"


def _project_baseline(
    case: CaseBundle,
    fixture: TrialFixture,
    root: Path,
    environment: dict[str, str],
    *,
    code_base: str | None = None,
) -> dict[str, Any]:
    """Commit declared B blobs through a private index, leaving C files untouched."""
    side = root.name
    entries = case.manifest["setup"]["nativeLeaf"]["baseline"][side]
    candidate_tree = _git(root, environment, "rev-parse", "HEAD^{tree}")
    isolated = {
        **environment,
        "GIT_INDEX_FILE": str(fixture.root / f"native-{side}-baseline.index"),
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    _git(root, isolated, "read-tree", "HEAD")
    bindings = []
    for entry in entries:
        if any(
            re.fullmatch("[0-9a-f]{40}", entry.get(key, "")) is None
            for key in ("sourceTree", "sourceBlob")
        ):
            raise AuthorityError("Native baseline requires exact recorded tree and blob bindings")
        source = next((item for item in case.files if item.path == entry["source"]), None)
        if source is None:
            raise AuthorityError("Native baseline source is not declared case material")
        path = child_path(root, entry["path"])
        source_path = source.verify(case.path)
        blob = _git(root, isolated, "hash-object", "-w", str(source_path))
        if entry["sourceBlob"] != blob:
            raise AuthorityError("Native baseline source blob differs from its era binding")
        _git(
            root,
            isolated,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob},{path.relative_to(root).as_posix()}",
        )
        bindings.append({**entry, "blob": blob, "sha256": source.sha256})
    tree = _git(root, isolated, "write-tree")
    message = f"D637 labelled {side} B projection from declared recorded inputs"
    if code_base is not None:
        message += "\n\nCode-Commit: " + code_base
    base = _git(
        root,
        isolated,
        "-c",
        "user.name=Curator eval fixture",
        "-c",
        "user.email=eval@invalid",
        "commit-tree",
        tree,
        "-m",
        message,
    )
    _git(root, environment, "branch", "eval-base", base)
    _git(root, environment, "checkout", "-b", "eval-candidate")
    _git(root, environment, "reset", "--mixed", base)
    return {"base": base, "baseTree": tree, "candidateTree": candidate_tree, "bindings": bindings}


def _link_leaf_memory(fixture: TrialFixture, memory: Path, environment: dict[str, str]) -> Path:
    """Give official and leaf distinct roots in one real Git repository.

    Keep the candidate files apart while Git registers an empty, no-checkout
    worktree. Only the official baseline is checked out; absent candidate cards
    are never restored into the leaf by this fixture construction.
    """
    official = fixture.actor_root / "coordination/memory-repos/ar-code"
    current = fixture.root / "native-memory-current"
    (memory / ".git").rename(official / ".git")
    memory.rename(current)
    _git(official, environment, "symbolic-ref", "HEAD", "refs/heads/eval-base")
    _git(official, environment, "reset", "--hard", "eval-base")
    _git(official, environment, "worktree", "add", "--no-checkout", str(memory), "eval-candidate")
    _git(memory, environment, "reset", "--mixed", "HEAD")
    for path in current.iterdir():
        shutil.move(str(path), memory / path.name)
    current.rmdir()
    return official


def prepare_native_leaf(
    case: CaseBundle, fixture: TrialFixture, code: Path, memory: Path, environment: dict[str, str]
) -> dict[str, Any] | None:
    metadata = case.manifest.get("setup", {}).get("nativeLeaf")
    if metadata is None:
        return None
    if (
        metadata.get("schema") != "ar-eval-native-leaf/v1"
        or metadata.get("workflowKind") != "light-task"
    ):
        raise AuthorityError("Unsupported native leaf fixture binding")
    if metadata["code"] != "projects/code" or metadata["memory"] != "projects/memory":
        raise AuthorityError("Native leaf repositories differ from trial bindings")
    for key in ("taskId", "leafId"):
        if re.fullmatch("[A-Za-z0-9-]+", metadata[key]) is None:
            raise AuthorityError("Invalid native leaf identity")
    authorization = child_path(fixture.actor_root, metadata["authorization"])
    if (
        not any(
            item["destination"] == metadata["authorization"]
            for item in case.manifest["actorInputs"]
        )
        or not authorization.is_file()
    ):
        raise AuthorityError("Native leaf authority is not a declared actor input")
    baseline = metadata.get("baseline", {})
    if baseline.get("schema") != "ar-eval-native-baseline/v1":
        raise AuthorityError("Native leaf requires explicit recorded B/C projection bindings")
    code_side = _project_baseline(case, fixture, code, environment)
    code_base = code_side["base"]
    memory_side = _project_baseline(case, fixture, memory, environment, code_base=code_base)
    memory_base = memory_side["base"]
    # These are labelled fixture authorities, not a claim about a historical
    # remote. Ordinary leaf tools require a real recorded default branch.
    _git(code, environment, "update-ref", "refs/remotes/origin/eval-base", code_base)
    _git(
        code,
        environment,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/eval-base",
    )
    _git(memory, environment, "config", "agents-remember.defaultBranch", "eval-base")
    official_memory = _link_leaf_memory(fixture, memory, environment)
    task = fixture.actor_root / "coordination/tasks/code" / metadata["taskId"].lower()
    task.mkdir(parents=True, exist_ok=False)
    task_document = TaskDocument(
        id=metadata["taskId"],
        slug=metadata["taskId"].lower(),
        title="D641 native evaluation leaf",
        kind="light",
        repo="code",
        createdAt="2000-01-01T00:00:00Z",
    )
    write_object(task / "task.json", task_document.model_dump(mode="json", by_alias=True))
    (task / "task.md").write_text("# D641 native evaluation leaf\n")
    contract = leaf_enclosure_path(task, metadata["leafId"])
    group = code.parent
    document = f"""---
schema: ar-series-contract/v1
schemaVersion: 1.0
kind: leaf
task_id: {metadata["taskId"]}
task_name: {metadata["taskId"].lower()}
repo_name: code
workflow_kind: light-task
memory_mode: external
coordination:
  root: {fixture.actor_root / "coordination"}
  task_root: {task}
  series_contract_path: {contract}
  task_artifact: {task / "task.md"}
  worktree_group: {group}
  leaf_id: {metadata["leafId"]}
  parent_task_name: {metadata["taskId"].lower()}
code:
  repo_path: {code}
  source_branch: eval-base
  work_branch: eval-candidate
  base_commit: {code_base}
  worktree: {code}
memory:
  mode: external
  repo_path: {official_memory}
  source_branch: eval-base
  work_branch: eval-candidate
  base_commit: {memory_base}
  worktree: {memory}
  ledger: {memory / "memory.md"}
---
"""
    parsed_contract = parse_contract_text(document, path=contract)
    publication = prepare_enclosure_publication(
        parsed_contract,
        contract_text=contract_publication_text(contract, parsed_contract),
        publication_kind="new-enclosure",
        audit_intent="D641 newly provisioned evaluation enclosure from declared B/C inputs; no real role or lifecycle adoption",
    )
    location = publish_enclosure_location(publication, contract_mode="publish")
    parsed = load_leaf_contract(str(contract))
    if isinstance(parsed, str):
        raise AuthorityError("Native leaf contract refused: " + parsed)
    return {
        "schema": metadata["schema"],
        "contractPath": str(contract),
        "taskId": metadata["taskId"],
        "leafId": metadata["leafId"],
        "authorization": metadata["authorization"],
        "codeBase": code_base,
        "memoryBase": memory_base,
        "codeRoot": str(code),
        "memoryRoot": str(memory),
        "officialMemoryRoot": str(official_memory),
        "citationProbeDocument": metadata["citationProbeDocument"],
        "branchAuthority": {
            "kind": "constructed fixture binding to declared B projections",
            "code": {"originHead": "refs/remotes/origin/eval-base", "commit": code_base},
            "memory": {"defaultBranch": "eval-base", "commit": memory_base},
        },
        "worktreeGroup": str(group),
        "candidateTrees": {
            "code": code_side["candidateTree"],
            "memory": memory_side["candidateTree"],
        },
        "baseline": {"code": code_side, "memory": memory_side},
        "publication": location.payload(),
        "requiredSubjects": metadata.get("requiredSubjects", []),
    }


def configured_intake(
    config: McpRuntimeConfig, leaf_binding: dict[str, Any] | None, output_root: Path
) -> dict[str, Any]:
    """Run native full MQC in one of the two explicitly configured trial modes."""
    output_root.mkdir(parents=True, exist_ok=False)
    contract = None if leaf_binding is None else leaf_binding["contractPath"]
    request = MemoryQualitySyncRequest(mode="sync", repo_id="code", contract_path=contract)
    reports = []
    for index in range(2):
        result = run_memory_quality_request(config, request)
        path = output_root / f"memory-quality-{index + 1}.json"
        write_object(path, result)
        reports.append({"path": str(path), "sha256": sha256_file(path)})
        if (
            result.get("code") != "memory-quality-candidate-changed"
            or result.get("nextAction") != "memory_quality_check"
        ):
            break
    expected_mode = "official-diagnostic" if leaf_binding is None else "leaf-candidate"
    intake_accepted = (
        result.get("scopeAuthority") == expected_mode
        and "checks" in result
        and result.get("status") != "scope-refused"
        and result.get("code") != "memory-quality-candidate-changed"
    )
    required = [] if leaf_binding is None else leaf_binding["requiredSubjects"]
    worklist = None
    covered = []
    if contract is not None:
        path = Path(contract).parent / "knowledge-worklist.json"
        if path.is_file():
            document = read_object(path)
            snapshot = output_root / "earned-knowledge-worklist.json"
            write_object(snapshot, document)
            covered = sorted({item["subject"] for item in document["items"]})
            worklist = {
                "path": str(snapshot),
                "sha256": sha256_file(snapshot),
                "digest": document["digest"],
                "state": document["state"],
                "items": document["items"],
            }
    answer = {
        "mode": expected_mode,
        "intakeAccepted": intake_accepted,
        "qualityStatus": result.get("status"),
        "qualityCode": result.get("code"),
        "reports": reports,
        "requiredSubjects": required,
        "coveredSubjects": covered,
        "missingSubjects": sorted(set(required) - set(covered)),
        "worklist": worklist,
    }
    write_object(output_root / "intake.json", answer)
    return answer


def citation_write_preflight(
    config: McpRuntimeConfig, leaf_binding: dict[str, Any], output_root: Path
) -> dict[str, Any]:
    """Destructive capability proof on a dedicated disposable probe, never an actor copy."""
    output_root.mkdir(parents=True, exist_ok=False)
    memory = Path(leaf_binding["memoryRoot"])
    official = Path(leaf_binding["officialMemoryRoot"])
    roots = {"code": Path(leaf_binding["codeRoot"]), "memory": memory}
    absent = [
        {"side": side, "path": item["path"]}
        for side, root in roots.items()
        for item in leaf_binding["baseline"][side]["bindings"]
        if not child_path(root, item["path"]).exists()
    ]

    def hashes() -> dict[str, str]:
        return {
            path.relative_to(memory).as_posix(): sha256_file(path)
            for path in sorted(memory.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(memory).parts
        }

    before = hashes()
    result = citation_fix_tool(
        config,
        repo_id="code",
        contract_path=leaf_binding["contractPath"],
        dry_run=False,
        operation_scope=CitationOperationScope(document=leaf_binding["citationProbeDocument"]),
    )
    report_path = output_root / "citation-fix.json"
    write_object(report_path, result)
    after = hashes()
    changed = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )
    tool_passed = result.get("ok") is True and result.get("dryRun") is False
    restored = [item for item in absent if child_path(roots[item["side"]], item["path"]).exists()]
    common = repository_identity(memory)
    answer = {
        "schema": "ar-eval-citation-write-preflight/v1",
        "disposableProbeOnly": True,
        "document": leaf_binding["citationProbeDocument"],
        "officialMemoryRoot": str(official.resolve()),
        "leafMemoryRoot": str(memory.resolve()),
        "distinctRoots": official.resolve() != memory.resolve(),
        "commonGitIdentity": None if common is None else str(common),
        "sameGitRepository": common is not None and common == repository_identity(official),
        "branchAuthority": leaf_binding["branchAuthority"],
        "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "toolOk": result.get("ok"),
        "writePassed": tool_passed and bool(changed),
        "writeOutcome": ("changed" if changed else "no-op") if tool_passed else "not-passing",
        "toolStatus": result.get("status"),
        "changedPaths": changed,
        "beforeFileHashes": before,
        "afterFileHashes": after,
        "absentBefore": absent,
        "restoredAbsentSubjects": restored,
        "absencePreserved": not restored,
    }
    write_object(output_root / "citation-write-preflight.json", answer)
    return answer
