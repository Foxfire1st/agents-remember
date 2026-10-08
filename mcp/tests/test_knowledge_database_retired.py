"""MIK-R26: no production module opens, writes or names a canonical knowledge database.

The packet's failure rule: "If any production path is found still opening a knowledge ``.sqlite``
other than the index cache, retirement is incomplete." Two lines of defence carry out that search.
Both judge **who** opens and performs an operation, never **which file**: a listed function that
another production module calls with any path passes both. Rule 8 and one run-time rule bound that.

**The static check** reads the source of every production module, whether a test runs it or not.

1. *Constructors.* The functions that construct a SQLite connection are exactly the named ones:
   the derived index (its builder and its read-only open), the conversion's read-only legacy
   reader, the cleanup's read-only content probe, and the citation source index, which is not a
   knowledge database. Every spelling of the import is followed (``import apsw.ext``,
   ``import sqlite3.dbapi2 as d``, ``from sqlite3 import dbapi2``) and an attribute chain is
   resolved to the constructor it reaches (``sqlite3.dbapi2.connect``).
2. *Nothing else of a SQLite module.* Outside a type annotation a module names only the listed
   error classes and open flags of ``apsw`` and ``sqlite3``. A constructor is never named without
   being called or imported under another name, and the module object is never passed, stored or
   looked into (``getattr(apsw, name)``, ``vars(apsw)``, ``apsw.__dict__``).
3. *Computed imports.* ``import_module`` and ``__import__`` never name a SQLite module, and take a
   name computed at run time only in the modules listed with their reason.
4. *Read-only and attach.* Each named exception that opens an existing file opens it read-only. No
   statement starts with ``ATTACH`` followed by ``DATABASE``, a parameter, a quoted literal, a
   parenthesis or a function call before ``AS``, or one bare word before ``AS`` and a name.
5. *Writers.* Only the index builder's two modules hold a statement that starts, directly or behind
   a ``WITH`` clause, with ``INSERT [OR ...] INTO``, ``REPLACE INTO``, ``DELETE FROM`` or
   ``UPDATE [OR ...] <table> SET`` (the table bare, quoted, bracketed, schema-qualified, aliased or
   ``INDEXED BY``), or that starts with ``VACUUM ... INTO``, ``DROP TABLE``,
   ``CREATE TABLE ... AS SELECT`` or ``PRAGMA writable_schema``. The citation package is left out.
6. *Copies through SQL.* No module outside the citation package holds ``VACUUM ... INTO`` anywhere
   in a string, or calls ``.backup(``.
7. *Names.* A ``*.sqlite`` or ``*.db`` file name appears only in the listed modules, and the
   retired dataset file names only in the modules that refuse, convert or test for such a file.
8. *Callers.* Each helper that opens, builds, probes or copies a database at the path its caller
   hands it is imported or called only by the modules listed with their reason.

Strings are matched as the source spells them and as it builds them from constants (``+``,
f-strings, ``join`` over a literal sequence, a name bound once to such a string; a part computed
at run time is a placeholder), without SQL comments and statement by statement. The reader is
``agents_remember_test_support.testing.database_source_reading``; the lists are below.

*It guarantees* that, in code written with these constructs, the places that construct a
connection, hold a statement of rules 4 to 6, spell a database file name or call a helper are
exactly the listed ones. *It cannot see* a file-API copy (``shutil.copyfile``,
``open(path, "wb")``); a name or statement assembled from values (``%``, ``str.format``, a suffix
read from data, a constant imported from another module); another schema statement;
``serialize`` and ``deserialize`` of a connection; a helper named only in a string; code run from
a string (``exec``, ``eval``); a SQLite module or connection class reached without importing
``apsw`` or ``sqlite3`` by name (``sys.modules``, ``globals()``, another module's namespace,
``importlib.util``, ``pkgutil``, ``_sqlite3``, ``dbm.sqlite3``, the type of a connection); or a
SQLite library other than those two.

**The run-time guard** is the session plugin ``database_retirement_guard`` beside the reader,
registered in ``conftest.py``; its docstring says whom it charges and what it cannot see. In every
test of every worker process it sees each SQLite connection, however the constructor was reached,
and each database file written, copied, moved or linked through the file API. It sees nothing
else: never a SQL statement, never whether a path is the right one. So of the gaps above it closes
the hidden routes to a constructor and the file copies, for code a test runs; a statement built
from values is seen by neither line. (It replaced ``_Opens`` of this module and the audit hook
``_record_database_write`` of ``test_worktree_sync_knowledge_merge.py``, one case each.)

*It guarantees* that in no test of the suite (a) a production module other than the named ones, or
a named one doing another operation than its own, opened a connection or wrote, copied, moved or
linked a database file through the file API; (b) a thread, an executor or a finalizer that was
handed a library function did so with no frame of this repository on its stack, except for a
standard-library ``sqlite3.connect``, which coverage makes for its data file; (c) one of the three
derived-index modules touched a file with a retired dataset name. *It cannot see* code that no
test executes, or another process (Git materialising a blob). This module tests the guard too: a
probe compiled under a production file name commits each violation.
"""

from __future__ import annotations

import ast
import functools
import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import apsw
import knowledge_writer_test_support as fixture
import pytest
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.published_intent import published_intent_block
from agents_remember.mcp.tools import knowledge as tools
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge_index import build_index, directory_snapshot, text_uuid
from agents_remember_test_support.testing import database_retirement_guard as guard
from agents_remember_test_support.testing import database_source_reading as reader

