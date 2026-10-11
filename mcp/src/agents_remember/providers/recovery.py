"""Shared provider recovery guidance."""

PROVIDER_WATCHER_RESTART_RECOVERY = (
    "Stop the named leftover process and rerun the install; no product path "
    "restarts provider watchers. Only the providers-remove command deletes "
    "leftover provider runtime folders, and server_info reports enabled "
    "provider keys once as unused."
)
