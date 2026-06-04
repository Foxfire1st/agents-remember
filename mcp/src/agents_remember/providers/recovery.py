"""Shared provider recovery guidance."""

PROVIDER_WATCHER_RESTART_RECOVERY = (
    "Run provider_watchers(action='restart') to recreate watcher containers against "
    "the current provider runner roots; this preserves provider indexes. Then run "
    "provider_status or provider_diagnostics to inspect any remaining provider issue."
)
