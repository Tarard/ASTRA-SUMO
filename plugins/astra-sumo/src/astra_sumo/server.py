from __future__ import annotations

import os
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .mcp_contract_tools import (
    astra_config_inspect,
    astra_demand_audit,
    astra_intersection_classify,
    astra_netedit_act,
    astra_netedit_close,
    astra_netedit_observe,
    astra_netedit_open,
    astra_network_audit,
    astra_network_compare_mcp,
    astra_place_resolve,
    astra_preflight,
    astra_review_create,
    astra_run_compare,
    astra_signal_classify,
)


DEFAULT_MCP_PROFILE = "default"
SUPPORTED_MCP_PROFILES = ("legacy", "default", "netedit")


def _profile_from(value: str | None) -> str:
    profile = (
        value
        or os.environ.get("ASTRA_MCP_PROFILE")
        or os.environ.get("TORII_MCP_PROFILE", DEFAULT_MCP_PROFILE)
    ).strip().lower()
    if profile not in SUPPORTED_MCP_PROFILES:
        raise ValueError(
            f"unsupported ASTRA_MCP_PROFILE {profile!r}; "
            f"expected one of {', '.join(SUPPORTED_MCP_PROFILES)}"
        )
    return profile


def _register_tool(
    server: FastMCP,
    function: Any,
    *,
    name: str,
    title: str,
    description: str,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> None:
    server.add_tool(
        function,
        name=name,
        title=title,
        description=description,
        annotations=ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=open_world,
        ),
    )


def _register_default_tools(server: FastMCP, prefix: str = "astra") -> None:
    """Register the reduced 10-tool MCP surface under stable contract names."""

    _register_tool(
        server,
        astra_preflight,
        name=f"{prefix}.preflight",
        title="Check ASTRA environment",
        description="Check Python, SUMO, and the ASTRA environment before network, demand, or replay work. Use this first.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        astra_config_inspect,
        name=f"{prefix}.config.inspect",
        title="Inspect a SUMO config pair",
        description="Inspect a baseline and variant .sumocfg pair for missing inputs and shared outputs before comparing two runs.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        astra_run_compare,
        name=f"{prefix}.run.compare",
        title="Compare two SUMO runs",
        description="Compare baseline and variant SUMO summary/tripinfo outputs and return comparison gates.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        astra_place_resolve,
        name=f"{prefix}.place.resolve",
        title="Resolve an OSM place",
        description="Resolve a place name to a candidate OSM area and bbox. The OSM endpoint is fixed by ASTRA.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
    _register_tool(
        server,
        astra_intersection_classify,
        name=f"{prefix}.intersection.classify",
        title="Classify an OSM intersection",
        description="Read-only classification of one local OSM intersection into a hash-bound finite composable archetype. It does not write files or mutate networks.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        astra_signal_classify,
        name=f"{prefix}.signal.classify",
        title="Classify a signal device inventory",
        description="Read-only classification of one OCIT-C supply snapshot into a hash-bound signal device inventory. It does not bind traffic lights.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        astra_network_audit,
        name=f"{prefix}.network.audit",
        title="Audit one SUMO network",
        description="Audit one local SUMO network with a quick topology check or the standard topology, Connection Mode, and overlap checks. Writes only separate audit artifacts and never runs SUMO.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_network_compare_mcp,
        name=f"{prefix}.network.compare",
        title="Compare source and candidate networks",
        description="Compare a source and candidate SUMO network with a differential audit. Writes only separate review artifacts.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_demand_audit,
        name=f"{prefix}.demand.audit",
        title="Audit detector counts",
        description="Compare expected detector counts against SUMO E1 detector output and report detector-fit metrics.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_review_create,
        name=f"{prefix}.review.create",
        title="Create a network review page",
        description="Create a human-review HTML page for a SUMO network and available audit artifacts without overwriting source files.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


def _register_netedit_tools(server: FastMCP, prefix: str = "astra") -> None:
    _register_tool(
        server,
        astra_netedit_open,
        name=f"{prefix}.netedit.open",
        title="Open a NetEdit review session",
        description="Open the single hash-bound NetEdit diagnostic session. Requires immutable source/candidate/output paths and the source SHA-256.",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_netedit_observe,
        name=f"{prefix}.netedit.observe",
        title="Observe a NetEdit review session",
        description="Capture the current viewport and read persisted XML state from the active NetEdit session. Each call writes a new screenshot and session report.",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_netedit_act,
        name=f"{prefix}.netedit.act",
        title="Act in a NetEdit review session",
        description="Execute exactly one whitelisted NetEdit mouse or shortcut action after the latest screenshot SHA. Requires confirmation.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        astra_netedit_close,
        name=f"{prefix}.netedit.close",
        title="Close a NetEdit review session",
        description="Finalize or abort the active NetEdit session. Finalize requires the latest screenshot SHA and runs the closing audits. Abort closes without saving. Promotion remains blocked.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


def create_server(profile: str | None = None, *, torii_names: bool = False) -> FastMCP:
    selected_profile = _profile_from(profile)
    server = FastMCP("ASTRA")
    prefix = "torii" if torii_names else "astra"

    if selected_profile == "default":
        _register_default_tools(server, prefix)
        return server
    if selected_profile == "netedit":
        _register_netedit_tools(server, prefix)
        return server

    from .legacy_tools import register_legacy_mcp_tools

    register_legacy_mcp_tools(server, torii_names=torii_names)
    return server


async def _run_stdio(torii_names: bool = False) -> None:
    server = create_server(torii_names=torii_names)
    await server.run_stdio_async()


def main() -> None:
    anyio.run(_run_stdio)


def compat_main() -> None:
    """Keep the old console entry point and MCP tool names available in 1.3.x."""
    anyio.run(_run_stdio, True)


if __name__ == "__main__":
    main()
