"""Materialize explicitly declared, hash-bound case inputs for the judge."""

from __future__ import annotations

import shutil
from pathlib import Path

from agents_remember.errors import AuthorityError

from .artifacts import EvidenceFile, child_path
from .fixtures import CaseBundle


def materialize_judge_inputs(case: CaseBundle, evidence_root: Path) -> tuple[EvidenceFile, ...]:
    """Keep judge-only authority separate from actor inputs and effect inventories."""
    paths = case.manifest.get("setup", {}).get("judgeInputs", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p for p in paths):
        raise AuthorityError("setup.judgeInputs must declare case-file path strings")
    if len(paths) != len(set(paths)):
        raise AuthorityError("setup.judgeInputs contains duplicate case-file paths")
    declared = {item.path: item for item in case.files}
    missing = [path for path in paths if path not in declared]
    if missing:
        raise AuthorityError(f"judge input is not a hash-bound case file: {missing}")
    originals = [(declared[path], declared[path].verify(case.path)) for path in paths]
    result = []
    for source, original in originals:
        item = EvidenceFile("judge-inputs/" + source.path, source.sha256)
        target = child_path(evidence_root, item.path)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        item.verify(evidence_root)
        result.append(item)
    return tuple(result)
