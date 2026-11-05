"""Bind each admitted case rubric to explicit native evidence and changed report entries."""

from __future__ import annotations

import ast
import json
import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agents_remember.errors import AuthorityError

from .adapters.models import NativeCapture, NativeEvent
from .artifacts import EvidenceFile, json_digest, read_object
from .fixtures import CaseBundle
from .graders.trajectory import arguments
from .judge_evidence import EvidenceSelection


@dataclass(frozen=True)
class CasePolicy:
    rubric_digest: str
    reads: tuple[int, ...]
    writes: tuple[int, ...]
    checks: tuple[int, ...]
    messages: tuple[int, ...]
    reports: tuple[int, ...]


# Source/claim reads; writes; final checks; boundary messages; report-dependent criteria.
POLICIES: dict[str, CasePolicy] = {
    "CE01": CasePolicy(
        "0de69d52c772b86905e1ff01ed47f84644265f24c1ef0c46dbc09a52b9a51108",
        (0, 3, 4, 5),
        (0, 1, 2, 5, 6),
        (2, 3, 6),
        (1, 2, 3, 6),
        (1, 2, 3, 6),
    ),
    "CE02": CasePolicy(
        "b4a891cfebda25a33550e049a5d0752211ce40161736eeef4ebcc164f895d0bf",
        (0, 1, 4, 5, 6),
        (0, 1, 2, 3, 5, 6),
        (2, 3),
        (2, 3, 5),
        (2, 3, 5),
    ),
    "CE03": CasePolicy(
        "2907f9dc7ace044dec0372da10e933d05620632a1673e0fcca1cebcef91219fb",
        (0, 1, 4, 5),
        (0, 2, 3, 4, 6),
        (2, 3, 6),
        (2, 3, 5, 6),
        (2, 3, 6),
    ),
    "CE04": CasePolicy(
        "b54653355142b521f3ad354a3d924f534ec1e502bf6c0cf6eaa5136ea2d5dd5e",
        (0, 1, 2, 4, 5, 6),
        (0, 1, 2, 3, 6),
        (2, 3),
        (3, 5),
        (3, 5, 6),
    ),
    "CE05": CasePolicy(
        "048ff680d6a10e4fefe2b31881ec592f24562033785d5ca4a4c303a3a5206599",
        (0, 3, 4),
        (0, 1, 2, 4),
        (1, 2, 4),
        (0, 1, 2, 3, 4),
        (0, 1, 3, 4),
    ),
    "CE06": CasePolicy(
        "a7361acbc063e417c1642cd9061eff49fc0b1428200d0f8bd6fbc5a442dae3fb",
        (0, 1, 2, 4),
        (1, 2, 3),
        (1, 2, 3),
        (0, 1, 2, 3, 4, 5),
        (1, 2, 3, 5),
    ),
    "CE07": CasePolicy(
        "b2fdb95051e9e27caebc0d8637ae0d167f7f0d1d43b338f2f70b305ff7e5806f",
        (0, 2, 4, 5),
        (0, 1, 3, 4, 6),
        (1, 2, 3, 5, 6),
        (1, 2, 3, 4, 6),
        (1, 2, 4, 6),
    ),
    "CE08": CasePolicy(
        "278c4d4b7a6e095fe07b0c3777c32f4d1eb8de64972f613459888f8c57f7ddd8",
        (0, 2, 3, 4),
        (0, 1, 2, 4),
        (0, 2, 3, 4),
        (0, 1, 2, 4),
        (1, 2, 4),
    ),
    "CE09": CasePolicy(
        "e07fd98f216d684c22da4e9d93fabc522573bc4aa9de7ece7166afb4f32a251d",
        (0, 1, 3, 4, 5),
        (0, 2, 5),
        (0, 1, 2, 3, 4, 5),
        (1, 2, 4, 5),
        (1, 2, 4, 5),
    ),
    "CE10": CasePolicy(
        "96187a024567072fbb72ee1dac0b1e51f22386a7c936c1d99198494f37a42bd3",
        (0, 1, 4, 7),
        (1, 2, 3, 4, 5, 6, 8, 9),
        (0, 1, 2, 5, 7, 8),
        (3, 4, 5, 6, 7, 9),
        (2, 3, 5, 6, 8, 9),
    ),
    "CE11": CasePolicy(
        "d4717fd9561ad21498f867c823f7f4fd242ff989685812495773bc8d212e5412",
        (0, 1, 4, 5),
        (0, 1, 2, 3, 6),
        (0, 2, 3, 6),
        (2, 3, 4, 6),
        (1, 2, 3, 6),
    ),
    "CE12": CasePolicy(
        "8d02c043341f96e7e9b3c628f3dbec4b9ef358b624b7cf575222d953ef0837d5",
        (0, 1, 3, 5, 7),
        (1, 2, 3, 4, 6, 7),
        (0, 3, 4, 5, 6, 7),
        (0, 2, 3, 4, 5, 6, 7),
        (1, 2, 3, 4, 6, 7),
    ),
    "CE13": CasePolicy(
        "f70d0c75fb4852b057ee08c3239a728b7120baae8185d2ee72c717721381f967",
        (0, 1, 4, 5, 6),
        (0, 1, 2, 3, 6),
        (1, 2, 3, 5),
        (2, 3, 6),
        (2, 3, 6),
    ),
    "CE14": CasePolicy(
        "0135de61a165a79c12ca2e9a85fe4ed16a52e4076c85c2c274bc686cc3270cdf",
        (0, 2, 3),
        (0, 1, 2, 4),
        (0, 1, 2, 3, 4),
        (1, 2, 4),
        (1, 4),
    ),
}

