"""Tests for git, atomic-publish, and task-document writer fitness functions.

The repository tests prove both real owners are the only offenders omitted from a
package-wide scan. Synthetic violations cover spawn/argv aliases and replace forms;
known-good fixtures pin neighbouring commands, data lists, string/datetime replacement,
computed argv, and other non-owner shapes. Offender tests require complete actionable
messages.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import pytest
from agents_remember.code_quality import single_owner

PACKAGE_ROOT = MCP_SRC / "agents_remember"


def _git(source: str) -> list[str]:
    """What the git rule reports for ``source``, as ``line [form]`` strings."""
    return [
        f"{offender.line} [{offender.form}]"
        for offender in single_owner.module_git_offenders(ast.parse(source), "fixture.py")
    ]


def _replace(source: str) -> list[str]:
    """What the atomic-write rule reports for ``source``."""
    return [
        f"{offender.line} [{offender.form}]"
        for offender in single_owner.module_replace_offenders(ast.parse(source), "fixture.py")
    ]


def _task_writers(source: str, module: str = "fixture.py") -> list[str]:
    """What the task-document writer census reports for one module."""
    return [
        f"{site.line} [{site.form}] {site.detail}"
        for site in single_owner.module_task_document_writer_sites(ast.parse(source), module)
    ]


class SingleOwnerPrimitiveTests(unittest.TestCase):
    """The armed checks run in the ordinary suite, so they run wherever it does."""

    @pytest.mark.fitness
    def test_only_the_kernel_git_runner_names_the_git_program(self) -> None:
        offenders = single_owner.git_program_offenders(PACKAGE_ROOT)
        self.assertEqual(
            [str(offender) for offender in offenders],
            [],
            msg=single_owner.report(
                offenders,
                headline=f"git is reached outside {single_owner.GIT_RUNNER_OWNER}",
                remediation=single_owner.GIT_REMEDIATION,
            ),
        )

    @pytest.mark.fitness
    def test_only_the_kernel_atomic_write_owner_reaches_the_replace_syscall(self) -> None:
        offenders = single_owner.os_replace_offenders(PACKAGE_ROOT)
        self.assertEqual(
            [str(offender) for offender in offenders],
            [],
            msg=single_owner.report(
                offenders,
                headline=f"os.replace is reached outside {single_owner.ATOMIC_WRITE_OWNER}",
                remediation=single_owner.ATOMIC_WRITE_REMEDIATION,
            ),
        )

    def test_task_document_writers_match_the_reviewed_authority_set(self) -> None:
        sites = single_owner.task_document_writer_sites(PACKAGE_ROOT)
        modules = {site.module for site in sites}
        self.assertEqual(modules, set(single_owner.TASK_DOCUMENT_WRITER_AUTHORITIES))
        offenders = single_owner.task_document_writer_offenders(PACKAGE_ROOT)
        self.assertEqual(
            [str(offender) for offender in offenders],
            [],
            msg=single_owner.report(
                offenders,
                headline="TaskDocument publication escaped its reviewed authorities",
                remediation=single_owner.TASK_DOCUMENT_WRITE_REMEDIATION,
            ),
        )

    @pytest.mark.fitness
    def test_both_owners_exist_and_are_the_files_the_rules_name(self) -> None:
        # A rule whose owner path is misspelled sweeps the owner too, finds its legitimate
        # use of the primitive, and reports an offender nobody can fix -- or, if the owner
        # is renamed and the constant is not, sweeps nothing and passes forever.
        for owner in (single_owner.GIT_RUNNER_OWNER, single_owner.ATOMIC_WRITE_OWNER):
            self.assertTrue((PACKAGE_ROOT / owner).is_file(), owner)

    def test_task_document_store_owner_exists_and_is_the_file_the_rule_names(self) -> None:
        owner = single_owner.TASK_DOCUMENT_STORE_OWNER
        self.assertTrue((PACKAGE_ROOT / owner).is_file(), owner)

    @pytest.mark.fitness
    def test_the_sweep_reaches_the_whole_package_except_runtime_assets(self) -> None:
        modules = single_owner.package_modules(PACKAGE_ROOT)
        relative = {path.relative_to(PACKAGE_ROOT).as_posix() for path in modules}
        self.assertIn(single_owner.GIT_RUNNER_OWNER, relative)
        self.assertIn("benchmarks/runner_modules/commands.py", relative)
        self.assertIn("serving/terminal_catalog.py", relative)
        # Docker/runtime images: they run outside this process and own their own tooling.
        self.assertFalse([name for name in relative if name.startswith("package_data/")])


@pytest.mark.fitness
class GitSweepReachTests(unittest.TestCase):
    """Every bypass the git rule claims to catch, planted and required to be caught."""

    def test_a_plain_subprocess_spawn_of_git_is_caught(self) -> None:
        self.assertEqual(
            _git("import subprocess\nsubprocess.run(['git', 'status'])\n"), ["2 [git spawn]"]
        )

    def test_a_spawn_imported_off_subprocess_is_still_a_spawn(self) -> None:
        self.assertEqual(
            _git("from subprocess import run\nrun(['git', 'status'])\n"), ["2 [git spawn]"]
        )

    def test_an_import_alias_is_followed_to_the_name_it_binds(self) -> None:
        source = "from subprocess import run as spawn\nspawn(['git', 'status'])\n"
        self.assertEqual(_git(source), ["2 [git spawn]"])

    def test_every_subprocess_entry_point_is_swept_not_only_run(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.Popen(['git', 'log'])\n"
            "subprocess.check_output(['git', 'log'])\n"
            "subprocess.check_call(['git', 'log'])\n"
            "subprocess.call(['git', 'log'])\n"
        )
        self.assertEqual(_git(source), [f"{line} [git spawn]" for line in (2, 3, 4, 5)])

    def test_a_path_qualified_git_is_git(self) -> None:
        posix = "import subprocess\nsubprocess.run(['/usr/bin/git', 'status'])\n"
        windows = "import subprocess\nsubprocess.run(['C:\\\\Git\\\\bin\\\\git.exe', 'status'])\n"
        self.assertEqual(_git(posix), ["2 [git spawn]"])
        self.assertEqual(_git(windows), ["2 [git spawn]"])

    def test_a_tuple_argv_is_an_argv(self) -> None:
        self.assertEqual(
            _git("import subprocess\nsubprocess.run(('git', 'status'))\n"), ["2 [git spawn]"]
        )

    def test_the_builder_l3_sanctioned_is_reported_even_with_no_spawn_in_sight(self) -> None:
        # The exact shape of the bypass this leaf closed: a function that returns a git argv,
        # spawned from another module through a name. No spawn-anchored sweep can see it.
        source = (
            "def git_command(*args):\n"
            "    return ['git', '-c', 'core.longpaths=true', '-c', 'safe.directory=*', *args]\n"
        )
        self.assertEqual(_git(source), ["2 [git argv]"])

    def test_an_argv_assembled_above_the_call_is_reported_at_its_construction(self) -> None:
        source = "import subprocess\nargv = ['git', 'status']\nsubprocess.run(argv)\n"
        self.assertEqual(_git(source), ["2 [git argv]"])

    def test_a_concatenated_argv_is_reported_at_its_literal_half(self) -> None:
        source = "import subprocess\nsubprocess.run(['git'] + extra)\n"
        self.assertEqual(_git(source), ["2 [git argv]"])

    def test_a_program_name_hidden_behind_a_constant_is_resolved(self) -> None:
        plain = "import subprocess\nBINARY = 'git'\nsubprocess.run([BINARY, 'status'])\n"
        annotated = "import subprocess\nBINARY: str = 'git'\nsubprocess.run([BINARY, 'status'])\n"
        chained = "import subprocess\nA = B = 'git'\nsubprocess.run([B, 'status'])\n"
        self.assertEqual(_git(plain), ["3 [git spawn]"])
        self.assertEqual(_git(annotated), ["3 [git spawn]"])
        self.assertEqual(_git(chained), ["3 [git spawn]"])

    def test_the_asyncio_spawns_are_swept_in_both_of_their_shapes(self) -> None:
        program = "import asyncio\nasyncio.create_subprocess_exec('git', 'status')\n"
        shell = "import asyncio\nasyncio.create_subprocess_shell('git status --porcelain')\n"
        self.assertEqual(_git(program), ["2 [git spawn]"])
        self.assertEqual(_git(shell), ["2 [git spawn]"])

    def test_a_shell_command_string_is_read_down_to_its_program_word(self) -> None:
        source = "import subprocess\nsubprocess.run('git status', shell=True)\n"
        self.assertEqual(_git(source), ["2 [git spawn]"])

    def test_a_git_argv_handed_to_a_local_runner_is_still_a_git_argv(self) -> None:
        # A module with its own `run`, or a `runner.run` that is not subprocess, is not a
        # spawn this sweep recognises -- but the argv it was handed is still one this
        # package must not be building outside the runner, and that is what gets reported.
        local = "def run(argv):\n    return argv\n\n\nrun(['git', 'status'])\n"
        attributed = "runner.run(['git', 'status'])\n"
        deep = "queue.runner.run(['git', 'status'])\n"
        self.assertEqual(_git(local), ["5 [git argv]"])
        self.assertEqual(_git(attributed), ["1 [git argv]"])
        self.assertEqual(_git(deep), ["1 [git argv]"])

    def test_a_spawn_and_its_own_argv_are_one_offender_not_two(self) -> None:
        # Reported twice, the fix looks twice as large as it is and the second entry is
        # unactionable. The argv a spawn consumed is attributed to the spawn.
        source = "import subprocess\nsubprocess.run(\n    ['git', 'status'],\n)\n"
        self.assertEqual(_git(source), ["2 [git spawn]"])


@pytest.mark.fitness
class GitSweepFalsePositiveTests(unittest.TestCase):
    """Known-good constructs the package really contains. None of these may be reported."""

    def test_gh_is_not_git(self) -> None:
        # worktrees/modules/landing.py: gh resolves the repository through git, so it takes
        # the same scrubbed environment -- and it is still not a git spawn.
        source = (
            "import subprocess\n"
            "subprocess.run(\n"
            "    ['gh', 'pr', 'list', '--head', head, '--json', 'number'],\n"
            "    cwd=repo,\n"
            "    env=git_environment(),\n"
            ")\n"
        )
        self.assertEqual(_git(source), [])

    def test_the_other_programs_this_package_spawns_are_not_git(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(['tmux', '-V'])\n"
            "subprocess.run(['docker', 'inspect', name])\n"
            "subprocess.run(['codex', 'exec'])\n"
            "subprocess.run(['claude', '--print'])\n"
            "subprocess.run(['sh', '-c', pipe])\n"
        )
        self.assertEqual(_git(source), [])

    def test_a_program_that_merely_starts_with_git_is_not_git(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(['gitk'])\n"
            "subprocess.run(['git-lfs', 'install'])\n"
            "subprocess.run(['/usr/bin/github'])\n"
        )
        self.assertEqual(_git(source), [])

    def test_git_as_a_mapping_key_or_value_is_not_an_argv(self) -> None:
        # kernel/memory_init.py renders `{"git": git, ...}` into a template.
        self.assertEqual(_git('payload = {"git": git, "branch": branch}\n'), [])

    def test_git_below_the_head_position_is_not_the_program(self) -> None:
        self.assertEqual(_git("import subprocess\nsubprocess.run(['gh', 'repo', 'git'])\n"), [])
        self.assertEqual(_git("labels = ['vcs', 'git']\n"), [])

    def test_a_splatted_program_is_not_read_as_git(self) -> None:
        # The four harness sites: claude_stream_transport, helper_host, pi_rpc_process,
        # codex_app_server_protocol all spawn `*argv` / `*launch.argv`.
        source = (
            "import asyncio\n"
            "await asyncio.create_subprocess_exec(*argv)\n"
            "await asyncio.create_subprocess_exec(*launch.argv, stdin=asyncio.subprocess.PIPE)\n"
        )
        self.assertEqual(_git(ast.unparse(ast.parse(source, mode="exec"))), [])

    def test_an_argv_that_is_a_bare_parameter_is_not_read(self) -> None:
        source = "import subprocess\ndef go(argv):\n    subprocess.run(argv)\n"
        self.assertEqual(_git(source), [])

    def test_degenerate_argv_does_not_crash_or_report(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run([])\n"
            "subprocess.run()\n"
            "subprocess.run([1, 'status'])\n"
            "subprocess.run('', shell=True)\n"
            "subprocess.run(command)\n"
            "handlers['run'](['git-lfs'])\n"
        )
        self.assertEqual(_git(source), [])

    def test_a_local_run_over_a_non_git_argv_is_left_alone(self) -> None:
        # The complement of the reach test above: the rule is about the program, not the
        # name of the function that was handed it.
        self.assertEqual(_git("def run(argv):\n    return argv\n\n\nrun(['gh', 'pr'])\n"), [])

    def test_a_non_string_or_attribute_binding_is_not_a_program_name(self) -> None:
        source = (
            "import subprocess\n"
            "TIMEOUT = 30\n"
            "config.binary = 'git'\n"
            "chosen = pick()\n"
            "subprocess.run([chosen, 'status'])\n"
        )
        self.assertEqual(_git(source), [])


@pytest.mark.fitness
class ReplaceSweepReachTests(unittest.TestCase):
    """Every way to reach the replace syscall, planted and required to be caught."""

    def test_the_module_attribute_form_is_caught(self) -> None:
        self.assertEqual(_replace("import os\nos.replace(tmp, path)\n"), ["2 [os.replace]"])

    def test_a_name_imported_from_os_is_caught(self) -> None:
        self.assertEqual(
            _replace("from os import replace\nreplace(tmp, path)\n"), ["2 [os.replace]"]
        )

    def test_an_import_alias_is_followed(self) -> None:
        source = "from os import replace as rename\nrename(tmp, path)\n"
        self.assertEqual(_replace(source), ["2 [os.replace]"])

    def test_the_path_method_form_is_caught_by_its_arity(self) -> None:
        # `Path.replace(target)` is the same syscall wearing a method. One positional
        # argument and no keywords is a signature `str.replace` cannot have.
        self.assertEqual(_replace("tmp.replace(path)\n"), ["1 [Path.replace]"])
        self.assertEqual(_replace("self._path.replace(other)\n"), ["1 [Path.replace]"])


@pytest.mark.fitness
class ReplaceSweepFalsePositiveTests(unittest.TestCase):
    """The 83 near neighbours measured in the package. None of these may be reported."""

    def test_string_substitution_is_not_the_replace_syscall(self) -> None:
        source = (
            "a = text.replace('a', 'b')\n"
            "b = text.replace('\\\\', '/')\n"
            "c = text.replace('a', 'b', 1)\n"
            "d = event.ts.replace('Z', '+00:00')\n"
        )
        self.assertEqual(_replace(source), [])

    def test_datetime_replace_is_keyword_only_and_therefore_not_reported(self) -> None:
        source = "a = parsed.replace(tzinfo=UTC)\nb = moment.replace(**changes)\n"
        self.assertEqual(_replace(source), [])

    def test_a_bare_replace_from_dataclasses_is_not_the_one_from_os(self) -> None:
        # observer/landing_state.py and serving/terminal_catalog.py both do exactly this.
        source = "from dataclasses import dataclass, replace\nrow = replace(entry, at=now)\n"
        self.assertEqual(_replace(source), [])
        self.assertEqual(_replace("from dataclasses import replace\nrow = replace(entry)\n"), [])

    def test_neighbouring_os_calls_are_not_the_replace_syscall(self) -> None:
        source = "import os\nos.rename(a, b)\nos.fsync(handle)\nos.getpid()\n"
        self.assertEqual(_replace(source), [])

    def test_a_call_that_is_neither_a_name_nor_an_attribute_is_skipped(self) -> None:
        self.assertEqual(_replace("handlers['replace'](tmp)\n"), [])

    def test_an_unrelated_bare_call_is_not_reported(self) -> None:
        self.assertEqual(_replace("row = build(entry)\n"), [])


class TaskDocumentWriterCensusTests(unittest.TestCase):
    """The production-writer census follows the import forms a new caller can use."""

    def test_direct_imports_and_aliases_are_caught(self) -> None:
        direct = "from agents_remember.tasks import write_task_docs\nwrite_task_docs(root, docs)\n"
        alias = (
            "from agents_remember.tasks.store import write_task_doc as publish\n"
            "publish(root, doc)\n"
        )
        self.assertEqual(
            _task_writers(direct), ["2 [TaskDocument writer] calls write_task_docs(...)"]
        )
        self.assertEqual(
            _task_writers(alias), ["2 [TaskDocument writer] calls write_task_doc(...)"]
        )

    def test_module_aliases_are_caught(self) -> None:
        tasks_alias = (
            "import agents_remember.tasks as task_api\ntask_api.write_task_docs(root, docs)\n"
        )
        store_alias = (
            "import agents_remember.tasks.store as store_api\nstore_api.write_task_doc(root, doc)\n"
        )
        self.assertEqual(
            _task_writers(tasks_alias),
            ["2 [TaskDocument writer] calls write_task_docs(...)"],
        )
        self.assertEqual(
            _task_writers(store_alias),
            ["2 [TaskDocument writer] calls write_task_doc(...)"],
        )

    def test_relative_import_aliases_are_caught(self) -> None:
        source = "from ..tasks.store import write_task_docs as publish\npublish(root, docs)\n"
        self.assertEqual(
            _task_writers(source, "worktrees/new_writer.py"),
            ["2 [TaskDocument writer] calls write_task_docs(...)"],
        )

    def test_reexport_without_a_call_and_unrelated_local_names_are_not_callers(self) -> None:
        reexport = "from agents_remember.tasks import write_task_docs\n"
        local = "def write_task_docs(root, docs):\n    return docs\nwrite_task_docs(root, docs)\n"
        self.assertEqual(_task_writers(reexport), [])
        self.assertEqual(_task_writers(local), [])


@pytest.mark.fitness
class OffenderReportTests(unittest.TestCase):
    """L6-R15: the message names every offender and the fix, or the check is unusable."""

    def test_a_clean_sweep_produces_no_message(self) -> None:
        self.assertEqual(single_owner.report([], headline="x", remediation="y"), "")

    def test_the_message_carries_the_whole_list_and_the_remediation(self) -> None:
        offenders = [
            single_owner.Offender("a/one.py", 12, "git spawn", "spawns 'git' directly"),
            single_owner.Offender("b/two.py", 40, "git argv", "builds a 'git' argv here"),
        ]
        message = single_owner.report(
            offenders, headline="git is reached outside the runner", remediation="call run_git"
        )
        self.assertIn("(2 found)", message)
        self.assertIn("a/one.py:12  [git spawn] spawns 'git' directly", message)
        self.assertIn("b/two.py:40  [git argv] builds a 'git' argv here", message)
        self.assertIn("remediation: call run_git", message)

    def test_both_remediations_name_the_owner_a_reader_has_to_call(self) -> None:
        self.assertIn("kernel.git_command.run_git", single_owner.GIT_REMEDIATION)
        self.assertIn("kernel.atomic_write", single_owner.ATOMIC_WRITE_REMEDIATION)


if __name__ == "__main__":
    unittest.main()
