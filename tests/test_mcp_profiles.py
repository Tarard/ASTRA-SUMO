from __future__ import annotations

import subprocess
import sys

import anyio
import pytest

from astra_sumo.server import (
    DEFAULT_MCP_PROFILE,
    SUPPORTED_MCP_PROFILES,
    create_server,
)


async def _list_tools(profile: str | None = None) -> list[object]:
    return await create_server(profile).list_tools()


def test_reduced_profile_is_the_safe_default() -> None:
    assert DEFAULT_MCP_PROFILE == "default"
    assert SUPPORTED_MCP_PROFILES == ("legacy", "default", "netedit")


@pytest.mark.parametrize("profile", ["default", "netedit"])
def test_reduced_profiles_do_not_load_the_legacy_bundle(profile: str) -> None:
    deferred_modules = {
        "astra_sumo.legacy_tools",
        "astra_sumo.tools.digital_twin_tools",
        "astra_sumo.tools.road_network_tools",
        "astra_sumo.tools.run_tools",
        "astra_sumo.tools.workflow_tools",
    }
    script = (
        "import sys; "
        "from astra_sumo.server import create_server; "
        f"create_server({profile!r}); "
        f"deferred={deferred_modules!r}; "
        "assert deferred.isdisjoint(sys.modules), deferred.intersection(sys.modules)"
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_default_profile_exposes_ten_short_tools() -> None:
    tools = anyio.run(_list_tools, "default")

    assert [tool.name for tool in tools] == [
        "astra.preflight",
        "astra.config.inspect",
        "astra.run.compare",
        "astra.place.resolve",
        "astra.intersection.classify",
        "astra.signal.classify",
        "astra.network.audit",
        "astra.network.compare",
        "astra.demand.audit",
        "astra.review.create",
    ]
    assert all(tool.title for tool in tools)
    assert all(tool.description for tool in tools)


def test_netedit_profile_exposes_four_observation_loop_tools() -> None:
    tools = anyio.run(_list_tools, "netedit")

    assert [tool.name for tool in tools] == [
        "astra.netedit.open",
        "astra.netedit.observe",
        "astra.netedit.act",
        "astra.netedit.close",
    ]
    assert all(tool.annotations is not None for tool in tools)
    assert (
        next(tool for tool in tools if tool.name == "astra.netedit.observe")
        .annotations.readOnlyHint
        is False
    )
    assert next(tool for tool in tools if tool.name == "astra.netedit.act").annotations.destructiveHint is True


def test_default_tools_do_not_expose_dangerous_execution_controls() -> None:
    tools = anyio.run(_list_tools, "default")
    dangerous = {"sumo_binary", "netconvert_binary", "nominatim_url", "overpass_url"}

    for tool in tools:
        properties = set(tool.inputSchema.get("properties", {}))
        assert not (dangerous & properties), tool.name


def test_default_tools_have_output_schemas_and_annotations() -> None:
    tools = anyio.run(_list_tools, "default")

    assert all(tool.outputSchema for tool in tools)
    assert all(tool.annotations for tool in tools)
    assert all(tool.title for tool in tools)
    assert all(tool.description for tool in tools)


def test_unsupported_mcp_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported ASTRA_MCP_PROFILE"):
        create_server("unknown")