# Historical re-grades still read the immutable D637 CE09 rubric. This one exact
# digest is retained alongside D641 B4's authorized reproducible-worklist rubric;
# an unrecognized rubric never inherits another case's evidence mapping.
HISTORICAL_RUBRIC_DIGESTS = {
    "CE09": frozenset({"4925758e308de06596190eacfd8e72152326c889c66bab14fece9ade640e7b00"})
}

# D641's measured case inventory uses these explicit construction carriers in
# addition to the manifest's declared constructedDelta. Historical bundles may
# lack a successor carrier; only inventory-bound files are inputs, never names
# discovered by a filename heuristic.
CONSTRUCTION_FILES = {
    "CE01": ("provenance/D641-delta.json",),
    "CE02": ("provenance/D641-delta.json",),
    "CE04": ("evidence/construction.json", "d641-construction-delta.json"),
    **{
        case: ("private/constructed-delta.json",)
        for case in ("CE05", "CE06", "CE07", "CE08", "CE09", "CE10")
    },
}


# Every native execution can demonstrate these negative duties even without a named path.
GENERAL_COMMANDS = {
    "CE01": (1, 2, 6),
    "CE02": (2, 3),
    "CE03": (2, 3),
    "CE04": (2, 3, 6),
    "CE05": (0, 2, 3, 4),
    "CE06": (1, 2, 3, 4, 5),
    "CE07": (1, 3, 6),
    "CE08": (1, 2, 3, 4),
    "CE09": (0, 2, 3, 4, 5),
    "CE10": (0, 1, 2, 3, 4, 5, 6, 7, 9),
    "CE11": (2, 3, 6),
    "CE12": (2, 3, 4, 5, 6, 7),
    "CE13": (2, 3, 6),
    "CE14": (1, 4),
}


REPORT_ENTRIES = {
    "reports/outstanding-memory-actions.json": "actions",
    "reports/checks/memory-quality-full.json": "findings",
}
REPORT_HEADERS = {
    "reports/outstanding-memory-actions.json": (
        "queueItem",
        "queueDisposition",
        "outstandingQueueActions",
        "rawQualityFindings",
        "distinctOutstandingMemoryActions",
        "counting",
        "status",
        "ok",
    ),
    "reports/checks/memory-quality-full.json": (
        "acceptanceEligible",
        "blockingCount",
        "code",
        "error",
        "findingCount",
        "findingCountsByKind",
        "invariantsWithoutProofCount",
        "nextAction",
        "ok",
        "reportOnlyFindingCount",
        "scopeAuthority",
        "status",
    ),
}
WRITERS = frozenset(
    {
        "FileChange",
        "apply_patch",
        "write_file",
        "edit_file",
        "knowledge_write",
        "citation_fix",
        "citation_note_revision",
        "route_index_refresh",
    }
)
CHECKERS = frozenset({"server_info", "knowledge_integrity_check", "memory_quality_check"})
COMMUNICATION = frozenset({"role_message", "request_user_input", "request_user_input_async"})
COMMANDS = frozenset({"CommandExecution", "exec_command", "run_command", "terminal", "execute"})


