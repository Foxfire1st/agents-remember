"""``ar-durable-store/1.0`` -- the one contract the control-plane JSONL stores implement.

CONTRACT FRONT MATTER
    contract:       ar-durable-store/1.0
    schemaVersion:  1.0          (stamped on every record; see :class:`DurableRecord`)
    shape:          append-only JSONL, one record per line, folded by id on read
    serialization:  POSIX ``flock`` on a sibling lockfile, per log, UNCONDITIONAL -- every
                    append and every rewrite takes it, in every process. This is what makes
                    the loss zero. Paired with a per-log in-process mutex so that thread-level
                    exclusion is DECLARED rather than inferred from how the handle is opened
                    (:func:`thread_mutex_for` states exactly what that adds and what it does not).
    compaction:     single-owner -- one named process role per log. ADVISORY: checked only in
                    a process that declared a role (the MCP server and the dashboard daemon).
    platform:       Linux and macOS, Windows through WSL; the coordination root must be on a
                    local POSIX filesystem, which is verified rather than assumed
    read policy:    two, deliberately -- strict for authority, tolerant for projection

WHY THIS MODULE EXISTS

Six stores under ``controlplane/`` were written independently against the same shape, and the
safety properties ended up distributed almost at random: one of six took a lock, three of six
used a pid-scoped temp name, none fsynced, and three of six tolerated a torn line. The store
that carries authority -- ``GateStore``, whose ``applied`` snapshots are what stop one human
approval being consumed twice -- had neither the lock nor the pid-scoped temp name. Measured on
the base commit, 10 runs per store under ordinary two-process operation: attention-dismissals
lost 31.45% of appended records (and 127 of 2000 writes raised outright), gate 11.50%,
supervisor-signals 10.50%, expectation-rows 10.20%, orchestration-nudges 9.20%. Operator-inbox,
the one store that already took a lock, lost 0.00%. That last figure is this module's whole
argument in one number.

Note which of those four is NOT a defect: the torn-line split is a real distinction and is kept
(see the read-policy section below). ``GateStore`` reading strictly was right; it was everything
else about that store that was wrong. This module is the single contract the six now share, so
that a difference between two of them is a decision someone can read rather than an accident.

THE THREE FAILURES THIS CLOSES

1. Lost update. ``compact``/``_replace`` reads the whole log, filters it, writes a temp and
   ``os.replace``s it. An append that lands between the read and the replace is discarded
   with no error and no log line. There is no lock-free rewrite that preserves a concurrent
   ``O_APPEND`` write -- the appended bytes are simply not in the temp file -- so
   :func:`exclusive_access` makes append and rewrite mutually exclusive. The leaf's requirement
   calls locking the fallback and single ownership the mechanism; measured, it is the lock that
   takes the loss to zero and ownership that removes the misplaced reclaim pass. Both were
   needed and the section below says which does which.
2. Unlinked inode. The rewrite called ``unlink`` when the filtered set came out empty, so an
   appender holding an ``"a"``-mode handle wrote into an inode with no remaining links: the
   record vanished with no torn line and no trace at all. :func:`rewrite_lines` never
   unlinks -- an empty record set is written as an empty file -- so the empty case stops
   being a more dangerous special case than the ordinary one.
3. Colliding temp path, which was a CRASH and not only a loss. Gate, attention-dismissal and
   operator-inbox rewrites all built the same temp name, ``<log>.tmp``, with nothing to
   distinguish one writer from another. Two concurrent rewriters shared it; one ``os.replace``d
   it away and the other raised ``FileNotFoundError``. Measured on the base commit: 127 of 2000
   ``AttentionDismissalStore.dismiss`` calls raised -- and that call is the dashboard's HTTP
   dismiss route (``serving/app.py``), so it reached the operator as a 500 on a click. Closed
   twice over: the lock means two rewriters never overlap, and :func:`rewrite_lines` builds the
   only temp name in the control plane, pid-scoped, so they could not collide even if they did.

WHAT PREVENTS LOSS, AND WHAT MERELY DOCUMENTS -- READ THIS BEFORE TRUSTING EITHER

These are two mechanisms of very different strength and the difference must not blur:

* THE LOCK IS THE MECHANISM, and it is UNCONDITIONAL. Every append and every rewrite of every
  one of the six logs takes that log's lock, in every process, whether or not that process
  declared anything. There is no flag that turns it off and no store exempt from it. This is
  what took the measured loss to zero.
* OWNERSHIP IS ADVISORY, and it is OPT-IN. It is expressed two ways, neither of which is a
  durability guarantee. :meth:`StoreOwnership.is_compaction_owner` is a QUESTION the reclaim
  call sites ask before reclaiming -- it never raises. :meth:`StoreOwnership.check_declared_writer`
  does raise, but only in a process that called :func:`declare_process_role`. Exactly three call
  sites do, covering the two long-lived processes in all their launch modes: ``mcp/server.py``
  ``main``, ``cli/dashboard.py`` ``run``, and ``cli/dashboard.py`` ``_dev_app`` (the ``--reload``
  worker is a spawn child that never reaches ``run``). :func:`declare_process_role` says why each
  sits where it does. Every OTHER CLI invocation, script and test writes these logs with no
  ownership check at all. Do not read either as a guarantee.

  Where ownership does real work it does it STRUCTURALLY, by moving code rather than by
  checking at runtime: ``observer/snapshots.py`` no longer rewrites a gate log on the
  projection tick, and ``mcp/tools/gates.py`` reclaims only when it is the owner. Those two
  edits are the mechanism; the methods above only make the intent legible and catch a new
  writer in the one process that declares itself.

Ownership still earned its place, because locking answers "who may write next" and never
answers "who is responsible for this file" -- and the gate defect came from that second
question. ``observer/snapshots.py`` rewrote every gate log every 30 seconds, not because the
dashboard owns gates but because that is where the projection tick happens to live. Naming an
owner per store is what moved that reclaim pass to the MCP process, and what makes a
wrong-process rewrite in either daemon fail loudly instead of silently racing.

SAME HOST, AND WHY THE FILESYSTEM CHECK IS THE ENFORCEMENT OF IT

The MCP process and the dashboard share a host. That is not left implicit:
:func:`exclusive_access` verifies, once per lockfile, that ``flock`` on that path really
excludes -- by taking the lock twice from two file descriptions in this one process, which
a working POSIX filesystem denies. NFS and SMB emulate ``flock`` with per-process byte-range
locks and WSL's DrvFs ignores it outright; all three let the second acquisition succeed and
are refused loudly by :class:`UnsafeLockFilesystemError`. A remote or DrvFs coordination
root is also the only way a *second host* could be writing these logs, so verifying the
lock's capability enforces the same-host assumption and the local-POSIX platform constraint
in one probe. A lock is never downgraded to a silent no-op: where it is required and
unavailable, this refuses to run.

THE TWO READ POLICIES ARE NOT AN ACCIDENT -- DO NOT COLLAPSE THEM

A torn line means different things to different consumers, so the contract deliberately
carries two policies rather than one:

* STRICT, for authority. ``GateStore.read`` backs the enforcement fold that
  ``serving/hosted_interactions.py`` and ``worktrees/modules/integrate.py`` consult before
  a mutation. Silently skipping a malformed record there could drop an ``applied`` marker,
  and the fold would conclude the gate was never consumed -- re-opening the replay window a
  human approval exists to close. It raises, which is loud and correct. Every rewrite OF AN
  AUTHORITY-BEARING LOG reads strictly, for the same reason: a compaction must never be the
  thing that erases an authority record it could not parse. That is the three strict stores,
  and each of them filters a list its OWN strict read produced -- ``GateStore.delete`` /
  ``compact``, ``ExpectationRowStore._compact_locked``, and every ``OperatorInboxStore`` rewrite
  (``delete`` / ``delete_by_gate`` / ``compact`` / ``reconcile_and_compact``, all via
  ``_read_unlocked``).
* TOLERANT, for projection. The dashboard's 1s tick must degrade, not crash: one unparseable
  row must not 500 an endpoint or freeze the fleet-wide projection. A tolerant read never
  writes back what it read, so a skipped line is only skipped for the duration of one tick.

DO NOT GENERALISE "EVERY REWRITE READS STRICTLY" TO ALL SIX -- it is false, and the three stores
it is false for are the ones to check before trusting it. ``AttentionDismissalStore.dismiss`` and
``_prune_locked``, ``OrchestrationNudgeStore.compact`` and
``SupervisorSignalCooldownStore._compact_locked`` each rewrite from a list their TOLERANT
``read()`` produced. The unparseable row was skipped by that read, so it is absent from the list
the rewrite writes back: those three DROP such a row PERMANENTLY, not for one tick.

That is accepted here for exactly one reason -- NONE OF THOSE THREE LOGS CARRIES AUTHORITY.
Nothing consumes an approval or decides a mutation off any of them. A lost dismissal re-shows an
attention item the operator had cleared; a lost nudge or cooldown row costs at most one extra
nudge. Each is a self-correcting UI or rate-limiting fact, which is why permanent loss of one is
a cost rather than a defect.

IT WOULD STOP BEING ACCEPTABLE THE MOMENT ONE OF THEM CARRIED AUTHORITY. If anything is ever
decided on one of these three logs -- a dismissal that suppresses an enforcement path, a cooldown
consulted before a mutation -- its rewrite must be moved onto a strict read FIRST, in the same
change. Otherwise the compaction silently becomes the thing that erases the record the decision
needed, which is precisely the defect this contract exists to remove, re-entered through the read
policy instead of through the lock.

:class:`DurableRecord` is what makes both policies follow from one rule: an unknown MAJOR
``schemaVersion`` fails validation, so a strict reader raises on it and a tolerant reader
skips it, with no version branch in either reader.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

DURABLE_STORE_CONTRACT = "ar-durable-store/1.0"
"""The contract these stores implement, as declared in the front matter above."""

SCHEMA_VERSION = "1.0"
"""The ``schemaVersion`` stamped on every record written through this contract.

