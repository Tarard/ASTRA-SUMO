"""Check the retained precision-sensitive junction without a downloaded city."""

import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.local_network_conversion import with_geometry_precision


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
def test_provided_geometry_precision_keeps_the_reduced_case_consistent_across_frames(tmp_path):
    folder = Path(__file__).resolve().parents[1] / "benchmarks/coordinate_precision_20261018_v1"
    manifest = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    for name, record in manifest["files"].items():
        assert (folder / name).stat().st_size == record["bytes"]
        assert file_sha256(folder / name) == record["sha256"]
    models = []
    for disabled in (True, False):
        output = tmp_path / f"normalized-{not disabled}.net.xml"
        command = with_geometry_precision([shutil.which("netconvert"), "--osm-files", str(folder / "source.osm.xml"),
            "--type-files", str(folder / "effective.typ.xml"), *manifest["native_options"],
            "--offset.disable-normalization", str(disabled).lower(), "--output-file", str(output)])
        result = run_command(command, cwd=tmp_path, timeout_seconds=30)
        (tmp_path / f"command-{disabled}.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        assert result.status == "pass", result.stderr
        root = ET.parse(output).getroot()
        lanes = root.find(f"edge[@id='{manifest['target_edge_id']}']").findall("lane")
        connections = {tuple(row.get(key) for key in ("from", "fromLane", "to", "toLane"))
            for row in root.findall("connection") if not row.get("from", "").startswith(":")
            and not row.get("to", "").startswith(":")}
        models.append(({row.get("id"): float(row.get("length")) for row in lanes}, connections))
    assert models[0][0].keys() == models[1][0].keys()
    assert all(abs(length - models[1][0][key]) < 1e-6 for key, length in models[0][0].items())
    assert models[0][1] == models[1][1]
