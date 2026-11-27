"""LanguageSet seam, generic tags.scm engine policies, and cache-key isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.knowledge_currentness import observe
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.errors import AgentsRememberError
from agents_remember.memory.conversion.code_objects import SYMBOL_SPANS, CodeObjects
from agents_remember.memory.knowledge import read_anchors
from agents_remember.memory.knowledge.read_anchor_memo import BLOB_DEFINITIONS
from agents_remember.memory_quality.style.citations import (
    extents,
    grammars,
    model,
    tags_engine,
)
from agents_remember.memory_quality.style.citations.language_set import (
    LEAN,
    LanguageSet,
    LeanLanguageProvider,
    TreeSitterLanguageProvider,
)
from knowledge_conversion_test_support import commit_files, init_repository

_FUNCTIONS_ONLY_PYTHON = """
; Captures functions only, with upstream @doc and @local.scope captures tolerated.
(function_definition
  name: (identifier) @name
  body: (block (expression_statement (string) @doc)?)) @definition.function
"""


def _functions_only_python_set() -> LanguageSet:
    return grammars.default_language_set().with_providers(
        [
            TreeSitterLanguageProvider(
                key="python",
                suffixes=frozenset({".py"}),
                grammar_version="tree-sitter-python=0.25.0",
                load_language=lambda: grammars.language("python"),
                tags_query_text=_FUNCTIONS_ONLY_PYTHON,
                wrappers=frozenset({"decorated_definition"}),
            )
        ]
    )


def test_lean_suffix_is_permanently_reserved_from_tree_sitter_providers() -> None:
    rogue_lean = TreeSitterLanguageProvider(
        key="lean_ts",
        suffixes=frozenset({".lean"}),
        grammar_version="tree-sitter-lean=0.0.1",
        load_language=lambda: grammars.language("python"),
        tags_query_text=_FUNCTIONS_ONLY_PYTHON,
    )
    with pytest.raises(AgentsRememberError, match="reserved for the built-in Lean scanner"):
        LanguageSet(providers=(rogue_lean,), tree_sitter_version="0.26.1")

    default_provider = grammars.default_language_set().resolve("Proof.lean")
    assert isinstance(default_provider, LeanLanguageProvider)
    assert default_provider.key == LEAN
    assert not default_provider.is_tree_sitter
    assert default_provider.uses_exact_bindings


def test_language_set_derives_extractor_version_and_isolates_caches(tmp_path: Path) -> None:
    default_set = grammars.default_language_set()
    narrow_set = _functions_only_python_set()
    assert default_set.extractor_version != narrow_set.extractor_version
    assert "tree-sitter=0.26.1" in default_set.extractor_version
    assert observe.EXTRACTOR_VERSION.endswith(default_set.extractor_version)

    lines = ["CONST = 1", "", "def build():", "    return CONST"]
    with extents.definition_scope():
        full = extents.definitions("mod.py", lines)
        assert {"CONST", "build"} == set(full)
        with grammars.language_set_scope(narrow_set):
            narrow = extents.definitions("mod.py", lines)
            assert set(narrow) == {"build"}
            assert observe.observation_key(
                "b" * 40, "mod.py", {"kind": "symbol", "name": "CONST"}, "a" * 40
            ) != (
                "b" * 40,
                '{"kind":"symbol","name":"CONST"}',
                "mod.py",
                "",
                observe.EXTRACTOR_VERSION,
            )
        assert set(extents.definitions("mod.py", lines)) == {"CONST", "build"}

    repo = tmp_path / "repo"
    init_repository(repo)
    commit = commit_files(repo, {"mod.py": "\n".join(lines) + "\n"}, "add mod.py")
    snapshot = CodeSnapshot.at_commit(repo, commit)
    blob = snapshot.blobs["mod.py"]

    BLOB_DEFINITIONS.clear()
    SYMBOL_SPANS.clear()
    try:
        assert snapshot.resolve("mod.py", {"kind": "symbol", "name": "CONST"}).blob == blob
        objects = CodeObjects(repo)
        assert objects.qualified_spans("mod.py", blob, "CONST") == ((1, 1),)
        claim = {
            "anchor_id": "00000000-0000-0000-0000-000000000001",
            "path": "mod.py",
            "source_identity": {"object_id": blob},
            "locator": {"kind": "symbol", "language": "python", "qualified_name": "CONST"},
        }
        assert (
            read_anchors.observe_anchor(
                claim, repository_root=repo, tree_id=snapshot.tree
            ).resolution
            == "exact_recorded_blob"
        )
        with grammars.language_set_scope(narrow_set):
            with pytest.raises(AnchorResolutionError, match="does not define 'CONST'"):
                snapshot.resolve("mod.py", {"kind": "symbol", "name": "CONST"})
            assert objects.qualified_spans("mod.py", blob, "CONST") == ()
            assert (
                read_anchors.observe_anchor(
                    claim, repository_root=repo, tree_id=snapshot.tree
                ).resolution
                == "recorded_blob_mismatch"
            )
    finally:
        BLOB_DEFINITIONS.clear()
        SYMBOL_SPANS.clear()


def test_tags_engine_handles_wrappers_destructuring_errors_and_exclusions() -> None:
    py_source = "\n".join(
        [
            "import os",
            "from pathlib import Path",
            "",
            "@dec1",
            "@dec2",
            "def decorated():",
            '    """Docstring."""',
            "    return 1",
            "",
            "a = b = 1",
            "(first,), second = call()",
            "holder.attr = 2",
            "items[0] = 3",
            "def broken(:",
            "    pass",
            "ok = 4",
        ]
    )
    py_bound = grammars.bindings("sample.py", py_source.splitlines())
    assert py_bound["decorated"] == [grammars.Binding(span=(4, 8), declaration=6)]
    assert py_bound["a"] == [grammars.Binding(span=(10, 10), declaration=10)]
    assert py_bound["b"] == [grammars.Binding(span=(10, 10), declaration=10)]
    assert py_bound["first"] == [grammars.Binding(span=(11, 11), declaration=11)]
    assert py_bound["second"] == [grammars.Binding(span=(11, 11), declaration=11)]
    assert py_bound["ok"] == [grammars.Binding(span=(16, 16), declaration=16)]
    for excluded in ("os", "Path", "call", "holder", "attr", "items", "broken"):
        assert excluded not in py_bound

    ts_source = "\n".join(
        [
            'import { keep } from "./other";',
            "@Component({})",
            "export class Widget {",
            "  render() { return <div data-id={keep} />; }",
            "}",
            "const { orig: renamed, plain, missing = fallback } = obj;",
            "const [head, ...tail] = list;",
            "const map = { objKey: 1 };",
        ]
    )
    ts_bound = grammars.bindings("Widget.tsx", ts_source.splitlines())
    assert ts_bound["Widget"] == [grammars.Binding(span=(2, 5), declaration=3)]
    assert set(ts_bound) >= {
        "Widget",
        "render",
        "renamed",
        "plain",
        "missing",
        "head",
        "tail",
        "map",
    }
    for excluded in ("keep", "orig", "fallback", "obj", "list", "objKey"):
        assert excluded not in ts_bound


def test_model_anchors_in_accepts_explicit_language_set() -> None:
    custom = grammars.default_language_set().without("python")
    anchors, skipped = model.anchors_in("`useEffect(cb, [a])`", language_set=custom)
    assert [anchor.text for anchor in anchors] == ["useEffect"]
    assert skipped == 0


def test_read_anchors_and_extents_share_holder_containment_for_qualified_names(
    tmp_path: Path,
) -> None:
    source = "\n".join(
        [
            "class First:",
            "    def run(self) -> int:",
            "        return 1",
            "",
            "class Second:",
            "    def run(self) -> int:",
            "        return 2",
        ]
    )
    lines = source.splitlines()
    bound = extents.definitions("pair.py", lines)
    assert extents.qualified_spans("First.run", bound, grammar="python") == ((2, 3),)
    assert extents.qualified_spans("Second.run", bound, grammar="python") == ((6, 7),)

    repo = tmp_path / "repo"
    init_repository(repo)
    commit = commit_files(repo, {"pair.py": source + "\n"}, "add pair.py")
    snapshot = CodeSnapshot.at_commit(repo, commit)
    blob = snapshot.blobs["pair.py"]

    BLOB_DEFINITIONS.clear()
    try:
        claim = {
            "anchor_id": "00000000-0000-0000-0000-000000000002",
            "path": "pair.py",
            "source_identity": {"object_id": blob},
            "locator": {"kind": "symbol", "language": "python", "qualified_name": "First.run"},
        }
        observed = read_anchors.observe_anchor(claim, repository_root=repo, tree_id=snapshot.tree)
        assert observed.resolution == "exact_recorded_blob"
        assert observed.resolved_ranges is not None
        assert [(r.start_line, r.end_line) for r in observed.resolved_ranges] == [(2, 3)]
    finally:
        BLOB_DEFINITIONS.clear()


def test_tags_engine_ignores_doc_and_local_captures_and_preserves_document_order() -> None:
    query_text = """
    (function_definition
      name: (identifier) @name
      body: (block (expression_statement (string) @doc)?)) @definition.function
    (parameters (identifier) @local.definition)
    """
    lang = grammars.language("python")
    query = tags_engine.compile_query(lang, query_text)
    source = 'def alpha(p):\n    """doc"""\n    return p\n\ndef beta(q):\n    return q\n'
    tree = grammars.Parser(lang).parse(source.encode("utf-8"))
    entries = tags_engine.extract_tag_entries(
        tree, query, wrappers=frozenset(), destructuring=tags_engine.DestructuringSpec()
    )
    merged = tags_engine.merge_entries(entries)
    assert list(merged.keys()) == ["alpha", "beta"]
    assert merged["alpha"] == [grammars.Binding(span=(1, 3), declaration=1)]
    assert merged["beta"] == [grammars.Binding(span=(5, 6), declaration=5)]
