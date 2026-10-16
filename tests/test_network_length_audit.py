import json
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_length_audit import audit_network_lengths, measure_lane_lengths


def test_length_measures_separate_normal_custom_turning_and_unknown_cases():
    root = ET.fromstring('''<net>
      <edge id="unequal"><lane id="u0" length="10" shape="0,0 10,0"/><lane id="u1" length="12" shape="0,3 12,3"/></edge>
      <edge id="custom" length="500"><lane id="c0" length="497.5" endOffset="5" shape="0,0 91,0"/>
        <lane id="c1" length="497.5" shape="0,3 96,3"/></edge>
      <edge id="wrong" length="500"><lane id="w" length="500" endOffset="5" shape="0,0 95,0"/></edge>
      <edge id="bidi" bidi="opposite" length="500"><lane id="b" length="400" shape="0,0 10,0"/></edge>
      <edge id=":turn" function="internal"><lane id="t0" length="5"/><lane id="t1" length="8"/></edge>
      <edge id="invalid"><lane id="nan" length="nan"/><lane id="missing"/></edge>
      </net>''')
    before = ET.tostring(root)
    result = measure_lane_lengths(root)
    assert result["status"] == "measured"
    assert result["unequal_normal_edge_ids"] == ["unequal"]
    assert result["custom_length_difference_edge_ids"] == ["wrong"]
    assert result["varying_non_normal_edge_ids"] == [":turn"]
    assert result["invalid_length_lane_ids"] == ["nan", "missing"]
    custom = next(row for row in result["normal_edges"] if row["edge_id"] == "custom")
    assert custom["expected_custom_common_length_m"] == 497.5
    assert custom["geometry_mean_m"] == 93.5
    bidi = next(row for row in result["normal_edges"] if row["edge_id"] == "bidi")
    assert bidi["expected_custom_common_length_m"] is None
    assert ET.tostring(root) == before
    json.dumps(result, allow_nan=False)


def test_stage_file_audit_keeps_unreadable_input_and_never_replaces_inputs(tmp_path):
    good, bad, output = [tmp_path / name for name in ("good.net.xml", "bad.net.xml", "report.json")]
    good.write_text('<net><edge id="a"><lane id="a0" length="10" shape="0,0 10,0"/></edge></net>', encoding="utf-8")
    bad.write_text("<net>", encoding="utf-8")
    before = [path.read_bytes() for path in (good, bad)]
    result = audit_network_lengths(net_files=[good, bad, good], output_file=output)
    assert [row["read_status"] for row in result["networks"]] == ["read", "unreadable"]
    assert all(row["unchanged_during_read"] for row in result["networks"])
    assert [path.read_bytes() for path in (good, bad)] == before
    with pytest.raises(ValueError, match="new length report"):
        audit_network_lengths(net_files=[good], output_file=good)
    with pytest.raises(ValueError, match="new length report"):
        audit_network_lengths(net_files=[good], output_file=output)
