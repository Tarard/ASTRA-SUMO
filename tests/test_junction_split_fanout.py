import gzip
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.junction_split_fanout import materialize_unsplit_fanout


REPO = Path(__file__).resolve().parents[1]
CASE = REPO / "benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final"
KEYS = [("139083723#0", 2, "234423652#1", 0), ("139083723#0", 2, "234423652#1", 1)]


def _source(tmp_path):
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress((CASE / "network.net.xml.gz").read_bytes()))
    return source


def _run(tmp_path, network, binary):
    tmp_path.mkdir()
    control = tmp_path / "control.add.xml"
    control.write_bytes(gzip.decompress((CASE / "signal-programs.add.xml.gz").read_bytes()))
    routes = tmp_path / "demand.rou.xml"
    routes.write_bytes((REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/cohort.rou.xml").read_bytes())
    result = subprocess.run([str(binary), "-n", str(network), "-r", str(routes), "-a", str(control),
        "--step-length", "0.1", "--seed", "104", "--end", "300", "--collision.check-junctions", "true",
        "--collision-output", str(tmp_path / "collisions.xml"), "--summary-output", str(tmp_path / "summary.xml"),
        "--no-step-log", "true"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return ET.parse(tmp_path / "collisions.xml").getroot(), ET.parse(tmp_path / "summary.xml").getroot().findall("step")[-1]


def test_native_hamburg_split_sibling_continuations_reproduce_then_clear_without_road_changes(tmp_path):
    sumo = pytest.importorskip("sumo")
    binaries = Path(sumo.__file__).parent / "bin"
    source = _source(tmp_path)
    original = source.read_bytes()
    collisions, _ = _run(tmp_path / "before", source, binaries / "sumo.exe")
    assert len(collisions) == 1 and collisions[0].get("lane") == ":LSA104_part0_12_0"
    result = materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=KEYS,
        output_dir=tmp_path / "candidate", netconvert_binary=binaries / "netconvert.exe")
    collisions, final = _run(tmp_path / "after", Path(result["candidate_file"]), binaries / "sumo.exe")
    assert len(collisions) == 0
    assert [int(final.get(k)) for k in ("inserted", "arrived", "running", "waiting", "collisions", "teleports")] == [2, 2, 0, 0, 0, 0]
    assert source.read_bytes() == original
    assert result["ordinary_roads_unchanged"] and result["turn_permissions_unchanged"]
    assert result["signal_bindings_unchanged"] and result["signal_programs_unchanged"]
    assert result["maximum_curve_offset_m"] < 1e-4
    assert result["status"] == "review_required"
    before = ET.fromstring(original)
    after = ET.parse(result["candidate_file"]).getroot()
    def inventory(root, tag):
        return {r.get("id"): ET.canonicalize(ET.tostring(r, encoding="unicode"), strip_text=True)
                for r in root.findall(tag) if tag != "edge" or not r.get("function")}
    assert inventory(before, "edge") == inventory(after, "edge")
    assert inventory(before, "tlLogic") == inventory(after, "tlLogic")
    assert before.find("junction[@id='LSA104_part0']").get("shape") == after.find("junction[@id='LSA104_part0']").get("shape")


def test_unsplit_requires_one_existing_split_fanout_and_new_output(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="same incoming lane"):
        materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=[KEYS[0], ("139083723#0", 3, "234423652#1", 1)], output_dir=tmp_path / "invalid")
    with pytest.raises(ValueError, match="new output"):
        materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=KEYS, output_dir=tmp_path)
    with pytest.raises(ValueError, match="same incoming lane"):
        materialize_unsplit_fanout(source_file=source, junction_id="LSA104_part0", connection_keys=KEYS[:1], output_dir=tmp_path / "single")


def _single_source(tmp_path):
    source = tmp_path / 'single-source.net.xml'
    packet = REPO/'benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1/prepared/network.net.xml.gz'
    source.write_bytes(gzip.decompress(packet.read_bytes()))
    return source


def test_native_single_unsplit_preserves_all_other_curves_and_exterior(tmp_path):
    import astra_sumo.core.junction_split_fanout as split

    sumo = pytest.importorskip('sumo')
    source = _single_source(tmp_path)
    original = source.read_bytes()
    result = split.materialize_unsplit_connection(source_file=source, junction_id='LSA119_part0',
        connection_key=('24483344#1', 0, '79014037#1', 0), output_dir=tmp_path/'single',
        netconvert_binary=Path(sumo.__file__).parent/'bin/netconvert.exe')
    assert source.read_bytes() == original
    assert result['status'] == 'review_required' and all(result['preservation_checks'].values())
    assert len(result['source_internal_path']) == 2 and len(result['candidate_internal_path']) == 1
    assert result['outside_junction_elements_unchanged']
    assert result['maximum_curve_offset_m'] < 1e-6
    assert all(row['curve_offset_m'] == 0 for row in result['movement_geometry'] if not row['selected'])
    assert all(row['logical_length_delta_m'] == 0 for row in result['movement_geometry'])
    assert len(result['movement_geometry']) == 21
    assert hashlib.sha256(Path(result['candidate_file']).read_bytes()).hexdigest() == (
        'b6acb7f542abf62b3c3f3fc67db6bad39a71a783cdc61fc71109928aad52df18')


@pytest.mark.parametrize('node,key,match', [
    ('LSA119_part0', ('24483344#1', 1, '79014037#1', 1), 'split'),
    ('LSA104_part0', ('24483344#1', 0, '79014037#1', 0), 'declared junction'),
    ('LSA119_part0', ('absent', 0, '79014037#1', 0), 'existing movement'),
])
def test_single_unsplit_requires_a_declared_existing_split(tmp_path, node, key, match):
    import astra_sumo.core.junction_split_fanout as split

    with pytest.raises(ValueError, match=match):
        split.materialize_unsplit_connection(source_file=_single_source(tmp_path), junction_id=node,
            connection_key=key, output_dir=tmp_path/'invalid', netconvert_binary='must-not-run')
    assert not (tmp_path/'invalid').exists()


def test_single_unsplit_rejects_a_native_change_to_an_unselected_internal_curve(tmp_path, monkeypatch):
    import astra_sumo.core.junction_split_fanout as split

    sumo = pytest.importorskip('sumo')
    compile_original = split._compile_connection_patch

    def corrupt(*args):
        result = compile_original(*args)
        tree = ET.parse(result['candidate_file'])
        lane = tree.getroot().find("edge[@id=':LSA119_part0_0']/lane[@index='0']")
        points = lane.get('shape').split()
        x, y = map(float, points[1].split(','))
        points[1] = f'{x+1},{y}'
        lane.set('shape', ' '.join(points))
        tree.write(result['candidate_file'], encoding='utf-8', xml_declaration=True)
        return result

    monkeypatch.setattr(split, '_compile_connection_patch', corrupt)
    with pytest.raises(ValueError, match='protected'):
        split.materialize_unsplit_connection(source_file=_single_source(tmp_path), junction_id='LSA119_part0',
            connection_key=('24483344#1', 0, '79014037#1', 0), output_dir=tmp_path/'corrupt',
            netconvert_binary=Path(sumo.__file__).parent/'bin/netconvert.exe')
    failure = json.loads((tmp_path/'corrupt/manifest.json').read_bytes())
    assert failure['status'] == 'blocked' and not failure['preservation_checks']['all_complete_curves_preserved']


def test_four_vehicle_measured_leader_boundary_reproduces_then_clears_with_native_safety(tmp_path):
    """The leader boundary is measured input, not a natural four-car queue history."""
    import astra_sumo.core.junction_split_fanout as split

    sumo = pytest.importorskip('sumo')
    pytest.importorskip('libsumo')
    packet = REPO/'benchmarks/hamburg_ring1_119_single_split_20261023_v1'
    inputs = tmp_path/'fixture'
    inputs.mkdir()
    for path in (packet/'fixture').iterdir():
        if path.is_file():
            content = path.read_bytes()
            (inputs/(path.stem if path.suffix == '.gz' else path.name)).write_bytes(
                gzip.decompress(content) if path.suffix == '.gz' else content)
    run_case = runpy.run_path(str(inputs/'runner.py'))['run_case']
    source = inputs/'baseline.net.xml'
    baseline = run_case(input_dir=inputs, net_file=source, output_dir=tmp_path/'before')
    case = json.loads((inputs/'case.json').read_bytes())
    assert baseline['collision_rows'] == case['expected_baseline_collisions']
    original_full = ET.fromstring(gzip.decompress((packet/'full-source-collisions.xml.gz').read_bytes()))
    assert baseline['collision_rows'][0] == original_full[0].attrib
    result = split.materialize_unsplit_connection(source_file=source, junction_id='LSA119_part0',
        connection_key=('24483344#1', 0, '79014037#1', 0), output_dir=tmp_path/'unsplit',
        netconvert_binary=Path(sumo.__file__).parent/'bin/netconvert.exe')
    candidate = run_case(input_dir=inputs, net_file=result['candidate_file'], output_dir=tmp_path/'after')
    assert not candidate['collision_rows']
    for native in (baseline, candidate):
        assert native['inputs_unchanged'] and native['samples'] == 271
        assert native['native_safety_modes'] == {identity: 31 for identity in case['vehicle_ids']}
        assert native['leader_max_speed_error_m_s'] == 0
        assert native['leader_max_xy_error_m'] < 1e-6
        assert native['leader_max_travelled_distance_error_m'] < 1e-8


def test_frozen_single_split_regression_bytes_match_manifest():
    packet = REPO/'benchmarks/hamburg_ring1_119_single_split_20261023_v1'
    manifest = json.loads((packet/'manifest.json').read_bytes())
    assert not manifest['formal_result']
    assert {r['path'] for r in manifest['files']} == {
        p.relative_to(packet).as_posix() for p in packet.rglob('*')
        if p.is_file() and p.name != 'manifest.json' and '__pycache__' not in p.parts}
    for row in manifest['files']:
        content = (packet/row['path']).read_bytes()
        assert len(content) == row['bytes'] and hashlib.sha256(content).hexdigest() == row['sha256']
        if row['path'].endswith('.gz'):
            content = gzip.decompress(content)
            assert len(content) == row['raw_bytes'] and hashlib.sha256(content).hexdigest() == row['raw_sha256']


def test_frozen_lsa104_records_keep_all_native_measurements_and_file_hashes(tmp_path):
    import csv
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict
    packet = REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1"
    manifest = json.loads((packet / "manifest.json").read_bytes())
    for row in manifest["files"]:
        data = (packet / row["path"]).read_bytes()
        assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
    expected = list(csv.DictReader(gzip.decompress((CASE / "expected-counts.csv.gz").read_bytes()).decode().splitlines()))
    scores = json.loads((packet / "results.json").read_bytes())
    for label in ("native_only", "body_hold"):
        folder = packet / label
        xml = tmp_path / (label + ".xml")
        xml.write_bytes(gzip.decompress((folder / "field-counts.xml.gz").read_bytes()))
        measured = audit_expected_to_e1_strict(expected, xml, count_attribute="nVehContrib")
        assert len(measured) == 130 and all(r["measured_nVehContrib"] is not None for r in measured)
        stored = list(csv.DictReader((folder / "field-comparison.csv").open(encoding="utf-8")))
        assert [int(r["measured_nVehContrib"]) for r in stored] == [r["measured_nVehContrib"] for r in measured]
        errors = [r["diff_nVehContrib_minus_expected"] for r in measured]
        assert sum(map(abs, errors)) / 130 == scores[label]["field_metrics"]["diagnostic_MAE"]
        assert scores[label]["healthy"] is False and scores[label]["exact_count_fit"] is False
