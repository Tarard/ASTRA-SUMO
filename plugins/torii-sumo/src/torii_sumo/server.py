from __future__ import annotations

import os
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .mcp_contract_tools import (
    torii_config_inspect,
    torii_demand_audit,
    torii_intersection_classify,
    torii_netedit_act,
    torii_netedit_close,
    torii_netedit_observe,
    torii_netedit_open,
    torii_network_audit,
    torii_network_compare_mcp,
    torii_place_resolve,
    torii_preflight,
    torii_review_create,
    torii_run_compare,
    torii_signal_classify,
)


DEFAULT_MCP_PROFILE = "default"
SUPPORTED_MCP_PROFILES = ("legacy", "default", "netedit")


def _profile_from(value: str | None) -> str:
    profile = (value or os.environ.get("TORII_MCP_PROFILE", DEFAULT_MCP_PROFILE)).strip().lower()
    if profile not in SUPPORTED_MCP_PROFILES:
        raise ValueError(
            f"unsupported TORII_MCP_PROFILE {profile!r}; "
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


def _register_default_tools(server: FastMCP) -> None:
    """Register the reduced 10-tool MCP surface under stable contract names."""

    _register_tool(
        server,
        torii_preflight,
        name="torii.preflight",
        title="Check Torii environment",
        description="Check Python, SUMO, and the Torii environment before network, demand, or replay work. Use this first.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        torii_config_inspect,
        name="torii.config.inspect",
        title="Inspect a SUMO config pair",
        description="Inspect a baseline and variant .sumocfg pair for missing inputs and shared outputs before comparing two runs.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        torii_run_compare,
        name="torii.run.compare",
        title="Compare two SUMO runs",
        description="Compare baseline and variant SUMO summary/tripinfo outputs and return comparison gates.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        torii_place_resolve,
        name="torii.place.resolve",
        title="Resolve an OSM place",
        description="Resolve a place name to a candidate OSM area and bbox. The OSM endpoint is fixed by Torii.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    )
    _register_tool(
        server,
        torii_intersection_classify,
        name="torii.intersection.classify",
        title="Classify an OSM intersection",
        description="Read-only classification of one local OSM intersection into a hash-bound finite composable archetype. It does not write files or mutate networks.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        torii_signal_classify,
        name="torii.signal.classify",
        title="Classify a signal device inventory",
        description="Read-only classification of one OCIT-C supply snapshot into a hash-bound signal device inventory. It does not bind traffic lights.",
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
    )
    _register_tool(
        server,
        torii_network_audit,
        name="torii.network.audit",
        title="Audit one SUMO network",
        description="Audit one local SUMO network with a quick topology check or the standard topology, Connection Mode, and overlap checks. Writes only separate audit artifacts and never runs SUMO.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_network_compare_mcp,
        name="torii.network.compare",
        title="Compare source and candidate networks",
        description="Compare a source and candidate SUMO network with a differential audit. Writes only separate review artifacts.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_demand_audit,
        name="torii.demand.audit",
        title="Audit detector counts",
        description="Compare expected detector counts against SUMO E1 detector output and report detector-fit metrics.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_review_create,
        name="torii.review.create",
        title="Create a network review page",
        description="Create a human-review HTML page for a SUMO network and available audit artifacts without overwriting source files.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


def _register_netedit_tools(server: FastMCP) -> None:
    _register_tool(
        server,
        torii_netedit_open,
        name="torii.netedit.open",
        title="Open a NetEdit review session",
        description="Open the single hash-bound NetEdit diagnostic session. Requires immutable source/candidate/output paths and the source SHA-256.",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_netedit_observe,
        name="torii.netedit.observe",
        title="Observe a NetEdit review session",
        description="Capture the current viewport and read persisted XML state from the active NetEdit session. Each call writes a new screenshot and session report.",
        read_only=False,
        destructive=False,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_netedit_act,
        name="torii.netedit.act",
        title="Act in a NetEdit review session",
        description="Execute exactly one whitelisted NetEdit mouse or shortcut action after the latest screenshot SHA. Requires confirmation.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )
    _register_tool(
        server,
        torii_netedit_close,
        name="torii.netedit.close",
        title="Close a NetEdit review session",
        description="Finalize or abort the active NetEdit session. Finalize requires the latest screenshot SHA and runs the closing audits. Abort closes without saving. Promotion remains blocked.",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=False,
    )


def create_server(profile: str | None = None) -> FastMCP:
    selected_profile = _profile_from(profile)
    server = FastMCP("Torii")

    if selected_profile == "default":
        _register_default_tools(server)
        return server
    if selected_profile == "netedit":
        _register_netedit_tools(server)
        return server

    from .legacy_tools import register_legacy_mcp_tools

    register_legacy_mcp_tools(server)
    return server


async def _run_stdio() -> None:
    server = create_server()
    await server.run_stdio_async()


def main() -> None:
    anyio.run(_run_stdio)


if __name__ == "__main__":
    main()