SOURCE = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
CONNECT, CONNECT_READ_ONLY, WRITE = guard.CONNECT, guard.CONNECT_READ_ONLY, guard.WRITE
COPY, MOVE, LINK, NO_REPOSITORY_FRAME = (
    guard.COPY,
    guard.MOVE,
    guard.LINK,
    guard.NO_REPOSITORY_FRAME,
)
_take_violations = guard._take_violations

# --- the lists ----------------------------------------------------------------------------------------

# The three named exceptions (MIK-R26, ruling Q8): a production function may construct a SQLite
# connection to a knowledge file only here.
NAMED_EXCEPTIONS = {
    "memory/knowledge_index/build.py::build_index": (
        "1. the derived index: its builder writes a new index file"
    ),
    "memory/knowledge/connection.py::open_read_only_database": (
        "1. the derived index: its one read-only open"
    ),
    "memory/conversion/legacy_db.py::export_database": (
        "2. the conversion's read-only legacy reader (MIK-R24 rule 9)"
    ),
    "application/legacy_dataset_copies.py::sqlite_table_names": (
        "3. the cleanup's read-only content probe (MIK-R26 rule 6)"
    ),
}
# Not a knowledge database: the citation source index of the memory-quality checks.
OTHER_DATABASES = {
    "memory_quality/style/citations/source_index_database.py::create",
    "memory_quality/style/citations/source_index_database.py::open",
}
# The exceptions that open a file which already exists; each must open it read-only.
READ_ONLY_EXCEPTIONS = {
    "memory/knowledge/connection.py::open_read_only_database",
    "memory/conversion/legacy_db.py::export_database",
    "application/legacy_dataset_copies.py::sqlite_table_names",
    "memory_quality/style/citations/source_index_database.py::open",
}
# What else of a SQLite module production names outside a type annotation. None of these opens.
OTHER_ATTRIBUTES_ALLOWED = {
    "apsw.Error": "the error class of every apsw failure",
    "apsw.SQLITE_OPEN_READONLY": "the flag of a read-only open",
    "apsw.SQLITE_OPEN_URI": "lets the read-only content probe name its file as a URI",
    "sqlite3.DatabaseError": "an error class the citation source index reports",
    "sqlite3.IntegrityError": "an error class the citation source index reports",
}
# A module name computed at run time cannot be checked, so each module that imports one is named.
# None of them may spell a SQLite module's name in any string.
COMPUTED_IMPORTS_ALLOWED = {
    "memory_quality/style/citations/grammars.py": (
        "loads the tree-sitter grammar package its own GRAMMAR_PACKAGES table names"
    ),
    "providers/lifecycle/__init__.py": "a lazy facade over its own _EXPORT_MODULES list",
    "providers/cgc/lifecycle/__init__.py": "a lazy facade over its own _EXPORT_MODULES list",
    "providers/grepai/lifecycle/__init__.py": "a lazy facade over its own _EXPORT_MODULES list",
}
# A production module may name a database file only here; a copy of a dataset needs such a name.
SQLITE_FILE_NAMES_ALLOWED = {
    "application/knowledge_proofs.py": "the scratch derived index of a tree read without a cache",
    "kernel/memory_init.py": "tests whether a root holds legacy memory",
    "memory/conversion/inputs.py": "the legacy reader's scratch copy of an unconverted tree's file",
    "memory/knowledge_index/cache.py": "the derived index's file suffix",
    "memory_quality/style/citations/source_index.py": "the citation source index, not knowledge",
}
_RETIRED_NAMES = ("knowledge.sqlite", "knowledge-candidate.sqlite", "snapshot.sqlite")
NAMES_ALLOWED = {
    "kernel/memory_init.py": "tests whether a root holds legacy memory, so it never marks it converted",
    "memory/conversion/inputs.py": "the legacy reader's input: the file of an unconverted tree",
}
# The one package whose database is not a knowledge database: the citation source index.
_OTHER_STORES = ("memory_quality/style/citations/",)
_ROW_WRITERS = {"memory/knowledge_index/build.py", "memory/knowledge_index/projection.py"}
_REVIEW = "a reviewer read of the dataset file of each side of a comparison"
_RE_EXPORT = "re-exports it for its package"
# Each helper opens, builds, probes or copies a database at the path its caller hands it, so a new
# caller is a new doorway to any file. Whoever imports or calls one is listed with what it does.
HELPER_CALLERS: dict[str, dict[str, str]] = {
    "open_read_only_database": {
        "memory/knowledge_index/query.py": "the index's reader opens the index file of one tree",
        "memory/knowledge/store.py": "the store's two opens of one derived index",
        "memory/knowledge/logical.py": "the logical identity of one dataset file",
        "memory/knowledge/view_source.py": "the reader port reads the rows of one dataset file",
        "application/knowledge_read.py": "the mounted read of the dataset its request resolved",
        "application/knowledge_views.py": "the five query views over one index snapshot",
        "application/knowledge_diff.py": "the comparison of a baseline and a candidate dataset",
        "application/review_attribution.py": _REVIEW,
        "application/review_family_rosters.py": _REVIEW,
        "application/review_intent_summary.py": _REVIEW,
        "application/review_recorded_selection.py": _REVIEW,
        "application/review_relationship_movement.py": _REVIEW,
        "application/review_revision_comparison.py": _REVIEW,
        "application/review_source_realization_link.py": _REVIEW,
    },
    "open_existing_knowledge_store": {
        "memory/knowledge/__init__.py": _RE_EXPORT,
        "application/review_family_rosters.py": _REVIEW,
        "application/review_subject_catalogue.py": _REVIEW,
    },
    "open_read_only_store": {"memory/knowledge/__init__.py": _RE_EXPORT},
    "build_index": {
        "memory/knowledge_index/__init__.py": _RE_EXPORT,
        "memory/knowledge_index/cache.py": "builds the index file of a tree key into the cache",
        "application/knowledge_proofs.py": "builds a scratch index of a tree read without a cache",
    },
    "export_database": {"memory/conversion/convert.py": "the conversion reads the legacy database"},
    "sqlite_table_names": {
        "application/review_artifact_cleanup.py": "the archive hook probes a file before deleting",
    },
    "memory_from_git": {
        "memory/conversion/base.py": "the converted base of a comparison",
        "memory/conversion/crossing_port.py": "the three memory commits of a crossing sync",
    },
    # The citation index's ``Database`` class, pinned by its module.
    "source_index_database": {
        "memory_quality/style/citations/source_index.py": "builds and reads the citation index",
    },
    # The package this guard lives in: production never reaches into it to switch the guard off.
    "agents_remember_test_support": {},
}


