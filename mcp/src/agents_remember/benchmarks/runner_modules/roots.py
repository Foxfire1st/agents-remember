from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from agents_remember.install.assets import packaged_source_root


@contextlib.contextmanager
def benchmark_root_context(benchmarks_root: Path | None) -> Iterator[Path]:
    if benchmarks_root is not None:
        yield benchmarks_root
        return

    with packaged_source_root() as source_root:
        yield source_root / "benchmarks"
