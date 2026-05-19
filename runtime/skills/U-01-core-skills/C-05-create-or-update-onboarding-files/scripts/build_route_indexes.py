from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = CORE_ROOT / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.route_index import build_route_indexes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate route-level onboarding index files.")
    parser.add_argument("--code-repository-root", required=True, type=Path)
    parser.add_argument("--onboarding-root", required=True, type=Path)
    parser.add_argument("--repository", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)

    result = build_route_indexes(
        code_root=args.code_repository_root,
        onboarding_root=args.onboarding_root,
        repository=args.repository,
        dry_run=args.dry_run,
    )

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        mode = "would write" if args.dry_run else "wrote"
        print(
            f"Route indexes: {result.routes} routes, {mode} {result.written}, "
            f"unchanged {result.unchanged}"
        )
        for index in result.indexes:
            print(index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
