"""One native exit subscription, feeding the dashboard's existing change pacer."""

from __future__ import annotations

import asyncio
import logging
import os
import select
from collections.abc import Callable
from pathlib import Path

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.serving.background_accounting import (
    OWNER_PROJECTION,
    BackgroundLedger,
    offload,
)
from agents_remember.serving.paseo.paseo_process_record import inspect_record, read_process

logger = logging.getLogger(__name__)


def _open_supervisor(home: Path) -> int | None:
    """Bind the proved process, then prove it again after opening its stable handle."""
    record = inspect_record(home, "host activity", read_process)
    if record.kind != "own" or record.pid is None:
        return None
    fd = os.pidfd_open(record.pid, 0)
    try:
        proved = inspect_record(home, "host activity", read_process)
        exited = select.poll()
        exited.register(fd, select.POLLIN)
        if proved.kind != "own" or proved.pid != record.pid or exited.poll(0):
            os.close(fd)
            return None
        return fd
    except BaseException:
        os.close(fd)
        raise


class HostSupervisorSubscription:
    """At most one pidfd/loop reader; shutdown or exit always releases it."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    @property
    def armed(self) -> bool:
        return self._fd is not None

    @property
    def started(self) -> bool:
        return self._started

    async def refresh(
        self, home: Path | None, changed: Callable[[], None], ledger: BackgroundLedger | None
    ) -> None:
        self._started = True
        opened: list[int] = []

        def open_current() -> None:
            if home is not None:
                fd = _open_supervisor(home)
                if fd is not None:
                    opened.append(fd)

        try:
            await offload(OWNER_PROJECTION, ledger, open_current)
            self.close()
            if opened:
                fd = opened[0]
                self._loop = asyncio.get_running_loop()
                self._loop.add_reader(fd, self._exited, fd, changed)
                self._fd = fd
        except (OSError, PaseoRuntimeFailure, NotImplementedError, AttributeError) as error:
            self.close()
            logger.warning(
                "Host activity is unknown: cannot subscribe to its supervisor: %s", error
            )
        except BaseException:
            self.close()
            raise
        finally:
            for fd in opened:
                if fd != self._fd:
                    os.close(fd)

    def _exited(self, fd: int, changed: Callable[[], None]) -> None:
        if self._fd != fd:
            return
        self.close()
        changed()

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            if self._loop is not None:
                self._loop.remove_reader(fd)
            os.close(fd)
