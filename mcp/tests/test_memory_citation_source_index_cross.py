from __future__ import annotations

import json
import os
import subprocess
import sys

from agents_remember.memory_quality.style.citations import source_index
from test_memory_citation_source_index import MCP_SRC, IndexCase


class CrossProcessTests(IndexCase):
    def process_environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PYTHONPATH": str(MCP_SRC),
            "XDG_CACHE_HOME": str(self.cache),
        }

    def test_concurrent_cold_acquisition_has_exactly_one_builder(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        script = """
import json, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
trees=Trees(Path(sys.argv[1]), Path(sys.argv[2]))
with source_index.open_repository_index(trees) as index:
    print(json.dumps({'state': index.metrics.state, 'snapshot': index.snapshot_id}))
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.code), str(self.memory)],
                env=self.process_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _index in range(6)
        ]
        results: list[dict[str, str]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))

        self.assertEqual([one["state"] for one in results].count("built"), 1)
        self.assertEqual({one["snapshot"] for one in results}, {results[0]["snapshot"]})

    def test_unseen_anchor_is_queryable_in_a_later_process_without_source_work(self) -> None:
        self.write("one.py", "def never_queried_during_build():\n    return 1\n")
        self.acquire()
        script = """
import json, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import model, source_index, symbol_index
from agents_remember.memory_quality.style.citations.resolution import Trees
trees=Trees(Path(sys.argv[1]), Path(sys.argv[2]))
anchor=model.Anchor(model.SYMBOL, 'never_queried_during_build')
with source_index.open_repository_index(trees) as index:
    seen=symbol_index.locate((anchor,), trees, index=index)[anchor]
    print(json.dumps({'files': seen.files, **index.telemetry()}))
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.code), str(self.memory)],
            env=self.process_environment(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["state"], "warm")
        self.assertEqual(result["sourceFilesRead"], 0)
        self.assertEqual(result["sourceFilesTokenized"], 0)
        self.assertEqual(result["sourceFilesParsed"], 0)

    def test_killed_lock_owner_releases_lock_and_next_publisher_reclaims_temp(self) -> None:
        self.write("one.py", "alpha = 1\n")
        script = """
import fcntl, signal, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
paths=source_index.cache_paths(Trees(Path(sys.argv[1]), Path(sys.argv[2])))
paths.slot.mkdir(parents=True, exist_ok=True)
handle=paths.lock.open('a+b')
fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
(paths.slot / '.index.sqlite3.999.killed.tmp').write_text('partial', encoding='utf-8')
print('locked', flush=True)
signal.pause()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.code), str(self.memory)],
            env=self.process_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline().strip(), "locked")
        process.kill()
        process.communicate(timeout=10)

        built, _snapshot = self.acquire()

        self.assertEqual(built["state"], "built")
        paths = source_index.cache_paths(self.trees)
        self.assertFalse((paths.slot / ".index.sqlite3.999.killed.tmp").exists())
