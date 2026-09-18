"""Instrument-discipline checks, each proved against the historical case it was written for.

Every constant below is **evidence**, transcribed from the record of
`260915_role-capsules-and-native-eve` at the path given in its comment — except `E13_CRASHED`, whose
file was overwritten in place by its own repair and which is therefore a reconstruction in the terms
of the verdict that quotes it, labelled as such where it is defined. The cases then assert the
two directions that matter: the shipped function refuses the historical artifact, and it accepts the
artifact that replaced it. A check with only the refusal direction is a check that cannot be shown
to be non-vacuous, which is the fault this module exists to catch.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from agents_remember_test_support.code_quality import instrument_discipline as discipline

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# `notes/reports/260915-CAPS-L23-evidence/memory-refresh-finding-21-routes.json` (object read from the
# published evidence package; `message` is 1,343 characters with 21 commas, `route_count` is 21).
ATTESTATION_MESSAGE = (
    "external-memory refresh validation failed; route overview: external-memory closeout requires "
    "updated route overview content for routes whose governed sources changed; the following "
    "governing route overviews have a body update without a new Update History entry: ., "
    "dashboard/src, dashboard/src/data, docs/design, mcp/src/agents_remember/application, "
    "mcp/src/agents_remember/certification, mcp/src/agents_remember/mcp/registration, "
    "mcp/src/agents_remember/models, mcp/src/agents_remember/models/closeout, "
    "mcp/src/agents_remember/models/lifecycles, mcp/src/agents_remember/serving, "
    "mcp/src/agents_remember/serving/conversation/library, "
    "mcp/src/agents_remember/worktrees/integration, "
    "mcp/src/agents_remember/worktrees/integration/closeout/preparation, "
    "mcp/src/agents_remember/worktrees/integration/lifecycle/generation, "
    "mcp/src/agents_remember/worktrees/queue, "
    "mcp/test_support/agents_remember_test_support/code_quality, scripts/e2e_harness, "
    "skills/c-09-git-worktree-manager, skills/l-01-agent-lifecycles, "
    "skills/l-01-agent-lifecycles/roles. Update each overview body through the "
    "c-05-create-or-update-onboarding-files skill and record the change in its Update History, or "
    "publish an exact candidate-bound no-route-impact judgment through curator_coherence. Advancing "
    "lastVerifiedCommitHash on stale content is a prohibited metadata-only refresh."
)

# The checklist row that carries that message, as rendered (line 28 of
# `notes/reports/260915-CAPS-L23-curator-evidence/daemon-settled-rendered-checklist.md`). The table has
# no blank lines between its rows, which is why a capture that runs to the next blank line swallows them.
ATTESTATION_ROW = f"| memory-refresh-attestations |  | memory-refresh-attestation-failed | {ATTESTATION_MESSAGE} |"
# The two rows immediately below it, kept `|`-separated like the real table. Their prose carries the
# commas whose capture is the fault: a window that does not stop at the row boundary counts them.
CHECKLIST_TAIL = "\n".join(
    (
        "| --- | --- | --- | --- |",
        ATTESTATION_ROW,
        "| style.citations.claim_reopen | mcp/src/agents_remember/install/assets.py.md | "
        "citation_claim_reopened | evidence changed after verification, so the claim reopens |",
        "| style.citations.claim_reopen | mcp/src/agents_remember/memory_quality/"
        "incremental_scope/owners.py.md | citation_provenance_invalid | provenance invalid, "
        "verified at a different revision |",
    )
)

# A row of the `n/a`-link family, verbatim from `notes/orchestrator-checks.md` (2026-09-18T03:5x).
NA_LINK_ROW = (
    "| Checkpoint responses record only the real integrated code and memory commits. "
    "| n/a | [path](path) |"
)
# The pattern the owning seat ran, which returned 0 and was reported as "the family is not live".
NA_FAULTY_PATTERN = r"^\| *Finding *\| *n/a *\|"
# The pattern the curator ran instead, which returned 645 rows across 135 documents.
NA_CORRECTED_PATTERN = r"^\| .* \| (n/a|N/A|-) \| \[[^]]+\]\([^)]+\)"

# `E13-render-verification.txt` as it stood before `L22R-15` was repaired: two crashed `awk`
# invocations immediately above the pass line. The raw file was overwritten in place, so this is a
# **reconstruction in the verdict's own terms**, not a transcription of bytes that no longer exist:
# `notes/reports/260915-CAPS-L22-verdict-fixverify-r2.md` finding `L22R-15` (citing `E13` lines 19-27)
# quotes the two `awk` failures verbatim and quotes the pass line as `IDENTICAL (34/34)`.
E13_CRASHED = """== check 1: capsule bodies, byte-for-byte against a fresh compile ==
awk: cmd. line:1: {$1"/"$2, $4}
awk: cmd. line:1:      ^ backslash not last character on line
awk: cmd. line:1: {$1"/"$2, $4}
awk: cmd. line:1:      ^ syntax error
   IDENTICAL (34/34)
