"""What the one call prepares on a host that has never run this product (MIK-R196 rules 2-3).

Three stages, in this order, each through the owner that already exists:

1. **The scaffold** -- ``runtime_install``'s own install pass, run only when the configured
   coordination root has no scaffold yet. The predicate names the retained assets the surviving
   installer creates in *every* delivery mode (:data:`SCAFFOLD_ENTRIES`); the capsule mode
   deliberately withholds ``AGENTS.md``, so a predicate that required it would read a correctly
   installed root as unprepared forever. A missing memory root, task or repository is not a
   reason to stop: setup is what this seat is for.
2. **The host authority** -- the ``paseoRuntime`` block of the installed MCP settings file, created
   only when that file has no block (:mod:`agents_remember.kernel.primitives.paseo_authority`).
   MIK-R137 keeps the developer's user settings file out of cold preparation: the product's own
   authority file is the one file a launch may write a host block into. The block is then read
   back through the same authority reader the rest of the product uses, so "configured" is what
   the product can actually load rather than what this module meant to write.
3. **The host itself** -- the existing provisioner, in its install intent, so a host that is
   already serving keeps its sessions; then one proof that the daemon answers on the configured
   address, and one that the AR plug-in stamped after loading. ``configured: false`` and a
   kept-running-but-restart-owed host are never read as a launchable host.

A stage that fails raises :class:`FirstRunPreparationError` naming the stage, every effect the
call had already made and the host state it left behind, so the start's answer reports partial
progress instead of a false success; the same report is on the call's own context. No stage is
undone: there is no rollback here, only an accurate account. Each entry to preparation runs these
three converging stages; receipt- or binding-owned launch retries reconcile without preparing again.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn

from fastapi import HTTPException

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.errors import PaseoRuntimeFailure, RolePreparationError
from agents_remember.install.effects import capture_install_effects
from agents_remember.install.experiment import ExperimentError
from agents_remember.install.runtime import RuntimeInstallRequest, install_runtime_from_config
from agents_remember.kernel.primitives.paseo_authority import (
    HostAuthorityRefused,
    authority_settings_path,
    establish_authority_paseo_runtime,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_plugin_files import (
    installed_plugin_path,
    read_loaded_stamp,
    tree_digest,
)
from agents_remember.serving.paseo.paseo_provision import provision_for_install

# The retained assets a prepared coordination root holds, as the surviving installer creates them
# (L188-B01). ``runtime_install`` writes both in the unmodified and in the capsule delivery mode:
# the packaged tool-detail docs and the user-owned task tree. It writes ``AGENTS.md`` only in the
# unmodified mode and removes it in the capsule mode, and it writes no ``skills`` copy at all since
# MIK-R196 rule 7 -- so neither of those can be a scaffold entry.
SCAFFOLD_ENTRIES = ("docs", "tasks")

_RETRY = "Resolve the reason named in detail, then call role_start again with the same request id."

# The report of the preparation this call made, read once by the receipt builder. It travels on
# the call's own context instead of through five signatures, and it is cleared with the call. It is
# set before the first stage writes anything, so a failed preparation leaves the stages it did
# complete readable on the same context (L188-B06).
_FIRST_RUN_REPORT: ContextVar[dict[str, Any] | None] = ContextVar(
    "ar_first_run_report", default=None
)


class FirstRunPreparationError(RolePreparationError):
    """A failed first-run stage, carrying the report of everything the call did before it.

    The start's answer only carries ``detail``, the refusal status and the next action
    (``cli.paseo_role_tools._start_refusal``), so the completed stages, their paths and the host
    state left behind travel inside ``detail``. ``report`` keeps the same facts as data for a
    caller that can carry them structurally.
    """

    def __init__(self, report: dict[str, Any], stage: str, status: str) -> None:
        self.report = report
        self.stage = stage
        super().__init__(status, _failure_detail(report, stage), _RETRY)


def recall_first_run_report() -> dict[str, Any] | None:
    """The preparation report of the start currently being served, if this call made one."""

    return _FIRST_RUN_REPORT.get()


def refuse_after_preparation(error: Exception) -> NoReturn:
    """Carry completed preparation through a later catalog/launch refusal, before reset."""

    report = recall_first_run_report()
    if report is None or isinstance(error.__cause__, FirstRunPreparationError):
        if isinstance(error, HTTPException):
            raise error
        raise HTTPException(status_code=409, detail=str(error)) from error
    detail = str(error.detail) if isinstance(error, HTTPException) else str(error)
    refusal = _fail(report, "launch", detail, status="launch-after-preparation-failed")
    raise HTTPException(
        status_code=error.status_code if isinstance(error, HTTPException) else 409,
        detail=str(refusal),
        headers=error.headers if isinstance(error, HTTPException) else None,
    ) from refusal


def scaffold_present(config: McpRuntimeConfig) -> bool:
    return all((config.coordination_root / entry).exists() for entry in SCAFFOLD_ENTRIES)


def prepare_first_run(config: McpRuntimeConfig) -> dict[str, Any]:
    """Run the three stages and answer with what each of them observed."""

    report: dict[str, Any] = {"ok": True, "stages": []}
    _FIRST_RUN_REPORT.set(report)
    _scaffold_stage(config, report)
    _authority_stage(config, report)
    _host_stage(config, report)
    return report


def _stage(report: dict[str, Any], name: str, **observed: Any) -> dict[str, Any]:
    entry = {"stage": name, **observed}
    report["stages"].append(entry)
    return entry


def _fail(
    report: dict[str, Any],
    stage: str,
    detail: str,
    *,
    status: str,
    retained: dict[str, Any] | None = None,
) -> FirstRunPreparationError:
    """Record the failing stage (and the host state it left) and answer with the refusal."""

    report["ok"] = False
    report["detail"] = detail
    report.setdefault("failedStage", stage)
    if retained is not None:
        report["retainedHost"] = retained
    return FirstRunPreparationError(report, stage, status)


def _failure_detail(report: dict[str, Any], stage: str) -> str:
    """The failed stage, the stages that did finish, and the host state that remains."""

    prefix = (
        "The role launch failed after first-run preparation"
        if stage == "launch"
        else f"The first-run preparation failed at the {stage} stage"
    )
    parts = [f"{prefix}: {report.get('detail', '')}"]
    effects = _completed_effects(report)
    if effects:
        parts.append("Completed before that failure: " + "; ".join(effects) + ".")
    retained = _retained_host(report, stage)
    if retained:
        parts.append(retained)
    return " ".join(parts)


def _completed_effects(report: dict[str, Any]) -> list[str]:
    """One clause per stage this call completed, in the order it completed them."""

    effects: list[str] = []
    for entry in report.get("stages", []):
        name = entry.get("stage")
        if name == "scaffold":
            effects.append(f"scaffold {entry.get('state')} at {entry.get('root')}")
            if entry.get("effects"):
                effects.append("installer effects: " + json.dumps(entry["effects"], sort_keys=True))
        elif name == "host-authority":
            effects.append(
                f"host authority {entry.get('state')} in {entry.get('path')} "
                f"(changed={entry.get('changed')})"
            )
        elif name == "host":
            effects.append(
                f"host provisioned (changed={entry.get('changed')}, "
                f"restart required: {entry.get('restartRequired')}, "
                f"daemon: {json.dumps(entry.get('daemon'), sort_keys=True)}, "
                f"changes: {json.dumps(entry.get('changes'), sort_keys=True)})"
            )
        elif name == "host-verify":
            effects.append(
                f"host verified running (server {entry.get('serverId')}, "
                f"plug-in {entry.get('pluginDigest')})"
            )
    return effects


def _retained_host(report: dict[str, Any], stage: str) -> str:
    """The host state this call left behind, in the host's own words where it spoke."""

    retained = report.get("retainedHost")
    if not isinstance(retained, dict):
        return ""
    daemon = retained.get("daemon")
    action = daemon.get("action") if isinstance(daemon, dict) else None
    owed = retained.get("restartRequired")
    owed_text = (
        f"; the provisioner reports it still running on settings it no longer holds "
        f"({', '.join(owed)})"
        if isinstance(owed, list) and owed
        else ""
    )
    if isinstance(action, str) and action:
        host = f"the provisioner's own report says the daemon action was {action!r}{owed_text}"
    else:
        host = "the provisioner's own report named no daemon state, so its process state is unknown"
    if stage == "host-verify":
        host += "; the daemon did not pass the post-provision check"
    parts = f"Remaining host state: {host}."
    changes = retained.get("changes")
    if isinstance(changes, list) and changes:
        parts += f" Provisioner changes before the failure: {len(changes)}; " + json.dumps(
            changes, sort_keys=True
        )
    return parts


