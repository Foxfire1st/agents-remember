"""Case admission distinguishes Git effects from benign retained bookkeeping."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import capture_codex_jsonl
from agent_evals.artifacts import json_digest, manifest_files
from agent_evals.fixtures import CaseBundle
from agent_evals.graders.cases import CaseEvidence, grade_case_code
from agent_evals.trials import _effect_diff
from test_agent_eval_git_semantics import _git, _refresh, _repo, _snapshot


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("Git case admission must never launch a judge")

    monkeypatch.setattr("agent_evals.trials.run_native_judge", refuse)


def _before(tmp_path: Path) -> tuple[Path, Path]:
    working = _repo(tmp_path)
    evidence = tmp_path / "evidence"
    destination = evidence / "before/projects/memory"
    destination.parent.mkdir(parents=True)
    _snapshot(working, destination)
    return working, evidence


def _case(working: Path, evidence: Path) -> CaseEvidence:
    destination = evidence / "after/projects/memory"
    destination.parent.mkdir(parents=True)
    _snapshot(working, destination)
    manifest = {
        "specification": {
            "prospectiveGrade": {
                "deterministicChecks": ["Changes stay within admitted memory paths"],
                "semanticRubric": ["Memory remains true"],
            }
        }
    }
    case = CaseBundle(evidence, "CE01", manifest, ())
    native = evidence / "native.jsonl"
    native.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "git-admission"}}) + "\n"
    )
    capture = capture_codex_jsonl([native], "git-admission")
    assert not capture.unknowns
    attempt = {
        "case": case.case_id,
        "caseDigest": json_digest(manifest),
        "status": "captured-ungraded",
        "unknowns": [],
        "capture": capture.to_dict(),
        **{
            phase: [item.to_dict() for item in manifest_files(evidence / phase)]
            for phase in ("before", "after")
        },
    }
    # Allow an ordinary content edit so semantic Git effects, rather than its
    # changed working file, decide the commit/staging integration tests.
    return CaseEvidence(case, attempt, frozenset({"projects/memory/tracked.txt"}), evidence, ())


@pytest.mark.parametrize("unreachable_object", [False, True])
def test_case_admits_index_refresh_and_unreachable_objects(
    tmp_path: Path, unreachable_object: bool
) -> None:
    working, evidence = _before(tmp_path)
    _refresh(working)
    if unreachable_object:
        _git(working, "hash-object", "-w", "--stdin", content="unreachable integration object\n")
    case = _case(working, evidence)
    before = manifest_files(evidence / "before")
    after = manifest_files(evidence / "after")
    assert (evidence / "before/projects/memory/.git/index").read_bytes() != (
        evidence / "after/projects/memory/.git/index"
    ).read_bytes()
    grade = grade_case_code(case)
    assert grade.status == "needs_rubric", grade
    assert not any(".git" in path for path in case.admitted_paths)
    assert manifest_files(evidence / "before") == before
    assert manifest_files(evidence / "after") == after


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        ("commit", "HEAD commit"),
        ("branch", "refs including branches/tags/stash"),
        ("stash", "refs including branches/tags/stash"),
        ("stage", "staged entries"),
    ],
)
def test_case_refuses_real_git_effect_even_when_working_edit_is_admitted(
    tmp_path: Path, operation: str, reason: str
) -> None:
    working, evidence = _before(tmp_path)
    if operation == "branch":
        _git(working, "branch", "new-branch")
    else:
        (working / "tracked.txt").write_text("changed\n")
        if operation == "stash":
            _git(working, "stash", "push", "-m", "unadmitted stash")
        else:
            _git(working, "add", "tracked.txt")
            if operation == "commit":
                _git(working, "commit", "-m", "unadmitted commit")
    case = _case(working, evidence)
    grade = grade_case_code(case)
    assert grade.status == "fail", grade
    assert any(reason in question for question in grade.questions), grade
    assert any(reference.startswith("git-semantics:") for reference in grade.evidence)


def test_case_still_refuses_an_unadmitted_non_git_file(tmp_path: Path) -> None:
    working, evidence = _before(tmp_path)
    _refresh(working)
    (working / "unadmitted.txt").write_text("outside case scope\n")
    grade = grade_case_code(_case(working, evidence))
    assert grade.status == "fail", grade
    assert "Unadmitted changed path: projects/memory/unadmitted.txt" in grade.questions


@pytest.mark.parametrize("product_path", [None, "product.py", ".pytest_cache.py"])
def test_pytest_cache_does_not_hide_a_real_product_edit(
    tmp_path: Path, product_path: str | None
) -> None:
    working, evidence = _before(tmp_path)
    case = _case(working, evidence)
    cache = evidence / "after/projects/code/.pytest_cache"
    cache.mkdir(parents=True)
    (cache / ".gitignore").write_bytes(b"# Created by pytest automatically.\n*\n")
    (cache / "v/cache").mkdir(parents=True)
    (cache / "v/cache/lastfailed").write_bytes(
        b'{\n  "mcp/tests/test_role_first_message.py": true\n}'
    )
    if product_path is not None:
        (cache.parent / product_path).write_text("unadmitted product change\n")
    case.attempt["after"] = [item.to_dict() for item in manifest_files(evidence / "after")]
    grade = grade_case_code(case)
    if product_path is None:
        assert grade.status == "needs_rubric", grade
    else:
        assert grade.status == "fail", grade
        assert grade.questions == ("Unadmitted changed path: projects/code/" + product_path,)


def test_effect_diff_omits_binary_git_metadata_and_preserves_real_text_change(
    tmp_path: Path,
) -> None:
    working, evidence = _before(tmp_path)
    _refresh(working)
    _git(working, "hash-object", "-w", "--stdin", content="unreachable binary storage\n")
    (working / "tracked.txt").write_text("actual admitted content\n")
    case = _case(working, evidence)
    index = evidence / "after/projects/memory/.git/index"
    with pytest.raises(UnicodeDecodeError):
        index.read_text(encoding="utf-8")
    diff = _effect_diff(case.attempt, evidence)
    assert [row["path"] for row in diff["files"]] == ["projects/memory/tracked.txt"]
    assert "+actual admitted content" in diff["fullUnifiedDiff"]
    assert ".git/" not in diff["fullUnifiedDiff"]
    assert diff["fullDiffBytes"] == len(diff["fullUnifiedDiff"].encode())
    assert grade_case_code(case).status == "needs_rubric"


@pytest.mark.parametrize(
    ("case", "trial", "source_sha256", "prefix_hex"),
    [
        (
            "CE02",
            "51af485e-e734-49a1-9aaa-f7dc980b8df6",
            "202856c6c74a9231f0c69868ff1100f82fd15957e0a344421dedcecbe8f9a23b",
            "4449524300000002000000086ae3cf480e9a896d6ae3cf48",
        ),
        (
            "CE14",
            "c0659cbe-c069-45b0-bc2b-0ff8553ece4e",
            "e2d33a1624afa52a6b0bc51da8888b25acbe73c6841d9bd4a066c1e58c9542fd",
            "4449524300000002000000056ae3d9d31f17eefe6ae3d9d3",
        ),
    ],
)
def test_effect_diff_skips_actual_hermes_failure_byte_boundary(
    tmp_path: Path, case: str, trial: str, source_sha256: str, prefix_hex: str
) -> None:
    """Exact first 24 bytes of the retained before index, not a full Git index.

    Originals: ar-coordination/notes/261029_agent-evals/runs/20261029-old-text/
    hermes/{case}/{trial}/before/projects/memory/.git/index. The supplied SHA
    identifies that complete original; evidence below hashes its own slice.
    Both source attempts recorded the same strict UTF-8 error at byte 13.
    """
    assert len(source_sha256) == 64 and case and trial
    prefix = bytes.fromhex(prefix_hex)
    assert len(prefix) == 24
    for phase in ("before", "after"):
        index = tmp_path / phase / "projects/memory/.git/index"
        index.parent.mkdir(parents=True)
        index.write_bytes(prefix + (b"\0" if phase == "after" else b""))
        (index.parent.parent / "claim.md").write_text(phase + "\n")
    index = tmp_path / "before/projects/memory/.git/index"
    # This is the old effect-diff operation, reproduced against actual bytes.
    with pytest.raises(UnicodeDecodeError) as caught:
        index.read_text(encoding="utf-8")
    assert caught.value.start == 13
    assert caught.value.object[13] == 0xE3
    assert caught.value.reason == "invalid continuation byte"
    report = {
        phase: [item.to_dict() for item in manifest_files(tmp_path / phase)]
        for phase in ("before", "after")
    }
    result = _effect_diff(report, tmp_path)
    assert [row["path"] for row in result["files"]] == ["projects/memory/claim.md"]
    assert "-before\n+after\n" in result["fullUnifiedDiff"]
    assert ".git/index" not in result["fullUnifiedDiff"]
    assert index.read_bytes() == prefix


def _byte_effects(tmp_path: Path, before: bytes | None, after: bytes | None) -> dict:
    report = {}
    for phase, data in (("before", before), ("after", after)):
        directory = tmp_path / phase
        directory.mkdir()
        if data is not None:
            path = directory / "projects/memory/payload.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
        report[phase] = [item.to_dict() for item in manifest_files(directory)]
    return report


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"old\0payload", b"new\0payload"),
        (b"old\xffpayload", b"new\xfepayload"),
        (None, b"new\0payload"),
        (b"old\xffpayload", None),
        (b"plain text", b"new\xffbinary"),
        (b"old\xffbinary", b"plain text"),
        (b"a" * 16_384 + b"\0", b"a" * 16_384 + b"x"),
    ],
    ids=["nul", "invalid-utf8", "added", "removed", "text-to-binary", "binary-to-text", "late-nul"],
)
def test_effect_diff_retains_nongit_binary_hashes_without_text_decode(
    tmp_path: Path, before: bytes | None, after: bytes | None
) -> None:
    report = _byte_effects(tmp_path, before, after)
    result = _effect_diff(report, tmp_path)
    (row,) = result["files"]
    assert row["binary"] is True
    assert row["beforeBytes"] == (len(before) if before is not None else None)
    assert row["afterBytes"] == (len(after) if after is not None else None)
    assert row["before"] == (report["before"][0]["sha256"] if before is not None else None)
    assert row["after"] == (report["after"][0]["sha256"] if after is not None else None)
    assert row["diffBytes"] == 0
    assert result["fullUnifiedDiff"] == ""


def test_effect_diff_preserves_valid_multibyte_text(tmp_path: Path) -> None:
    report = _byte_effects(tmp_path, "old π 😀\n".encode(), "new π 😀\n".encode())
    result = _effect_diff(report, tmp_path)
    (row,) = result["files"]
    assert row["binary"] is False
    assert "-old π 😀\n+new π 😀\n" in result["fullUnifiedDiff"]
    assert row["diffBytes"] == len(result["fullUnifiedDiff"].encode("utf-8"))