``MAJOR.MINOR``. A reader REJECTS an unknown major (the record means something it cannot be
trusted to interpret) and ACCEPTS an unknown minor (additive, and pydantic's ``extra="forbid"``
is the separate guard against a field nobody declared). This is the whole versioning story:
there is deliberately no migration framework, because nothing exists to migrate yet and the
capability that is cheap now and unbuildable later is telling an old record from a new one.
"""

SUPPORTED_SCHEMA_MAJOR = 1

ProcessRole = Literal["mcp", "dashboard"]
"""The two long-lived processes that write these logs concurrently."""


class DurableStoreError(RuntimeError):
    """A durable-store contract violation. Never downgraded to a warning or a no-op."""


class CompactionOwnerError(DurableStoreError):
    """A process that is not a log's declared compaction owner tried to rewrite it."""


class UnsafeLockFilesystemError(DurableStoreError):
    """``flock`` on the coordination root does not actually exclude, so it cannot be relied on."""


_declared: dict[str, ProcessRole] = {}


def declare_process_role(role: ProcessRole) -> None:
    """Declare which of the two concurrent writers this process is.

    Called once at process start. The callers are ``mcp/server.py`` ``main``, ``cli/dashboard.py``
    ``run``, and ``cli/dashboard.py`` ``_dev_app`` -- and no others. The first two sit at the
    process ENTRY POINT rather than inside ``create_server`` / ``create_app`` on purpose: those
    factories are called in-process by the test suite, so declaring there would stamp a role onto
    every later test in the same interpreter.

    ``_dev_app`` is the deliberate exception and the only factory that declares. ``--reload`` runs
    the app in a uvicorn reload worker, and uvicorn 0.49.0 starts that worker through
    ``multiprocessing.get_context("spawn")`` (``uvicorn/_subprocess.py``,
    ``supervisors/basereload.py``): a spawn child re-imports this module, so it starts with an
    EMPTY ``_declared`` and never runs ``run``. Measured before the fix: the reload worker saw
    ``{}`` and therefore answered TRUE to :meth:`StoreOwnership.is_compaction_owner` for every
    log, so the dev dashboard ran the gate reclaim a real dashboard deliberately skips. The
    worker's own entry point is ``_dev_app``, so that is where its role is declared, and
    foreground / ``--daemon`` / ``--reload`` now agree. ``_dev_app`` is reload-only (it reads its
    config from ``AR_DASHBOARD_DEV_CONFIG`` and refuses ``--sim``), which is why it can carry a
    declaration that ``create_app`` cannot.

    That is the whole reach of the ownership checks: a process that does not call this writes
    these logs unchecked. It is the lock, not this, that keeps such a process from losing records.
    """
    _declared["role"] = role


