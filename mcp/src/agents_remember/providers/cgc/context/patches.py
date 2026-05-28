"""CodeGraphContext provider patch application helpers."""

# ruff: noqa: F403,F405

from __future__ import annotations

from pathlib import Path

from agents_remember.providers.cgc.context.constants import *
from agents_remember.providers.context.common import ContextProviderError


def cgc_cgcignore_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_PATCH_MARKER in text and "if not local_cgcignore_path.exists():" in text


def apply_cgc_cgcignore_patch(path: Path) -> bool:
    """Patch CGC so an explicit .cgcignore path wins before repo-local creation.

    Returns true when the file was changed and false when the patch was already
    present.
    """

    text = path.read_text(encoding="utf-8")
    if cgc_cgcignore_patch_applied(path):
        return False
    if CGC_OLD_PATCHED_SNIPPET in text:
        path.write_text(
            text.replace(CGC_OLD_PATCHED_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8"
        )
        return True
    if CGC_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError("CGC cgcignore.py did not match the expected unpatched snippet")
    path.write_text(text.replace(CGC_ORIGINAL_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8")
    return True


def cgc_delete_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_DELETE_PATCH_MARKER in text and "prefix_backslash=path_prefix_backslash" in text


def apply_cgc_delete_patch(path: Path) -> bool:
    """Patch CGC repository deletion so Windows child paths are removed on --force."""

    text = path.read_text(encoding="utf-8")
    if cgc_delete_patch_applied(path):
        return False

    replacements = [
        (CGC_DELETE_PREFIX_ORIGINAL_SNIPPET, CGC_DELETE_PREFIX_PATCHED_SNIPPET),
        (CGC_DELETE_REL_ORIGINAL_SNIPPET, CGC_DELETE_REL_PATCHED_SNIPPET),
        (CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET, CGC_DELETE_CONTAINS_PATCHED_SNIPPET),
        (CGC_DELETE_NODE_ORIGINAL_SNIPPET, CGC_DELETE_NODE_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC writer.py did not match the expected unpatched delete snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_graph_builder_extensions_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER in text
        and CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER in text
        and '".cc": "cpp"' in text
        and '".td",' in text
        and "for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh')" in text
    )


def apply_cgc_graph_builder_extensions_patch(path: Path) -> bool:
    """Patch CGC graph builder discovery for TensorFlow C++ and TableGen files."""

    text = path.read_text(encoding="utf-8")
    if cgc_graph_builder_extensions_patch_applied(path):
        return False

    replacements = [
        (CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_PARSER_PATCHED_SNIPPET),
        (CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_GENERIC_PATCHED_SNIPPET),
        (CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_PRESCAN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC graph_builder.py did not match the expected unpatched extension snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_discovery_extensions_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER in text and '".td",' in text


def apply_cgc_discovery_extensions_patch(path: Path) -> bool:
    """Patch CGC file discovery so TensorFlow TableGen files become File nodes."""

    text = path.read_text(encoding="utf-8")
    if cgc_discovery_extensions_patch_applied(path):
        return False
    if CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError(
            "CGC discovery.py did not match the expected unpatched generic extension snippet"
        )
    path.write_text(
        text.replace(
            CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET, CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET, 1
        ),
        encoding="utf-8",
    )
    return True


def cgc_viz_repo_query_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_REPO_QUERY_PATCH_MARKER in text
        and "WITH repo_path, repo_prefix, node LIMIT 3000" in text
    )


def apply_cgc_viz_repo_query_patch(path: Path) -> bool:
    """Patch CGC visualizer repo graph query so large repos do not time out."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_repo_query_patch_applied(path):
        return False
    if CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError(
            "CGC viz/server.py did not match the expected unpatched repo query snippet"
        )
    path.write_text(
        text.replace(CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET, CGC_VIZ_REPO_QUERY_PATCHED_SNIPPET, 1),
        encoding="utf-8",
    )
    return True


def cgc_viz_server_route_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_SERVER_ROUTE_PATCH_MARKER in text
        and "RedirectResponse(_default_route)" in text
        and 'JSONResponse({"detail": "Not found"}, status_code=404)' in text
        and "default_route: Optional[str] = None" in text
    )


def apply_cgc_viz_server_route_patch(path: Path) -> bool:
    """Patch CGC visualizer server routing for local explorer launches."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_server_route_patch_applied(path):
        return False

    replacements = [
        (CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_RESPONSES_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_GLOBAL_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_FALLBACK_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_RUN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC viz/server.py did not match the expected unpatched route snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_viz_cli_route_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_CLI_ROUTE_PATCH_MARKER in text
        and 'default_route = f"/explore?{query_string}"' in text
        and "default_route=default_route" in text
    )


def apply_cgc_viz_cli_route_patch(path: Path) -> bool:
    """Patch CGC visualize helper so opening / redirects to the explorer route."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_cli_route_patch_applied(path):
        return False

    replacements = [
        (CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET, CGC_VIZ_CLI_URL_PATCHED_SNIPPET),
        (CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET, CGC_VIZ_CLI_RUN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC cli/cli_helpers.py did not match the expected unpatched visualizer snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True
