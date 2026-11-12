"""The census route delivers the complete population, not only a bounded answer (MIK-R198 rule 1).

Carried finding `L191-PRE-03`: a valid corpus with more targets than the bound produced ``targets: []``
and dropped the per-sidecar totals, with no full-artifact path or continuation. These cases hold the
route to the acceptance it names: the bounded answer advertises how many rows it omitted and names the
exact, retrievable, complete artifact those rows are in, and that artifact reconciles with the answer.

The census itself is stubbed where the case is about delivery, and exercised for real where the case is
about the artifact's content: the arithmetic between the answer and the artifact is what the finding
was about, and a synthetic population makes the over-bound case exact instead of lucky.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import census_artifacts, memory_tools
from agents_remember.application.memory_tools import (
    CENSUS_TARGET_ROWS,
    citation_census_tool,
)
from agents_remember.cli import memory_citations

REPO_ID = "demo"


def _row(index: int) -> dict[str, Any]:
    return {
        "sidecar": f"onboarding/pkg/m{index}.py.json",
        "field": "references.1.targets.0",
        "kind": "code",
        "path": f"pkg/m{index}.py",
        "locator": "symbol:alpha",
        "recordedBlob": "a" * 40,
        "actualBlob": "a" * 40,
        "recordedContent": "sha256:" + "1" * 64,
        "actualContent": "sha256:" + "2" * 64,
        "state": "stale:content",
        "repair": "judgment",
        "reason": "the located bytes differ from the recorded content",
    }


def _census(count: int) -> dict[str, Any]:
    rows = [_row(index) for index in range(count)]
    return {
        "ok": False,
        "schema": "ar-reference-census/v1",
        "pair": {"code": "/tmp/code", "memory": "/tmp/memory"},
        "denominator": count,
        "currentCount": 0,
        "debtCount": count,
        "states": {"stale:content": count},
        "repairs": {"judgment": count},
        "targets": rows,
        "sidecars": {row["sidecar"]: 1 for row in rows},
        "sidecarCount": count,
        "documents": [],
        "judgmentDocuments": [row["sidecar"] for row in rows],
        "unreadableSidecars": [],
        "routes": {"pkg": {"targets": count, "states": {"stale:content": count}}},
        "routeCount": 1,
    }


def _scope(root: Path) -> SimpleNamespace:
    memory = root / "memory"
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        repo_id=REPO_ID,
        onboarding_root=memory / "onboarding",
        code_root=root / "code",
        contract=None,
    )


def _config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(coordination_root=root / "coord")


def _call(root: Path, population: int, **kwargs: Any) -> dict[str, Any]:
    config = cast(Any, _config(root))
    with (
        mock.patch.object(memory_tools, "_memory_scope", return_value=_scope(root)),
        mock.patch.object(memory_tools.reference_state, "is_converted_memory", return_value=True),
        mock.patch.object(
            memory_tools,
            "measured_census",
            return_value=(
                _census(population),
                census_artifacts.CensusInputs(
                    identity=census_artifacts.pair_identities(root / "memory", root / "code"),
                    stable=True,
                ),
            ),
        ),
    ):
        return citation_census_tool(config, repo_id=REPO_ID, contract_path="contract.md", **kwargs)


def test_an_over_bound_population_is_written_whole_and_the_answer_names_it(
    tmp_path: Path,
) -> None:
    population = CENSUS_TARGET_ROWS + 1

    answer = _call(tmp_path, population)

    assert answer["targetsReturned"] == CENSUS_TARGET_ROWS
    assert answer["targetsOmitted"] == 1
    assert answer["artifactTargets"] == population
    artifact = Path(answer["artifactPath"])
    assert artifact.is_file(), answer["artifactPath"]
    written = json.loads(artifact.read_text(encoding="utf-8"))
    assert len(written["targets"]) == population, "the artifact is the whole population"
    assert written["sidecars"] and written["denominator"] == population
    assert answer["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert answer["artifactPath"].startswith((tmp_path / "coord").as_posix())


def test_the_answer_and_its_artifact_reconcile_at_every_boundary(tmp_path: Path) -> None:
    for population, expected_omitted in (
        (1, 0),
        (CENSUS_TARGET_ROWS, 0),
        (CENSUS_TARGET_ROWS + 7, 7),
    ):
        answer = _call(tmp_path / f"p{population}", population)
        assert answer["denominator"] == population
        assert answer["targetsReturned"] + answer["targetsOmitted"] == answer["denominator"]
        assert answer["targetsOmitted"] == expected_omitted
        written = json.loads(Path(answer["artifactPath"]).read_text(encoding="utf-8"))
        assert len(written["targets"]) == answer["denominator"]
        assert (
            written["routes"]
            and sum(route["targets"] for route in written["routes"].values())
            == answer["denominator"]
        )


def test_a_named_artifact_path_is_honoured_and_one_outside_the_root_is_refused(
    tmp_path: Path,
) -> None:
    named = tmp_path / "coord" / "worklist.json"
    named.parent.mkdir(parents=True, exist_ok=True)

    answer = _call(tmp_path, CENSUS_TARGET_ROWS + 2, artifact_path=named.as_posix())

    assert answer["artifactPath"] == named.as_posix()
    assert len(json.loads(named.read_text(encoding="utf-8"))["targets"]) == CENSUS_TARGET_ROWS + 2

    with pytest.raises(Exception) as refused:
        _call(tmp_path, 3, artifact_path="/etc/not-ours.json")
    assert "artifact_path" in str(refused.value) or "coordination" in str(refused.value)


def test_two_populations_in_one_second_keep_two_retrievable_addresses(tmp_path: Path) -> None:
    """A default artifact address keeps resolving to the bytes it named, even across a collision.

    Two different populations measured in the same formatted second used to choose one name and
    replace each other, so the first answer's advertised digest no longer resolved. The address now
    carries the digest of its own bytes: both answers are retrievable, each at its own address.
    """

    first = _call(tmp_path, 5)
    second = _call(tmp_path, 9)

    assert first["artifactPath"] != second["artifactPath"], "one population may not replace another"
    for answer, population in ((first, 5), (second, 9)):
        artifact = Path(answer["artifactPath"])
        assert artifact.is_file()
        assert answer["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert len(json.loads(artifact.read_text(encoding="utf-8"))["targets"]) == population


def test_re_measuring_the_same_population_resolves_the_artifact_it_already_wrote(
    tmp_path: Path,
) -> None:
    first = _call(tmp_path, 4)
    second = _call(tmp_path, 4)

    assert second["artifactPath"] == first["artifactPath"]
    assert second["artifactSha256"] == first["artifactSha256"]
    stored = list((tmp_path / "coord" / "temp" / "census").glob("*-census-*.json"))
    assert len(stored) == 1, "an identical re-measurement is not a second artifact"


def _write_artifact(
    root: Path, population: int, artifact_path: str | None = None
) -> dict[str, Any]:
    """The actual storage producer, without process-global census mocks in concurrent tests."""

    return census_artifacts.write_census_artifact(
        cast(Any, _config(root)),
        _census(population),
        artifact_path,
        repo_id=REPO_ID,
        inputs=census_artifacts.CensusInputs(identity={}, stable=True),
    )


def test_the_artifact_store_is_bounded_and_reclaims_oldest_first(tmp_path: Path) -> None:
    """Concurrent writes keep a bounded store and name every retired receipt at both populations."""

    keep = census_artifacts.CENSUS_ARTIFACT_KEEP
    for population in (5, keep + 5):
        root = tmp_path / str(population)
        folder = root / "coord" / "temp" / "census"
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(partial(_write_artifact, root), range(population)))
        stored = {path.name for path in folder.glob("*-census-*.json")}
        ledger = json.loads((folder / census_artifacts.OWNED_ARTIFACTS).read_text())
        retired = {name for answer in answers for name in answer["artifactRetention"]["pruned"]}
        assert len(stored) == min(population, keep)
        assert set(ledger) == stored
        assert len(retired) == max(0, population - keep)
        for answer in answers:
            artifact = Path(answer["artifactPath"])
            assert answer["artifactRetention"]["keep"] == keep
            assert answer["artifactRetention"]["store"] == folder.as_posix()
            if artifact.name in stored:
                assert answer["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
            else:
                assert artifact.name in retired, "every expired receipt has named retirement"


def test_the_artifact_carries_the_measured_identities_of_both_trees(tmp_path: Path) -> None:
    """The pair is named by what was measured -- commit and work-tree tree -- not by a directory path."""

    code = tmp_path / "codegit"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    (code / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(code), "add", "a.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(code),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@x",
            "commit",
            "-qm",
            "one",
        ],
        check=True,
    )
    (code / "a.txt").write_text("two\n", encoding="utf-8")

    identity = census_artifacts.pair_identities(tmp_path / "memory-absent", code)

    assert identity["code"]["kind"] == "git"
    assert len(identity["code"]["commit"]) == 40
    assert len(identity["code"]["worktreeTree"]) == 40
    assert identity["memory"] == {"kind": "absent"}
    answer = _call(tmp_path, 2)
    assert set(answer["artifactInputIdentities"]) == {"code", "memory"}


def test_the_cli_refuses_a_census_without_a_contract_by_name(tmp_path: Path) -> None:
    """A malformed invocation is a named refusal, not an assertion from inside the product.

    ``--census`` is a leaf read of a pair a contract encloses, so a census with no contract is
    refused before any work: the command prints the reason and exits 1. It used to pass admission
    and then raise ``AssertionError`` where only ``ValueError`` was caught.
    """

    parser = argparse.ArgumentParser()
    memory_citations.add_arguments(parser)
    args = parser.parse_args(["--repo", REPO_ID, "--census", "--config", "settings.json"])
    with mock.patch.object(
        memory_citations, "load_config", side_effect=AssertionError("must not be reached")
    ):
        status = memory_citations.run(args)

    assert status == 1


def test_a_relative_root_is_measured_the_same_as_an_absolute_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identity that depends on the caller's working directory is not an identity.

    Git is entered *at* the root it is given, so a relative root reaches it as a path it cannot enter
    and the measurement silently degrades to a content digest. The same tree measured by a relative
    and by an absolute path must answer the same commit and the same working-tree tree.
    """

    code = tmp_path / "git-relative"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    (code / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(code), "add", "a.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(code),
            "-c",
            "user.name=F",
            "-c",
            "user.email=f@x",
            "commit",
            "-qm",
            "one",
        ],
        check=True,
    )

    absolute = census_artifacts.tree_identity(code)
    monkeypatch.chdir(tmp_path)
    relative = census_artifacts.tree_identity(Path("git-relative"))

    assert absolute["kind"] == relative["kind"] == "git"
    assert absolute == relative


