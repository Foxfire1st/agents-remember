#!/usr/bin/env python3
"""Build, start, stop, reset and safety-check the disposable PNT sandbox (PNT-R11).

    python3 scripts/pnt-sandbox.py build
    python3 scripts/pnt-sandbox.py start <PNT build checkout>
    python3 scripts/pnt-sandbox.py stop
    python3 scripts/pnt-sandbox.py reset
    python3 scripts/pnt-sandbox.py check

See ``scripts/pnt_sandbox/README.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, Path(__file__).resolve().parent.as_posix())

from pnt_sandbox.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
