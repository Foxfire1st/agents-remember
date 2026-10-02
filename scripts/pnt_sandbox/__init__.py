"""Build, start, stop, reset and safety-check the disposable PNT sandbox (PNT-R11).

Development tooling for running a PNT build without touching the live roots; it is not part of
the shipped wheel. ``scripts/pnt-sandbox.py`` is the entry point and runs with the system Python
(3.10 or newer) as well as with a checkout's ``mcp/.venv``. Two modules are different: they run
inside the Python environment of the PNT build under test and import that build's own code
(``build_roots.py``, ``build_tool_calls.py``).
"""
