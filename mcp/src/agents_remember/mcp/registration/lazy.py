"""A lazy view of the payload builders (MIK-R204 rule 1, spike only)."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any


class _Tools:
    """Attribute access imports the payload-builder package on first use, once."""

    def __getattr__(self, name: str) -> Any:
        """The package's export first, then the submodule the name lives in.

        A payload builder need not be re-exported by the package (`knowledge_read_payload` is one such),
        so a name the package does not carry is looked for in its own module -- `<name>` without the
        `_payload` suffix -- rather than reported missing from a package that simply never exported it.
        """

        from agents_remember.mcp import tools as package  # noqa: PLC0415

        try:
            return getattr(package, name)
        except AttributeError:
            # Not every builder is re-exported, and guessing its module by name is how a wrong path gets
            # written: search the package's own submodules and return the one that carries it.
            for path in getattr(package, "__path__", []):
                for module_path in Path(path).glob("*.py"):
                    if module_path.stem in {"__init__", "base"}:
                        continue
                    module = import_module(f"agents_remember.mcp.tools.{module_path.stem}")
                    found = getattr(module, name, None)
                    if found is not None:
                        return found
            raise


tools = _Tools()
