from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_primary_branch_repair import branch_cost_evidence, replace_primary_tails
from astra_sumo.core.road_hierarchy import _path, _paths


def test_transition_costs_are_explicit_and_do_not_change_old_callers():
    graph = {'a': {'short', 'main'}, 'short': {'end'}, 'main': {'end'}, 'end': set()}
    lengths = {'a': 1, 'short': 5, 'main': 10, 'end': 1}
    _, old = _paths(graph, lengths, 'a', set(graph))
    _, complete = _paths(graph, lengths, 'a', set(graph), transition_costs={('a', 'short'): 20})
    assert _path(old, 'a', 'end') == ['a', 'short', 'end']
    assert _path(complete, 'a', 'end') == ['a', 'main', 'end']


def test_frozen_source_ordinary_lengths_reverse_the_primary_branch_order():
    source = Path(__file__).parents[1] / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs/network.net.xml'
    result = branch_cost_evidence(ET.parse(source).getroot())
    assert result['ordinary_only_choice'] == 'residential'
    assert result['complete_path_choice'] == 'primary'
    assert result['primary']['ordinary_intermediate_m'] == pytest.approx(10.264055)
    assert result['residential']['ordinary_intermediate_m'] == pytest.approx(5.837944)
    assert result['primary']['complete_intermediate_m'] == pytest.approx(36.301008)
    assert result['residential']['complete_intermediate_m'] == pytest.approx(46.018787)


def test_real_corridor_keeps_the_primary_branch_only_with_complete_path_costs(monkeypatch):
    from astra_sumo.core import hamburg_corridor_pruning as pruning

    source = Path(__file__).parents[1] / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs'
    options = dict(net_file=source/'network.net.xml', route_file=source/'demand.rou.xml.gz',
                   detector_file=source/'field-loops.add.xml')
    corrected = set(pruning.select_ring1_corridor(**options)['selected_edge_ids'])
    monkeypatch.setattr(pruning, '_internal_transition_lengths', lambda *args: {})
    original = set(pruning.select_ring1_corridor(**options)['selected_edge_ids'])
    assert corrected - original == {'1108564798#0', '1108564798#1'}
    assert not {'1108564798#0', '1108564798#1'} & original


def test_tail_replacement_preserves_requests_fields_stops_and_unrelated_routes():
    demand = ET.fromstring('<routes><vType id="car"/><vehicle id="a" depart="20" type="car">'
        '<route edges="entry 73474895 27064186#0 1073054122#1 1345098540 24483197 199500676"/>'
        '<stop lane="entry_0" endPos="5"/></vehicle><vehicle id="b" depart="30">'
        '<route edges="entry other"/></vehicle></routes>')
    original = ET.tostring(demand)
    result, changed = replace_primary_tails(demand)
    assert ET.tostring(demand) == original
    assert changed == ['a']
    assert result.find("vehicle[@id='a']").attrib == demand.find("vehicle[@id='a']").attrib
    assert ET.tostring(result.find("vehicle[@id='a']/stop")) == ET.tostring(demand.find("vehicle[@id='a']/stop"))
    assert result.find("vehicle[@id='a']/route").get('edges') == (
        'entry 73474895 1108564798#0 1108564798#1 24483197 199500676')
    assert ET.tostring(result.find("vehicle[@id='b']")) == ET.tostring(demand.find("vehicle[@id='b']"))


@pytest.mark.parametrize('suffix', ['27064186#0', '27064186#0 1073054122#1 1345098540 24483197 wrong_exit'])
def test_tail_replacement_rejects_an_unreviewed_suffix(suffix):
    root = ET.fromstring(f'<routes><vehicle id="a" depart="0"><route edges="entry 73474895 {suffix}"/></vehicle></routes>')
    with pytest.raises(ValueError, match='suffix'):
        replace_primary_tails(root)