def declared_process_role() -> ProcessRole | None:
    """This process's declared role, or ``None`` for a CLI/test invocation that declared none."""
    return _declared.get("role")


@dataclass(frozen=True)
class StoreOwnership:
    """Who writes one durable log, who may rewrite it, and why it came out that way.

    Every field is a decision a reader can check against the tree: ``writers`` is the MEASURED
    set of processes that append, ``compaction_owner`` is the single process allowed to perform
    the destructive rewrite, and ``rationale`` says why. ``compaction_owner`` is ``None`` only
    for a store that genuinely cannot be given one -- the leaf's declared exception.

    There is deliberately NO ``serialized`` field. Every log is locked, and that is not a
    setting a store may opt out of. Two of the six (attention-dismissals, supervisor-signals)
    have one writer today and an earlier draft left them unlocked on that basis; the proof run
    measured 31.45% loss on attention-dismissals doing exactly that. "Only one process writes
    this file" is a deployment fact, not a structural one, and a store whose durability rests on
    a deployment fact is precisely what this leaf was called in to repair -- ``GateStore`` lost
    records for years behind a docstring asserting the same thing.
    :meth:`check_declared_writer` makes the ``writers`` claim checkable in the two daemons -- all
    six stores call it on their write entry point, so a new writer appearing in either daemon is
    caught rather than assumed away. The lock makes the store correct regardless, including in the
    processes that check nothing.
    """

    store: str
    writers: tuple[ProcessRole, ...]
    compaction_owner: ProcessRole | None
    rationale: str

    def check_declared_writer(self) -> None:
        """ADVISORY. Refuse a write from a DECLARED role that is not one of this log's writers.

        Silent in any process that never called :func:`declare_process_role` -- which is every
        CLI invocation, script and test. It catches a new writer appearing in the MCP server or
        the dashboard, where the writer set is a claim this contract makes; it guarantees nothing
        anywhere else, and the log's lock is what keeps those processes safe.
        """
        role = _declared.get("role")
        if role is not None and role not in self.writers:
            raise CompactionOwnerError(
                f"{self.store}: written by {'/'.join(self.writers)}, not by the {role} process. "
                f"Adding a writer changes this store's concurrency contract -- update its "
                f"StoreOwnership so the lock decision is re-made with the new writer in view."
            )

    def is_compaction_owner(self) -> bool:
        """Whether THIS process should run this log's reclaim pass. A QUESTION, never a throw.

        Ownership is expressed as something call sites ASK rather than something a low-level
        rewrite REFUSES, and the difference is deliberate. ``gate_decide_payload`` is executed
        by the MCP tool surface AND called directly by the dashboard's decision route, so "am I
        the owner" has to be answerable by shared code; and a raise buried in the rewrite would
        make an interpreter that has hosted both roles -- the test suite is exactly that --
        unable to run either. Where ownership matters it is enforced STRUCTURALLY instead: the
        projection tick no longer rewrites a GATE log (it still rewrites one --
        ``observer/projection_store.py`` prunes attention-dismissals every tick, a log the
        dashboard owns), and the shared decide path asks this before reclaiming. The lock, not
        this, is what makes a rewrite safe.

        True in a process that declared nothing (a CLI or test run is nobody's competitor) and
        true for a store with no single owner, which is the inbox by design.

        THE UNDECLARED-IS-OWNER DEFAULT IS LOAD-BEARING AND WAS RE-DECIDED, NOT INHERITED. It was
        once false in a way that falsified the sentence above: the ``--reload`` dashboard is a
        uvicorn spawn worker that starts with an empty ``_declared`` (see
        :func:`declare_process_role`), so it read as "declared nothing" while being very much a
        competitor -- and ran the gate reclaim a foreground dashboard skips. Note what that was
        NOT: the per-log lock is unconditional, so the rewrite itself was still serialized and no
        record was at risk. What it broke was this default's stated reason.

        Two fixes were available and the reload path was fixed rather than the default inverted.
        Inverting it -- undeclared means NOT owner -- would have turned this predicate FALSE in
        every process that legitimately declares nothing: every CLI invocation, every script, and
        the whole test suite. Its only call site today is the gate reclaim in
        ``mcp/tools/gates.py``, so the concrete effect would have been gate logs that no
        undeclared process ever reclaims -- discovered by their growth, if at all -- and every
        future call site would inherit the same silent-off default. A default whose failure mode
        is "the reclaim quietly never runs" is worse than one whose failure mode was a single
        dev-only process running a reclaim on a lock-protected log. So the default stands, and the
        one process that contradicted it now declares its role.
        """
        role = _declared.get("role")
        return role is None or self.compaction_owner is None or role == self.compaction_owner


