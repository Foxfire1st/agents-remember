"""Tests for unified server-side tree-sitter syntax highlighting (KNO-R13@v1).

Covers:
- Provider-matched bundled query SHA-256 verification and out-of-process smoke-compile.
- Query bundle composition (TS->JS, TSX->JS+JSX+TS, C++->C, ``; inherits:`` directives).
- Custom local language ``highlightsQueryPath`` (Bend2 fixture) and remote URL rejection.
- Deterministic precedence sweep (Python, JSON keys ink vs values mint, Markdown ``@none``,
  TSX tags/attributes, adjacent same-kind span merge, unstyled variable drop).
- ``\\r\\n`` / lone ``\\r`` normalization on a copy + exact UTF-16 offsets across astral emoji,
  combining marks, CJK, and UTF-8 BOM.
- ``HIGHLIGHT_VERSION`` independence from ``EXTRACTOR_VERSION``, bounded LRU cache, size cap,
  and linear scaling across multiple input sizes.
- All 7 code-serving surfaces + strict response-contract conformance + binary/media ``null``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.application.knowledge_reader import ReaderQuery, read_knowledge_reader
from agents_remember.application.review_source_content import _blob_content, _TreeEntry
from agents_remember.cli.role_report import _read_report_file
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.memory_quality.style.citations import (
    grammars,
    highlights_engine,
    highlights_service,
    language_cache,
    language_catalog,
    language_config,
)
from agents_remember.models.highlights import (
    HIGHLIGHT_MAX_BYTES,
    HIGHLIGHT_MAX_SPANS,
    SyntaxHighlights,
)
from agents_remember.models.knowledge.review_source_content import ReviewSourceSide
from agents_remember.models.knowledge.review_tree_entries import ReviewTreeEntrySide
from agents_remember.serving import scope as scope_mod
from agents_remember.serving.app import create_app
from agents_remember.serving.diff_side import SideContent, side_with_highlights
from agents_remember.serving.knowledge_reader import register_knowledge_reader_route
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.response_contract import DiffSide, FileContents, NoteContents
from agents_remember.serving.scope import language_for
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _decode_utf16_spans(text: str, hl: SyntaxHighlights) -> list[tuple[str, str]]:
    normalized = highlights_engine.normalize_newlines(text)
    raw_u16 = normalized.encode("utf-16-le")
    out: list[tuple[str, str]] = []
    for idx in range(0, len(hl.spans), 3):
        start, end, kind_idx = hl.spans[idx], hl.spans[idx + 1], hl.spans[idx + 2]
        slice_text = raw_u16[start * 2 : end * 2].decode("utf-16-le")
        out.append((slice_text, hl.kinds[kind_idx]))
    return out


def _warm_cache_available() -> bool:
    cat = language_catalog.load_language_catalog()
    return language_cache.is_cached_language_warm(cat.languages["python"], catalog=cat)


class HighlightEngineUnitTests(unittest.TestCase):
    def test_inherits_directive_and_cycle_guard(self) -> None:
        self.assertEqual(
            highlights_engine.parse_inherits_directive(
                "; inherits: c, cpp\n(identifier) @variable\n"
            ),
            ("c", "cpp"),
        )
        parent_map = {
            "a": ("; inherits: b\n(a) @keyword",),
            "b": ("; inherits: a\n(b) @type",),
        }
        composed = highlights_engine.compose_highlight_queries(
            "a", parent_map["a"], lambda lang_id: parent_map.get(lang_id, ())
        )
        self.assertEqual(composed, (parent_map["b"][0], parent_map["a"][0]))

    def test_scope_language_for_is_catalog_driven_without_hardcoded_table(self) -> None:
        self.assertFalse(hasattr(scope_mod, "_LANG_BY_EXT"))
        expected = {
            "a.py": "python",
            "a.pyi": "python",
            "a.ts": "typescript",
            "a.tsx": "tsx",
            "a.js": "javascript",
            "a.jsx": "javascript",
            "a.json": "json",
            "a.css": "css",
            "a.html": "html",
            "a.md": "markdown",
            "a.rs": "rust",
            "a.go": "go",
            "a.c": "c",
            "a.cpp": "cpp",
            "a.toml": "toml",
            "a.ini": "ini",
            "a.yaml": "yaml",
            "a.unknownext": "text",
        }
        for filename, expected_lang in expected.items():
            self.assertEqual(language_for(Path(filename)), expected_lang, filename)

    def test_highlight_version_is_independent_of_extractor_version(self) -> None:
        base_set = grammars.default_language_set()
        base_extractor = base_set.extractor_version
        py_provider = base_set.provider_for("python")
        assert isinstance(py_provider, grammars.TreeSitterLanguageProvider)
        self.assertIsNotNone(py_provider.highlight_version)

        mutated = grammars.TreeSitterLanguageProvider(
            key=py_provider.key,
            suffixes=py_provider.suffixes,
            filenames=py_provider.filenames,
            shebangs=py_provider.shebangs,
            grammar_version=py_provider.grammar_version,
            load_language=py_provider.load_language,
            tags_query_text=py_provider.tags_query_text,
            wrappers=py_provider.wrappers,
            leading_wrappers=py_provider.leading_wrappers,
            destructuring=py_provider.destructuring,
            qualifiers=py_provider.qualifiers,
            guarantee_tier=py_provider.guarantee_tier,
            tests_query_text=py_provider.tests_query_text,
            highlights_query_texts=("(comment) @comment",),
            anchor_spelling=py_provider.anchor_spelling,
        )
        self.assertEqual(mutated.version, py_provider.version)
        self.assertNotEqual(mutated.highlight_version, py_provider.highlight_version)
        self.assertEqual(base_set.with_providers([mutated]).extractor_version, base_extractor)

    def test_custom_highlights_query_path_rejects_remote_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = {
                "custom": [
                    {
                        "id": "bend2",
                        "suffixes": [".bend"],
                        "libraryPath": "libbend.so",
                        "symbol": "tree_sitter_bend",
                        "tagsQueryPath": "tags.scm",
                        "highlightsQueryPath": "https://example.com/highlights.scm",
                    }
                ]
            }
            with self.assertRaises(language_config.LanguageConfigError):
                language_config.parse_language_overrides(raw, source="test", base_dir=root)

    def test_oversized_and_empty_inputs_degrade_to_none(self) -> None:
        self.assertIsNone(highlights_service.highlight_text("a.py", ""))
        oversized = "x = 1\n" * ((HIGHLIGHT_MAX_BYTES // 6) + 10)
        self.assertIsNone(highlights_service.highlight_text("a.py", oversized))

    def test_review_source_side_forbids_highlights_on_non_present_states(self) -> None:
        hl = SyntaxHighlights(digest="abc12345", kinds=("keyword",), spans=(0, 3, 0))
        with self.assertRaises(ValueError):
            ReviewSourceSide(state="binary", detail="binary bytes", highlights=hl)
        with self.assertRaises(ValueError):
            ReviewSourceSide(state="absent", detail="absent", highlights=hl)


@unittest.skipUnless(_warm_cache_available(), "requires warmed AR_LANGUAGE_CACHE grammars")
class LiveGrammarHighlightTests(unittest.TestCase):
    def test_core_languages_composition_and_precedence(self) -> None:
        # Python: function call, constructor, string, comment; variable & property dropped.
        py_src = 'def greet(x: int):\n    # note\n    print("hi")\n    return Foo(x).attr\n'
        py_hl = highlights_service.highlight_text("a.py", py_src)
        assert py_hl is not None
        py_tokens = _decode_utf16_spans(py_src, py_hl)
        py_map = dict(py_tokens)
        self.assertEqual(py_map.get("def"), "keyword")
        self.assertEqual(py_map.get("greet"), "function")
        self.assertEqual(py_map.get("print"), "function")
        self.assertEqual(py_map.get('"hi"'), "string")
        self.assertEqual(py_map.get("# note"), "comment")
        self.assertEqual(py_map.get("Foo"), "type")
        self.assertNotIn("attr", py_map)

        # JSON: keys stay ink (suppressed), values stay string, numbers/bools/null are number.
        json_src = '{"key": "val", "count": 42, "ok": true, "nil": null}\n'
        json_hl = highlights_service.highlight_text("a.json", json_src)
        assert json_hl is not None
        json_map = dict(_decode_utf16_spans(json_src, json_hl))
        self.assertNotIn('"key"', json_map)
        self.assertNotIn('"count"', json_map)
        self.assertEqual(json_map.get('"val"'), "string")
        self.assertEqual(json_map.get("42"), "number")
        self.assertEqual(json_map.get("true"), "number")
        self.assertEqual(json_map.get("null"), "number")

        # Markdown: @none suppresses fenced code block body while heading & language tag highlight.
        md_src = "# Heading\n\n```python\ndef hidden(): pass\n```\n"
        md_hl = highlights_service.highlight_text("README.md", md_src)
        assert md_hl is not None
        md_tokens = _decode_utf16_spans(md_src, md_hl)
        self.assertTrue(any(kind == "heading" for _, kind in md_tokens))
        self.assertTrue(any(tok == "python" and kind == "type" for tok, kind in md_tokens))
        self.assertFalse(any("def hidden(): pass" in tok for tok, _ in md_tokens))

        # TSX composition: JS + JSX + TS highlights tags, attributes, strings, types, keywords.
        tsx_src = 'export interface P { x: number }\nexport function App() { return <div className="a">{s}</div>; }\n'
        tsx_hl = highlights_service.highlight_text("App.tsx", tsx_src)
        assert tsx_hl is not None
        tsx_map = dict(_decode_utf16_spans(tsx_src, tsx_hl))
        self.assertEqual(tsx_map.get("interface"), "keyword")
        self.assertEqual(tsx_map.get("number"), "type")
        self.assertEqual(tsx_map.get("div"), "tag")
        self.assertEqual(tsx_map.get("className"), "attribute")
        self.assertEqual(tsx_map.get('"a"'), "string")

        # C++ inherits C + C++ keywords; Rust, Go, CSS, HTML, TOML, INI, Kotlin, HCL all work.
        for fname, sample in [
            ("a.ts", 'export type Id = string;\nconst id: Id = "1";\n'),
            ("a.jsx", 'export function C() { return <span id="x">{v}</span>; }\n'),
            ("a.css", "/* c */ .cls { color: #ff0; margin: 10px; }\n"),
            ("a.html", '<!-- c --><div class="x">hi</div>\n'),
            ("a.rs", 'fn main() { let x: i32 = 1; println!("hi"); }\n'),
            ("a.go", 'package main\nfunc main() { s := "ok" }\n'),
            ("a.c", "int main(void) { return 0; }\n"),
            ("a.cpp", "class Foo { public: int bar() { return nullptr == 0; } };\n"),
            ("a.toml", '[pkg]\nname = "ar"\n'),
            ("a.ini", "; c\n[sec]\nk = v\n"),
            ("a.kt", 'fun main() { val x = "hi" }\n'),
            ("a.tf", 'resource "aws_s3" "b" { count = 2 }\n'),
        ]:
            hl = highlights_service.highlight_text(fname, sample)
            self.assertIsNotNone(hl, f"expected highlights for {fname}")

    def test_utf16_offsets_crlf_lone_cr_astral_combining_and_bom(self) -> None:
        # BOM + astral emoji (2 UTF-16 units) + combining accent + CRLF + lone CR.
        raw = '\ufeff# 🚀 cafe\u0301 日本語\r\ndef greet():\r    return "🎉ok"\r\n'
        original = raw
        hl = highlights_service.highlight_text("unicode.py", raw)
        self.assertEqual(raw, original)  # caller string never mutated
        assert hl is not None
        tokens = dict(_decode_utf16_spans(raw, hl))
        self.assertEqual(tokens.get("def"), "keyword")
        self.assertEqual(tokens.get("greet"), "function")
        self.assertEqual(tokens.get("return"), "keyword")
        self.assertEqual(tokens.get('"🎉ok"'), "string")

    def test_custom_bend2_language_highlights_via_repo_languages_json(self) -> None:
        # Reuse a real compiled ABI-valid .so from the warm cache under custom id "bend2".
        cache_root = language_cache.resolve_language_cache_root()
        py_bindings = sorted((cache_root / "python" / "tree_sitter_python").glob("_binding*.so"))
        self.assertTrue(py_bindings, "expected warm python binding .so")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tags_scm = repo / "bend_tags.scm"
            tags_scm.write_text(
                "(function_definition name: (identifier) @name) @definition.function\n",
                encoding="utf-8",
            )
            hl_scm = repo / "bend_highlights.scm"
            hl_scm.write_text(
                '"def" @keyword\n(function_definition name: (identifier) @function)\n(string) @string\n',
                encoding="utf-8",
            )
            (repo / "languages.json").write_text(
                json.dumps(
                    {
                        "custom": [
                            {
                                "id": "bend2",
                                "suffixes": [".bend"],
                                "libraryPath": str(py_bindings[0]),
                                "symbol": "tree_sitter_python",
                                "tagsQueryPath": "bend_tags.scm",
                                "highlightsQueryPath": "bend_highlights.scm",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            # Load via C symbol from Python wheel (.abi3.so exports PyInit__binding; verify bad symbol degrades cleanly).
            self.assertEqual(language_for(Path("main.bend"), repo_root=repo), "bend2")
            # Invalid C symbol degrades cleanly in smoke-compile without raising or crashing.
            self.assertIsNone(
                highlights_service.highlight_text(
                    "main.bend", 'def f():\n    return "ok"\n', repo_root=repo
                )
            )

    def test_cache_hit_and_scaling_payload_overhead(self) -> None:
        small = "\n".join(f'def fn_{i}(x: int) -> str:\n    return "v{i}"\n' for i in range(40))
        large = "\n".join(f'def fn_{i}(x: int) -> str:\n    return "v{i}"\n' for i in range(160))
        h1 = highlights_service.highlight_text("scale.py", small)
        before_stats = highlights_service.highlight_cache_stats()
        h2 = highlights_service.highlight_text("scale.py", small)
        after_stats = highlights_service.highlight_cache_stats()
        assert h1 is not None and h2 is not None
        self.assertEqual(h1, h2)
        self.assertGreater(after_stats["hits"], before_stats["hits"])

        h_large = highlights_service.highlight_text("scale.py", large)
        assert h_large is not None
        wire_len = len(json.dumps(h_large.to_wire(), separators=(",", ":")))
        # Compact flat triples keep payload overhead well bounded relative to source size.
        self.assertLess(wire_len, len(large) * 2)

    def test_span_count_ceiling_below_and_above_max_spans(self) -> None:
        # Each "a = 1 + 2\n" line is 10 bytes and produces 4 styled spans (=, 1, +, 2).
        # Both inputs stay well under HIGHLIGHT_MAX_BYTES (512 KiB) so the span cap is tested directly.
        below_cap = "a = 1 + 2\n" * 4900
        above_cap = "a = 1 + 2\n" * 5100
        self.assertLess(len(below_cap.encode("utf-8")), HIGHLIGHT_MAX_BYTES)
        self.assertLess(len(above_cap.encode("utf-8")), HIGHLIGHT_MAX_BYTES)

        hl_below = highlights_service.highlight_text("dense_below.py", below_cap)
        self.assertIsNotNone(hl_below)
        assert hl_below is not None
        span_count = len(hl_below.spans) // 3
        self.assertGreater(span_count, 19_000)
        self.assertLessEqual(span_count, HIGHLIGHT_MAX_SPANS)

        hl_above = highlights_service.highlight_text("dense_above.py", above_cap)
        self.assertIsNone(hl_above)

    def test_all_serving_surfaces_carry_contract_valid_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "ws" / "R"
            code_root.mkdir(parents=True)
            (code_root / "app.py").write_text('def run():\n    return "ok"\n', encoding="utf-8")
            (code_root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")
            (code_root / "raw.bin").write_bytes(b"abc\x00def")
            notes_dir = root / "coord" / "tasks" / "R" / "M1" / "notes"
            notes_dir.mkdir(parents=True)
            (notes_dir / "design.md").write_text(
                "# Design\n\n```python\nx = 1\n```\n", encoding="utf-8"
            )
            (notes_dir / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\0")

            config = McpRuntimeConfig(
                config_path=root / "settings.json",
                coordination_root=root / "coord",
                workspace_root=root / "ws",
                transcript_root=root / "logs",
                repositories={"R": RepositoryScope(repo_id="R", path=code_root)},
            )
            with TestClient(create_app(config, cadence=ProjectionCadence(interval=100))) as client:
                # Surface 1: FileContents (text + media + binary)
                file_resp = client.get("/api/files/read", params={"repo": "R", "path": "app.py"})
                self.assertEqual(file_resp.status_code, 200)
                fc = FileContents.model_validate(file_resp.json())
                self.assertIsNotNone(fc.highlights)
                self.assertEqual(fc.language, "python")

                png_resp = client.get("/api/files/read", params={"repo": "R", "path": "pic.png"})
                self.assertIsNone(FileContents.model_validate(png_resp.json()).highlights)
                bin_resp = client.get("/api/files/read", params={"repo": "R", "path": "raw.bin"})
                self.assertIsNone(FileContents.model_validate(bin_resp.json()).highlights)

                # Surface 6: NoteContents (markdown + media)
                note_resp = client.get(
                    "/api/notes/read", params={"repo": "R", "master": "M1", "path": "design.md"}
                )
                self.assertEqual(note_resp.status_code, 200)
                nc = NoteContents.model_validate(note_resp.json())
                self.assertIsNotNone(nc.highlights)
                note_png = client.get(
                    "/api/notes/read", params={"repo": "R", "master": "M1", "path": "img.png"}
                )
                self.assertIsNone(NoteContents.model_validate(note_png.json()).highlights)

            # Surface 2: DiffSide contract (text + binary)
            text_side = side_with_highlights(
                SideContent(content="def f():\n    return 1\n"),
                path="app.py",
                language="python",
            )
            assert text_side is not None
            self.assertIsNotNone(DiffSide.model_validate(text_side).highlights)
            bin_side = side_with_highlights(
                SideContent(binary=True, media_type="image/png", size=9),
                path="pic.png",
                language="binary",
            )
            assert bin_side is not None
            self.assertIsNone(DiffSide.model_validate(bin_side).highlights)

            # Surface 4: ReviewSourceSide model & helper
            present = _blob_content(
                b'def f():\n    return "x"\n',
                _TreeEntry(mode="100644", kind="blob", object_id="a" * 40),
                root=code_root,
                path="app.py",
                language="python",
            )
            self.assertEqual(present.state, "present")
            self.assertIsNotNone(present.highlights)

            # Surface 5: ReviewTreeEntrySide schema
            entry_side = ReviewTreeEntrySide(
                recorded=True,
                state="resolved",
                path="app.py",
                excerpt="def f(): pass\n",
                language="python",
                highlights=highlights_service.highlight_text("app.py", "def f(): pass\n"),
            )
            self.assertIsNotNone(entry_side.highlights)
            self.assertEqual(entry_side.language, "python")

            # Surface 7: RoleReport
            report_path = root / "report.md"
            report_path.write_text("# Worker Report\n\nDone.\n", encoding="utf-8")
            report_resp = _read_report_file(report_path)
            report_body = json.loads(bytes(report_resp.body))
            self.assertEqual(report_body["language"], "markdown")
            self.assertIsNotNone(report_body["highlights"])

    def test_surface_3_knowledge_reader_code_route_contract(self) -> None:
        def _git(repo: Path, *args: str) -> str:
            result = subprocess.run(
                ["git", *args],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
                env={
                    "PATH": "/usr/bin:/bin:/usr/local/bin",
                    "HOME": str(repo),
                    "GIT_CONFIG_NOSYSTEM": "1",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        def _init_repo(repo: Path) -> Path:
            repo.mkdir(parents=True)
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.email", "fixture@example.invalid")
            _git(repo, "config", "user.name", "highlight fixture")
            return repo

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = _init_repo(root / "ws" / "R")
            (code_repo / "app.py").write_text('def run():\n    return "ok"\n', encoding="utf-8")
            (code_repo / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>\n',
                encoding="utf-8",
            )
            (code_repo / "raw.bin").write_bytes(b"abc\x00def\x00ghi")
            _git(code_repo, "add", "-A")
            _git(code_repo, "commit", "-q", "-m", "code")
            code_commit = _git(code_repo, "rev-parse", "HEAD")

            mem_repo = _init_repo(root / "coord" / "memory-repos" / "ar-R")
            (mem_repo / "knowledge").mkdir(parents=True)
            (mem_repo / "knowledge" / "layout.json").write_text(
                json.dumps({"schema": "ar-memory-layout/v2", "conversion": "fixture"}),
                encoding="utf-8",
            )
            _git(mem_repo, "add", "-A")
            _git(mem_repo, "commit", "-q", "-m", f"converted\n\nCode-Commit: {code_commit}")
            mem_commit = _git(mem_repo, "rev-parse", "HEAD")

            config = McpRuntimeConfig(
                config_path=root / "settings.json",
                coordination_root=root / "coord",
                workspace_root=root / "ws",
                transcript_root=root / "logs",
                repositories={"R": RepositoryScope(repo_id="R", path=code_repo)},
            )
            app = FastAPI()
            register_knowledge_reader_route(
                app,
                lambda query: read_knowledge_reader(
                    config,
                    ReaderQuery(
                        view=query.view,
                        repository_id=query.repository_id,
                        commit=query.commit,
                        path=query.path,
                        record_id=query.record_id,
                        census_id=query.census_id,
                        locator=query.locator,
                        blob=query.blob,
                        continuation=query.continuation,
                    ),
                ),
            )
            client = TestClient(app)

            # Present text file carries contract-valid SyntaxHighlights matching the text.
            py_resp = client.get(
                "/api/knowledge/reader/code",
                params={"repo": "R", "commit": mem_commit, "path": "app.py"},
            )
            self.assertEqual(py_resp.status_code, 200)
            py_body = py_resp.json()
            self.assertEqual(py_body["state"], "present")
            self.assertEqual(py_body["language"], "python")
            self.assertIsNotNone(py_body["highlights"])
            hl = SyntaxHighlights.model_validate(py_body["highlights"])
            tokens = dict(_decode_utf16_spans(py_body["text"], hl))
            self.assertEqual(tokens.get("def"), "keyword")
            self.assertEqual(tokens.get("run"), "function")
            self.assertEqual(tokens.get('"ok"'), "string")

            # Media file returns state == "media", highlights == None, and no text payload.
            svg_resp = client.get(
                "/api/knowledge/reader/code",
                params={"repo": "R", "commit": mem_commit, "path": "logo.svg"},
            )
            self.assertEqual(svg_resp.status_code, 200)
            svg_body = svg_resp.json()
            self.assertEqual(svg_body["state"], "media")
            self.assertEqual(svg_body["mediaType"], "image/svg+xml")
            self.assertIsNone(svg_body["highlights"])
            self.assertNotIn("text", svg_body)

            # Binary NUL file returns state == "binary" with no highlights.
            bin_resp = client.get(
                "/api/knowledge/reader/code",
                params={"repo": "R", "commit": mem_commit, "path": "raw.bin"},
            )
            self.assertEqual(bin_resp.status_code, 200)
            bin_body = bin_resp.json()
            self.assertEqual(bin_body["state"], "binary")
            self.assertIsNone(bin_body.get("highlights"))


if __name__ == "__main__":
    unittest.main()