"""

# The same check after the repair: `E5-verify-render.py`, a producer that exits non-zero on failure.
E13_REPAIRED = """== check 1: capsule bodies, byte-for-byte against a fresh compile ==
   34/34 marked bodies equal render_instructions() + one line break
== check 2: INDEX.md token and byte column, against the same compiles ==
   34/34 rows identical
exit 0
"""

# `E18-declared-tools-result.txt`, the shipped declared-tools baseline: exit 1 and a non-vacuity
# section, because a check that reports findings has demonstrated it can fail.
DECLARED_TOOLS_RESULT = """$ python E18-verify-declared-tools.py /tmp/stage/tree   # exit 1

**10 findings across 5 seats:**
  == findings ==
     architect: names `read_ar_files` on its retrieval surface (line 171) but the manifest does not declare it for this role, while other roles do declare it

  == can this check fail? ==
"""

# The F18 before-file as shipped: a run whose producer is described in prose and whose transcript
# evidences no run at all — no exit status, no findings, no non-vacuity statement.
DECLARED_TOOLS_SILENT = """== declared tools versus the retrieval each role file names ==
   architect          declared= 4 retrieval-named= 2 undeclared-and-declared-elsewhere=[] ambient=['context_packet'] cross-referenced=[]
"""


def _probe() -> discipline.Probe:
    return discipline.Probe(
        positive=discipline.Witness(
            kind="positive",
            pattern=NA_CORRECTED_PATTERN,
            sample=NA_LINK_ROW,
            describes="a body row whose Anchor cell is n/a and whose Source cell is a markdown link",
        ),
        negative=discipline.Witness(
            kind="negative",
            pattern=NA_FAULTY_PATTERN,
            sample=NA_LINK_ROW,
            describes="the anchor-cell-first pattern that returned 0 on a live family",
        ),
    )


class PatternProbeWitnessTests:
    """A zero from a text probe is admissible only when the probe was shown to see the other answer."""

    def test_a_probe_that_cannot_match_its_own_positive_is_refused(self) -> None:
        unusable = discipline.Probe(
            positive=discipline.Witness(
                kind="positive",
                pattern=r"^ZZZ_NEVER_PRESENT$",
                sample=NA_LINK_ROW,
                describes="a miss",
            ),
            negative=discipline.Witness(
                kind="negative",
                pattern=r"^ZZZ_NEVER_PRESENT$",
                sample=NA_LINK_ROW,
                describes="a miss",
            ),
        )
        with pytest.raises(ValueError, match="cannot see the shape it is looking for"):
            discipline.counted_pattern(NA_FAULTY_PATTERN, NA_LINK_ROW, probe=unusable)

    def test_a_probe_that_matches_its_negative_witness_is_refused(self) -> None:
        loose = discipline.Probe(
            positive=discipline.Witness(
                kind="positive", pattern=NA_CORRECTED_PATTERN, sample=NA_LINK_ROW, describes="a hit"
            ),
            negative=discipline.Witness(
                kind="negative", pattern=r"^", sample=NA_LINK_ROW, describes="every line"
            ),
        )
        with pytest.raises(ValueError, match="not discriminating"):
            discipline.counted_pattern(NA_CORRECTED_PATTERN, NA_LINK_ROW, probe=loose)

    def test_the_corrected_pattern_counts_the_live_family_the_faulty_one_could_not(self) -> None:
        # The historical zero was produced on a corpus that no longer exists (L21's migration rewrote
        # it), so the control is the shape itself: the record's own row, which the faulty pattern
        # cannot match and the corrected one does. That is the whole fault, in two lines.
        assert discipline._scan(NA_FAULTY_PATTERN, NA_LINK_ROW) == 0
        licensed = discipline.counted_pattern(NA_CORRECTED_PATTERN, NA_LINK_ROW, probe=_probe())
        assert licensed.count == 1
        assert licensed.witnesses[0].sample == NA_LINK_ROW

    def test_a_pattern_the_probe_did_not_prove_is_refused(self) -> None:
        # A proved probe licenses the pattern it proved and nothing else. Fault 2's shape — a pattern
        # that matches nothing at all — was admitted as a licensed zero when the count was taken with
        # a *different* pattern than the one the probe proved.
        discipline._prove_probe(_probe())  # the proof itself is sound; only the binding is missing
        unproved = r"^ZZZ_CANNOT_EVER_MATCH_ZZZ$"
        assert discipline._scan(unproved, NA_LINK_ROW) == 0
        with pytest.raises(ValueError, match="not the one the probe proved"):
            discipline.counted_pattern(unproved, NA_LINK_ROW, probe=_probe())


class BoundedCaptureTests:
    """A count taken from a capture that ran past its unit is a count of something else."""

    def test_row_bounded_capture_matches_the_historical_21_routes_and_22_comma_items(self) -> None:
        captured = discipline.capture_bounded_window(CHECKLIST_TAIL)
        assert captured.start_line == 2
        assert captured.end_line == 2
        assert captured.closed_by == "boundary line"
        assert captured.text.count(",") == 21
        assert captured.items == 22
        assert captured.text == ATTESTATION_ROW

    def test_a_capture_that_runs_past_the_unit_counts_the_table_instead_of_the_row(self) -> None:
        # The fault in its own terms: the same start line, captured to the next blank line, takes the
        # following table rows with it — so the "item" count measures the table, not the row.
        bounded = discipline.capture_bounded_window(CHECKLIST_TAIL)
        unbounded = "\n".join(CHECKLIST_TAIL.splitlines()[1:])
        assert unbounded.startswith(ATTESTATION_ROW)
        assert bounded.items == 22
        assert len(unbounded.split(",")) > bounded.items

    def test_the_line_cap_reports_itself_as_the_closer(self) -> None:
        # A window cut off at `max_lines` says so, and the lines it left unread stay visible. Before
        # the fix the same capture reported `end of evidence` with 391 lines still below it.
        text = "\n".join(("BEGIN-UNIT", *(f"payload line {n} with a , comma" for n in range(400))))
        window = discipline.capture_bounded_window(
            text, start_pattern=r"^BEGIN-UNIT$", max_lines=10
        )
        assert window.closed_by == "line cap"
        assert window.end_line - window.start_line + 1 == 10
        assert len(text.splitlines()) - window.end_line == 391

    def test_a_blank_line_closes_the_window(self) -> None:
        # The third boundary in the docstring, observed on its own: the window stops before the blank
        # line and does not run on to the table rows below it.
        text = "\n".join(
            (
                "BEGIN-UNIT",
                "payload line with a , comma",
                "",
                "| a table row a boundary pattern would match |",
                "| another row |",
            )
        )
        window = discipline.capture_bounded_window(
            text, start_pattern=r"^BEGIN-UNIT$", boundary_pattern=r"^\|"
        )
        assert window.closed_by == "blank line"
        assert window.end_line == 2
        assert window.text == "BEGIN-UNIT\npayload line with a , comma"


class CrashReadAsPassTests:
    """A pass line printed after a crashed command does not describe the command that produced it."""

    def test_the_historical_crash_adjacent_to_a_pass_is_caught(self) -> None:
        scan = discipline.crash_scan(E13_CRASHED)
        assert scan.crashed_before_pass is True
        assert scan.crash_lines[0] < scan.pass_lines[-1]
        assert "not attributable to a run that completed" in scan.detail

    def test_the_repaired_producer_transcript_is_clean(self) -> None:
        scan = discipline.crash_scan(E13_REPAIRED)
        assert scan.crashed_before_pass is False
        assert scan.crash_lines == ()
        assert scan.detail == "no crash marker in the captured output"


class ArtifactAdmissibilityTests:
    """A clean result is evidence only when the artifact also shows the check could have failed."""

    def test_the_shipped_baseline_is_admissible(self) -> None:
        assert (
            discipline.check_artifact_refusal(
                text=DECLARED_TOOLS_RESULT,
                script_name="E18-verify-declared-tools.py",
                probe=_probe(),
            )
            is None
        )

    def test_a_transcript_with_no_run_and_no_verdict_is_refused(self) -> None:
        reason = discipline.check_artifact_refusal(
            text=DECLARED_TOOLS_SILENT, script_name="E18-verify-declared-tools.py", probe=_probe()
        )
        assert reason is not None
        assert "no exit status and no producer transcript" in reason

    def test_a_run_with_no_way_to_fail_is_refused_as_vacuous(self) -> None:
        reason = discipline.check_artifact_refusal(
            text=f"$ python check.py   # exit 0\n{DECLARED_TOOLS_SILENT}",
            script_name="check.py",
            probe=_probe(),
        )
        assert reason is not None
        assert "vacuous" in reason
        # The same artifact is admissible once the caller states how its producer can fail: the
        # refusal is about the missing statement, not about the check being weak.
        assert (
            discipline.check_artifact_refusal(
                text=f"$ python check.py   # exit 0\n{DECLARED_TOOLS_SILENT}",
                script_name="check.py",
                probe=_probe(),
                can_fail_evidence="the producer exits non-zero on any body mismatch",
            )
            is None
        )

    def test_a_crashing_artifact_is_refused_before_its_result_is_read(self) -> None:
        reason = discipline.check_artifact_refusal(
            text=E13_CRASHED, script_name="E13-render-verification.txt", probe=_probe()
        )
        assert reason is not None
        assert "not attributable to a run that completed" in reason

    def test_a_run_that_exited_nonzero_with_no_result_is_refused_as_a_crash(self) -> None:
        # Fault 7's shape: the harness raised `FileNotFoundError` and exited 1 (`L22R-19`, r3:170),
        # and the re-executed shape records `exit 2` with no pass line at all. `crash_scan` cannot see
        # it — no traceback reached the captured output — so the status itself has to refuse it.
        crashed = (
            "exit 2\n"
            "gate not found: neither amalgamation_gate.py nor amalgamation_gate.py sits beside "
            "E16-old-gate-name.py; pass --gate <path>\n"
        )
        scan = discipline.crash_scan(crashed)
        assert scan.crash_lines == ()
        assert scan.pass_lines == ()
        reason = discipline.check_artifact_refusal(
            text=crashed, script_name="E16-gate-teeth.py", probe=_probe()
        )
        assert reason is not None
        assert "exited 2 and reported no result" in reason
        assert "a crash, not a pass" in reason

    def test_prose_that_describes_the_exit_convention_is_not_a_run(self) -> None:
        # The no-run refusal read the whole document for `exit <n>`, so a page that merely says how
        # the check exits satisfied it. The status must be recorded as a run.
        prose = (
            "# How to run this check\n"
            "The check must exit 0 on success; a mismatch makes it exit 1. Run it before landing.\n"
            "  0 errors found; nothing was compared in this document\n"
        )
        assert discipline.EXIT_CODE.search(prose) is not None
        reason = discipline.check_artifact_refusal(
            text=prose, script_name="check.py", probe=_probe()
        )
        assert reason is not None
        assert "no exit status and no producer transcript" in reason

    def test_a_clean_disclaimer_is_not_evidence_the_check_can_fail(self) -> None:
        # `0 errors found` begins a line with the word the vacuity guard looked for, so a disclaimer
        # was read as a finding. The evidence is a *non-zero* result.
        disclaimer = (
            "$ python check.py   # exit 0\n"
            "  0 errors found; no finding was produced and nothing was compared\n"
        )
        reason = discipline.check_artifact_refusal(
            text=disclaimer, script_name="check.py", probe=_probe()
        )
        assert reason is not None
        assert "vacuous" in reason

    def test_an_unproved_probe_refuses_before_any_verdict_is_reached(self) -> None:
        # The probe proof is the caller's licence to reach a verdict at all; it is not a decoration.
        unusable = discipline.Probe(
            positive=discipline.Witness(
                kind="positive",
                pattern=r"^ZZZ_NEVER_PRESENT$",
                sample=NA_LINK_ROW,
                describes="a miss",
            ),
            negative=discipline.Witness(
                kind="negative",
                pattern=r"^ZZZ_NEVER_PRESENT$",
                sample=NA_LINK_ROW,
                describes="a miss",
            ),
        )
        with pytest.raises(ValueError, match="cannot see the shape it is looking for"):
            discipline.check_artifact_refusal(
                text=DECLARED_TOOLS_RESULT,
                script_name="E18-verify-declared-tools.py",
                probe=unusable,
            )


class ProducerIdentityTests:
    """A record names the producer that made it, or it is not a record of a measurement."""

    def test_a_named_producer_absent_from_the_package_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        # The historical direction: a table whose producer is named in prose and exists in no package.
        reason = discipline.producer_identity_refusal(
            discipline.ProducerIdentity(
                script=pathlib.Path("E1-measure-capsule-tokens.py"), command="--", revision="derive"
            ),
            worktree=tmp_path,
        )
        assert reason is not None
        assert "not in the package" in reason

    def test_a_present_producer_without_a_command_or_revision_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        script = tmp_path / "producer.py"
        script.write_text("", encoding="utf-8")
        for command, revision, expected in (
            ("", "rev", "names no command"),
            ("--", "", "names no revision"),
        ):
            reason = discipline.producer_identity_refusal(
                discipline.ProducerIdentity(
                    script=pathlib.Path("producer.py"), command=command, revision=revision
                ),
                worktree=tmp_path,
            )
            assert reason is not None
            assert expected in reason

    def test_a_fully_identified_producer_passes(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "producer.py").write_text("", encoding="utf-8")
        assert (
            discipline.producer_identity_refusal(
                discipline.ProducerIdentity(
                    script=pathlib.Path("producer.py"),
                    command="producer.py in.txt",
                    revision="f7619b3d",
                ),
                worktree=tmp_path,
            )
            is None
        )

    def test_a_record_that_names_no_producer_script_is_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        # An empty name is its own refusal: without the branch the *next* refusal answers for it
        # ("not in the package"), which is true but hides the fault the record actually carries.
        reason = discipline.producer_identity_refusal(
            discipline.ProducerIdentity(script=pathlib.Path(""), command="--", revision="rev"),
            worktree=tmp_path,
        )
        assert reason is not None
        assert "names no producer script" in reason

    def test_reproduction_is_byte_exact_and_a_different_table_is_not_a_reproduction(
        self, tmp_path: pathlib.Path
    ) -> None:
        source = tmp_path / "E1.txt"
        source.write_text("a 1\nb 2\n", encoding="utf-8")
        producer = tmp_path / "derive.py"
        producer.write_text(
            "import pathlib, sys\n"
            "rows = pathlib.Path(sys.argv[1]).read_text().split()\n"
            "pathlib.Path(sys.argv[2]).write_text(f'total={sum(int(v) for v in rows[1::2])}\\n')\n",
            encoding="utf-8",
        )
        recorded = tmp_path / "table.txt"
        recorded.write_text("total=3\n", encoding="utf-8")
        reproduced = discipline.reproduce_recorded_producer(
            producer=producer,
            arguments=[str(source)],
            recorded=recorded,
            output_argument=True,
        )
        assert reproduced.matches is True
        assert reproduced.produced_digest == reproduced.recorded_digest
        recorded.write_text("total=4\n", encoding="utf-8")
        stale = discipline.reproduce_recorded_producer(
            producer=producer,
            arguments=[str(source)],
            recorded=recorded,
            output_argument=True,
        )
        assert stale.matches is False
        assert stale.produced_digest != stale.recorded_digest

    def test_a_producer_that_exits_nonzero_is_not_a_reproduction(
        self, tmp_path: pathlib.Path
    ) -> None:
        # The producer writes the recorded bytes and still fails. Byte equality is not the question
        # when the run that produced the bytes reports failure; the answer is `matches=False`.
        recorded = tmp_path / "table.txt"
        recorded.write_text("total=3\n", encoding="utf-8")
        failing = tmp_path / "failing.py"
        failing.write_text(
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[-1]).write_text('total=3\\n')\n"
            "sys.exit(3)\n",
            encoding="utf-8",
        )
        reproduction = discipline.reproduce_recorded_producer(
            producer=failing, arguments=[], recorded=recorded, output_argument=True
        )
        assert reproduction.matches is False
        assert reproduction.produced_digest is None
        assert reproduction.output.startswith("exit 3")

    def test_a_producer_that_writes_nothing_is_a_mismatch_not_an_exception(
        self, tmp_path: pathlib.Path
    ) -> None:
        # A producer that exits 0 without writing its output argument is a non-reproduction; the
        # helper's contract is an answer, so it returns one instead of raising FileNotFoundError.
        recorded = tmp_path / "table.txt"
        recorded.write_text("total=3\n", encoding="utf-8")
        silent = tmp_path / "silent.py"
        silent.write_text("print('I wrote nothing')\n", encoding="utf-8")
        reproduction = discipline.reproduce_recorded_producer(
            producer=silent, arguments=[], recorded=recorded, output_argument=True
        )
        assert reproduction.matches is False
        assert reproduction.produced_digest is None
        assert "wrote no artifact" in reproduction.output

    def test_the_reproduction_does_not_write_beside_the_recorded_artifact(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Re-running a producer must not touch the directory that holds the evidence it is checking:
        # the output argument the producer receives is a scratch path, not `<recorded>.reproduced`.
        recorded = tmp_path / "table.txt"
        recorded.write_text("total=3\n", encoding="utf-8")
        reporter = tmp_path / "reporter.py"
        reporter.write_text(
            "import pathlib, sys\n"
            "out = pathlib.Path(sys.argv[-1])\n"
            "out.write_text('total=3\\n')\n"
            "pathlib.Path(sys.argv[1]).write_text(str(out.parent))\n",
            encoding="utf-8",
        )
        reported = tmp_path / "where.txt"
        reproduction = discipline.reproduce_recorded_producer(
            producer=reporter,
            arguments=[str(reported)],
            recorded=recorded,
            output_argument=True,
        )
        assert reproduction.matches is True
        assert pathlib.Path(reported.read_text(encoding="utf-8")).resolve() != tmp_path.resolve()


def test_the_evidence_transcription_is_the_one_the_record_carries() -> None:
    """The constants above are the record's, checked against the record where it still exists."""
    source = pathlib.Path(
        "/home/firefox/projects/ar-coordination/tasks/agents-remember/"
        "260915_role-capsules-and-native-eve/notes/reports/260915-CAPS-L23-curator-evidence/"
        "memory-refresh-finding-21-routes.json"
    )
    if not source.is_file():
        pytest.skip("the 260915 campaign evidence package is not present in this workspace")
    payload = json.loads(source.read_text(encoding="utf-8"))
    assert payload["message"] == ATTESTATION_MESSAGE
    assert len(ATTESTATION_MESSAGE) == 1343
    assert ATTESTATION_MESSAGE.count(",") == 21
    assert payload["route_count"] == 21
    assert len(payload["routes"]) == 21
