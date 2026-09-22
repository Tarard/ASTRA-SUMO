from __future__ import annotations

import inspect
from pathlib import Path

import anyio
import pytest

from torii_sumo.core.osm_workflow import run_osm_cleanup_workflow
from torii_sumo.server import create_server
from torii_sumo.tools import osm_tools


EXPECTED_PARAMETERS = (
    "output_dir",
    "bbox",
    "profile",
    "source_osm_path",
    "traffic_layers",
    "reference_net_file",
    "timeout_seconds",
)


def test_osm_cleanup_v2_has_one_small_public_contract() -> None:
    assert tuple(inspect.signature(run_osm_cleanup_workflow).parameters) == EXPECTED_PARAMETERS
    assert tuple(inspect.signature(osm_tools.sumo_osm_cleanup_workflow).parameters) == EXPECTED_PARAMETERS


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"profile": "unknown", "traffic_layers": "passenger"}, "profile"),
        ({"profile": "standard"}, "traffic_layers"),
        (
            {"profile": "standard", "traffic_layers": "passenger", "reference_net_file": Path("reference.net.xml")},
            "reference_net_file",
        ),
        ({"profile": "reference_matched"}, "reference_net_file"),
        (
            {
                "profile": "reference_matched",
                "reference_net_file": Path("reference.net.xml"),
                "traffic_layers": "passenger",
            },
            "traffic_layers",
        ),
    ],
)
def test_osm_cleanup_v2_rejects_ambiguous_profiles(tmp_path: Path, kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run_osm_cleanup_workflow(output_dir=tmp_path, bbox="1,2,3,4", **kwargs)


def test_osm_cleanup_cli_adapter_only_converts_paths(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_cleanup(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "pass", "claim_status": "diagnostic-demo"}

    monkeypatch.setattr(osm_tools, "run_osm_cleanup_workflow", fake_cleanup)
    source = tmp_path / "source.osm.xml"

    report = osm_tools.sumo_osm_cleanup_workflow(
        output_dir=str(tmp_path / "out"),
        bbox="1,2,3,4",
        profile="standard",
        source_osm_path=str(source),
        traffic_layers="passenger,bicycle",
        timeout_seconds=90.0,
    )

    assert report["status"] == "pass"
    assert captured == {
        "output_dir": tmp_path / "out",
        "bbox": "1,2,3,4",
        "profile": "standard",
        "source_osm_path": source,
        "traffic_layers": "passenger,bicycle",
        "reference_net_file": None,
        "timeout_seconds": 90.0,
    }


async def _legacy_tool_names() -> list[str]:
    return [tool.name for tool in await create_server("legacy").list_tools()]


def test_osm_cleanup_is_cli_only() -> None:
    assert "sumo_osm_cleanup_workflow" not in anyio.run(_legacy_tool_names)
