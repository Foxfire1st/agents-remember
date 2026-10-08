"""Session-wide guard: production code touches a SQLite database only in the named modules.

MIK-R26 retired the canonical knowledge database: "If any production path is found still opening a
knowledge ``.sqlite`` other than the index cache, retirement is incomplete." The source check in
``mcp/tests/test_knowledge_database_retired.py`` reads patterns, and a pattern can be written
around. This plugin watches what the process does instead, in every test of every worker process:

* **Connections.** ``apsw.connection_hooks`` is called with each new ``apsw.Connection``, however
  the constructor was reached, and the standard library raises the audit event ``sqlite3.connect``
  for each of its connections.
* **Database files.** A file named ``*.sqlite``, ``*.sqlite3``, ``*.db`` or ``*.db3`` that is opened
  for writing, copied, moved or linked through the Python file API raises one of the audit events
  ``open``, ``shutil.copyfile``, ``os.rename`` (``os.replace`` and ``shutil.move`` raise it too),
  ``os.link`` and ``os.symlink``. A copy is recorded twice: as the copy, and as the write of its
  destination.

It sees nothing else: never a SQL statement, and never whether a path is the right one.

**Who did it.** The stack is walked outwards from the event to the nearest frame of this
repository. A frame of ``FILE_PRIMITIVES`` is passed over, so the caller of the primitive is judged.

* A test frame (``mcp/tests``, ``mcp/test_support``) means test code built a fixture, also when
  production called that test code as a double: allowed.
* A production frame decides by its module: the one operation ``ALLOWED_MODULES`` names for it is
  allowed, and every other event is recorded. The guard judges who performs the operation, never
  which file: a named module's helper that another production module calls with any path passes.
  One rule looks at the file: a module of ``DERIVED_INDEX_MODULES`` that touches a file with a name
  of ``RETIRED_DATASET_NAMES`` is recorded, because the index never opens the canonical database.
* No frame of this repository at all is charged to ``NO_REPOSITORY_FRAME``: a thread, an executor
  or ``asyncio.to_thread`` was handed a library function, or a ``weakref.finalize`` callback ran
  (what lies beneath a finalizer on the stack is whoever dropped the last reference, so the walk
  ends there). Such an ``apsw`` connection or file operation is recorded. A standard-library
  ``sqlite3.connect`` without a frame stays allowed, because coverage opens its data file that way.

**How it fails.** The test during which a recorded event happened fails at its teardown with the
operation, the path and the frame. The event itself does not raise, because production code may
catch the error and carry on. The same teardown fails a test that took the guard's hook out of
``apsw.connection_hooks`` (by assigning a new list, emptying it, or patching it), and puts the
hook back.

**What this guard cannot see.** Code that no test executes. Another process: Git materialising a
blob, a provider container, any child. A SQLite library other than ``apsw`` and the standard
library's. The journal and write-ahead files SQLite creates beside a connection. Bytes read from a
database file through the file API and written under a name that is not a database file name. A
renamed directory that holds a database file. A production open that passes through a constructor
a test replaced with its own function: the test frame is nearer, so that test alone answers for
what it lets through. A hook that is taken out and put back within one test. A library function
run by a plain ``weakref.ref`` callback or by the garbage collector on top of a repository frame:
it is charged to that frame. An event after the last test's teardown. An event raised by a thread
that outlives its test is charged to the test that is running then.
"""

from __future__ import annotations

import os
import sys
import threading
import weakref
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import agents_remember
import apsw
import pytest

CONNECT: Final = "connect"
CONNECT_READ_ONLY: Final = "connect read-only"
WRITE: Final = "write"
COPY: Final = "copy"
MOVE: Final = "move"
LINK: Final = "link"