@functools.cache
def _modules() -> dict[str, ast.Module]:
    return {
        path.relative_to(SOURCE).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE.rglob("*.py"))
    }


def _readings() -> dict[str, reader.Reading]:
    return {relative: reader.read_module(tree) for relative, tree in _modules().items()}


def _constructor_sites() -> tuple[dict[str, list[ast.Call]], list[str]]:
    sites: dict[str, list[ast.Call]] = {}
    aliases: list[str] = []
    for relative, reading in _readings().items():
        for function, call in reading.constructors:
            sites.setdefault(f"{relative}::{function}", []).append(call)
        aliases.extend(f"{relative}: {alias}" for alias in reading.aliases)
    return sites, aliases


# --- the static check ---------------------------------------------------------------------------------


def test_the_functions_that_construct_a_sqlite_connection_are_exactly_the_named_ones() -> None:
    """A new open anywhere in production fails here, and so does one reached through an alias."""

    sites, aliases = _constructor_sites()

    assert aliases == [], f"a SQLite constructor is reached through an alias: {aliases}"
    expected = set(NAMED_EXCEPTIONS) | OTHER_DATABASES
    assert set(sites) == expected, {
        "new opens (retirement incomplete)": sorted(set(sites) - expected),
        "named but no longer present (remove the name)": sorted(expected - set(sites)),
    }
    assert all(len(calls) == 1 for calls in sites.values()), {
        site: len(calls) for site, calls in sites.items() if len(calls) != 1
    }


def test_a_sqlite_module_is_named_only_for_its_listed_error_classes_and_flags() -> None:
    """Whatever else a module takes from ``apsw`` or ``sqlite3`` may open a database unseen."""

    named: dict[str, list[str]] = {}
    for relative, reading in _readings().items():
        for attribute in reading.attributes:
            named.setdefault(attribute, []).append(relative)
    assert set(named) == set(OTHER_ATTRIBUTES_ALLOWED), {
        "new (a submodule, a function or a flag the check does not know)": {
            attribute: named[attribute] for attribute in set(named) - set(OTHER_ATTRIBUTES_ALLOWED)
        },
        "gone (remove the name)": sorted(set(OTHER_ATTRIBUTES_ALLOWED) - set(named)),
    }


def test_a_module_name_computed_at_run_time_is_imported_only_by_the_named_modules() -> None:
    """``import_module(name)`` could load a SQLite module under a name no check can read."""

    readings = _readings()
    computing = {relative for relative, reading in readings.items() if reading.computed_imports}
    assert computing == set(COMPUTED_IMPORTS_ALLOWED), {
        "new": {relative: readings[relative].computed_imports for relative in computing},
        "gone": sorted(set(COMPUTED_IMPORTS_ALLOWED) - computing),
    }
    for relative in sorted(COMPUTED_IMPORTS_ALLOWED):
        spelled = [
            text
            for text in readings[relative].texts
            if text.split(".")[0].strip() in reader.DATABASE_MODULES
        ]
        assert spelled == [], f"{relative} spells a SQLite module's name: {spelled}"


def test_every_open_of_an_existing_knowledge_file_is_read_only() -> None:
    """Each named exception that opens an existing file cannot write it, and nothing attaches."""

    sites, _aliases = _constructor_sites()
    for site in sorted(READ_ONLY_EXCEPTIONS):
        (call,) = sites[site]
        text = ast.unparse(call)
        assert "SQLITE_OPEN_READONLY" in text or "mode=ro" in text, f"{site} opens writable: {text}"
        assert "SQLITE_OPEN_READWRITE" not in text and "SQLITE_OPEN_CREATE" not in text, text
    attaching = {
        relative: reading.attaches for relative, reading in _readings().items() if reading.attaches
    }
    assert attaching == {}, f"a statement attaches a database: {attaching}"


def test_only_the_index_builder_writes_knowledge_rows() -> None:
    """No production writer survives: a statement that writes a row is in the index builder."""

    writers = {
        relative: reading.writes
        for relative, reading in _readings().items()
        if reading.writes and not relative.startswith(_OTHER_STORES)
    }
    assert set(writers) == _ROW_WRITERS, writers


def test_no_sql_outside_the_index_builder_copies_a_database() -> None:
    """``VACUUM INTO`` and the backup API copy a database without a file copy call."""

    offenders = {
        relative: reading.copies
        for relative, reading in _readings().items()
        if reading.copies and not relative.startswith(_OTHER_STORES)
    }
    assert offenders == {}