def test_a_named_path_holding_other_bytes_is_replaced_so_the_digest_resolves(
    tmp_path: Path,
) -> None:
    """The advertised digest must be the file's digest — a stale file at the same path is not reused."""

    named = tmp_path / "coord" / "worklist.json"
    named.parent.mkdir(parents=True, exist_ok=True)
    named.write_text('{"stale": true}\n', encoding="utf-8")

    answer = _call(tmp_path, 3, artifact_path=named.as_posix())

    assert answer["artifactSha256"] == hashlib.sha256(named.read_bytes()).hexdigest()
    assert len(json.loads(named.read_text(encoding="utf-8"))["targets"]) == 3


def test_a_caller_named_artifact_in_the_store_folder_is_never_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default cleanup honors caller ownership, including a former default's transfer to the caller."""

    keep = census_artifacts.CENSUS_ARTIFACT_KEEP
    monkeypatch.setattr(census_artifacts.time, "strftime", lambda *_args: "20260101T000000Z")
    first = _write_artifact(tmp_path, 3)
    promoted = _write_artifact(tmp_path, 3, first["artifactPath"])
    folder = Path(first["artifactPath"]).parent
    named = _write_artifact(tmp_path, 7, (folder / "caller-census.json").as_posix())
    # Re-measuring a promoted default must not silently take ownership back.
    repeated = _write_artifact(tmp_path, 3)
    assert repeated["artifactPath"] == first["artifactPath"]
    # Replacing a former default intentionally is legal. A later default must preserve its new bytes.
    former = _write_artifact(tmp_path, 8)
    replaced = _write_artifact(tmp_path, 9, former["artifactPath"])
    rerouted = _write_artifact(tmp_path, 8)
    assert rerouted["artifactPath"] != replaced["artifactPath"]
    receipts = (promoted, named, replaced)

    pruned: set[str] = set()
    checkpoints: list[tuple[int, int, int]] = []
    for start, population in ((0, 5), (5, keep + 5)):
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(
                pool.map(
                    lambda index: _write_artifact(tmp_path, 100 + index), range(start, population)
                )
            )
        pruned.update(name for answer in answers for name in answer["artifactRetention"]["pruned"])
        ledger = json.loads((folder / census_artifacts.OWNED_ARTIFACTS).read_text())
        checkpoints.append((population, len(ledger), len(list(folder.glob("*.json")))))
        for receipt in receipts:
            artifact = Path(receipt["artifactPath"])
            assert artifact.is_file(), f"caller artifact reclaimed at {population} defaults"
            assert artifact.name not in pruned
            assert receipt["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
            assert receipt["artifactRetention"]["lifetime"] == "owned by the caller"
    assert checkpoints == [(5, 6, 10), (keep + 5, keep, keep + 4)]
    assert pruned, "the default calls must actually exercise reclamation"