# ---------------------------------------------------------------------------------------------
# THE OWNERSHIP REGISTER -- all six stores in one place, so the differences are comparable.
#
# ``writers`` was MEASURED on 2026-08-01 by enumerating every writer of every log across the
# tree, not inherited from what the modules claimed: four logs are written by both long-lived
# processes and two by the dashboard alone. What differs between the six, and only this:
#
#   * WHO MAY REWRITE. Five name a single compaction owner. One -- operator-inbox -- cannot be
#     given one, and is the leaf's declared exception.
#   * WHO MAY WRITE AT ALL. Four accept both processes; two accept only the dashboard.
#
# Both differences are ADVISORY (see the module docstring): they are checked in the two daemons
# and nowhere else. Locking is NOT a difference -- all six take their log's lock, always.
# ---------------------------------------------------------------------------------------------

GATE_OWNERSHIP = StoreOwnership(
    store="gate",
    writers=("mcp", "dashboard"),
    compaction_owner="mcp",
    rationale=(
        "The MCP process mints, decides, applies and deletes gates (mcp/tools/gates.py, "
        "worktrees/modules/closeout.py), so it owns reclamation. The dashboard appends too -- "
        "serving/hosted_interactions.py raises and resolves agent-question gates for "
        "adapter-owned sessions -- so the rewrite is serialized against it. Compaction used to "
        "run on the dashboard's 30s projection tick, which owned nothing here; that is the "
        "reclaim pass this ownership moved."
    ),
)