def test_a_sqlite_file_name_appears_only_in_the_listed_modules() -> None:
    naming = {
        relative
        for relative, reading in _readings().items()
        if any(reader.DATABASE_FILE_NAME.search(text) for text in reading.texts)
    }
    assert naming == set(SQLITE_FILE_NAMES_ALLOWED), {
        "new": sorted(naming - set(SQLITE_FILE_NAMES_ALLOWED)),
        "gone": sorted(set(SQLITE_FILE_NAMES_ALLOWED) - naming),
    }


def test_the_retired_dataset_file_names_appear_only_where_named() -> None:
    naming = {
        relative
        for relative, reading in _readings().items()
        if any(name in text for text in reading.texts for name in _RETIRED_NAMES)
    }
    expected = set(NAMES_ALLOWED)
    assert naming == expected, {
        "new": sorted(naming - expected),
        "gone": sorted(expected - naming),
    }


def test_a_helper_that_opens_a_database_is_called_only_by_the_listed_modules() -> None:
    """A module that starts to import or call one of the helpers fails here until it is listed."""

    callers = {
        helper: sorted(
            name for name, reading in _readings().items() if helper in reading.foreign_names
        )
        for helper in HELPER_CALLERS
    }
    assert callers == {helper: sorted(listed) for helper, listed in HELPER_CALLERS.items()}


# --- the static check, checked ------------------------------------------------------------------------

