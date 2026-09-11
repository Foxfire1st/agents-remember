"""Real waiting-door, selected-operation and writer fixtures for lifecycle boundary suites.

The queued-operation fixtures that used to live here drove the detached worker through
``OperationRuntime``. Closeout and integration are synchronous now, so their driving
tests were rewritten on the public tool surface and this module keeps only the fixture
that still has a consumer.
"""

from pathlib import Path

from test_closeout_queue import MASTER_A, QueueFixture


def selected_fixture(root: Path, *, memory_mode: str) -> QueueFixture:
    fixture = QueueFixture(root, memory_mode=memory_mode)
    fixture.declare(MASTER_A)
    return fixture
