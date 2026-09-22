from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "plugins" / "torii-sumo" / "scripts" / "freeze_hamburg_lsa_nodes.py"


def _module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("freeze_hamburg_lsa_nodes_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_parser_keeps_pipe_delimited_road_names() -> None:
    module = _module()
    request = module._request("2403=Am Sandtorkai|Osakaallee")
    assert request.expected_node_id == "2403"
    assert request.road_name_components == ("am sandtorkai", "osakaallee")


@pytest.mark.parametrize("value", ["2403", "=Am Sandtorkai|Osakaallee", "2403=Am Sandtorkai|"])
def test_request_parser_rejects_ambiguous_cli_shape(value: str) -> None:
    module = _module()
    with pytest.raises(module.argparse.ArgumentTypeError):
        module._request(value)