_CONSTRUCTORS = test_the_functions_that_construct_a_sqlite_connection_are_exactly_the_named_ones
_ATTRIBUTES = test_a_sqlite_module_is_named_only_for_its_listed_error_classes_and_flags
_IMPORTS = test_a_module_name_computed_at_run_time_is_imported_only_by_the_named_modules
_READ_ONLY = test_every_open_of_an_existing_knowledge_file_is_read_only
_WRITERS = test_only_the_index_builder_writes_knowledge_rows
_COPIES = test_no_sql_outside_the_index_builder_copies_a_database
_NAMES = test_a_sqlite_file_name_appears_only_in_the_listed_modules
_RETIRED = test_the_retired_dataset_file_names_appear_only_where_named
_CALLERS = test_a_helper_that_opens_a_database_is_called_only_by_the_listed_modules
_WRITE_SPELLINGS = (
    "UPDATE main.invariant SET a = 1",
    'UPDATE "invariant" SET a = 1',
    "UPDATE [invariant] SET a = 1",
    "UPDATE invariant AS i SET a = 1",
    "UPDATE invariant INDEXED BY x SET a = 1",
    "INSERT INTO main.invariant VALUES (1)",
    "DELETE FROM main.invariant",
    "WITH t AS (SELECT 1 AS x) INSERT INTO invariant SELECT x FROM t",
    "WITH t AS (SELECT 1 AS x) UPDATE invariant SET a = (SELECT x FROM t)",
    "WITH t AS (SELECT 1 AS x) DELETE FROM invariant WHERE a IN t",
    "WITH RECURSIVE t(x) AS (SELECT 1) REPLACE INTO invariant SELECT x FROM t",
    "CREATE TABLE copy AS SELECT * FROM invariant",
    "DROP TABLE invariant",
    "PRAGMA writable_schema = 1",
)
# Each source below, added to the tree as one more production module, must fail the test named
# beside it. The first group passed every test before the test was taught the form.
_FORMS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("import apsw.ext\ndef f(p): return apsw.Connection(p)\n", _CONSTRUCTORS),
    ("import sqlite3\ndef f(p): return sqlite3.dbapi2.connect(p)\n", _CONSTRUCTORS),
    ("from sqlite3 import dbapi2\ndef f(p): return dbapi2.connect(p)\n", _CONSTRUCTORS),
    ("import sqlite3.dbapi2 as d\ndef f(p): return d.connect(p)\n", _CONSTRUCTORS),
    ("import apsw.ext as e\ndef f(p): return e.Connection(p)\n", _CONSTRUCTORS),
    ('import apsw\nN = "Conn" + "ection"\ndef f(p): return getattr(apsw, N)(p)\n', _CONSTRUCTORS),
    ('import apsw\ndef f(p): return vars(apsw)["Connection"](p)\n', _CONSTRUCTORS),
    ('import apsw\ndef f(p): return apsw.__dict__["Connection"](p)\n', _ATTRIBUTES),
    ("import apsw\nopener = apsw\n", _CONSTRUCTORS),
    ("import sqlite3\nlibrary = sqlite3.dbapi2\n", _ATTRIBUTES),
    ("import apsw\nFLAGS = apsw.SQLITE_OPEN_READWRITE\n", _ATTRIBUTES),
    ('def f(c): c.execute("ATTACH ? AS other", ("x",))\n', _READ_ONLY),
    ("def f(c): c.execute(\"ATTACH '/tmp/a' AS other\")\n", _READ_ONLY),
    ('def f(c, p): c.execute(f"attach {p!r} as other")\n', _READ_ONLY),
    ('def f(c): c.execute("SELECT 1; ATTACH ? AS other", ("x",))\n', _READ_ONLY),
    (
        'import importlib\ndef f(p): return importlib.import_module("sq" + "lite3").connect(p)\n',
        _CONSTRUCTORS,
    ),
    ("import importlib\ndef f(n, p): return importlib.import_module(n).connect(p)\n", _IMPORTS),
    ("from importlib import import_module as load\ndef f(n): return load(n)\n", _IMPORTS),
    ('def f(n, p): return __import__("sq" + n).connect(p)\n', _IMPORTS),
    ('def f(c): c.execute("-- x\\nINSERT INTO t VALUES (1)")\n', _WRITERS),
    ('def f(c): c.execute("/* x */ DELETE FROM t")\n', _WRITERS),
    ('def f(c): c.execute("SELECT 1; UPDATE t SET a = 1")\n', _WRITERS),
    ('def f(c, t): c.execute(f"UPDATE {t} SET a = 1")\n', _WRITERS),
    ('NAME = "knowledge" + ".sql" + "ite"\n', _NAMES),
    ('SUFFIX = ".sql" + "ite"\ndef f(d, stem): return d / f"{stem}{SUFFIX}"\n', _NAMES),
    ('def f(d): return d / "".join(["copy", ".d", "b"])\n', _NAMES),
    # The forms the tests caught before, which they must go on catching.
    ("import apsw\nopener = getattr(apsw, 'Connection')\n", _CONSTRUCTORS),
    ("import importlib\nm = importlib.import_module('sqlite3')\n", _CONSTRUCTORS),
    ("m = __import__('apsw')\n", _CONSTRUCTORS),
    ("from apsw import *\n", _CONSTRUCTORS),
    ("import sqlite3\nc = sqlite3.Connection('x')\n", _CONSTRUCTORS),
    ("import sqlite3 as s\nc = s.connect('x')\n", _CONSTRUCTORS),
    ("from sqlite3 import connect as c\n", _CONSTRUCTORS),
    ("from apsw import Connection as C\n", _CONSTRUCTORS),
    ("def f(p):\n    import apsw\n    return apsw.Connection(p)\n", _CONSTRUCTORS),
    ("import apsw\nopener = apsw.Connection\n", _CONSTRUCTORS),
    ('def f(c): c.execute("ATTACH DATABASE ? AS other", ("x",))\n', _READ_ONLY),
    ('def f(c): c.execute("replace into t values (1)")\n', _WRITERS),
    ('def f(c): c.execute("INSERT OR REPLACE INTO t VALUES (1)")\n', _WRITERS),
    ('def f(c): c.execute("UPDATE OR REPLACE t SET a = 1")\n', _WRITERS),
    ('def f(c, t): c.execute(f"INSERT INTO {t} VALUES (1)")\n', _WRITERS),
    ('def f(c, t): c.execute("INSERT INTO " + t + " VALUES (1)")\n', _WRITERS),
    ('def f(c): c.execute("vacuum into ?", ("x",))\n', _COPIES),
    ('def f(c): c.execute("VACUUM main INTO ?", ("x",))\n', _COPIES),
    ('def f(c, d): return d.backup("main", c, "main")\n', _COPIES),
    # Further spellings of an attach and of a write, and a helper reached by an unlisted module.
    ('def f(c): c.execute("ATTACH (?) AS other", ("x",))\n', _READ_ONLY),
    ("def f(c): c.execute(\"ATTACH ('/p') AS other\")\n", _READ_ONLY),
    ('def f(c): c.execute("ATTACH CAST(? AS TEXT) AS other", ("x",))\n', _READ_ONLY),
    *((f"def f(c): c.execute({statement!r})\n", _WRITERS) for statement in _WRITE_SPELLINGS),
    *((f"from agents_remember.elsewhere import {helper}\n", _CALLERS) for helper in HELPER_CALLERS),
    ("from ..memory.knowledge import connection as c\nf = c.open_read_only_database\n", _CALLERS),
    ("import agents_remember.memory.knowledge_index as index\nf = index.build_index\n", _CALLERS),
    (
        "from ..memory.conversion.inputs import *\ndef f(r): return memory_from_git(r, 'x', r)\n",
        _CALLERS,
    ),
)
# Every static test: each one a form names, and the retired-names test.
_STATIC_TESTS = tuple({test for _source, test in _FORMS} | {_RETIRED})
# What the legitimate modules write: it must fail no test, or the forms above prove nothing.
_LEGITIMATE = (
    "import apsw\n"
    "def f(c: apsw.Connection, flags: int = apsw.SQLITE_OPEN_READONLY) -> list[apsw.Connection]:\n"
    "    try:\n"
    '        c.execute("SELECT 1 -- not an INSERT INTO anything")\n'
    '        c.execute("WITH t AS (SELECT update_time FROM x) SELECT update_time FROM t")\n'
    "    except apsw.Error:\n"
    "        return []\n"
    "    return [c]\n"
    "def g(args):\n"
    "    build_index = args.build_index\n"
    "    return build_index\n"
)


def _failing(
    source: str, monkeypatch: pytest.MonkeyPatch, relative: str = "application/probe_slip.py"
) -> set[Callable[[], None]]:
    """The static tests that fail once ``source`` is one more production module."""

    modules = {**_modules(), relative: ast.parse(source)}
    failing: set[Callable[[], None]] = set()
    with monkeypatch.context() as patched:
        patched.setitem(globals(), "_modules", lambda: modules)
        for test in _STATIC_TESTS:
            try:
                test()
            except AssertionError:
                failing.add(test)
    return failing