EXPECTATION_ROW_OWNERSHIP = StoreOwnership(
    store="expectation-rows",
    writers=("mcp", "dashboard"),
    compaction_owner="dashboard",
    rationale=(
        "The supervisor sweep (serving/supervisor.py) is the only reclamation pass this log has "
        "and it needs the folded snapshot it produces, so the dashboard owns compaction. Every "
        "dispatch surface appends, and half of them are MCP tools (gate open, inbox post), so "
        "the rewrite is serialized against those appends."
    ),
)

ATTENTION_DISMISSAL_OWNERSHIP = StoreOwnership(
    store="attention-dismissals",
    writers=("dashboard",),
    compaction_owner="dashboard",
    rationale=(
        "Single writer: the dashboard both records dismissals (serving/app.py) and prunes them "
        "on the projection pass (observer/projection_store.py); nothing in the MCP process "
        "touches this log. This store has no plain append -- both of its writes are whole-file "
        "rewrites -- so check_declared_writer is called from BOTH of them (dismiss and "
        "prune_lifecycles) rather than from a single append, which is what keeps the "
        "single-writer claim checkable inside the daemons. "
        "Locked all the same -- a dismissal is a whole-file read-modify-write, so the "
        "HTTP dismiss route and the projection sweep are themselves a lost-update pair, and a "
        "draft that left this log unlocked on the strength of single-writer measured 31.45% "
        "loss."
    ),
)

OPERATOR_INBOX_OWNERSHIP = StoreOwnership(
    store="operator-inbox",
    writers=("mcp", "dashboard"),
    compaction_owner=None,
    rationale=(
        "THE DECLARED EXCEPTION (leaf 260731-EFA-L5 R2). This is the one store that genuinely "
        "cannot be given a single compaction owner: both processes must physically remove rows, "
        "not merely append them. The MCP deletes the inbox rows tied to a cancelled gate "
        "(mcp/tools/gates.py delete_by_gate) at the moment the gate is cancelled, while the "
        "dashboard's supervisor sweep must resolve and compact under one held lock "
        "(reconcile_and_compact) so that a consume which won the lock stays terminal. Neither "
        "can be moved to the other process without moving the decision it implements. It is "
        "therefore the one log where the lock is the whole mechanism rather than the backstop "
        "behind an owner -- which is what it already was before this leaf, and why its "
        "pre-existing flock was the right call kept rather than a habit inherited."
    ),
)

ORCHESTRATION_NUDGE_OWNERSHIP = StoreOwnership(
    store="orchestration-nudges",
    writers=("mcp", "dashboard"),
    compaction_owner="dashboard",
    rationale=(
        "Appended by the MCP nudge tool and by the supervisor sweep. No production reclaim pass "
        "exists yet; replace_records is the store's declared rewrite entry point, and the "
        "supervisor is the only sweep that could ever drive it, so the dashboard is named owner "
        "now rather than left to be decided by whoever writes the reclaim pass."
    ),
)