def _delivery_mode(result: dict[str, Any]) -> str:
    """The delivery mode the install resolved, for a refusal that has to name it."""

    experiment = result.get("experiment")
    delivery = experiment.get("delivery") if isinstance(experiment, dict) else None
    mode = delivery.get("mode") if isinstance(delivery, dict) else None
    return mode if isinstance(mode, str) and mode else "unreported"


def _scaffold_stage(config: McpRuntimeConfig, report: dict[str, Any]) -> None:
    root: Path = config.coordination_root
    if scaffold_present(config):
        _stage(report, "scaffold", state="present", root=root.as_posix())
        return
    with capture_install_effects() as effects:
        _install_scaffold(config, report, effects)


def _install_scaffold(
    config: McpRuntimeConfig, report: dict[str, Any], effects: list[dict[str, Any]]
) -> None:
    root = config.coordination_root
    try:
        result = install_runtime_from_config(config, RuntimeInstallRequest())
    except ExperimentError as error:
        # A selected delivery mode that cannot be installed refuses before writing anything.
        raise _fail(
            report,
            "scaffold",
            f"The configured root {root} was refused by the runtime installer before it wrote "
            f"anything: {error}",
            status="bootstrap-install-refused",
        ) from error
    except (OSError, ValueError, RuntimeError) as error:
        # ValueError covers the settings owner's refusal of the configured root: an old-location
        # settings file is refused by name and is never migrated, moved or replaced here.
        _stage(
            report,
            "scaffold",
            state="partial" if effects else "unchanged",
            root=root.as_posix(),
            changed=bool(effects),
            effects=effects,
        )
        raise _fail(
            report,
            "scaffold",
            f"The coordinator scaffold at {root} could not be created: {error}",
            status="bootstrap-scaffold-failed",
        ) from error
    mode = _delivery_mode(result)
    raw_summary = result.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    created_dirs = summary.get("createdDirs")
    copied_files = summary.get("copiedFiles")
    wrote = bool(created_dirs or copied_files)
    _stage(
        report,
        "scaffold",
        state="created" if wrote else "unchanged",
        root=root.as_posix(),
        changed=wrote,
        delivery=mode,
        createdDirs=created_dirs,
        copiedFiles=copied_files,
        effects=effects,
    )
    if not scaffold_present(config):
        raise _fail(
            report,
            "scaffold",
            f"runtime_install ran in its {mode} delivery mode but {root} still holds no "
            f"{', '.join(SCAFFOLD_ENTRIES)}; its answer was ok={result.get('ok')!r}",
            status="bootstrap-scaffold-failed",
        )