# The production modules that may touch a database, each with the one operation it performs and
# why. A module is named, never a path: the index cache and the scratch copies lie wherever the
# caller's coordination root puts them. ``CONNECT`` includes a read-only connection.
ALLOWED_MODULES: Final[dict[str, tuple[str, str]]] = {
    "memory/knowledge_index/build.py": (
        CONNECT,
        "the derived index: its builder writes a new index file",
    ),
    "memory/knowledge_index/cache.py": (
        MOVE,
        "the derived index: a finished index file is moved into the cache",
    ),
    "memory/knowledge/connection.py": (
        CONNECT_READ_ONLY,
        "the derived index: its one read-only open",
    ),
    "memory/conversion/legacy_db.py": (
        CONNECT_READ_ONLY,
        "the conversion's read-only legacy reader",
    ),
    "memory/conversion/inputs.py": (
        WRITE,
        "the legacy reader's scratch copy of an unconverted tree's database",
    ),
    "application/legacy_dataset_copies.py": (
        CONNECT_READ_ONLY,
        "the cleanup's read-only content probe",
    ),
    "memory_quality/style/citations/source_index_database.py": (
        CONNECT,
        "the citation source index, which is not a knowledge database",
    ),
    "memory_quality/style/citations/source_index.py": (
        MOVE,
        "the citation source index: its finished file is moved into place",
    ),
}
# The three modules of the derived index. Whatever path a caller hands them, they never touch a
# file that carries the name of a retired dataset.
DERIVED_INDEX_MODULES: Final = frozenset(
    {
        "memory/knowledge_index/build.py",
        "memory/knowledge_index/cache.py",
        "memory/knowledge/connection.py",
    }
)
RETIRED_DATASET_NAMES: Final = ("knowledge.sqlite", "knowledge-candidate.sqlite", "snapshot.sqlite")
# The package's generic file primitives. An operation through one is charged to its caller, so that
# a module cannot move a database file under the primitive's name.
FILE_PRIMITIVES: Final[dict[str, str]] = {
    "kernel/atomic_write.py": "the single owner of atomic publishes",
}
# Whom an event is charged to when its stack holds no frame of this repository.
NO_REPOSITORY_FRAME: Final = "no repository frame"

DATABASE_FILE_SUFFIXES: Final = (".sqlite", ".sqlite3", ".db", ".db3")
PRODUCTION_ROOT: Final = Path(agents_remember.__file__).parent
PLUGIN_NAME: Final = __name__

_PRODUCTION_PREFIX: Final = str(PRODUCTION_ROOT) + os.sep
_OWN_FILE: Final = __file__
_TEST_ROOTS: Final = (Path(__file__).parents[2], Path(__file__).parents[3] / "tests")
_TEST_PREFIXES: Final = tuple(
    {str(root) + os.sep for one in _TEST_ROOTS for root in (one, one.resolve())}
)
_FINALIZER: Final = weakref.finalize.__call__.__code__
_WRITE_FLAGS: Final = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_REMEDY: Final = (
    "The canonical knowledge database is retired: production reads knowledge through the derived "
    "index (agents_remember.memory.knowledge_index) and writes it as text files. Remove this "
    "operation, or, if the module owns a database that is not knowledge, name it with its "
    f"operation and reason in ALLOWED_MODULES of {__name__} and in the lists of "
    "mcp/tests/test_knowledge_database_retired.py."
)
_NO_FRAME_REMEDY: Final = (
    f"An event from '{NO_REPOSITORY_FRAME}' has nobody to answer for it: a thread, an executor or "
    "a finalizer ran a library function. Call the library function from a function of the module "
    "(or the test) that owns the operation, and hand that function over instead."
)
_RETIRED_NAME_REMEDY: Final = (
    "A derived-index module was handed a file with a retired dataset name "
    f"({', '.join(RETIRED_DATASET_NAMES)}): the index never opens or writes the canonical "
    "database. Find the caller that passed this path."
)
_HOOK_REMOVED: Final = (
    "this test took the retirement guard's hook out of apsw.connection_hooks (it assigned a new "
    "list, emptied the list or patched it), so the connections opened meanwhile went unseen. The "
    "guard put its hook back. A test that needs a connection hook of its own appends it to the "
    "list and removes that one entry again."
)