SUPERVISOR_SIGNAL_OWNERSHIP = StoreOwnership(
    store="supervisor-signals",
    writers=("dashboard",),
    compaction_owner="dashboard",
    rationale=(
        "Single writer: the supervisor sweep is the only thing that appends a signal and the "
        "only thing that compacts the cooldown log. Locked all the same, for the same reason as "
        "attention-dismissals -- the claim is about today's callers, the lock is about the file."
    ),
)


def schema_version_supported(value: str) -> bool:
    """Whether a record's ``schemaVersion`` is one this build may interpret.

    Rejects an unknown major, accepts an unknown minor. An unparseable version is rejected:
    a record that cannot say what it is cannot be trusted to be what we assume.

    UNKNOWN MEANS "NOT THIS ONE", IN BOTH DIRECTIONS -- the comparison is equality, not ``<=``.
    An earlier form accepted any major at or below :data:`SUPPORTED_SCHEMA_MAJOR`, which let
    ``"0.9"`` through while every docstring in this module and in ``worktrees/worktree_contract``
    said an unknown major is refused. Equality is the honest rule and it is also the correct one:
    there has never been a 0.x record. :data:`SCHEMA_VERSION` has been ``1.0`` since this contract
    existed and a record with no version field defaults to it, so nothing this tree can read was
    ever written under an older major. A row claiming one is corruption or a foreign artifact, and
    the reason to refuse it is the same as the reason to refuse ``"2.0"``: the record says it means
    something this code was not written to interpret. If a major 0 format is ever genuinely
    introduced, accepting it is a deliberate change here plus the migration to go with it, not a
    comparison operator that already quietly said yes.
    """
    major, _, minor = value.partition(".")
    if not major.isdigit() or not minor.isdigit():
        return False
    return int(major) == SUPPORTED_SCHEMA_MAJOR


class DurableRecord(BaseModel):
    """The fields and validation every record written through this contract shares.

    ``extra="forbid"`` is inherited rather than repeated per store (all six already declared
    it), and ``schemaVersion`` is validated on the way in, which is what gives the two read
    policies their behaviour for free: an unknown major raises ``ValidationError``, so a strict
    reader fails loudly and a tolerant reader skips the row, with no version check written into
    either of them.
    """

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str = SCHEMA_VERSION

    @field_validator("schemaVersion")
    @classmethod
    def _reject_unknown_major(cls, value: str) -> str:
        if not schema_version_supported(value):
            raise ValueError(
                f"unsupported schemaVersion {value!r}: this build implements "
                f"{DURABLE_STORE_CONTRACT} (major {SUPPORTED_SCHEMA_MAJOR}). A newer major means "
                f"the record says something this code cannot be trusted to read."
            )
        return value


class _LockDepth(threading.local):
    """Per-THREAD nesting depth for :func:`exclusive_access`, and it must stay per-thread.

    ``flock`` is held by an open file description, so two threads that each ``open`` the lockfile
    genuinely exclude one another -- which is what the dashboard needs, since its ASGI handlers
    and its projection/supervisor sweeps touch these logs from different threads. A depth counter
    shared across threads would let the second thread see "already held" and skip the lock it does
    not in fact hold. Thread-local, the counter only ever suppresses a re-acquisition the SAME
    thread would deadlock itself on.

    It is also what makes :func:`thread_mutex_for` composable rather than a deadlock: the nested
    acquisition is recognised HERE, before either lock is touched, so a thread that already holds
    a log's mutex never queues for it again.
    """

    def __init__(self) -> None:
        self.depth: dict[Path, int] = {}


_lock_depth = _LockDepth()
_verified_lock_paths: set[Path] = set()
_thread_mutexes: dict[Path, threading.RLock] = {}
_thread_mutex_registry = threading.Lock()