def _configured_host(
    config: McpRuntimeConfig, report: dict[str, Any], *, stage: str
) -> PaseoRuntimeSettings | None:
    """The host authority as the product reads it now; an unreadable one is a stage failure."""

    try:
        return config.paseo_runtime
    except HostAuthorityRefused as error:
        raise _fail(
            report, stage, f"The host authority cannot be read: {error.detail}", status=error.code
        ) from error
    except ValueError as error:
        raise _fail(
            report,
            stage,
            f"The host authority cannot be read: {error}",
            status="host-authority-unreadable",
        ) from error


def _authority_stage(config: McpRuntimeConfig, report: dict[str, Any]) -> None:
    """Read the host authority first, and create its block once when there is none."""

    path = authority_settings_path(config.config_path)
    if _configured_host(config, report, stage="host-authority") is not None:
        # A configured host is read, not written: the developer decides when real settings change.
        _stage(report, "host-authority", state="present", path=path.as_posix(), changed=False)
        return
    try:
        written = establish_authority_paseo_runtime(config)
    except HostAuthorityRefused as error:
        raise _fail(
            report,
            "host-authority",
            f"The host authority could not be established: {error.detail}",
            status=error.code,
        ) from error
    except OSError as error:
        raise _fail(
            report,
            "host-authority",
            f"The host authority file could not be written: {error}",
            status="host-authority-write-failed",
        ) from error
    _stage(
        report,
        "host-authority",
        state=written["state"],
        path=written["path"],
        changed=written["changed"],
    )


