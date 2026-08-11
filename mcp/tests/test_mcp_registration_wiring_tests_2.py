from __future__ import annotations

from test_mcp_registration_wiring import RegistrationWiringTests


class RegistrationWiringTests2(RegistrationWiringTests):
    def test_worktree_start_defaults_to_a_real_light_task_start(self) -> None:
        recorder = self.invoke(
            "worktree_start",
            "agents_remember.mcp.registration.worktrees.worktree_start_payload",
            {
                "repo_id": "agents-remember",
                "task_name": "260731-EFA",
                "worktree_name": "efa-l2",
            },
        )

        _config, identity = recorder.args
        self.assertEqual(identity.workflow_kind, "light-task")
        execution = recorder.kwargs["execution"]
        self.assertEqual(
            [execution.dry_run, execution.skip_provider_setup, execution.retry_provider_setup],
            [False, False, False],
        )

    def test_worktree_attach_locates_the_task_by_reference(self) -> None:
        recorder = self.invoke(
            "worktree_attach",
            "agents_remember.mcp.registration.worktrees.worktree_attach_payload",
            {"repo_id": "agents-remember", "contract_path": "/tmp/contract.yaml"},
        )

        _config, task_ref = recorder.args
        self.assertEqual(task_ref.repo_id, "agents-remember")
        self.assertEqual(task_ref.contract_path, "/tmp/contract.yaml")

    def test_worktree_status_locates_the_task_by_reference(self) -> None:
        recorder = self.invoke(
            "worktree_status",
            "agents_remember.mcp.registration.worktrees.worktree_status_payload",
            {"repo_id": "agents-remember", "leaf_id": "260731-EFA-L2"},
        )

        _config, task_ref = recorder.args
        self.assertEqual(task_ref.repo_id, "agents-remember")
        self.assertEqual(task_ref.leaf_id, "260731-EFA-L2")

    def test_worktree_sync_takes_the_contract_path_directly(self) -> None:
        recorder = self.invoke(
            "worktree_sync",
            "agents_remember.mcp.registration.worktrees.worktree_sync_payload",
            {"contract_path": "/tmp/contract.yaml", "memory_sync_choice": "merge-memory"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"memory_sync_choice": "merge-memory", "dry_run": False})

    def test_closeout_preview_groups_the_three_commit_messages(self) -> None:
        recorder = self.invoke(
            "worktree_closeout_preview",
            "agents_remember.mcp.registration.closeout.worktree_closeout_preview_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "code_commit_message": "code msg",
                "memory_commit_message": "memory msg",
                "ledger_commit_message": "ledger msg",
            },
        )

        config, contract_path, messages = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(contract_path, "/tmp/contract.yaml")
        self.assertEqual(messages.code, "code msg")
        self.assertEqual(messages.memory, "memory msg")
        self.assertEqual(messages.ledger, "ledger msg")

    def test_closeout_apply_keeps_the_approval_separate_from_the_messages(self) -> None:
        """``CloseoutApproval`` is the gate-bearing half: the intent note and whether this
        is a preview. Folding it into the commit messages would let a dry run read as an
        approved apply."""
        recorder = self.invoke(
            "worktree_closeout_apply",
            "agents_remember.mcp.registration.closeout.worktree_closeout_apply_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "code_commit_message": "code msg",
                "intent_note": "approved by developer",
                "dry_run": True,
            },
        )

        config, contract_path, messages, approval = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(contract_path, "/tmp/contract.yaml")
        self.assertEqual(messages.code, "code msg")
        self.assertEqual(approval.intent_note, "approved by developer")
        self.assertIs(approval.dry_run, True)

    def test_worktree_integrate_defaults_to_a_fast_forward_only_landing(self) -> None:
        recorder = self.invoke(
            "worktree_integrate",
            "agents_remember.mcp.registration.closeout.worktree_integrate_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs["strategy"], "ff-only")
        self.assertIs(recorder.kwargs["dry_run"], False)

    def test_worktree_cleanup_tears_down_providers_by_default(self) -> None:
        recorder = self.invoke(
            "worktree_cleanup",
            "agents_remember.mcp.registration.closeout.worktree_cleanup_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": False, "teardown_providers": True})

    def test_worktree_abandon_refuses_dirty_worktrees_unless_forced(self) -> None:
        recorder = self.invoke(
            "worktree_abandon",
            "agents_remember.mcp.registration.closeout.worktree_abandon_payload",
            {"contract_path": "/tmp/contract.yaml"},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": False, "force": False})

    def test_task_reopen_takes_the_contract_path_and_a_preview_flag(self) -> None:
        recorder = self.invoke(
            "task_reopen",
            "agents_remember.mcp.registration.tasks.task_reopen_payload",
            {"contract_path": "/tmp/contract.yaml", "dry_run": True},
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        self.assertEqual(recorder.kwargs, {"dry_run": True})

    def test_lifecycle_finalize_task_groups_the_documents_it_ticks(self) -> None:
        recorder = self.invoke(
            "lifecycle_finalize_task",
            "agents_remember.mcp.registration.tasks.lifecycle_finalize_task_payload",
            {
                "contract_path": "/tmp/contract.yaml",
                "task_doc_path": "/tmp/leaf.json",
                "master_doc_path": "/tmp/master.json",
                "subtask_number": "2",
            },
        )

        self.assertEqual(recorder.args, (self.config, "/tmp/contract.yaml"))
        docs = recorder.kwargs["docs"]
        self.assertEqual(docs.task_doc_path, "/tmp/leaf.json")
        self.assertEqual(docs.master_doc_path, "/tmp/master.json")
        self.assertEqual(docs.subtask_number, "2")
        self.assertIs(recorder.kwargs["dry_run"], False)
        self.assertIs(recorder.kwargs["teardown_providers"], True)

    def test_task_doc_splits_the_document_target_from_the_edit(self) -> None:
        """Which document to edit is a different question from what the edit is; the two
        parameter objects keep the locator fields out of the edit payload."""
        recorder = self.invoke(
            "task_doc",
            "agents_remember.mcp.registration.tasks.task_doc_payload",
            {
                "repo_id": "agents-remember",
                "operation": "set_step",
                "task_name": "260731-EFA",
                "contract_path": "/tmp/contract.yaml",
                "slug": "efa",
                "step": {"id": "1", "title": "do it", "status": "done"},
                "fields": {"status": "in-progress"},
                "decision": {"at": "2026-07-31", "decision": "d", "rationale": "r"},
                "subtask": {"number": "2", "name": "leaf"},
                "section": {"heading": "Notes"},
                "dry_run": True,
            },
        )

        config, target = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(target.repo_id, "agents-remember")
        self.assertEqual(target.task_name, "260731-EFA")
        self.assertEqual(target.contract_path, "/tmp/contract.yaml")
        self.assertEqual(target.slug, "efa")

        self.assertEqual(recorder.kwargs["operation"], "set_step")
        self.assertIs(recorder.kwargs["dry_run"], True)
        edit = recorder.kwargs["edit"]
        self.assertEqual(edit.step, {"id": "1", "title": "do it", "status": "done"})
        self.assertEqual(edit.fields, {"status": "in-progress"})
        self.assertEqual(edit.decision, {"at": "2026-07-31", "decision": "d", "rationale": "r"})
        self.assertEqual(edit.subtask, {"number": "2", "name": "leaf"})
        self.assertEqual(edit.section, {"heading": "Notes"})

    def test_task_doc_leaves_every_edit_slot_unset_for_a_read(self) -> None:
        recorder = self.invoke(
            "task_doc",
            "agents_remember.mcp.registration.tasks.task_doc_payload",
            {"repo_id": "agents-remember", "operation": "get"},
        )

        edit = recorder.kwargs["edit"]
        self.assertEqual(
            [edit.fields, edit.step, edit.decision, edit.subtask, edit.section],
            [None, None, None, None, None],
        )

    def test_codex_benchmark_prepare_defaults_to_a_preview(self) -> None:
        """ "Defaults to dry_run=true because a real prepare clones repos" -- the default
        the docstring promises is on the preparation object, not lost in the wiring."""
        recorder = self.invoke(
            "codex_benchmark_prepare",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_prepare_payload",
        )

        self.assertEqual(recorder.args, (self.config,))
        selection = recorder.kwargs["selection"]
        self.assertEqual(selection.target, "all")
        self.assertEqual(selection.case_id, None)
        preparation = recorder.kwargs["preparation"]
        self.assertIs(preparation.dry_run, True)
        self.assertIs(preparation.force_clone, False)
        self.assertEqual(preparation.skill_exposure_mode, "copy")
        self.assertEqual(preparation.provider_timeout, 1800)

    def test_codex_benchmark_run_defaults_to_a_preview_in_codex_own_sandbox(self) -> None:
        recorder = self.invoke(
            "codex_benchmark_run",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_run_payload",
            {"case_id": "case-1"},
        )

        self.assertEqual(recorder.kwargs["selection"].case_id, "case-1")
        self.assertIs(recorder.kwargs["preparation"].dry_run, True)
        self.assertEqual(recorder.kwargs["run"].codex_sandbox, "default")

    def test_codex_benchmark_run_forwards_the_execution_knobs(self) -> None:
        recorder = self.invoke(
            "codex_benchmark_run",
            "agents_remember.mcp.registration.benchmarks.codex_benchmark_run_payload",
            {
                "target": "case",
                "case_id": "case-1",
                "prompt": "do the thing",
                "variant": "b",
                "repetitions": 3,
                "jobs": 2,
                "dry_run": False,
                "skip_prepare": True,
                "codex_sandbox": "danger-full-access",
            },
        )

        self.assertIs(recorder.kwargs["preparation"].dry_run, False)
        run = recorder.kwargs["run"]
        self.assertEqual(run.prompt, "do the thing")
        self.assertEqual(run.variant, "b")
        self.assertEqual(run.repetitions, 3)
        self.assertEqual(run.jobs, 2)
        self.assertIs(run.skip_prepare, True)
        self.assertEqual(run.codex_sandbox, "danger-full-access")

    def test_lifecycle_start_acts_on_the_ambient_lifecycle_not_the_config(self) -> None:
        """The registrar takes the config only to keep one signature; these six payloads
        act on the process-wide ambient lifecycle and must be called without it."""
        recorder = self.invoke(
            "lifecycle_start",
            "agents_remember.mcp.registration.lifecycle.lifecycle_start_payload",
        )

        self.assertEqual(recorder.args, ())
        self.assertEqual(recorder.kwargs, {})

    def test_lifecycle_resume_takes_no_arguments(self) -> None:
        recorder = self.invoke(
            "lifecycle_resume",
            "agents_remember.mcp.registration.lifecycle.lifecycle_resume_payload",
        )

        self.assertEqual(recorder.args, ())

    def test_lifecycle_turn_end_notification_forwards_the_summary(self) -> None:
        recorder = self.invoke(
            "lifecycle_turn_end_notification",
            "agents_remember.mcp.registration.lifecycle.lifecycle_turn_end_notification_payload",
            {"summary": "turn done"},
        )

        self.assertEqual(recorder.args, ("turn done",))

    def test_lifecycle_end_forwards_the_outcome(self) -> None:
        recorder = self.invoke(
            "lifecycle_end",
            "agents_remember.mcp.registration.lifecycle.lifecycle_end_payload",
            {"outcome": "completed"},
        )

        self.assertEqual(recorder.args, ("completed",))

    def test_switch_lifecycle_defaults_to_no_unsaved_answer(self) -> None:
        recorder = self.invoke(
            "switch_lifecycle",
            "agents_remember.mcp.registration.lifecycle.switch_lifecycle_payload",
        )

        self.assertEqual(recorder.args, (None,))

    def test_switch_lifecycle_forwards_the_unsaved_answer(self) -> None:
        recorder = self.invoke(
            "switch_lifecycle",
            "agents_remember.mcp.registration.lifecycle.switch_lifecycle_payload",
            {"on_unsaved": "save"},
        )

        self.assertEqual(recorder.args, ("save",))

    def test_lifecycle_phase_forwards_the_phase(self) -> None:
        recorder = self.invoke(
            "lifecycle_phase",
            "agents_remember.mcp.registration.lifecycle.lifecycle_phase_payload",
            {"phase": "build"},
        )

        self.assertEqual(recorder.args, ("build",))

    def test_lifecycle_gate_forwards_flat_fields_for_application_composition(
        self,
    ) -> None:
        recorder = self.invoke(
            "lifecycle_gate",
            "agents_remember.mcp.registration.gates.structural_lifecycle_gate_payload",
            {
                "kind": "plan-approval",
                "ask": {"kind": "decision", "question": "ship?"},
                "packet": {"summary": "the plan"},
                "required_decision": ["approve", "reject"],
                "evidence_refs": [{"path": "plan.md"}],
            },
        )

        config, request = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(
            [
                request.kind,
                request.ask,
                request.packet,
                request.required_decision,
                request.evidence_refs,
                request.wait,
            ],
            [
                "plan-approval",
                {"kind": "decision", "question": "ship?"},
                {"summary": "the plan"},
                ["approve", "reject"],
                [{"path": "plan.md"}],
                True,
            ],
        )
        self.assertEqual(recorder.kwargs, {})

    def test_lifecycle_gate_wait_false_raises_without_a_timeout(self) -> None:
        """A non-blocking raise has nothing to time out; the wait it passes must say so
        rather than inheriting ``GateWait``'s blocking default of 300 seconds."""
        recorder = self.invoke(
            "lifecycle_gate",
            "agents_remember.mcp.registration.gates.structural_lifecycle_gate_payload",
            {"kind": "master-handover-approval", "wait": False},
        )

        self.assertIs(recorder.args[1].wait, False)
        self.assertEqual(recorder.kwargs, {})

    def test_gate_decide_forwards_document_and_kind_without_gate_id(self) -> None:
        task_ref = {"repository": "repo-a", "path": "master/task.json"}
        recorder = self.invoke(
            "gate_decide",
            "agents_remember.mcp.registration.gates.structural_gate_decide_payload",
            {
                "task_document_ref": task_ref,
                "kind": "master-handover-approval",
                "decision": "approve",
                "note": "looks right",
            },
        )

        config, request = recorder.args
        self.assertIs(config, self.config)
        self.assertEqual(request.task_document_ref.model_dump(), task_ref)
        self.assertEqual(request.kind, "master-handover-approval")
        self.assertEqual(request.decision, "approve")
        self.assertEqual(request.note, "looks right")
        self.assertIsNone(request.evidence_refs)
        self.assertEqual(recorder.kwargs, {})

    def test_gate_list_defaults_to_the_ambient_lifecycle(self) -> None:
        recorder = self.invoke(
            "gate_list", "agents_remember.mcp.registration.gates.structural_gate_list_payload"
        )

        self.assertEqual(recorder.args, (self.config,))
        self.assertEqual(recorder.kwargs, {})