def lock_path_for(log_path: Path) -> Path:
    """The sibling lockfile guarding one log. Named after the log, so it is obvious what it is.

    ``<log>.jsonl`` is guarded by ``<log>.jsonl.lock`` -- the WHOLE log name plus ``.lock``, so a
    directory holding several logs cannot end up with two of them sharing a lock, and so the
    lockfile of any log can be read off the log's own name with no table to consult.

    THIS RENAMED A LOCKFILE, AND THAT MAKES A ROLLING RESTART UNSAFE. The operator inbox used to
    lock on ``operator-inbox.lock``; it now locks on ``operator-inbox.jsonl.lock``. Two processes
    running different builds would take DIFFERENT lockfiles for the same log and therefore not
    exclude each other at all -- exactly the unserialized read-filter-rewrite this contract was
    written to remove, and silent while it happens. THE MCP SERVER AND THE DASHBOARD MUST BE
    RESTARTED TOGETHER; do not restart one and leave the other up.

    There is deliberately no compatibility path -- no dual-lock period, no fall back to the old
    name (master 260731-EFA: "no deferrals, no transition periods"). A build that took both locks
    to bridge the gap would double every lock acquisition permanently to protect a window measured
    in seconds, and the surviving code would be a second lock nobody could later prove was dead.
    The restart requirement is the cost, and it is stated here rather than discovered.
    """
    return log_path.with_name(f"{log_path.name}.lock")


def thread_mutex_for(log_path: Path) -> threading.RLock:
    """The in-process mutex paired with one log's ``flock``, created once per log.

    WHY A SECOND LOCK EXISTS, stated precisely, because a lock that guards nothing is worse
    than no lock. ``flock`` DOES exclude two threads of one process -- measured, not assumed:
    the lock lives on the open file description, and :func:`exclusive_access` opens a fresh
    description on every non-reentrant acquisition, so thread B's ``LOCK_EX`` blocks on thread
    A's exactly as a second process would. (That property is what :func:`_verify_lock_capability`
    already depends on, since it proves the filesystem locks by taking the lock twice from one
    process.) So this mutex is NOT closing a loss that is reproducible today, and must not be
    described as though it were.

    What it closes is the dependence of thread exclusion on WHERE the handle comes from. The
    exclusion above is a consequence of opening the lockfile per acquisition, not of anything
    declared; cache that handle on the store -- the obvious cost fix for an append path that
    opens two files per record -- and every thread of the process would share one file
    description, ``flock`` would stop excluding them, and nothing in the tree would fail. This
    leaf exists because six stores had safety properties that held by accident of how they
    happened to be written; leaving the last one to rest on an implementation detail of the
    handle would reproduce the same shape at a smaller scale. With this, in-process exclusion
    is stated by the contract and holds however the file is opened.

    RE-ENTRANT (``RLock``), and the reason is worth stating rather than assuming. No store nests
    exclusivity today: every one of the six splits its reclaim into a public method that takes
    the lock and a ``_locked``/``_unlocked`` half that does the work, precisely so it cannot.
    That is a convention, though, not a mechanism -- one call to a public ``append`` from inside
    a ``_locked`` body would nest it -- and ``exclusive_access`` already spends a thread-local
    depth counter tolerating that case, because ``flock`` on a second file description would
    deadlock a thread against itself. Pairing that with a NON-re-entrant mutex would put the
    deadlock back one layer down, where the depth counter could not reach it. So the two locks
    tolerate re-entry or neither does; ``RLock`` keeps them composable, and the durable-store
    contract test holds it shut.
    """
    path = lock_path_for(log_path)
    mutex = _thread_mutexes.get(path)
    if mutex is not None:
        return mutex
    # Two threads reaching an unseen log at once must get the SAME mutex, or they would each
    # guard the log with a lock the other does not hold.
    with _thread_mutex_registry:
        return _thread_mutexes.setdefault(path, threading.RLock())


def _verify_lock_capability(path: Path, store: str) -> None:
    """Refuse a filesystem whose ``flock`` does not actually exclude.

    Taken twice from two file descriptions in this one process: POSIX says the second must be
    denied, so a success proves the lock is decorative -- NFS/SMB emulate ``flock`` with
    per-process byte-range locks, and WSL's DrvFs ignores it. Probing the capability is
    stronger than parsing a mount table and works the same on Linux and macOS.
    """
    if path in _verified_lock_paths:
        return
    with path.open("a+b") as first:
        fcntl.flock(first.fileno(), fcntl.LOCK_EX)
        try:
            with path.open("a+b") as second:
                try:
                    fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    _verified_lock_paths.add(path)
                    return
                fcntl.flock(second.fileno(), fcntl.LOCK_UN)
        finally:
            fcntl.flock(first.fileno(), fcntl.LOCK_UN)
    raise UnsafeLockFilesystemError(
        f"the {store} log's lock ({path}) is on a filesystem where flock does not exclude "
        f"(NFS, SMB or WSL DrvFs). The MCP server and the dashboard share a host and serialize "
        f"through this lock; on this filesystem they would not, and records would be lost "
        f"silently. Put the coordination root on a local POSIX filesystem."
    )


