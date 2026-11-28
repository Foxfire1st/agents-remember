"""Table-driven soundness fixtures for the 15 core pilots and 56 language-pack languages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PilotFixture:
    """One core pilot soundness, qualifier, wrapper, and pure-move fixture."""

    language: str
    path: str
    source: str
    expected_spans: dict[str, tuple[int, int]]
    forbidden_names: tuple[str, ...]
    qualified_cases: dict[str, tuple[int, int] | None]
    move_symbol: str
    shift_prefix: str


@dataclass(frozen=True)
class LongTailFixture:
    """One language-pack positive, negative-soundness, and pure-move fixture."""

    language: str
    path: str
    source: str
    expected_symbol: str
    forbidden_names: tuple[str, ...]
    shift_prefix: str


CORE_PILOT_IDS: tuple[str, ...] = (
    "c",
    "cpp",
    "rust",
    "zig",
    "go",
    "java",
    "csharp",
    "kotlin",
    "swift",
    "ruby",
    "php",
    "bash",
    "elixir",
    "sql",
    "hcl",
)

PILOT_FIXTURES: tuple[PilotFixture, ...] = (
    PilotFixture(
        language="c",
        path="src/engine.c",
        source=(
            "#include <stdio.h>\n"
            "// int fake_comment(int x) { return x; }\n"
            'static const char *fake_msg = "int fake_str(int x) { return x; }";\n'
            "#define MAX_ITEMS 64\n"
            "#define DOUBLE(v) ((v) * 2)\n"
            "\n"
            "enum Mode {\n"
            "    MODE_FAST,\n"
            "    MODE_SAFE\n"
            "};\n"
            "typedef int Score;\n"
            "\n"
            "struct Point {\n"
            "    int x;\n"
            "    int y;\n"
            "};\n"
            "\n"
            "int compute(struct Point p) {\n"
            "    struct Point tmp = { .x = p.x, .y = p.y };\n"
            "    return phantom_call(tmp.x);\n"
            "}\n"
        ),
        expected_spans={
            "fake_msg": (3, 3),
            "MAX_ITEMS": (4, 4),
            "DOUBLE": (5, 5),
            "Mode": (7, 10),
            "Score": (11, 11),
            "Point": (13, 16),
            "compute": (18, 21),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "stdio", "x", "y"),
        qualified_cases={"Point.compute": None, "Point::compute": None, "NoSuch.fn": None},
        move_symbol="compute",
        shift_prefix="// shifted line 1\n// shifted line 2\n\n",
    ),
    PilotFixture(
        language="cpp",
        path="src/engine.cpp",
        source=(
            "#include <vector>\n"
            "// int fake_comment() { return 0; }\n"
            'static const char* msg = "void fake_str() {}";\n'
            "#define MAX_RETRIES 3\n"
            "static constexpr int kMaxDepth = 8;\n"
            "\n"
            "namespace core {\n"
            "enum class Mode {\n"
            "    Fast,\n"
            "    Safe\n"
            "};\n"
            "using Score = int;\n"
            "\n"
            "template <typename T>\n"
            "class Engine {\n"
            "public:\n"
            "    int inline_run(T x) {\n"
            "        return phantom_call(x.phantom_field);\n"
            "    }\n"
            "    int run(int x);\n"
            "};\n"
            "\n"
            "int Engine::run(int x) {\n"
            "    return phantom_call(x);\n"
            "}\n"
            "}\n"
        ),
        expected_spans={
            "MAX_RETRIES": (4, 4),
            "kMaxDepth": (5, 5),
            "core": (7, 26),
            "Mode": (8, 11),
            "Score": (12, 12),
            "Engine": (14, 21),
            "inline_run": (17, 19),
            "run": (23, 25),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "phantom_field", "vector"),
        qualified_cases={
            "core::Mode": (8, 11),
            "core::Score": (12, 12),
            "core::Engine": (14, 21),
            "core::Engine::inline_run": (17, 19),
            "Engine::run": (23, 25),
            "core::Engine::run": (23, 25),
            "Engine.run": None,
            "core::Missing::run": None,
        },
        move_symbol="core::Engine::run",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="rust",
        path="src/net.rs",
        source=(
            "use std::fmt::Debug;\n"
            "// fn fake_comment() {}\n"
            'const MSG: &str = "fn fake_str() {}";\n'
            "macro_rules! trace_val {\n"
            "    ($x:expr) => { $x };\n"
            "}\n"
            "\n"
            "pub fn top_helper(v: usize) -> usize {\n"
            "    v + 1\n"
            "}\n"
            "\n"
            "mod net {\n"
            "    pub enum Mode {\n"
            "        Fast,\n"
            "        Safe,\n"
            "    }\n"
            "    pub type Score = usize;\n"
            "    pub trait Runner {\n"
            "        fn step(&self) -> usize;\n"
            "    }\n"
            "\n"
            "    #[derive(Debug)]\n"
            "    pub struct Client {\n"
            "        pub host: &'static str,\n"
            "    }\n"
            "\n"
            "    impl Client {\n"
            "        #[inline]\n"
            "        pub fn connect(&self) -> usize {\n"
            "            let item = Client { host: self.host };\n"
            "            phantom_call(item.host)\n"
            "        }\n"
            "    }\n"
            "\n"
            "    pub struct Backup;\n"
            "    impl Backup {\n"
            "        pub fn connect(&self) -> usize {\n"
            "            0\n"
            "        }\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "MSG": (3, 3),
            "trace_val": (4, 6),
            "top_helper": (8, 10),
            "net": (12, 41),
            "Mode": (13, 16),
            "Score": (17, 17),
            "Runner": (18, 20),
            "step": (19, 19),
            "Client": (22, 25),
            "Backup": (35, 35),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "Debug", "host"),
        qualified_cases={
            "net::Mode": (13, 16),
            "net::Score": (17, 17),
            "net::Runner": (18, 20),
            "net::Runner::step": (19, 19),
            "net::Client": (22, 25),
            "Client::connect": (28, 32),
            "net::Client::connect": (28, 32),
            "Backup::connect": (37, 39),
            "net::Backup::connect": (37, 39),
            "connect": None,
            "Client.connect": None,
            "net::Missing::connect": None,
        },
        move_symbol="net::Client::connect",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="zig",
        path="src/engine.zig",
        source=(
            'const std = @import("std");\n'
            "// fn fake_comment() void {}\n"
            'const msg = "fn fake_str() void {}";\n'
            "\n"
            "const Mode = enum {\n"
            "    fast,\n"
            "    safe,\n"
            "};\n"
            "\n"
            "const Engine = struct {\n"
            "    value: i32,\n"
            "\n"
            "    pub fn compute(self: Engine) i32 {\n"
            "        const tmp = Engine{ .value = self.value };\n"
            "        return phantom_call(tmp.value);\n"
            "    }\n"
            "};\n"
        ),
        expected_spans={
            "msg": (3, 3),
            "Mode": (5, 8),
            "Engine": (10, 17),
            "compute": (13, 16),
        },
        forbidden_names=("std", "fake_comment", "fake_str", "phantom_call", "value"),
        qualified_cases={
            "Engine.compute": (13, 16),
            "Engine::compute": None,
            "Missing.compute": None,
        },
        move_symbol="Engine.compute",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="go",
        path="src/service.go",
        source=(
            "package service\n"
            "\n"
            'import "fmt"\n'
            "\n"
            "// func fake_comment() {}\n"
            'var msg = "func fake_str() {}"\n'
            "const MaxRetries = 3\n"
            "type Score = int\n"
            "\n"
            "type Runner interface {\n"
            "\tStep() int\n"
            "}\n"
            "\n"
            "func TopHelper(v int) int {\n"
            "\treturn v + 1\n"
            "}\n"
            "\n"
            "type Alpha struct {\n"
            "\tName string\n"
            "}\n"
            "\n"
            "type Beta struct {\n"
            "\tName string\n"
            "}\n"
            "\n"
            "func (a *Alpha) Run() string {\n"
            "\titem := Alpha{Name: a.Name}\n"
            "\treturn phantom_call(fmt.Sprint(item.Name))\n"
            "}\n"
            "\n"
            "func (b Beta) Run() string {\n"
            "\treturn b.Name\n"
            "}\n"
        ),
        expected_spans={
            "msg": (6, 6),
            "MaxRetries": (7, 7),
            "Score": (8, 8),
            "Runner": (10, 12),
            "Step": (11, 11),
            "TopHelper": (14, 16),
            "Alpha": (18, 20),
            "Beta": (22, 24),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "fmt", "Name"),
        qualified_cases={
            "Runner.Step": (11, 11),
            "Alpha.Run": (26, 29),
            "Beta.Run": (31, 33),
            "Run": None,
            "Gamma.Run": None,
            "Alpha::Run": None,
        },
        move_symbol="Alpha.Run",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="java",
        path="src/Service.java",
        source=(
            "import java.util.Map;\n"
            "// void fake_comment() {}\n"
            "\n"
            "interface Runner {\n"
            "    int step();\n"
            "}\n"
            "\n"
            "enum Mode {\n"
            "    FAST,\n"
            "    SAFE\n"
            "}\n"
            "\n"
            "@Deprecated\n"
            "public class Service {\n"
            '    private static final String MSG = "void fake_str() {}";\n'
            "\n"
            "    @Override\n"
            "    public int run(int value) {\n"
            '        var box = Map.of("phantom_key", value);\n'
            "        return phantom_call(box.size());\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "Runner": (4, 6),
            "step": (5, 5),
            "Mode": (8, 11),
            "Service": (13, 22),
            "MSG": (15, 15),
            "run": (17, 21),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_key", "phantom_call", "Map"),
        qualified_cases={
            "Runner.step": (5, 5),
            "Service.run": (17, 21),
            "Service::run": None,
            "Other.run": None,
        },
        move_symbol="Service.run",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="csharp",
        path="src/Worker.cs",
        source=(
            "using System;\n"
            "// void fake_comment() {}\n"
            "\n"
            "namespace Demo.Core {\n"
            "    public interface IRunner {\n"
            "        int Step();\n"
            "    }\n"
            "    public enum Mode {\n"
            "        Fast,\n"
            "        Safe\n"
            "    }\n"
            "    public delegate int Transform(int x);\n"
            "\n"
            "    [Serializable]\n"
            "    public class Worker {\n"
            '        private const string Msg = "void fake_str() {}";\n'
            "\n"
            "        [Obsolete]\n"
            "        public int Run(int value) {\n"
            "            var item = new { phantom_key = value };\n"
            "            return phantom_call(item.phantom_key);\n"
            "        }\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "Demo.Core": (4, 24),
            "IRunner": (5, 7),
            "Step": (6, 6),
            "Mode": (8, 11),
            "Transform": (12, 12),
            "Worker": (14, 23),
            "Msg": (16, 16),
            "Run": (18, 22),
        },
        forbidden_names=(
            "fake_comment",
            "fake_str",
            "phantom_key",
            "phantom_call",
            "System",
        ),
        qualified_cases={
            "Demo.Core.IRunner.Step": (6, 6),
            "Demo.Core.Mode": (8, 11),
            "Demo.Core.Transform": (12, 12),
            "Worker.Run": (18, 22),
            "Demo.Core.Worker": (14, 23),
            "Demo.Core.Worker.Run": (18, 22),
            "Worker::Run": None,
            "Demo.Core.Missing.Run": None,
        },
        move_symbol="Demo.Core.Worker.Run",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="kotlin",
        path="src/Service.kt",
        source=(
            "import kotlin.math.abs\n"
            "// fun fake_comment() {}\n"
            "val TOP_LIMIT = 10\n"
            "typealias Score = Int\n"
            "\n"
            "interface Runner {\n"
            "    fun step(): Int\n"
            "}\n"
            "\n"
            "enum class Mode {\n"
            "    FAST,\n"
            "    SAFE\n"
            "}\n"
            "\n"
            '@Deprecated("old")\n'
            "class Service {\n"
            '    private val msg = "fun fake_str() {}"\n'
            "\n"
            "    fun run(value: Int): Int {\n"
            '        val m = mapOf("phantom_key" to value)\n'
            "        return phantom_call(m.size)\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "TOP_LIMIT": (3, 3),
            "Score": (4, 4),
            "Runner": (6, 8),
            "step": (7, 7),
            "Mode": (10, 13),
            "Service": (15, 23),
            "msg": (17, 17),
            "run": (19, 22),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_key", "phantom_call", "abs"),
        qualified_cases={
            "Runner.step": (7, 7),
            "Service.run": (19, 22),
            "Service::run": None,
            "Other.run": None,
        },
        move_symbol="Service.run",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="swift",
        path="src/Alpha.swift",
        source=(
            "import Foundation\n"
            "// func fake_comment() {}\n"
            "let topLimit = 10\n"
            "typealias Score = Int\n"
            "\n"
            "protocol Runner {\n"
            "    func step() -> Int\n"
            "}\n"
            "\n"
            "enum Mode {\n"
            "    case fast\n"
            "    case safe\n"
            "}\n"
            "\n"
            "struct Alpha {\n"
            '    let label = "func fake_str() {}"\n'
            "\n"
            "    @discardableResult\n"
            "    func run(value: Int) -> Int {\n"
            '        let map = ["phantom_key": value]\n'
            "        return phantom_call(map.count)\n"
            "    }\n"
            "}\n"
            "\n"
            "extension Alpha {\n"
            "    func bonus() -> Int {\n"
            "        return 1\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "topLimit": (3, 3),
            "Score": (4, 4),
            "Runner": (6, 8),
            "step": (7, 7),
            "Mode": (10, 13),
            "Alpha": (15, 23),
            "label": (16, 16),
            "run": (18, 22),
            "bonus": (26, 28),
        },
        forbidden_names=(
            "fake_comment",
            "fake_str",
            "phantom_key",
            "phantom_call",
            "Foundation",
        ),
        qualified_cases={
            "Runner.step": (7, 7),
            "Alpha.run": (18, 22),
            "Alpha.bonus": (26, 28),
            "Alpha::run": None,
            "Beta.bonus": None,
        },
        move_symbol="Alpha.bonus",
        shift_prefix="// shifted\n\n",
    ),
    PilotFixture(
        language="ruby",
        path="src/service.rb",
        source=(
            'require "json"\n'
            "# def fake_comment; end\n"
            "\n"
            "module Core\n"
            "  class Service\n"
            '    MSG = "def fake_str; end"\n'
            "\n"
            "    def self.call(arg)\n"
            "      cfg = { phantom_key: arg }\n"
            "      phantom_call(cfg[:phantom_key])\n"
            "    end\n"
            "\n"
            "    def call(arg)\n"
            "      phantom_call(arg.phantom_field)\n"
            "    end\n"
            "  end\n"
            "end\n"
        ),
        expected_spans={"Core": (4, 17), "Service": (5, 16), "MSG": (6, 6)},
        forbidden_names=(
            "fake_comment",
            "fake_str",
            "phantom_key",
            "phantom_call",
            "phantom_field",
            "json",
        ),
        qualified_cases={
            "Core::Service": (5, 16),
            "Core::Service.call": (8, 11),
            "Core::Service#call": (13, 15),
            "Service.call": (8, 11),
            "Service#call": (13, 15),
            "Core::Service#missing": None,
            "Core::Service.missing": None,
        },
        move_symbol="Core::Service#call",
        shift_prefix="# shifted\n\n",
    ),
    PilotFixture(
        language="php",
        path="src/Service.php",
        source=(
            "<?php\n"
            "namespace App;\n"
            "use DateTime;\n"
            "// function fake_comment() {}\n"
            "const MAX_LIMIT = 10;\n"
            "\n"
            "interface Runnable {\n"
            "    public function step();\n"
            "}\n"
            "\n"
            "trait Traceable {\n"
            "    public function tag() {\n"
            '        return "ok";\n'
            "    }\n"
            "}\n"
            "\n"
            "enum Mode: string {\n"
            "    case Fast = 'fast';\n"
            "}\n"
            "\n"
            "function top_helper($v) {\n"
            "    return $v + 1;\n"
            "}\n"
            "\n"
            "#[\\AllowDynamicProperties]\n"
            "class Service {\n"
            "    public function run($x) {\n"
            "        $data = ['phantom_key' => \"function fake_str() {}\"];\n"
            "        return phantom_call($data['phantom_key']);\n"
            "    }\n"
            "}\n"
            "\n"
            "class Runner {\n"
            "    public function run($x) {\n"
            "        return $x;\n"
            "    }\n"
            "}\n"
        ),
        expected_spans={
            "App": (2, 2),
            "MAX_LIMIT": (5, 5),
            "Runnable": (7, 9),
            "step": (8, 8),
            "Traceable": (11, 15),
            "tag": (12, 14),
            "Mode": (17, 19),
            "top_helper": (21, 23),
            "Service": (25, 31),
            "Runner": (33, 37),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_key", "phantom_call", "DateTime"),
        qualified_cases={
            "Runnable::step": (8, 8),
            "Traceable::tag": (12, 14),
            "Service::run": (27, 30),
            "Runner::run": (34, 36),
            "Service.run": (27, 30),
            "run": None,
            "Missing::run": None,
        },
        move_symbol="Service::run",
        shift_prefix="",  # PHP requires <?php first; shift handled via comment after <?php
    ),
    PilotFixture(
        language="bash",
        path="scripts/deploy.sh",
        source=(
            "#!/usr/bin/env bash\n"
            "source ./helpers.sh\n"
            "# fake_comment() { echo no; }\n"
            'MSG="fake_str() { echo no; }"\n'
            "\n"
            "deploy() {\n"
            '    phantom_call "$MSG"\n'
            "}\n"
        ),
        expected_spans={"MSG": (4, 4), "deploy": (6, 8)},
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "source"),
        qualified_cases={"ns.deploy": None, "ns::deploy": None, "missing.fn": None},
        move_symbol="deploy",
        shift_prefix="# shifted line 1\n# shifted line 2\n",
    ),
    PilotFixture(
        language="elixir",
        path="lib/engine.ex",
        source=(
            "import Enum\n"
            "# def fake_comment(x), do: x\n"
            "\n"
            "defprotocol Measurable do\n"
            "  def measure(item)\n"
            "end\n"
            "\n"
            "defmodule MyApp.Engine do\n"
            "  defmacro trace_val(expr) do\n"
            "    expr\n"
            "  end\n"
            "\n"
            "  def run(x) do\n"
            '    data = %{phantom_key: "def fake_str(x), do: x"}\n'
            "    phantom_call(data.phantom_key)\n"
            "  end\n"
            "end\n"
        ),
        expected_spans={
            "Measurable": (4, 6),
            "measure": (5, 5),
            "MyApp.Engine": (8, 17),
            "trace_val": (9, 11),
            "run": (13, 16),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_key", "phantom_call", "Enum"),
        qualified_cases={
            "Measurable.measure": (5, 5),
            "MyApp.Engine.trace_val": (9, 11),
            "MyApp.Engine.run": (13, 16),
            "MyApp.Engine::run": None,
            "Other.Engine.run": None,
        },
        move_symbol="MyApp.Engine.run",
        shift_prefix="# shifted\n\n",
    ),
    PilotFixture(
        language="sql",
        path="db/schema.sql",
        source=(
            "-- CREATE TABLE fake_comment (id INT);\n"
            "SELECT 'CREATE TABLE fake_str (id INT);', phantom_call(t.phantom_field) FROM t;\n"
            "\n"
            "CREATE SCHEMA app;\n"
            "\n"
            "CREATE TYPE app.mode AS ENUM ('fast', 'safe');\n"
            "\n"
            "CREATE TABLE app.users (\n"
            "    id INT PRIMARY KEY\n"
            ");\n"
            "\n"
            "CREATE VIEW app.active_users AS\n"
            "    SELECT id FROM app.users;\n"
            "\n"
            "CREATE FUNCTION app.total_budget() RETURNS INT AS $$\n"
            "    SELECT 1;\n"
            "$$ LANGUAGE SQL;\n"
        ),
        expected_spans={
            "app": (4, 4),
            "mode": (6, 6),
            "users": (8, 10),
            "active_users": (12, 13),
            "total_budget": (15, 17),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "phantom_field", "id"),
        qualified_cases={
            "app.mode": (6, 6),
            "app.users": (8, 10),
            "app.active_users": (12, 13),
            "app.total_budget": (15, 17),
            "other.users": None,
            "app::users": None,
        },
        move_symbol="app.total_budget",
        shift_prefix="-- shifted\n\n",
    ),
    PilotFixture(
        language="hcl",
        path="infra/main.tf",
        source=(
            '# resource "aws_s3_bucket" "fake_comment" {}\n'
            "locals {\n"
            '  msg = "resource \\"aws_s3_bucket\\" \\"fake_str\\" {}"\n'
            "}\n"
            "\n"
            'variable "region" {\n'
            '  default = "us-east-1"\n'
            "}\n"
            "\n"
            'data "aws_caller_identity" "current" {}\n'
            "\n"
            'resource "aws_s3_bucket" "artifacts" {\n'
            "  bucket = phantom_call(var.region)\n"
            "}\n"
            "\n"
            'output "bucket_name" {\n'
            "  value = aws_s3_bucket.artifacts.bucket\n"
            "}\n"
        ),
        expected_spans={
            "msg": (3, 3),
            "region": (6, 8),
            "current": (10, 10),
            "artifacts": (12, 14),
            "bucket_name": (16, 18),
        },
        forbidden_names=("fake_comment", "fake_str", "phantom_call", "bucket", "default"),
        qualified_cases={
            "variable.region": (6, 8),
            "aws_caller_identity.current": (10, 10),
            "aws_s3_bucket.artifacts": (12, 14),
            "output.bucket_name": (16, 18),
            "aws_s3_bucket.missing": None,
            "variable::region": None,
        },
        move_symbol="aws_s3_bucket.artifacts",
        shift_prefix="# shifted\n\n",
    ),
)

# 56 long-tail tree-sitter-language-pack fixtures: (lang_id, path, source, expected_symbol, comment_prefix)
_LT_TRAP = ("fake_comment_sym", "phantom_call_sym")
_LT_RAW: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "ada",
        "src/core.adb",
        "-- function fake_comment_sym return Integer is\npackage body Core is\n  function Compute(X : Integer) return Integer is\n  begin\n    return phantom_call_sym(X);\n  end Compute;\nend Core;\n",
        "Compute",
        "-- shifted\n\n",
    ),
    (
        "bicep",
        "infra/main.bicep",
        "// param fake_comment_sym string = 'x'\nparam location string = 'eastus'\nresource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {\n  name: location\n}\n",
        "storage",
        "// shifted\n\n",
    ),
    (
        "cairo",
        "src/core.cairo",
        "// fn fake_comment_sym() {}\nstruct Point { x: felt252 }\nfn compute(p: Point) -> felt252 {\n  phantom_call_sym(p.x)\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "clojure",
        "src/core.clj",
        "; (defn fake_comment_sym [x] x)\n(ns demo.core)\n(defn compute [x]\n  (phantom_call_sym x))\n",
        "compute",
        "; shifted\n\n",
    ),
    (
        "cmake",
        "cmake/helpers.cmake",
        "# function(fake_comment_sym x)\nfunction(compute_target name)\n  phantom_call_sym(${name})\nendfunction()\n",
        "compute_target",
        "# shifted\n\n",
    ),
    (
        "commonlisp",
        "src/core.lisp",
        "; (defun fake_comment_sym (x) x)\n(defun compute (x)\n  (phantom_call_sym x))\n",
        "compute",
        "; shifted\n\n",
    ),
    (
        "cuda",
        "src/kernel.cu",
        "// void fake_comment_sym() {}\nstruct Point { int x; };\n__global__ void compute(int *out) {\n  phantom_call_sym(out);\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "d",
        "src/core.d",
        "// int fake_comment_sym() { return 0; }\nstruct Point { int x; }\nint compute(int v) { return phantom_call_sym(v); }\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "dart",
        "lib/service.dart",
        "// int fake_comment_sym() => 0;\nclass Service {\n  int run(int x) {\n    return phantom_call_sym(x);\n  }\n}\n",
        "Service",
        "// shifted\n\n",
    ),
    (
        "dockerfile",
        "Dockerfile",
        "# FROM alpine AS fake_comment_sym\nFROM python:3.14 AS builder\nRUN echo phantom_call_sym\n",
        "builder",
        "# shifted\n\n",
    ),
    (
        "elisp",
        "lisp/core.el",
        "; (defun fake_comment_sym (x) x)\n(defun compute (x)\n  (phantom_call_sym x))\n",
        "compute",
        "; shifted\n\n",
    ),
    (
        "elm",
        "src/Core.elm",
        "-- fake_comment_sym x = x\nmodule Core exposing (compute)\ntype Mode = Fast | Safe\ncompute : Int -> Int\ncompute x =\n  phantom_call_sym x\n",
        "compute",
        "-- shifted\n\n",
    ),
    (
        "erlang",
        "src/core.erl",
        "% fake_comment_sym(X) -> X.\n-module(core).\ncompute(X) ->\n  phantom_call_sym(X).\n",
        "compute",
        "% shifted\n\n",
    ),
    (
        "fennel",
        "src/core.fnl",
        "; (fn fake_comment_sym [x] x)\n(fn compute [x]\n  (phantom_call_sym x))\n",
        "compute",
        "; shifted\n\n",
    ),
    (
        "fish",
        "scripts/core.fish",
        "# function fake_comment_sym\nfunction compute\n  phantom_call_sym $argv\nend\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "fortran",
        "src/core.f90",
        "! function fake_comment_sym(x)\nmodule core\ncontains\nfunction compute(x) result(res)\n  integer, intent(in) :: x\n  integer :: res\n  res = phantom_call_sym(x)\nend function compute\nend module core\n",
        "compute",
        "! shifted\n\n",
    ),
    (
        "fsharp",
        "src/Core.fsx",
        "// let fake_comment_sym x = x\nmodule Core\ntype Mode = Fast | Safe\nlet compute x = phantom_call_sym x\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "gdscript",
        "scripts/engine.gd",
        "# func fake_comment_sym(): pass\nclass_name Engine\nfunc compute(x):\n  return phantom_call_sym(x)\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "gleam",
        "src/core.gleam",
        "// pub fn fake_comment_sym(x) { x }\npub type Mode {\n  Fast\n  Safe\n}\npub fn compute(x: Int) -> Int {\n  phantom_call_sym(x)\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "glsl",
        "shaders/light.glsl",
        "// float fake_comment_sym(float x) { return x; }\nstruct Light { float intensity; };\nfloat compute(float x) {\n  return phantom_call_sym(x);\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "graphql",
        "schema/api.graphql",
        "# type fake_comment_sym { id: ID! }\ntype User {\n  id: ID!\n}\ninterface Node {\n  id: ID!\n}\n",
        "User",
        "# shifted\n\n",
    ),
    (
        "hare",
        "src/core.ha",
        "// fn fake_comment_sym() void = void;\ntype point = struct { x: int };\nfn compute(p: point) int = {\n  return phantom_call_sym(p.x);\n};\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "haskell",
        "src/Core.hs",
        "-- fake_comment_sym x = x\nmodule Demo where\ndata Mode = Fast | Safe\ncompute :: Int -> Int\ncompute x = phantom_call_sym x\n",
        "compute",
        "-- shifted\n\n",
    ),
    (
        "haxe",
        "src/Service.hx",
        "// class fake_comment_sym {}\nclass Service {\n  public function run(x: Int): Int {\n    return phantom_call_sym(x);\n  }\n}\n",
        "run",
        "// shifted\n\n",
    ),
    (
        "hlsl",
        "shaders/light.hlsl",
        "// float fake_comment_sym(float x) { return x; }\nstruct Light { float intensity; };\nfloat compute(float x) {\n  return phantom_call_sym(x);\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "janet",
        "src/core.janet",
        "# (defn fake_comment_sym [x] x)\n(defn compute [x]\n  (phantom_call_sym x))\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "jsonnet",
        "config/core.jsonnet",
        "// local fake_comment_sym(x) = x;\nlocal helper(x) = phantom_call_sym(x);\n{\n  compute(v): helper(v),\n}\n",
        "helper",
        "// shifted\n\n",
    ),
    (
        "julia",
        "src/core.jl",
        "# function fake_comment_sym(x) end\nmodule Core\nstruct Point\n  x::Int\nend\nfunction compute(p::Point)\n  return phantom_call_sym(p.x)\nend\nend\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "lua",
        "src/core.lua",
        "-- function fake_comment_sym(x) return x end\nfunction compute(a)\n  return phantom_call_sym(a)\nend\n",
        "compute",
        "-- shifted\n\n",
    ),
    (
        "make",
        "build/rules.mk",
        "# fake_comment_sym:\nBUILD_DIR := build\nbuild:\n\techo $(BUILD_DIR)\n",
        "build",
        "# shifted\n\n",
    ),
    (
        "nim",
        "src/core.nim",
        "# proc fake_comment_sym(x: int): int = x\ntype Point = object\n  x: int\nproc compute(p: Point): int =\n  result = phantom_call_sym(p.x)\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "nix",
        "nix/core.nix",
        "# fake_comment_sym = 1;\n{\n  compute = x: phantom_call_sym x;\n  limit = 10;\n}\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "ocaml",
        "src/core.ml",
        "(* let fake_comment_sym x = x *)\ntype mode = Fast | Safe\nlet compute x = phantom_call_sym x\n",
        "compute",
        "(* shifted *)\n\n",
    ),
    (
        "ocaml_interface",
        "src/core.mli",
        "(* val fake_comment_sym : int -> int *)\ntype mode = Fast | Safe\nval compute : int -> int\n",
        "compute",
        "(* shifted *)\n\n",
    ),
    (
        "odin",
        "src/core.odin",
        "// fake_comment_sym :: proc() {}\npackage core\nPoint :: struct { x: int }\ncompute :: proc(p: Point) -> int {\n  return phantom_call_sym(p.x)\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "pascal",
        "src/core.pas",
        "// function fake_comment_sym(X: Integer): Integer;\nunit Core;\ninterface\nfunction Compute(X: Integer): Integer;\nimplementation\nfunction Compute(X: Integer): Integer;\nbegin\n  Result := phantom_call_sym(X);\nend;\nend.\n",
        "Core",
        "// shifted\n\n",
    ),
    (
        "perl",
        "lib/Core.pm",
        "# sub fake_comment_sym { }\npackage Core;\nsub compute {\n  my ($x) = @_;\n  return phantom_call_sym($x);\n}\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "pony",
        "src/service.pony",
        "// class fake_comment_sym\nclass Service\n  fun run(x: U64): U64 =>\n    phantom_call_sym(x)\n",
        "run",
        "// shifted\n\n",
    ),
    (
        "powershell",
        "scripts/core.ps1",
        "# function fake_comment_sym {}\nfunction Compute($x) {\n  return phantom_call_sym $x\n}\n",
        "Compute",
        "# shifted\n\n",
    ),
    (
        "prisma",
        "prisma/schema.prisma",
        "// model fake_comment_sym { id Int @id }\nmodel User {\n  id Int @id\n}\nenum Role {\n  ADMIN\n}\n",
        "User",
        "// shifted\n\n",
    ),
    (
        "proto",
        "proto/store.proto",
        '// message fake_comment_sym {}\nsyntax = "proto3";\nmessage Item {\n  string id = 1;\n}\nservice Store {\n  rpc Get(Item) returns (Item);\n}\n',
        "Store",
        "// shifted\n\n",
    ),
    (
        "puppet",
        "manifests/service.pp",
        "# class fake_comment_sym {}\nclass core::service {\n  notify { 'ok': }\n}\n",
        "core::service",
        "# shifted\n\n",
    ),
    (
        "purescript",
        "src/Core.purs",
        "-- fake_comment_sym x = x\nmodule Core where\ndata Mode = Fast | Safe\ncompute :: Int -> Int\ncompute x = phantom_call_sym x\n",
        "compute",
        "-- shifted\n\n",
    ),
    (
        "r",
        "R/core.r",
        "# fake_comment_sym <- function(x) x\ncompute <- function(x) {\n  phantom_call_sym(x)\n}\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "racket",
        "src/core.rkt",
        "#lang racket\n; (define (fake_comment_sym x) x)\n(define (compute x)\n  (phantom_call_sym x))\n",
        "compute",
        "",
    ),
    (
        "rego",
        "policy/authz.rego",
        "# fake_comment_sym if { true }\npackage authz\nallow if {\n  phantom_call_sym(input.role)\n}\n",
        "allow",
        "# shifted\n\n",
    ),
    (
        "scala",
        "src/Service.scala",
        "// def fake_comment_sym(x: Int): Int = x\npackage demo\nclass Service {\n  def run(x: Int): Int = phantom_call_sym(x)\n}\n",
        "Service",
        "// shifted\n\n",
    ),
    (
        "scheme",
        "src/core.ss",
        "; (define (fake_comment_sym x) x)\n(define (compute x)\n  (phantom_call_sym x))\n",
        "compute",
        "; shifted\n\n",
    ),
    (
        "solidity",
        "contracts/Vault.sol",
        "// contract fake_comment_sym {}\npragma solidity ^0.8.0;\ncontract Vault {\n  function deposit(uint256 x) public pure returns (uint256) {\n    return phantom_call_sym(x);\n  }\n}\n",
        "Vault",
        "// shifted\n\n",
    ),
    (
        "squirrel",
        "src/core.nut",
        "// function fake_comment_sym(x) { return x; }\nfunction compute(v) {\n  return phantom_call_sym(v);\n}\n",
        "compute",
        "// shifted\n\n",
    ),
    (
        "starlark",
        "defs.bzl",
        "# def fake_comment_sym(x): return x\ndef compute(x):\n  return phantom_call_sym(x)\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "tcl",
        "scripts/core.tcl",
        "# proc fake_comment_sym {x} { return $x }\nproc compute {x} {\n  return [phantom_call_sym $x]\n}\n",
        "compute",
        "# shifted\n\n",
    ),
    (
        "thrift",
        "idl/store.thrift",
        "// struct fake_comment_sym {}\nstruct Item {\n  1: string id\n}\nservice Store {\n  Item get(1: string id)\n}\n",
        "Store",
        "// shifted\n\n",
    ),
    (
        "vhdl",
        "rtl/core.vhd",
        "-- entity fake_comment_sym is end entity;\nentity core is\nend entity core;\narchitecture rtl of core is\nbegin\nend architecture rtl;\n",
        "core",
        "-- shifted\n\n",
    ),
    (
        "vim",
        "plugin/core.vim",
        '" function! fake_comment_sym(x)\nfunction! Compute(x)\n  return phantom_call_sym(a:x)\nendfunction\n',
        "Compute",
        '" shifted\n\n',
    ),
    (
        "wgsl",
        "shaders/core.wgsl",
        "// fn fake_comment_sym(x: f32) -> f32 { return x; }\nstruct Vertex { pos: vec3<f32> };\nfn compute(x: f32) -> f32 {\n  return phantom_call_sym(x);\n}\n",
        "compute",
        "// shifted\n\n",
    ),
)

LONG_TAIL_FIXTURES: tuple[LongTailFixture, ...] = tuple(
    LongTailFixture(
        language=lang_id,
        path=path,
        source=source,
        expected_symbol=symbol,
        forbidden_names=_LT_TRAP,
        shift_prefix=prefix,
    )
    for lang_id, path, source, symbol, prefix in _LT_RAW
)
