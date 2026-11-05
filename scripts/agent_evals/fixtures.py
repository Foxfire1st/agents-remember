"""Admit D637's scripted cases and reset hash-bound decision inputs."""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError
from agents_remember.models.knowledge_files.canonical import canonical_text
from pnt_sandbox.environment import is_removed

from .artifacts import (
    EvidenceFile,
    child_path,
    json_digest,
    manifest_files,
    read_object,
    write_object,
)

MARKER = "project/agent-eval-fixture.json"
CASE_IDS = tuple(f"CE{number:02}" for number in range(1, 15))
SETUP_FILES = ("prompt", "authority", "queue", "reset", "expected", "baselineNotes")
CONSTRUCTION_LABEL = "cut from the record (D637)"
NATIVE_LAYOUT_BYTES = canonical_text(
    {"schema": "ar-memory-layout/v2", "conversion": "D637-eval-material"}
).encode("utf-8")


def _construction_gaps(construction: dict[str, Any]) -> list[str]:
    gaps = []
    if construction.get("kind") != CONSTRUCTION_LABEL or construction.get("authority") != "D637":
        gaps.append("D637 construction label/authority")
    if not construction.get("sourceEpisode"):
        gaps.append("source episode")
    trees = construction.get("startingTrees", {})
    for owner in ("code", "memory"):
        tree = trees.get(owner, {}).get("tree", "")
        if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree):
            gaps.append(owner + " starting tree")
    return gaps


def _grading_gaps(manifest: dict[str, Any]) -> list[str]:
    specification = manifest.get("specification", {})
    if not isinstance(specification, dict) or specification.get("id") != manifest["id"]:
        return ["case grading specification"]
    grade = specification.get("prospectiveGrade", {})
    if not isinstance(grade, dict) or not isinstance(grade.get("expected"), str):
        return ["case expected outcome"]
    return [
        "grading " + name
        for name in ("deterministicChecks", "semanticRubric")
        if not isinstance(grade.get(name), list)
        or not grade[name]
        or not all(isinstance(item, str) and item.strip() for item in grade[name])
    ]


@dataclass(frozen=True)
class CaseBundle:
    path: Path
    case_id: str
    manifest: dict[str, Any]
    files: tuple[EvidenceFile, ...]

    @classmethod
    def read(cls, path: Path) -> CaseBundle:
        manifest = read_object(path / "case.json")
        case_id = manifest["id"]
        if case_id not in CASE_IDS or manifest["schema"] != "ar-curator-eval-case/v1":
            raise AuthorityError(f"{path}: unsupported case identity; recover the approved CE case")
        files = tuple(EvidenceFile(**item) for item in manifest["files"])
        for item in files:
            item.verify(path)
        return cls(path, case_id, manifest, files)

    def admission_gaps(self) -> list[str]:
        """D637 replaces historical recovery; no replay-completeness claim is accepted."""
        construction = self.manifest.get("construction", {})
        setup = self.manifest.get("setup", {})
        declared = {item.path for item in self.files}
        gaps = _construction_gaps(construction) + _grading_gaps(self.manifest)
        for name in SETUP_FILES:
            if setup.get(name) not in declared:
                gaps.append("setup." + name)
        sources = {item["source"] for item in self.manifest["actorInputs"]}
        for name in ("prompt", "authority", "queue"):
            if setup.get(name) not in sources:
                gaps.append("actor input " + name)
        if sources & {setup.get("expected"), setup.get("baselineNotes")}:
            gaps.append("grader-only input exposed to actor")
        if not self.manifest["effectRoots"]:
            gaps.append("allowed effect roots")
        return gaps

    def require_admitted(self) -> None:
        gaps = self.admission_gaps()
        if self.manifest["state"] != "admitted" or gaps:
            raise AuthorityError(
                f"{self.case_id} is not admitted under D637 ({', '.join(gaps)}); "
                "supply the labelled construction and hashed scripted decision inputs"
            )


@dataclass(frozen=True)
class TrialFixture:
    root: Path
    identity: str
    case_id: str
    case_digest: str

    @property
    def actor_root(self) -> Path:
        return self.root / "project"

    @property
    def case_copy(self) -> Path:
        return self.root / "case"

    def environment(self, inherited: dict[str, str]) -> dict[str, str]:
        """Keep native login selectors; never copy credential files or parent AR identity."""
        env = {key: value for key, value in inherited.items() if not is_removed(key)}
        env.update(
            {
                "PASEO_HOME": str(self.root / "paseo"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
                "AR_DAGGER_AUTHORITY_ROOT": str(self.root / "authority"),
                "TMUX_TMPDIR": str(self.root / "tmux"),
                "GIT_OPTIONAL_LOCKS": "0",
                "PWD": str(self.actor_root),
            }
        )
        return env


def reset_case(case: CaseBundle, trials_root: Path) -> TrialFixture:
    """Every attempt gets a new root; never reuse a previous actor's state."""
    case.require_admitted()
    inventory = {item.path for item in manifest_files(case.path)}
    if inventory != {"case.json", *(item.path for item in case.files)}:
        raise AuthorityError("case folder has undeclared or missing files; bind every case file")
    identity = str(uuid.uuid4())
    root = trials_root / identity
    root.mkdir(parents=True, exist_ok=False)
    fixture = TrialFixture(root, identity, case.case_id, json_digest(case.manifest))
    write_object(root / MARKER, {"identity": identity, "case": case.case_id})
    try:
        shutil.copytree(case.path, fixture.case_copy)
        copied = CaseBundle.read(fixture.case_copy)
        if json_digest(copied.manifest) != fixture.case_digest:
            raise AuthorityError("case manifest changed during reset")
        _copy_inputs(copied, fixture.actor_root)
        for name in ("paseo", "config", "data", "state", "cache"):
            (root / name).mkdir(exist_ok=True)
        for name in ("projects", "reports"):
            (fixture.actor_root / name).mkdir(exist_ok=True)
        write_object(
            root / "reset.json",
            {
                "identity": identity,
                "caseDigest": fixture.case_digest,
                "construction": case.manifest["construction"],
                "inputs": [
                    EvidenceFile.capture(fixture.actor_root, item["destination"]).to_dict()
                    for item in case.manifest["actorInputs"]
                ],
            },
        )
    except (OSError, ValueError, KeyError):
        release_fixture(fixture)
        raise
    return fixture


def _copy_inputs(case: CaseBundle, root: Path) -> None:
    declared = {item.path: item for item in case.files}
    for item in case.manifest["actorInputs"]:
        source = declared[item["source"]].verify(case.path)
        destination = child_path(root, item["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(int(item.get("mode", "644"), 8))


def release_fixture(fixture: TrialFixture) -> None:
    """The measurement owner calls this after capture; evidence lives outside this root."""
    marker = read_object(fixture.root / MARKER)
    if marker != {"identity": fixture.identity, "case": fixture.case_id}:
        raise AuthorityError("fixture ownership marker changed; preserve it for owner diagnosis")
    shutil.rmtree(fixture.root)


def inspect_cases(root: Path) -> list[dict]:
    """Report each fixed population slot, including an absent case held by its owner."""
    observations = []
    for case_id in CASE_IDS:
        path = root / case_id
        if not (path / "case.json").exists():
            observations.append(
                {
                    "id": case_id,
                    "state": "absent",
                    "gaps": ["No D637 constructed case manifest. No trial is scheduled."],
                }
            )
        else:
            case = CaseBundle.read(path)
            observations.append(
                {"id": case_id, "state": case.manifest["state"], "gaps": case.admission_gaps()}
            )
    return observations
