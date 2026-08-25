from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import reopen as reopen_module
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from task_reopen_test_support import _completed_leaf_contract, _leaf_doc, _master_doc
from test_worktree_support import git, init_repo


def _completed_external_leaf_contract(root: Path):
    disabled = _completed_leaf_contract(root)
    memory_repo = root / "memory-a"
    memory_base = init_repo(memory_repo, "main")
    (memory_repo / "onboarding").mkdir()
    (memory_repo / "onboarding" / "fact.md").write_text("landed\n", encoding="utf-8")
    git(memory_repo, "add", "onboarding/fact.md")
    git(memory_repo, "commit", "-m", "memory content")
    memory_content = git(memory_repo, "rev-parse", "HEAD")
    write_ledger(
        memory_repo / "memory.md",
        create_initial_ledger("repo-a", disabled.integrated_code_commit, memory_content),
    )
    git(memory_repo, "add", "memory.md")
    git(memory_repo, "commit", "-m", "ledger mapping")
    ledger = git(memory_repo, "rev-parse", "HEAD")
    git(memory_repo, "branch", "super", ledger)
    git(memory_repo, "branch", "ar/01-demo-leaf", "super")

    contract = default_contract(
        ContractTask(
            name=disabled.task_name,
            repo_name=disabled.repo_name,
            coordination_root=disabled.coordination_root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(
            worktree_name="01-demo-leaf",
            leaf_id="260698-l1",
            lifecycle_id="LC-OLD",
        ),
        code=RepoBranchPlan(
            disabled.code_repo_path,
            "super",
            "ar/01-demo-leaf",
            disabled.code_base_commit,
        ),
        memory=RepoBranchPlan(
            memory_repo,
            "super",
            "ar/01-demo-leaf",
            memory_base,
        ),
    )
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=disabled.code_commit,
        memory_content_commit=memory_content,
        ledger_commit=ledger,
        integration_status="completed",
        integrated_code_commit=disabled.integrated_code_commit,
        integrated_memory_content_commit=memory_content,
        integrated_ledger_commit=ledger,
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    return contract


def _reopen_artifacts(contract, leaf_path: Path, master_path: Path) -> tuple[Path, ...]:
    landing = contract.contract_path.parent / "landing-final.json"
    landing.write_text('{"finished": true}\n', encoding="utf-8")
    return (
        contract.contract_path,
        leaf_path,
        leaf_path.with_suffix(".md"),
        master_path,
        master_path.with_suffix(".md"),
        landing,
    )


class ReopenPublicationAuthorityTests(unittest.TestCase):
    def test_locked_publication_rechecks_exact_code_and_memory_source_tips(self) -> None:
        for side in ("code", "memory"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                contract = (
                    _completed_leaf_contract(root)
                    if side == "code"
                    else _completed_external_leaf_contract(root)
                )
                leaf_path = _leaf_doc(contract.task_root)
                master_path = _master_doc(contract.task_root)
                paths = _reopen_artifacts(contract, leaf_path, master_path)
                before = {path: path.read_bytes() for path in paths}

                def race_then_publish(
                    _coordination_root,
                    _repo_id,
                    *,
                    validate,
                    _contract_snapshot=contract,
                    _side=side,
                    **_kwargs,
                ):
                    repo = (
                        _contract_snapshot.code_repo_path
                        if _side == "code"
                        else _contract_snapshot.memory_repo_path
                    )
                    assert repo is not None
                    git(repo, "checkout", "super")
                    (repo / f"{_side}-race.txt").write_text("raced\n", encoding="utf-8")
                    git(repo, "add", f"{_side}-race.txt")
                    git(repo, "commit", "-m", f"advance {_side} source")
                    return validate()

                with mock.patch.object(
                    reopen_module,
                    "publish_task_fact_mutation",
                    side_effect=race_then_publish,
                ):
                    result = reopen_task(contract.contract_path)

                self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
                self.assertIn("Reopen refused", str(result.payload["summary"]))
                self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_locked_publication_replans_and_preserves_raced_leaf_or_master_docs(self) -> None:
        for changed_doc in ("leaf", "master"):
            with self.subTest(changed_doc=changed_doc), tempfile.TemporaryDirectory() as tmp:
                contract = _completed_leaf_contract(Path(tmp))
                leaf_path = _leaf_doc(contract.task_root)
                master_path = _master_doc(contract.task_root)
                paths = _reopen_artifacts(contract, leaf_path, master_path)
                before = {path: path.read_bytes() for path in paths}
                raced: dict[Path, bytes] = {}

                def race_then_publish(
                    _coordination_root,
                    _repo_id,
                    *,
                    validate,
                    _changed_doc=changed_doc,
                    _leaf_path=leaf_path,
                    _master_path=master_path,
                    _contract_snapshot=contract,
                    _raced=raced,
                    **_kwargs,
                ):
                    path = _leaf_path if _changed_doc == "leaf" else _master_path
                    data = read_task_doc(path).model_dump(by_alias=True)
                    if _changed_doc == "leaf":
                        data["master"] = "missing-master.md"
                    else:
                        data["subTasks"] = []
                    write_task_doc(
                        _contract_snapshot.task_root,
                        TaskDocument.model_validate(data),
                    )
                    _raced[path] = path.read_bytes()
                    _raced[path.with_suffix(".md")] = path.with_suffix(".md").read_bytes()
                    return validate()

                with mock.patch.object(
                    reopen_module,
                    "publish_task_fact_mutation",
                    side_effect=race_then_publish,
                ):
                    result = reopen_task(contract.contract_path)

                self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
                self.assertIn("task-document-reset", str(result.payload["summary"]))
                selected = leaf_path if changed_doc == "leaf" else master_path
                self.assertEqual(selected.read_bytes(), raced[selected])
                self.assertEqual(
                    selected.with_suffix(".md").read_bytes(),
                    raced[selected.with_suffix(".md")],
                )
                unchanged = {
                    path: payload
                    for path, payload in before.items()
                    if path not in {selected, selected.with_suffix(".md")}
                }
                self.assertEqual({path: path.read_bytes() for path in unchanged}, unchanged)

    def test_locked_publication_refuses_a_contract_changed_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            leaf_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            paths = _reopen_artifacts(contract, leaf_path, master_path)
            before_noncontract = {path: path.read_bytes() for path in paths[1:]}
            raced: dict[str, bytes] = {}

            def race_then_publish(
                _coordination_root,
                _repo_id,
                *,
                validate,
                **_kwargs,
            ):
                write_contract(
                    contract.contract_path,
                    replace(contract, lifecycle_id="LC-RACED"),
                )
                raced["contract"] = contract.contract_path.read_bytes()
                return validate()

            with mock.patch.object(
                reopen_module,
                "publish_task_fact_mutation",
                side_effect=race_then_publish,
            ):
                result = reopen_task(contract.contract_path)

            self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
            self.assertIn("contract changed", str(result.payload["summary"]))
            self.assertEqual(contract.contract_path.read_bytes(), raced["contract"])
            self.assertEqual(
                {path: path.read_bytes() for path in paths[1:]},
                before_noncontract,
            )

    def test_external_reopen_requires_exact_reachable_integrated_ledger_mapping(self) -> None:
        for defect in ("wrong-mapping", "unreachable-content"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                contract = _completed_external_leaf_contract(Path(tmp))
                _leaf_doc(contract.task_root)
                _master_doc(contract.task_root)
                assert contract.memory_repo_path is not None
                repo = contract.memory_repo_path
                git(repo, "checkout", "super")
                expected_content = contract.integrated_memory_content_commit
                if defect == "wrong-mapping":
                    mapped_content = contract.memory_base_commit
                else:
                    tree = git(repo, "rev-parse", f"{expected_content}^{{tree}}")
                    mapped_content = git(repo, "commit-tree", tree, "-m", "unreachable content")
                    expected_content = mapped_content
                write_ledger(
                    repo / "memory.md",
                    create_initial_ledger(
                        contract.repo_name,
                        contract.integrated_code_commit,
                        mapped_content,
                    ),
                )
                git(repo, "add", "memory.md")
                git(repo, "commit", "-m", f"forge {defect}")
                forged_ledger = git(repo, "rev-parse", "HEAD")
                forged = replace(
                    contract,
                    integrated_memory_content_commit=expected_content,
                    integrated_ledger_commit=forged_ledger,
                )
                write_contract(forged.contract_path, forged)
                before = forged.contract_path.read_bytes()

                result = reopen_task(forged.contract_path)

                self.assertEqual((result.returncode, result.payload["state"]), (2, "blocked"))
                self.assertIn("integrated-memory-landing", str(result.payload["blockers"]))
                self.assertEqual(forged.contract_path.read_bytes(), before)