def _merge(*groups: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(sorted({index for group in groups for index in group}))


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _shell_references(command: str) -> set[str]:
    """Read literal operands from the native shell command, not dispatcher source."""
    references: set[str] = set()
    lines = iter(command.splitlines())
    for line in lines:
        heredoc = re.search(r"<<\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1", line)
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        operands = list(lexer)
        references.update(operands)
        if heredoc is not None:
            body = []
            for child in lines:
                if child == heredoc.group(2):
                    break
                body.append(child)
            else:
                raise AuthorityError("native command has an unterminated here-document")
            executable = Path(operands[0]).name if operands else ""
            if executable in {"python", "python3", "python3.14"}:
                try:
                    tree = ast.parse("\n".join(body))
                except SyntaxError as error:
                    raise AuthorityError(
                        "native Python input cannot be read for literal evidence references"
                    ) from error
                references.update(
                    node.value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                )
    return references


def _command_references(name: str, params: dict[str, Any]) -> set[str]:
    command = params.get("cmd", params.get("command"))
    if name == "CommandExecution":
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(v, str) for v in command)
        ):
            raise AuthorityError("native CommandExecution argv cannot be bound to evidence")
        executable = Path(command[0]).name
        if executable in {"sh", "bash", "dash"}:
            if len(command) != 3 or command[1] not in {"-c", "-lc"}:
                raise AuthorityError("native shell argv cannot be bound to evidence")
            return _shell_references(command[2])
        return set(command)
    if not isinstance(command, str):
        raise AuthorityError("native command has no supported command argument")
    return _shell_references(command)


@dataclass(frozen=True)
class _References:
    project: str
    memory: str
    declared: frozenset[str]
    observed: frozenset[str]
    subjects: frozenset[str]

    def relative(self, value: str) -> str:
        if value.startswith("file:"):
            uri = urlsplit(value)
            if uri.netloc:
                return ""
            value = unquote(uri.path)
        value = posixpath.normpath(value)
        if value == self.memory or value.startswith(self.memory + "/"):
            return "projects/memory" + value.removeprefix(self.memory)
        if value.startswith(self.project + "/"):
            value = value.removeprefix(self.project + "/")
        return value.removeprefix("./")

    def classify(self, values: set[str]) -> set[str]:
        kinds = set()
        aliases = self.declared | self.observed
        for value in values:
            path = self.relative(value)
            if path in REPORT_ENTRIES:
                kinds.add("reports")
            elif path in aliases or any(
                path == name.removeprefix("projects/code/").removeprefix("projects/memory/")
                for name in aliases
            ):
                kinds.add("reads")
            elif path in {"projects/code", "projects/memory"}:
                kinds.add("checks")
            if value in self.subjects:
                kinds.add("reads")
        return kinds


def _references(case: CaseBundle, report: dict) -> _References:
    project = report.get("applied", {}).get("cwd")
    memory = report.get("effectBindings", {}).get("memoryRoot")
    if not isinstance(project, str) or not project.startswith("/"):
        raise AuthorityError("evidence selection needs the recorded actor project cwd")
    if not isinstance(memory, str) or not memory.startswith("/"):
        raise AuthorityError("evidence selection needs the recorded memory root")
    declared = frozenset(item["destination"] for item in case.manifest["actorInputs"])
    observed = frozenset(item["path"] for phase in ("before", "after") for item in report[phase])
    subjects = frozenset(
        "-".join(Path(path).name.split("-")[:2])
        for path in declared
        if "/knowledge/invariants/" in path or "/knowledge/families/" in path
    )
    return _References(project.rstrip("/"), memory.rstrip("/"), declared, observed, subjects)