def test_the_static_check_catches_the_forms_the_review_found_it_missed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each hidden constructor, lookup, statement and name fails the real test over the tree."""

    assert _failing(_LEGITIMATE, monkeypatch) == set()
    for source, test in _FORMS:
        assert test in _failing(source, monkeypatch), source
    # No package but the citation index's is left out of the writer and copy rules.
    insert = 'def f(c): c.execute("INSERT INTO t VALUES (1)")\n'
    assert _WRITERS in _failing(insert, monkeypatch, "serving/probe_slip.py")
    assert _WRITERS not in _failing(insert, monkeypatch, _OTHER_STORES[0] + "probe_slip.py")


# --- the run-time guard, checked ----------------------------------------------------------------------

_PROBE_MODULE = "application/probe_slip.py"
# Each function commits one operation; the file name it is compiled under decides who is charged.
_PROBE_SOURCE = """
import asyncio, concurrent.futures, importlib, os, shutil, sqlite3, threading, weakref
import apsw

class _Token:
    pass

def hidden_constructor(path, name):
    getattr(apsw, name)(str(path)).close()

def dotted_submodule(path):
    sqlite3.dbapi2.connect(path).close()

def computed_import(path, name):
    importlib.import_module(name).connect(path).close()

def file_copy(source, directory, suffix):
    shutil.copyfile(source, directory / ("copy" + suffix))

def file_write(path):
    with open(path, "wb") as handle:
        handle.write(b"SQLite format 3")

def file_move(source, destination):
    os.replace(source, destination)

def file_link(source, hard, symbolic):
    os.link(source, hard)
    os.symlink(source, symbolic)

def read_only(path):
    apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READONLY).close()

def through(function, *arguments):
    return function(*arguments)

def on_a_thread(function, *arguments):
    worker = threading.Thread(target=function, args=arguments)
    worker.start()
    worker.join()

def in_a_pool(function, *arguments):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(function, *arguments).result()

def to_a_thread(function, *arguments):
    asyncio.run(asyncio.to_thread(function, *arguments))

def at_finalization(function, *arguments):
    weakref.finalize(_Token(), function, *arguments)