@contextmanager
def exclusive_access(log_path: Path, ownership: StoreOwnership) -> Iterator[None]:
    """Serialize appends and rewrites of one log against every other writer of that log.

    Unconditional -- there is no store and no process this is skipped for. ``ownership`` never
    decides WHETHER to lock; it names the log in the refusal when the filesystem turns out not
    to support locking at all.

    Held across a whole read-filter-rewrite, never around the rewrite alone: a list of records
    chosen by a read that happened outside the lock is already stale, and rewriting from it is
    the lost update under a different name -- see :func:`require_lock_held`.

    Re-entrant within one thread, because ``flock`` treats two file descriptions on one path as
    separate holders and would deadlock a nested acquisition against itself. See
    :class:`_LockDepth` for why the counter is per-thread and not per-process.

    TWO locks, taken in ONE order, always: the in-process mutex first and the log's ``flock``
    inside it. :func:`thread_mutex_for` says what the mutex adds over ``flock`` and what it
    deliberately does not claim to add. The order is what makes the pair safe -- taking ``flock``
    first would leave a thread holding a lock every other PROCESS waits on while it queues behind
    a thread of its own, and it would put the once-per-path capability probe (which takes that
    same ``flock`` twice) behind a hold this process already has. Mutex first, so a thread that
    is going to wait waits before it holds anything another host-local process needs.

    The nested acquisition returns BEFORE either lock, so the outermost frame on a thread owns
    the mutex for the whole nest and a re-entrant call cannot queue behind itself.
    """
    path = lock_path_for(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    depth = _lock_depth.depth.get(path, 0)
    if depth:
        _lock_depth.depth[path] = depth + 1
        try:
            yield
        finally:
            _lock_depth.depth[path] = depth
        return
    with thread_mutex_for(log_path):
        _verify_lock_capability(path, ownership.store)
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _lock_depth.depth[path] = 1
            try:
                yield
            finally:
                _lock_depth.depth.pop(path, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def require_lock_held(log_path: Path, store: str) -> None:
    """Refuse a rewrite whose caller is not holding the log's lock. The invariant, checked.

    A rewrite takes a list of records somebody chose. If that choice came from a read outside
    the lock, every record appended in between is about to be discarded -- the lost update this
    contract exists to remove, reached through code that looked safe because it locked its own
    write. Holding the lock across the read AND the rewrite is therefore the invariant, and this
    is the one place it is checked rather than remembered: :func:`rewrite_lines` calls it, so no
    store can rewrite a log it has not locked, however the call was reached.

    Unlike the ownership predicate this DOES raise, and it can afford to: it asks about this
    thread's own lock rather than about a process-wide declaration, so it is true or false for
    real in every process, test hosts included.
    """
    if not _lock_depth.depth.get(lock_path_for(log_path), 0):
        raise DurableStoreError(
            f"{store}: rewrite attempted without holding {log_path.name}'s lock. Hold "
            f"exclusive_access across the read AND the rewrite, or use the store's compact()."
        )


def read_log_text(log_path: Path) -> str:
    """The log's raw text, or ``""`` when it does not exist yet. The one read both policies share."""
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8")


def append_line(log_path: Path, line: str) -> None:
    """Append one record. The caller holds :func:`exclusive_access`; this only writes.

    ``fsync`` before the handle closes: an append-only log whose last records are still in the
    page cache is not durable across a host loss, and every store here exists to survive exactly
    the restart that a durable timer would not.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def rewrite_lines(log_path: Path, lines: list[str], ownership: StoreOwnership) -> None:
    """Replace a log with ``lines``: the ONE destructive rewrite in the control plane.

    Refuses unless the caller holds the log's lock, so a rewrite can never be driven by a read
    that raced an append (:func:`require_lock_held`). NEVER unlinks -- an empty record set is
    an empty file, so the appender that opened the log a moment ago writes into an inode that
    still has a name, and the empty case stops being the most dangerous branch in the store.
    The temp name carries the pid so two owners can never collide on one temp path, and both
    the temp and its directory entry are fsynced, so the rename either happened or did not.
    """
    require_lock_held(log_path, ownership.store)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = log_path.with_name(f".{log_path.name}.{os.getpid()}.tmp")
    payload = "".join(f"{line}\n" for line in lines)
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, log_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    directory = os.open(log_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
