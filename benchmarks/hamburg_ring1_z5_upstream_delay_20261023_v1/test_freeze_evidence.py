"""An explicit lane transform must not change requests or route attributes."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("freeze_upstream", Path(__file__).with_name("freeze_evidence.py"))
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


@pytest.mark.parametrize("changed_depart", [False, True])
def test_lane_only_scope_rejects_a_changed_request_time(tmp_path, changed_depart):
    before, after = tmp_path / "before", tmp_path / "after"
    for folder, swap in ((before, False), (after, True)):
        folder.mkdir()
        fields = ["hh_0119_Z_5_30494", "hh_0119_Z_4_29437"]
        lanes = [1, 0]
        if swap:
            fields.reverse()
            lanes.reverse()
        xml = '<routes>' + ''.join(
            f'<vehicle id="v{i}" depart="{20 if changed_depart and swap and i == 0 else 10}"><route edges="a 24483344#1 b"/><stop lane="24483344#1_{lane}" endPos="87.630000" speed="13.888889"/></vehicle>'
            for i, lane in enumerate(lanes)) + '</routes>'
        (folder / "demand.rou.xml").write_text(xml, encoding="utf-8")
        (folder / "lane-intentions.json").write_text(json.dumps({"mechanism": "prior", "vehicles": [
            {"vehicle_id": f"v{i}", "field_id": f} for i, f in enumerate(fields)]}), encoding="utf-8")
    if changed_depart:
        with pytest.raises(AssertionError):
            freeze.check_scope(before, after, ["v0", "v1"])
    else:
        freeze.check_scope(before, after, ["v0", "v1"])