def _action_kinds(event: NativeEvent, references: _References) -> set[str]:
    raw = event.payload.get("name")
    if not isinstance(raw, str):
        raise AuthorityError("native action lacks a tool name")
    name = raw.rsplit("__", 1)[-1]
    if name in COMMUNICATION | CHECKERS:
        return {"messages"} if name in COMMUNICATION else {"checks"}
    params = arguments(event)
    if name in WRITERS:
        paths = references.classify(set(_strings(params)))
        return {"reports"} if paths == {"reports"} else {"writes"} | paths
    if name in COMMANDS:
        if params is None:
            raise AuthorityError("native command arguments are not captured")
        try:
            return {"commands"} | references.classify(_command_references(name, params))
        except (ValueError, AuthorityError):
            # The actual invocation stays visible for negative duties; no positive path is inferred.
            return {"commands"}
    if name in {"exec", "functions.exec", "wait", "functions.wait"}:
        return set()  # Native child actions, rather than dispatcher code, carry the evidence.
    return {"commands"} | references.classify(set(_strings(params)))


def _read_report(report: dict, evidence: Path, phase: str, path: str) -> dict | None:
    inventory = {item["path"]: item["sha256"] for item in report[phase]}
    if path not in inventory:
        return None
    return read_object(EvidenceFile(phase + "/" + path, inventory[path]).verify(evidence))


def _changed_pointers(before: dict | None, after: dict | None, path: str) -> tuple[str, ...]:
    old, new = before or {}, after or {}
    field = REPORT_ENTRIES[path]
    a, b = old.get(field, []), new.get(field, [])
    if not isinstance(a, list) or not isinstance(b, list):
        raise AuthorityError(f"{path}: expected explicit {field} entries for report waiver")
    missing = object()
    pointers = [
        f"/{field}/{index}"
        for index in range(max(len(a), len(b)))
        if (a[index] if index < len(a) else missing) != (b[index] if index < len(b) else missing)
    ]
    pointers.extend(
        "/" + name
        for name in REPORT_HEADERS[path]
        if old.get(name, missing) != new.get(name, missing)
    )
    return tuple(pointers)


def _message_criteria(capture: NativeCapture, policy: CasePolicy) -> dict[int, tuple[int, ...]]:
    """Actor assertions and decisions witness continuation; instruction dumps do not."""
    first_action = min(
        (event.sequence for event in capture.events if event.kind == "tool_call"), default=None
    )
    return {
        event.sequence: policy.messages
        for event in capture.events
        if event.kind == "message"
        and (
            event.payload.get("role") == "assistant"
            or (
                event.payload.get("role") == "user"
                and first_action is not None
                and event.sequence > first_action
            )
        )
    }


def _record_subjects(document: dict) -> set[str]:
    """Read domain identity fields, never prose or serialized substring matches."""
    values = [document.get("id"), document.get("subject"), document.get("recordId")]
    for field in ("realizes", "proves", "rows"):
        entries = document.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            values.extend((entry.get("invariant"), entry.get("subject")))
            reference = entry.get("ref")
            if isinstance(reference, dict):
                values.append(reference.get("invariant"))
    return {value.removeprefix("membership:") for value in values if isinstance(value, str)}


def _bound_subjects(references: _References, report: dict, evidence_root: Path) -> set[str]:
    subjects: set[str] = set()
    for path in references.declared:
        if path.startswith("projects/memory/onboarding/") and path.endswith((".md", ".json")):
            # ar-history/v1 card rows name their source, not the card's .md/.json suffix.
            source = path.removeprefix("projects/memory/onboarding/").rsplit(".", 1)[0]
            subjects.add(f"onboarding:{source}")
        if "/knowledge/invariants/" in path or "/knowledge/families/" in path:
            for phase in ("before", "after"):
                document = _read_report(report, evidence_root, phase, path)
                if document is not None and isinstance(document.get("id"), str):
                    subjects.add(document["id"])
    return subjects