def _host_stage(config: McpRuntimeConfig, report: dict[str, Any]) -> None:
    # Read the authority again: the block that was just written is only useful if this build
    # loads it back, and a failure here is the state the caller must be told (rule 3).
    settings = _configured_host(config, report, stage="host")
    if settings is None:
        # The write is never read as success: if the product cannot load the block back, this is
        # the state it must be told about, not a configured host (MIK-R196 rule 3).
        raise _fail(
            report,
            "host",
            "The host authority is still absent after this call wrote it; "
            f"{authority_settings_path(config.config_path)} carries no usable host block for "
            "this build.",
            status="host-not-configured",
        )
    try:
        provisioned = provision_for_install(settings)
    except (PaseoRuntimeFailure, OSError) as error:
        payload = error.as_payload() if isinstance(error, PaseoRuntimeFailure) else str(error)
        raise _fail(
            report,
            "host",
            f"The host could not be provisioned: {payload}",
            status="host-provision-failed",
        ) from error
    if not provisioned.get("ok"):
        raise _fail(
            report,
            "host",
            f"The host provisioner failed at {provisioned.get('error')}",
            status="host-provision-failed",
            retained={
                "daemon": provisioned.get("daemon"),
                "restartRequired": provisioned.get("restartRequired") or [],
                "changes": provisioned.get("changes") or [],
            },
        )
    restart_owed = provisioned.get("restartRequired") or []
    _stage(
        report,
        "host",
        state="provisioned",
        changed=provisioned.get("changed"),
        daemon=provisioned.get("daemon"),
        restartRequired=list(restart_owed),
        changes=provisioned.get("changes"),
    )
    if restart_owed:
        raise _fail(
            report,
            "host",
            "The host was provisioned but is still running on settings it no longer holds "
            f"({', '.join(restart_owed)}); a seat started now would run on the old host",
            status="host-restart-required",
            retained={
                "daemon": provisioned.get("daemon"),
                "restartRequired": list(restart_owed),
                "changes": provisioned.get("changes") or [],
            },
        )
    _verify_running(config, settings, report)


def _verify_running(
    config: McpRuntimeConfig, settings: PaseoRuntimeSettings, report: dict[str, Any]
) -> None:
    # The host state a failed check leaves behind is the provisioner's own account, not a guess.
    host = next((entry for entry in report["stages"] if entry.get("stage") == "host"), {})
    retained = {"daemon": host.get("daemon"), "restartRequired": []}
    try:
        info = bridge_call(config, "runtime-info", {})
    except PaseoBridgeFailure as error:
        raise _fail(
            report,
            "host-verify",
            f"The daemon at {settings.listen} did not answer after provisioning: {error}",
            status="host-not-running",
            retained=retained,
        ) from error
    server_id = info.get("serverId")
    if not isinstance(server_id, str) or not server_id:
        raise _fail(
            report,
            "host-verify",
            f"The daemon at {settings.listen} answered without its server id",
            status="host-not-running",
            retained=retained,
        )
    stamp = read_loaded_stamp(settings.home)
    source = tree_digest(installed_plugin_path(settings.home))
    loaded = stamp.get("plugin") if isinstance(stamp, dict) else None
    if source is None or loaded != source:
        raise _fail(
            report,
            "host-verify",
            f"The AR plug-in at {installed_plugin_path(settings.home)} is not recorded as loaded "
            f"(stamp {stamp!r}, installed {source!r}); the daemon answers but the tool surface "
            "the seat needs is not in it",
            status="host-plugin-not-loaded",
            retained=retained,
        )
    _stage(
        report,
        "host-verify",
        state="running",
        serverId=server_id,
        listen=settings.listen,
        pluginDigest=source,
    )
