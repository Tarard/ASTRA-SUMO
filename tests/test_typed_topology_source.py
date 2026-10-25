import json
from pathlib import Path
import shutil

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.network_edit_applicability import verify_network_edit
from test_network_edit_operations import _source


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize(
    "lane,in_scope,kind",
    [
        ("middle_0", False, "set_speed"),
        ("remote-in_0", True, "set_speed"),
        ("remote-in_0", True, "set_lane_width"),
    ],
)
def test_typed_edit_distinguishes_inherited_topology_from_junction_reconstruction(tmp_path, lane, in_scope, kind):
    source = _source(tmp_path, "set_speed", remote_controller="remote")
    digest = file_sha256(source)
    operation = dict(
        id="speed",
        operation=kind,
        status="accepted",
        params=dict(lane_id=lane, **({"speed_m_s": 8} if kind == "set_speed" else {"width_m": 3.4})),
        rationale="Synthetic scope comparison.",
        evidence=[dict(path=str(source), sha256=digest)],
        rollback=dict(action="discard_candidate"),
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(dict(source_net_file=str(source), source_sha256=digest, operations=[operation])), encoding="utf-8"
    )
    report = verify_network_edit(
        ledger_file=ledger,
        output_dir=tmp_path / "release",
        mode="release",
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["status"] == ("review_required" if in_scope and kind == "set_lane_width" else "pass"), report
    topology = report["candidate_gates"]["gates"]["topology"]
    if kind == "set_lane_width":
        assert topology["inherited_finding_count"] == 0
        assert len(topology["new_or_changed_findings"]) == 1
    else:
        assert topology["inherited_finding_count"] == 1
        assert not topology["new_or_changed_findings"]
    assert bool(topology["scope_findings"]) == in_scope
    assert bool(topology.get("scope_findings_informational")) == (in_scope and kind == "set_speed")
    assert report["whole_network_review"]["status"] == "review_required"
    assert report["whole_network_review"]["inherited_finding_count"] == topology["inherited_finding_count"]
    assert Path(report["candidate_gates"]["source_topology_report"]["report_file"]).is_file()
    assert file_sha256(source) == digest