"""


def _production_probe(module: str) -> dict[str, Any]:
    """The probe's functions, compiled as if their source were the production module ``module``.

    Only the file name of the code is production's: nothing is written into the package.
    """

    namespace: dict[str, Any] = {}
    exec(compile(_PROBE_SOURCE, str(guard.PRODUCTION_ROOT / module), "exec"), namespace)
    return namespace


def test_the_run_time_guard_records_what_the_source_cannot_show(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    """A violation a production frame commits is recorded, whatever its source looked like.

    The first three reach a constructor by a computed name, a submodule and a computed import;
    the others copy, write and link a database file, the copy under a name built at run time.
    """

    assert pytestconfig.pluginmanager.hasplugin(guard.PLUGIN_NAME)
    assert guard.PLUGIN_NAME in {
        implementation.function.__module__
        for implementation in pytestconfig.hook.pytest_runtest_teardown.get_hookimpls()
    }
    # Only the plugin and this file name the function that empties the record.
    tests = Path(__file__).parent
    emptying = {
        path.name
        for path in (*tests.glob("*.py"), *(tests.parent / "test_support").rglob("*.py"))
        if "_take_violations" in path.read_text(encoding="utf-8")
    }
    assert emptying == {Path(__file__).name, f"{guard.PLUGIN_NAME.rsplit('.', 1)[-1]}.py"}
    probe = _production_probe(_PROBE_MODULE)
    dataset = tmp_path / "knowledge.sqlite"
    apsw.Connection(str(dataset)).close()
    other, links = tmp_path / "other.db", (tmp_path / "hard.sqlite", tmp_path / "symbolic.sqlite")
    cases: dict[str, tuple[tuple[Any, ...], list[str]]] = {
        "hidden_constructor": ((dataset, "Conn" + "ection"), [CONNECT]),
        "dotted_submodule": ((other,), [CONNECT]),
        "computed_import": ((other, "sq" + "lite3"), [CONNECT]),
        "file_copy": ((dataset, tmp_path, ".sql" + "ite"), [COPY, WRITE]),
        "file_write": ((tmp_path / "written.db",), [WRITE]),
        "file_link": ((dataset, *links), [LINK] * 2),
    }
    for name, (arguments, operations) in cases.items():
        probe[name](*arguments)
        recorded = _take_violations()
        assert [(event.operation, event.module, event.function) for event in recorded] == [
            (operation, _PROBE_MODULE, name) for operation in operations
        ], name
    assert (tmp_path / "copy.sqlite").is_file() and (tmp_path / "written.db").is_file()

    # The teardown hook refuses what is left in the record, and names what happened and where.
    probe["file_write"](tmp_path / "again.db")
    teardown = guard.pytest_runtest_teardown()
    next(teardown)
    with pytest.raises(pytest.fail.Exception) as refused:
        next(teardown)
    message = str(refused.value)
    assert f"write {tmp_path / 'again.db'}, from {_PROBE_MODULE}:" in message, message
    assert "in file_write" in message and "ALLOWED_MODULES" in message, message
    # A teardown that fails by itself still carries the refusal, in the message pytest prints.
    for own in (
        RuntimeError("the fixture's own error"),
        pytest.fail.Exception("own", pytrace=False),
    ):
        probe["file_write"](tmp_path / "again.db")
        teardown = guard.pytest_runtest_teardown()
        next(teardown)
        with pytest.raises(type(own)) as carried:
            teardown.throw(own)
        shown = [str(carried.value), *getattr(carried.value, "__notes__", [])]
        assert any(f"write {tmp_path / 'again.db'}" in text for text in shown), shown
    assert _take_violations() == []


def test_the_run_time_guard_allows_the_named_modules_and_test_code(tmp_path: Path) -> None:
    """The same operations are no violation from test code, or from the module named for them."""

    dataset = tmp_path / "index.sqlite"
    written, moved = tmp_path / "written.db", tmp_path / "moved.db"
    with guard.observed_database_events() as observed:
        apsw.Connection(str(dataset)).close()
        sqlite3.connect(tmp_path / "other.db").close()
        shutil.copyfile(dataset, tmp_path / "copy.sqlite")
        written.write_bytes(b"")
        # A library function that production calls is charged to production; a test double that
        # production calls is test code, because the nearest repository frame is the test's.
        _production_probe(_PROBE_MODULE)["through"](shutil.copyfile, dataset, written)
        _production_probe(_PROBE_MODULE)["through"](_double, dataset)
    events = guard.events_beneath(tmp_path, observed)
    assert [event.operation for event in events] == [
        *(CONNECT, CONNECT, COPY, WRITE, WRITE),
        *(COPY, WRITE, CONNECT_READ_ONLY),
    ]
    assert [event.module for event in events] == [*[None] * 5, *[_PROBE_MODULE] * 2, None]
    assert [(event.operation, event.function) for event in _take_violations()] == [
        (COPY, "through"),
        (WRITE, "through"),
    ]

    operations = {CONNECT, CONNECT_READ_ONLY, WRITE, MOVE}
    for module, (operation, _reason) in guard.ALLOWED_MODULES.items():
        probe = _production_probe(module)
        with guard.observed_database_events() as observed:
            probe["hidden_constructor"](dataset, "Connection")
            probe["read_only"](dataset)
            probe["file_write"](written)
            probe["file_move"](written, moved)
        events = guard.events_beneath(tmp_path, observed)
        assert [event.module for event in events] == [module] * 4
        allowed = {event.operation for event in events if event.allowed}
        assert allowed == ({CONNECT, CONNECT_READ_ONLY} if operation == CONNECT else {operation})
        assert {event.operation for event in _take_violations()} == operations - allowed, module

    # A file primitive is passed over: its caller is charged, and judged by its own entry.
    primitive = _production_probe(next(iter(guard.FILE_PRIMITIVES)))["file_move"]
    written.write_bytes(b"")
    _production_probe("memory/knowledge_index/cache.py")["through"](primitive, written, moved)
    assert _take_violations() == []
    _production_probe(_PROBE_MODULE)["through"](primitive, moved, written)
    assert [(event.operation, event.module) for event in _take_violations()] == [
        (MOVE, _PROBE_MODULE)
    ]


def _double(path: Path) -> None:
    apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READONLY).close()


def test_an_event_with_no_repository_frame_is_refused_unless_coverage_could_make_it(
    tmp_path: Path,
) -> None:
    """A library function handed to a thread, an executor or a finalizer answers for itself."""

    probe = _production_probe(_PROBE_MODULE)
    dataset = tmp_path / "index.sqlite"
    apsw.Connection(str(dataset)).close()
    for name in ("on_a_thread", "in_a_pool", "to_a_thread", "at_finalization"):
        probe[name](shutil.copyfile, dataset, tmp_path / f"{name}.sqlite")
        assert [(event.operation, event.module) for event in _take_violations()] == [
            (COPY, NO_REPOSITORY_FRAME),
            (WRITE, NO_REPOSITORY_FRAME),
        ], name
    probe["in_a_pool"](apsw.Connection, str(dataset)).close()
    (refused,) = _take_violations()
    assert (refused.operation, refused.module) == (CONNECT, NO_REPOSITORY_FRAME)
    assert f"from {NO_REPOSITORY_FRAME} in thread " in refused.render()
    # Coverage opens its data file with the standard library, with no frame of this repository.
    connect = functools.partial(sqlite3.connect, check_same_thread=False)
    with guard.observed_database_events() as observed:
        probe["in_a_pool"](connect, tmp_path / "coverage.db").close()
    (made,) = guard.events_beneath(tmp_path, observed)
    assert made.allowed and (made.operation, made.module) == (CONNECT, NO_REPOSITORY_FRAME)
    assert _take_violations() == []


def test_a_test_that_takes_the_guards_hook_out_fails_and_the_hook_is_put_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new list, an emptied list or a patched list would switch the connection half off."""

    probe, kept = _production_probe(_PROBE_MODULE), apsw.connection_hooks
    dataset = tmp_path / "index.sqlite"
    apsw.Connection(str(dataset)).close()
    for how in ("assigned", "emptied", "patched"):
        if how == "assigned":
            apsw.connection_hooks = []
        elif how == "emptied":
            apsw.connection_hooks.clear()
        else:
            monkeypatch.setattr(apsw, "connection_hooks", [])
        probe["read_only"](dataset)
        assert _take_violations() == []
        teardown = guard.pytest_runtest_teardown()
        next(teardown)
        monkeypatch.undo()  # what the finalizers do between the two halves of the hook
        with pytest.raises(pytest.fail.Exception, match="took the retirement guard's hook out"):
            next(teardown)
        probe["read_only"](dataset)
        assert [event.operation for event in _take_violations()] == [CONNECT_READ_ONLY]
    apsw.connection_hooks = kept