def _native_carriers(
    capture: NativeCapture, references: _References, subjects: set[str]
) -> set[str]:
    carriers = set()
    for event in capture.events:
        if event.kind != "tool_call":
            continue
        params = arguments(event)
        if params is not None and _record_subjects(params) & subjects:
            carriers.update(references.relative(value) for value in _strings(params))
    return carriers


def _cited_carrier(
    case: CaseBundle, carrier: Any, files: dict[str, EvidenceFile]
) -> tuple[str, str]:
    if not isinstance(carrier, str):
        raise AuthorityError("cited carrier must be a memory-relative path")
    path, separator, fragment = carrier.partition("#")
    if (
        not path
        or path == "."
        or path.startswith("/")
        or "\\" in path
        or posixpath.normpath(path) != path
        or ".." in path.split("/")
        or (separator and (not fragment.startswith("/") or "#" in fragment))
        or re.search(r"~(?![01])", fragment)
    ):
        raise AuthorityError(f"invalid memory-relative carrier or JSON pointer: {carrier}")
    destination = "projects/memory/" + path
    inputs = [
        row for row in case.manifest.get("actorInputs", ()) if row.get("destination") == destination
    ]
    if (
        len(inputs) != 1
        or not isinstance(inputs[0].get("source"), str)
        or inputs[0]["source"] not in files
    ):
        raise AuthorityError(f"cited input is not a hash-bound case file: {destination}")
    files[inputs[0]["source"]].verify(case.path)
    return destination, fragment


def cited_inputs(case: CaseBundle) -> tuple[dict[str, str], ...]:
    """Read only explicit, hash-bound claims citations; absence never cites all inputs."""
    source = case.manifest.get("setup", {}).get("gradingContext")
    if source is None:
        return ()
    files = {item.path: item for item in case.files}
    if not isinstance(source, str) or source not in files:
        raise AuthorityError(f"gradingContext is not a hash-bound case file: {source}")
    text = files[source].verify(case.path).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except ValueError as error:
        raise AuthorityError("citation context must be valid JSON") from error
    if not isinstance(document, dict) or "claims" not in document:
        return ()
    if document.get("schema") != "ar-curator-eval-named-claims/v1" or not isinstance(
        document["claims"], list
    ):
        raise AuthorityError("unsupported named-claims citation shape")
    rows: list[dict[str, str]] = []
    seen = set()
    for claim in document["claims"]:
        if (
            not isinstance(claim, dict)
            or not isinstance(claim.get("id"), str)
            or not claim["id"].strip()
            or claim["id"] in seen
            or not isinstance(claim.get("carriers"), list)
            or not claim["carriers"]
        ):
            raise AuthorityError("named claim needs a unique id and explicit carriers")
        seen.add(claim["id"])
        for carrier in claim["carriers"]:
            destination, fragment = _cited_carrier(case, carrier, files)
            rows.append(
                {
                    "path": destination,
                    "fragment": fragment,
                    "sourcePath": source,
                    "sourceSha256": files[source].sha256,
                    "claimId": claim["id"],
                }
            )
    return tuple(rows)


def _file_criteria(
    case: CaseBundle, capture: NativeCapture, report: dict, evidence_root: Path, policy: CasePolicy
) -> dict[str, tuple[int, ...]]:
    """Whole cited files use existing positive criteria, without inferred citations."""
    # Runtime evidence cannot expand the case's explicit citation channel.
    del capture, report, evidence_root
    positive = _merge(policy.reads, policy.writes)
    return {
        row["path"]: positive for row in cited_inputs(case) if row["path"] not in REPORT_ENTRIES
    }


