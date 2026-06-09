"""Watcher entrypoint guard: never start watch-only over a poisoned graph.

Runs before `cgc watch` inside the watcher container and closes the startup
race seen on daemon restarts: `restart: unless-stopped` revives the watcher in
arbitrary order relative to FalkorDB, and a connect during dataset loading can
leave upstream cgc's "already indexed" check trusting an empty or partial
graph, which silently skips the initial scan forever.

The guard:
1. waits until FalkorDB answers a genuine PONG (a LOADING reply is not ready),
2. counts File nodes for this repo's graph with GRAPH.RO_QUERY (read-only:
   plain GRAPH.QUERY would auto-create the very empty key we are detecting),
3. deletes the graph key when the count is below CGC_MIN_INDEXED_FILES so
   cgc's own indexed check fails and triggers the initial scan,
4. then execs `cgc` with the original arguments.
"""

from __future__ import annotations

import os
import sys
import time

# redis is installed inside the runner image as a codegraphcontext dependency;
# it is intentionally not a host/MCP dependency, so host type checking skips it.
import redis  # pyright: ignore[reportMissingImports]

DEFAULT_WAIT_SECONDS = 300
DEFAULT_MIN_INDEXED_FILES = 1


def _connection() -> redis.Redis:
    return redis.Redis(
        host=os.environ.get("FALKORDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        socket_connect_timeout=5,
        socket_timeout=10,
    )


def wait_for_ready(deadline_seconds: int) -> redis.Redis | None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            client = _connection()
            if client.ping():
                return client
        except redis.exceptions.BusyLoadingError:
            print("[watch-guard] FalkorDB is loading the dataset; waiting...", flush=True)
        except redis.exceptions.RedisError as error:
            print(f"[watch-guard] FalkorDB not ready yet: {error}", flush=True)
        time.sleep(2)
    return None


def indexed_file_count(client: redis.Redis, graph_name: str) -> int | None:
    """Return the File node count, or None when the graph key does not exist."""
    try:
        reply = client.execute_command(
            "GRAPH.RO_QUERY", graph_name, "MATCH (f:File) RETURN count(f)"
        )
    except redis.exceptions.ResponseError as error:
        if "empty key" in str(error).lower():
            return None
        raise
    try:
        return int(reply[1][0][0])
    except (IndexError, TypeError, ValueError):
        return None


def clear_poisoned_graph(client: redis.Redis, graph_name: str, minimum: int) -> None:
    count = indexed_file_count(client, graph_name)
    if count is None:
        print(f"[watch-guard] graph {graph_name} absent; initial scan will run", flush=True)
        return
    if count >= minimum:
        print(f"[watch-guard] graph {graph_name} has {count} File nodes; keeping it", flush=True)
        return
    print(
        f"[watch-guard] graph {graph_name} has {count} File nodes (<{minimum}); "
        "deleting poisoned key so the initial scan runs",
        flush=True,
    )
    client.execute_command("GRAPH.DELETE", graph_name)


def main() -> None:
    graph_name = os.environ.get("FALKORDB_GRAPH_NAME")
    wait_seconds = int(os.environ.get("CGC_GUARD_WAIT_SECONDS", str(DEFAULT_WAIT_SECONDS)))
    minimum = int(os.environ.get("CGC_MIN_INDEXED_FILES", str(DEFAULT_MIN_INDEXED_FILES)))
    if graph_name:
        client = wait_for_ready(wait_seconds)
        if client is None:
            print(
                f"[watch-guard] FalkorDB not ready after {wait_seconds}s; "
                "starting cgc anyway",
                flush=True,
            )
        else:
            try:
                clear_poisoned_graph(client, graph_name, minimum)
            except redis.exceptions.RedisError as error:
                print(f"[watch-guard] graph check failed: {error}; starting cgc", flush=True)
    else:
        print("[watch-guard] FALKORDB_GRAPH_NAME not set; skipping graph check", flush=True)
    os.execvp("cgc", ["cgc", *sys.argv[1:]])


if __name__ == "__main__":
    main()
