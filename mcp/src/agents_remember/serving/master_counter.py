"""The series counter's lean projection of the same native commit-range rows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.changeset_cache import ChangesetCache, cached_read
from agents_remember.serving.changeset_reads import change_counters
from agents_remember.serving.changeset_scheduler import _BoundedReads, read_async
from agents_remember.serving.master_net_generation import (
    MASTER_NET_SCOPE,
    MasterNetPins,
    MasterNetSelection,
    load_master_contract,
    select_master_net,
)
from agents_remember.serving.response_contract import CHANGESET_READ_RESPONSES, MasterCounters
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.serving.master_preparation import MasterPreparation


class PreparationBusy(RuntimeError):
    """The bounded master preparation owner cannot accept another generation."""


class NetDiff(Protocol):
    def __call__(
        self, repo_path: Path | None, base: str, tip: str, *, side: str, master: str
    ) -> list[dict[str, Any]]: ...


class LeafSummaries(Protocol):
    def __call__(
        self, config: McpRuntimeConfig, repo_id: str, master: str
    ) -> list[dict[str, Any]]: ...


class CounterReader(Protocol):
    def __call__(
        self,
        config: McpRuntimeConfig,
        repo_id: str,
        master: str,
        *,
        include_leaves: bool = True,
        pins: MasterNetPins | None = None,
    ) -> dict[str, Any]: ...


class MasterResponse(Protocol):
    def __call__(self, produce: Any) -> Response: ...


@dataclass(frozen=True)
class MasterChangesetRef:
    """A master selection shared by its full listing and its lean counter."""

    repo: str
    master: str
    include_leaves: Annotated[bool, Query(alias="includeLeaves")] = True
    code_base: Annotated[str, Query(alias="codeBase")] = ""
    code_tip: Annotated[str, Query(alias="codeTip")] = ""
    memory_base: Annotated[str, Query(alias="memoryBase")] = ""
    memory_tip: Annotated[str, Query(alias="memoryTip")] = ""

    def pins(self) -> MasterNetPins:
        return MasterNetPins(self.code_base, self.code_tip, self.memory_base, self.memory_tip)


@dataclass(frozen=True)
class CounterSource:
    """Project native range rows using the full listing's calculation owners."""

    net_diff: NetDiff
    leaf_summaries: LeafSummaries

    def read(
        self,
        config: McpRuntimeConfig,
        repo_id: str,
        master: str,
        *,
        include_leaves: bool = True,
        pins: MasterNetPins | None = None,
    ) -> dict[str, Any]:
        """Count complete native rows without reading sidecars or transmitting file lists.

        Selection and native refusal handling are the full listing's own owners. The
        only omitted work is presentation data that the counter cannot display.
        """
        selection = select_master_net(config, repo_id, master, pins or MasterNetPins())
        counters = {"code": change_counters([]), "memory": change_counters([])}
        generation = None
        if selection is not None:
            contract = load_master_contract(config, repo_id, master)
            endpoints = selection.endpoints
            counters = cached_read(
                (
                    "master-counter-net",
                    str(contract.code_repo_path if contract else ""),
                    str(contract.memory_repo_path if contract else ""),
                    selection.digest,
                ),
                (),
                lambda: self._net_counters(contract, selection, master),
            )
            generation = {
                "codeBase": endpoints.code_base,
                "codeTip": endpoints.code_tip,
                "memoryBase": endpoints.memory_base,
                "memoryTip": endpoints.memory_tip,
                "digest": selection.digest,
            }
        return {
            "master": master,
            "leaves": self.leaf_summaries(config, repo_id, master)
            if selection is not None and include_leaves
            else [],
            "counters": counters,
            "generation": generation,
            "currentness": selection.currentness if selection else "unmeasured",
            "scope": selection.scope if selection else MASTER_NET_SCOPE,
        }

    def _net_counters(
        self, contract: WorktreeContract | None, selection: MasterNetSelection, master: str
    ) -> dict[str, Any]:
        """Pure pair totals share the same bounded, immutable in-process admission."""
        endpoints = selection.endpoints
        code = self.net_diff(
            contract.code_repo_path if contract else None,
            endpoints.code_base,
            endpoints.code_tip,
            side="code",
            master=master,
        )
        memory = self.net_diff(
            contract.memory_repo_path if contract else None,
            endpoints.memory_base,
            endpoints.memory_tip,
            side="memory",
            master=master,
        )
        return {"code": change_counters(code), "memory": change_counters(memory)}


@dataclass(frozen=True)
class CounterRoute:
    config: McpRuntimeConfig
    cache: ChangesetCache
    reads: _BoundedReads
    reader: CounterReader
    render: MasterResponse

    def register(self, app: FastAPI) -> None:
        @app.get(
            "/api/changeset/master/counters",
            response_model=MasterCounters,
            responses=CHANGESET_READ_RESPONSES,
        )
        async def counters(
            ref: Annotated[MasterChangesetRef, Depends()], request: Request
        ) -> Response:
            preparation = self.cache.master_preparation
            if preparation is None:
                raise RuntimeError("master counter preparation was not composed")

            async def produce() -> Response:
                try:
                    value = await preparation.answer(
                        ref.repo, ref.master, include_leaves=ref.include_leaves, pins=ref.pins()
                    )
                except PreparationBusy:
                    return Response(status_code=503, headers={"Retry-After": "1"})
                except (AuthorityError, FileNotFoundError) as error:
                    return preparation_error(self.render, error)
                return self.cache.response(request, lambda: self.render(lambda: value))

            return await read_async(request, produce)


def preparation_error(render: MasterResponse, error: Exception) -> Response:
    """Keep selection failures in the full listing's existing status mapping."""

    def failed() -> None:
        raise error

    return render(failed)


async def prepared_master_pins(
    request: Request,
    preparation: MasterPreparation,
    ref: MasterChangesetRef,
    render: MasterResponse,
) -> MasterNetPins | Response:
    async def resolve() -> MasterNetPins | Response:
        try:
            return await preparation.ensure(
                ref.repo, ref.master, include_leaves=ref.include_leaves, pins=ref.pins()
            )
        except PreparationBusy:
            return Response(status_code=503, headers={"Retry-After": "1"})
        except (AuthorityError, FileNotFoundError) as error:
            return preparation_error(render, error)

    return await read_async(request, resolve)