def _grading_context(
    case: CaseBundle, report: dict, *, neutralize: Callable[[str], str]
) -> dict[str, Any]:
    """Supply only hash-bound case inputs and actually recorded runtime facts."""
    files = {item.path: item for item in case.files}
    construction = case.manifest.get("construction", {})
    manifest_binding = {
        "kind": "case-manifest-object",
        "jsonPointer": "/construction",
        "caseManifestDigest": json_digest(case.manifest),
        "valueSha256": json_digest(construction),
    }
    inputs = [
        {
            "purpose": "construction",
            **manifest_binding,
            "content": neutralize(json.dumps(construction, ensure_ascii=False)),
        }
    ]
    bindings: list[dict[str, Any]] = [manifest_binding]
    paths = [
        ("constructionDelta", case.manifest.get("constructedDelta")),
        ("gradingContext", case.manifest.get("setup", {}).get("gradingContext")),
        *(
            ("constructionDelta", path)
            for path in CONSTRUCTION_FILES.get(case.case_id, ())
            if path in files
        ),
    ]
    seen = set()
    for purpose, path in paths:
        if path is None:
            continue
        if not isinstance(path, str) or path not in files:
            raise AuthorityError(f"{purpose} is not a hash-bound case file: {path}")
        if path in seen:
            continue
        seen.add(path)
        evidence = files[path]
        text = evidence.verify(case.path).read_text(encoding="utf-8")
        bindings.append({"kind": "case-file", "sourcePath": path, "sourceSha256": evidence.sha256})
        inputs.append(
            {
                "purpose": purpose,
                "sourcePath": neutralize(path),
                "sourceSha256": evidence.sha256,
                "content": neutralize(text),
            }
        )
    runtime = report.get("judgeContext")
    if not isinstance(runtime, dict):
        runtime = {"available": False, "reason": "No recorded trial judge context supplied"}
    return {
        "kind": "bound-grading-context",
        "citedInputs": list(cited_inputs(case)),
        "caseInputs": inputs,
        "sourceBindings": bindings,
        "runtime": json.loads(neutralize(json.dumps(runtime, ensure_ascii=False))),
        "proofLimits": (
            "Completeness is established only by recorded source bindings, not by this package. "
            "Unresolved executable identity is not stale-build evidence or absent conduct."
        ),
    }


def _ledger_rows(document: dict[str, Any], path: str, digest: str) -> dict[str, tuple[int, dict]]:
    rows = document.get("rows")
    result = {}
    if isinstance(rows, list):
        for index, row in enumerate(rows):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or row["id"] in result
            ):
                break
            result[row["id"]] = (index, row)
        else:
            return result
    raise AuthorityError(
        f"unsupported ledger shape: kind=ar-history/v1 keys={sorted(document)} path={path} hash={digest}; rows require distinct native IDs"
    )


def _ledger_documents(path: str, inventories: dict, evidence: Path) -> tuple[dict, dict]:
    docs, refs = {}, {}
    for phase in ("before", "after"):
        digest = inventories[phase].get(path)
        if digest is None:
            continue
        source = EvidenceFile(phase + "/" + path, digest)
        raw = source.verify(evidence).read_bytes()
        try:
            doc = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue  # Native schema, never a filename, identifies the ledger.
        if isinstance(doc, dict) and doc.get("schema") == "ar-history/v1":
            docs[phase], refs[phase] = doc, source.to_dict()
    return docs, refs