@dataclass(frozen=True)
class DatabaseEvent:
    """One operation on a SQLite database or a database file, and who did it.

    ``module`` is the production module the event is charged to, ``None`` for test code, or
    ``NO_REPOSITORY_FRAME``; ``function`` then names the thread instead of a function.
    """

    operation: str
    paths: tuple[str, ...]
    module: str | None
    function: str | None
    line: int | None
    allowed: bool

    def render(self) -> str:
        where = "test code" if self.module is None else self.module
        where = where if self.line is None else f"{where}:{self.line}"
        paths = " -> ".join(path or "(in memory)" for path in self.paths)
        return f"{self.operation} {paths}, from {where} in {self.function}"


_VIOLATIONS: list[DatabaseEvent] = []
_OBSERVERS: list[list[DatabaseEvent]] = []


def _take_violations() -> list[DatabaseEvent]:
    """The recorded violations, removed from the record.

    Private: the teardown hook and the guard's own test file are the only callers, and that test
    file pins that no other test names this function. A test that emptied the record would hide
    its own violations.
    """

    taken = list(_VIOLATIONS)
    del _VIOLATIONS[: len(taken)]
    return taken


@contextmanager
def observed_database_events() -> Iterator[list[DatabaseEvent]]:
    """Every database event of this process while the block runs, allowed or not."""

    events: list[DatabaseEvent] = []
    _OBSERVERS.append(events)
    try:
        yield events
    finally:
        _OBSERVERS[:] = [one for one in _OBSERVERS if one is not events]


def events_beneath(root: Path, events: list[DatabaseEvent]) -> list[DatabaseEvent]:
    """The events on files under ``root``: a thread another test left behind is not this test's."""

    return [event for event in events if all(path.startswith(str(root)) for path in event.paths)]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown() -> Generator[None, object, object]:
    """Judge a test after its last finalizer, so a fixture's operation is charged to its test.

    The test fails when the guard recorded an event, or when its hook is missing from
    ``apsw.connection_hooks``. The hook is looked for before the finalizers too: a list a test
    patched in with ``monkeypatch`` is still in place then and gone afterwards. A teardown that
    already fails by itself carries the refusal with its own error.
    """

    unhooked = _hook_was_removed()
    try:
        result = yield
    except (Exception, pytest.fail.Exception) as error:
        refusal = _refusal(_take_violations(), unhooked=_hook_was_removed() or unhooked)
        if refusal and isinstance(error, pytest.fail.Exception):
            # A failure raised without a traceback shows its message alone, never a note.
            error.msg = f"{error.msg}\n{refusal}"
        elif refusal:
            error.add_note(refusal)
        raise
    refusal = _refusal(_take_violations(), unhooked=_hook_was_removed() or unhooked)
    if refusal:
        pytest.fail(refusal, pytrace=False)
    return result


def _hook_was_removed() -> bool:
    """Whether the guard's hook is missing from ``apsw.connection_hooks``; it is put back."""

    if _on_apsw_connection in apsw.connection_hooks:
        return False
    apsw.connection_hooks.append(_on_apsw_connection)
    return True


def _refusal(found: list[DatabaseEvent], *, unhooked: bool) -> str:
    """What the teardown reports, with the remedy of each kind of event; empty when nothing is."""

    parts = [_HOOK_REMOVED] if unhooked else []
    if found:
        parts.append(
            "a SQLite database was touched during this test in a way the retirement guard "
            "refuses:\n" + "\n".join(f"  {event.render()}" for event in found)
        )
    frameless = [event for event in found if event.module == NO_REPOSITORY_FRAME]
    retired = [event for event in found if _index_on_retired_name(event.module, event.paths)]
    if len(found) > len(frameless) + len(retired):
        parts.append(_REMEDY)
    if frameless:
        parts.append(_NO_FRAME_REMEDY)
    if retired:
        parts.append(_RETIRED_NAME_REMEDY)
    return "\n".join(parts)


def _index_on_retired_name(module: str | None, paths: tuple[str, ...]) -> bool:
    """Whether a derived-index module touches a file that carries a retired dataset's name."""

    return module in DERIVED_INDEX_MODULES and any(
        os.path.basename(path.partition("?")[0]).lower() in RETIRED_DATASET_NAMES for path in paths
    )