def test_the_run_time_guard_names_the_modules_the_static_check_names() -> None:
    """One retirement, two lists: the run-time guard allows no module the source check does not."""

    sites = set(NAMED_EXCEPTIONS) | OTHER_DATABASES
    writable = {site.split("::")[0] for site in sites - READ_ONLY_EXCEPTIONS}
    connecting = {
        module: operation
        for module, (operation, _reason) in guard.ALLOWED_MODULES.items()
        if operation in {CONNECT, CONNECT_READ_ONLY}
    }
    assert connecting == {
        module: CONNECT if module in writable else CONNECT_READ_ONLY
        for module in (site.split("::")[0] for site in sites)
    }
    assert set(guard.ALLOWED_MODULES) - set(connecting) <= set(SQLITE_FILE_NAMES_ALLOWED)
    assert set(guard.ALLOWED_MODULES) > guard.DERIVED_INDEX_MODULES
    assert guard.RETIRED_DATASET_NAMES == _RETIRED_NAMES
    assert all(
        (SOURCE / module).is_file() for module in [*guard.ALLOWED_MODULES, *guard.FILE_PRIMITIVES]
    )


# --- the tool surface under the run-time guard --------------------------------------------------------


def test_the_tool_surface_and_the_writer_open_only_the_derived_index_cache(tmp_path: Path) -> None:
    """What the knowledge surface actually touches, by every route the run-time guard sees.

    The memory tree holds a file named ``knowledge.sqlite`` (the database a converted tree still
    carried before this leaf removed it), so an open or a copy of it would be caught by path.
    """

    world = fixture.build_world(tmp_path)
    frozen = world.memory / "knowledge.sqlite"
    frozen.write_bytes(b"SQLite format 3\x00 frozen at the cutover")
    coordination = tmp_path / "coordination"
    cache = coordination / "runtime" / "knowledge-index"

    with guard.observed_database_events() as events:
        _drive_the_knowledge_surface(world, coordination)

    assert events, "the guard saw no database event at all, so it measured nothing"
    assert {event.module for event in events} == guard.DERIVED_INDEX_MODULES, [
        event.render() for event in events if event.module not in guard.DERIVED_INDEX_MODULES
    ]
    outside = [
        event.render()
        for event in events
        if any(Path(path).parent != cache for path in event.paths)
    ]
    assert outside == [], f"touched outside the derived index cache: {outside}"
    # A writable open in the cache is the index builder creating a new index file; every later
    # open of that file is read-only.
    built = [event.paths[0] for event in events if event.operation == CONNECT]
    assert built and len(built) == len(set(built)), "an index file was opened writable twice"
    assert frozen.read_bytes().startswith(b"SQLite format 3\x00 frozen")
    assert json.loads((world.memory / "knowledge/layout.json").read_text())["schema"]

    # The index's helpers judge the file as well: handed a retired dataset name by any caller,
    # they are refused, and the legacy reader, whose file that is, is not.
    through = _production_probe(_PROBE_MODULE)["through"]
    snapshot = directory_snapshot(world.memory)
    retired, scratch = tmp_path / "elsewhere" / "knowledge.sqlite", cache / "scratch.sqlite"
    retired.parent.mkdir()
    through(build_index, snapshot, scratch)
    through(open_read_only_database, scratch).close()
    _production_probe("memory/conversion/legacy_db.py")["read_only"](frozen)
    assert _take_violations() == []
    through(build_index, snapshot, retired)
    through(open_read_only_database, retired).close()
    _production_probe("memory/knowledge_index/cache.py")["file_move"](scratch, retired)
    assert [(event.operation, event.module) for event in _take_violations()] == [
        (CONNECT, "memory/knowledge_index/build.py"),
        (CONNECT_READ_ONLY, "memory/knowledge/connection.py"),
        (MOVE, "memory/knowledge_index/cache.py"),
    ]


def _drive_the_knowledge_surface(world: Any, coordination: Path) -> None:
    """The mounted read, diff and integrity check, the published-intent read and the writer."""

    def read(**fields: Any) -> dict[str, Any]:
        return tools.knowledge_read_payload(
            tools.ReadToolRequest(memory_root=str(world.memory), **fields),
            workspace_root=str(world.code),
            coordination_root=str(coordination),
        )

    family = read(
        view="family", family_revision_id=text_uuid("revision", f"{fixture.BASE_FAMILY}@1")
    )
    assert family["state"] in {"view", "page"}, family
    invariant = read(
        view="invariant", invariant_revision_id=text_uuid("revision", f"{fixture.BASE_INVARIANT}@1")
    )
    assert invariant["state"] in {"view", "page"}, invariant
    leaf = read(view="source_context", source_path=fixture.CODE_FILE)
    assert leaf["state"] == "page", leaf
    assert read(view="curation_queue")["state"] in {"view", "page"}

    compared = tools.knowledge_diff_payload(
        tools.DiffToolRequest(memory_root=str(world.memory), before="HEAD", after="HEAD")
    )
    assert compared["state"] == "compared", compared

    checked = tools.knowledge_integrity_check_payload(
        tools.IntegrityCheckRequest(contractPath=str(world.contract))
    )
    assert checked["state"] == "reported", checked

    block = published_intent_block(_context(world, coordination), [fixture.CODE_FILE])
    assert block["state"] == "recorded", block

    written = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=fixture.TASK_ID, kind="leaf", id=fixture.LEAF_ID),
            handoff_path="handoff.json",
            document={"entries": [_entry()]},
            commit=True,
            coordination_root=coordination,
        )
    )
    assert written.state == "written", written.render()


def _entry() -> dict[str, Any]:
    proves = [fixture.target("land_pair")]
    return fixture.entry("G-1", target=proves, scope=fixture.SCOPE, admission=fixture.ADMISSION)


def _context(world: Any, coordination: Path) -> Any:
    """The coordination context a taskless read of ``world``'s memory tree resolves."""

    return SimpleNamespace(
        memory_root=world.memory,
        coordination_root=coordination,
        code_repository_root=world.code,
        code_repository_name="agents-remember",
    )