def _ledger_changes(
    report: dict, evidence: Path, neutralize: Callable[[str], str]
) -> dict[str, dict[str, Any]]:
    """Bind row deltas to actual ledger bytes; exact-copy aliases are not new rows."""
    inventories = {
        phase: {row["path"]: row["sha256"] for row in report[phase]}
        for phase in ("before", "after")
    }
    before, after = inventories["before"], inventories["after"]
    before_by_digest: dict[str, list[str]] = {}
    for name, digest in sorted(before.items()):
        before_by_digest.setdefault(digest, []).append(name)
    result = {}
    for path in sorted(before.keys() | after.keys()):
        if (
            not path.endswith(".json")
            or ".git" in Path(path).parts
            or before.get(path) == after.get(path)
        ):
            continue
        docs, refs = _ledger_documents(path, inventories, evidence)
        if not docs:
            continue
        if any(phase not in docs for phase in ("before", "after") if path in inventories[phase]):
            raise AuthorityError(
                f"unsupported ledger transition: kind=ar-history/v1 keys={sorted(docs)} path={path} hash={after.get(path) or before[path]}"
            )
        # A newly created scratch image can be an exact copy of an unchanged base ledger.
        if "before" not in docs and "after" in docs:
            aliases = before_by_digest.get(after[path], [])
            if aliases:
                source = EvidenceFile("before/" + aliases[0], after[path])
                source.verify(evidence)
                docs["before"], refs["before"] = docs["after"], source.to_dict()
        blinded = {
            phase: json.loads(neutralize(json.dumps(doc, ensure_ascii=False)))
            for phase, doc in docs.items()
        }
        rows = {
            phase: _ledger_rows(doc, path, refs[phase]["sha256"]) for phase, doc in blinded.items()
        }
        a, b = rows.get("before", {}), rows.get("after", {})
        changes = [
            {
                "id": identity,
                **{
                    phase: {
                        "present": identity in values,
                        "index": values[identity][0] if identity in values else None,
                        "value": values[identity][1] if identity in values else None,
                    }
                    for phase, values in (("before", a), ("after", b))
                },
            }
            for identity in sorted(a.keys() | b.keys())
            if a.get(identity) != b.get(identity)
        ]
        headers = {
            phase: {key: value for key, value in doc.items() if key != "rows"}
            for phase, doc in blinded.items()
        }
        old, new = headers.get("before", {}), headers.get("after", {})
        header_changes = {
            key: {
                phase: {"present": key in value, "value": value.get(key)}
                for phase, value in (("before", old), ("after", new))
            }
            for key in sorted(old.keys() | new.keys())
            if (key in old, old.get(key)) != (key in new, new.get(key))
        }
        result[path] = {
            "kind": "native-ledger-changes",
            "sourceFiles": {
                phase: {**ref, "path": neutralize(ref["path"])} for phase, ref in refs.items()
            },
            "documentSha256": {phase: json_digest(doc) for phase, doc in blinded.items()},
            "rowCounts": {"before": len(a), "after": len(b), "changed": len(changes)},
            "rowOrderSha256": {
                phase: json_digest(list(values)) for phase, values in (("before", a), ("after", b))
            },
            "headerChanges": header_changes,
            "changedRows": changes,
        }
    return result


def select_case_evidence(
    case: CaseBundle,
    capture: NativeCapture,
    report: dict,
    evidence_root: Path,
    *,
    neutralize: Callable[[str], str],
) -> EvidenceSelection:
    """Select by reviewed case duties and native references, never by output size or claimed success."""
    policy = POLICIES.get(case.case_id)
    grade = case.manifest["specification"]["prospectiveGrade"]
    rubric = grade["deterministicChecks"] + grade["semanticRubric"]
    if policy is None or json_digest(rubric) not in {
        policy.rubric_digest,
        *HISTORICAL_RUBRIC_DIGESTS.get(case.case_id, ()),
    }:
        raise AuthorityError(f"{case.case_id}: no reviewed evidence mapping for this exact rubric")
    if capture.unknowns:
        raise AuthorityError("evidence selection cannot establish the complete native action list")
    references = _references(case, report)
    actions = {}
    for event in capture.events:
        if event.kind != "tool_call":
            continue
        kinds = _action_kinds(event, references)
        if not kinds:
            continue
        identity = event.payload.get("call_id", event.payload.get("id"))
        if not isinstance(identity, str) or not identity:
            raise AuthorityError("selected native action has no exact call identity")
        actions[identity] = _merge(
            *(
                GENERAL_COMMANDS[case.case_id] if kind == "commands" else getattr(policy, kind)
                for kind in sorted(kinds)
            )
        )
    dependencies, pointers = {}, {}
    for path in REPORT_ENTRIES:
        before = _read_report(report, evidence_root, "before", path)
        after = _read_report(report, evidence_root, "after", path)
        changed = _changed_pointers(before, after, path)
        if changed:
            dependencies[path], pointers[path] = policy.reports, changed
    ledgers = _ledger_changes(report, evidence_root, neutralize)
    carriers = _file_criteria(case, capture, report, evidence_root, policy)
    return EvidenceSelection(
        case.case_id,
        actions,
        dependencies,
        pointers,
        _message_criteria(capture, policy),
        {path: indices for path, indices in carriers.items() if path not in ledgers},
        _grading_context(case, report, neutralize=neutralize),
        ledgers,
    )
