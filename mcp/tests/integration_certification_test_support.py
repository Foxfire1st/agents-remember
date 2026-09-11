"""Real atomic integration journal and exact generic profile fixture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agents_remember.certification.frozen_run.models import freeze_certification_run
from agents_remember.certification.repository_profiles.models import ProfileMode
from agents_remember.worktrees.modules.quality import certification_records, clean_executor, gate
from agents_remember.worktrees.modules.quality.certification_terminal import RecordedGateTerminal
from agents_remember.worktrees.modules.quality.report_publication_paths import (
    published_report_path_from_manifest,
)
from gate_certification_test_support import (
    _checkout_with_profile,
    _green_outcome_factory,
    _lane_for,
)
from repository_profile_test_support import AGENTS_REMEMBER_PROFILE_REFERENCE
from test_worktree_support import git


@dataclass(frozen=True)
class SelectedCodeFixture:
    target: gate.QualityGateTarget
    prepared: certification_records.PreparedCertificationRun
    terminals: tuple[RecordedGateTerminal, ...]

    def render(self) -> dict[str, object]:
        return gate.render_selected_code_certification(
            self.target,
            self.prepared,
            self.terminals,
            diff_base=git(self.target.code_worktree, "rev-parse", "HEAD"),
            plan=gate.QualityGatePlan(mode=self.prepared.frozen_run.repositoryPlan.mode),
        )


def selected_code_fixture(root: Path, *, mode: ProfileMode = "full") -> SelectedCodeFixture:
    """Issue real original objects and physical publications for explicit renderer tests."""
    code = _checkout_with_profile(root / "code")
    admitted, lane, candidate = _lane_for(code, mode)
    group = root / "enclosure"
    prepared = certification_records._persist_admission(
        certification_records.PreparedCertificationRun(
            group,
            freeze_certification_run(admitted.canonical, lane),
        )
    )
    request = clean_executor.CleanQualityRequest(
        code,
        group,
        "agents-remember",
        AGENTS_REMEMBER_PROFILE_REFERENCE,
        mode,
        git(code, "rev-parse", "HEAD"),
    )
    outcome = _green_outcome_factory(group, lane, candidate)(request)
    manifest = outcome.manifest
    assert manifest is not None
    payload = json.loads(
        published_report_path_from_manifest(
            group / "reports",
            manifest,
            manifest.result_decoder.artifactPath,
        ).read_bytes()
    )
    recorded = certification_records.record_published_generation(prepared, manifest, payload)
    assert recorded.as_payload()["refused"] == []
    return SelectedCodeFixture(
        gate.QualityGateTarget(code, group, "agents-remember", AGENTS_REMEMBER_PROFILE_REFERENCE),
        prepared,
        recorded.terminals,
    )


def structural_quality_references() -> dict[str, object]:
    """Wire-shape fixtures only; physical acceptance is tested by selected_code_fixture."""

    def reference(kind: str, number: int) -> dict[str, object]:
        digest = hashlib.sha256(f"{kind}-{number}".encode()).hexdigest()
        return {"kind": kind, "semanticDigest": digest, "contentSha256": digest, "sizeBytes": 1}

    publication = {"canonicalBytes": "{}", "contentSha256": hashlib.sha256(b"{}").hexdigest()}
    return {
        "frozenRun": reference("frozen-run", 0),
        "terminals": [
            {
                "gate": number,
                "result": reference("result-manifest", number),
                "certificate": reference("certificate", number),
                "publication": publication,
            }
            for number in range(1, 5)
        ],
    }