def _allowed(module: str | None, operation: str, paths: tuple[str, ...], library: bool) -> bool:
    """Whether an event passes: by whom it is charged to, and for the index by its file.

    ``library`` says the event is a standard-library connection, the one event that is allowed
    without a repository frame.
    """

    if module is None:
        return True
    if module == NO_REPOSITORY_FRAME:
        return library
    named = ALLOWED_MODULES.get(module)
    if named is None or _index_on_retired_name(module, paths):
        return False
    return named[0] == operation or (named[0] == CONNECT and operation == CONNECT_READ_ONLY)


def _charged() -> tuple[str | None, str | None, int | None]:
    """Whom an event is charged to: the module, the function and the line of the nearest frame."""

    frame = sys._getframe()
    while frame is not None and frame.f_code is not _FINALIZER:
        code = frame.f_code
        if code.co_filename.startswith(_PRODUCTION_PREFIX):
            module = code.co_filename[len(_PRODUCTION_PREFIX) :].replace(os.sep, "/")
            if module not in FILE_PRIMITIVES:
                return module, code.co_name, frame.f_lineno
        elif code.co_filename.startswith(_TEST_PREFIXES) and code.co_filename != _OWN_FILE:
            return None, code.co_name, frame.f_lineno
        frame = frame.f_back
    return NO_REPOSITORY_FRAME, f"thread {threading.current_thread().name}", None


def _record(operation: str, *paths: str, standard_library: bool = False) -> None:
    module, function, line = _charged()
    allowed = _allowed(module, operation, paths, standard_library)
    event = DatabaseEvent(operation, paths, module, function, line, allowed)
    for events in _OBSERVERS:
        events.append(event)
    if not allowed:
        _VIOLATIONS.append(event)


def _path(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | os.PathLike):
        return os.fsdecode(value)
    return None


def _names_database_file(path: str | None) -> bool:
    return path is not None and path.lower().endswith(DATABASE_FILE_SUFFIXES)


def _on_open(arguments: tuple[object, ...]) -> None:
    if len(arguments) < 3:
        return
    flags = arguments[2]
    if not isinstance(flags, int) or not flags & _WRITE_FLAGS:
        return
    path = _path(arguments[0])
    if path is not None and _names_database_file(path):
        _record(WRITE, path)


def _on_two_paths(operation: str) -> Callable[[tuple[object, ...]], None]:
    def watch(arguments: tuple[object, ...]) -> None:
        if len(arguments) < 2:
            return
        source, destination = _path(arguments[0]), _path(arguments[1])
        if _names_database_file(source) or _names_database_file(destination):
            _record(operation, source or "?", destination or "?")

    return watch


def _on_sqlite3_connect(arguments: tuple[object, ...]) -> None:
    database = _path(arguments[0]) if arguments else None
    text = database if database is not None else "?"
    read_only = text.startswith("file:") and "mode=ro" in text.partition("?")[2]
    _record(CONNECT_READ_ONLY if read_only else CONNECT, text, standard_library=True)


def _on_apsw_connection(connection: apsw.Connection) -> None:
    read_only = bool(connection.open_flags & apsw.SQLITE_OPEN_READONLY)
    _record(CONNECT_READ_ONLY if read_only else CONNECT, connection.filename)


_WATCHERS: Final[dict[str, Callable[[tuple[object, ...]], None]]] = {
    "open": _on_open,
    "shutil.copyfile": _on_two_paths(COPY),
    "os.rename": _on_two_paths(MOVE),
    "os.link": _on_two_paths(LINK),
    "os.symlink": _on_two_paths(LINK),
    "sqlite3.connect": _on_sqlite3_connect,
}


def _audit_hook() -> Callable[[str, tuple[object, ...]], None]:
    """The process audit hook. It runs for every audit event, so an unwatched one costs one lookup."""

    watcher_for = _WATCHERS.get

    def audit(event: str, arguments: tuple[object, ...]) -> None:
        watcher = watcher_for(event)
        if watcher is not None:
            watcher(arguments)

    return audit


# An audit hook cannot be removed, so both hooks are installed once, when the plugin is imported.
sys.addaudithook(_audit_hook())
apsw.connection_hooks.append(_on_apsw_connection)
