from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.connection_mode_audit import audit_network_connection_mode
from astra_sumo.core.traffic_effect_review import (
    build_network_traffic_effect_review,
    compare_model_access,
    rank_traffic_effects,
)


def _connection():
    return {"structural_failure_count": 2, "review_finding_count": 2,
            "junctions": [{"junction_id": "j", "connection_mode_audit": {
                "structural_failures": ["connection_mode:path_endpoint_gap:1:2:8m",
                                        "connection_mode:internal_path_mode_permission_empty:2"],
                "review_findings": ["connection_mode:lane_rank_jump:1:2", "connection_mode:new_unknown_rule:1"]}}]}


def _safety():
    return {"findings": [
        {"category": category, "subject_id": "program", "severity": "safety", "witness_signature": category,
         "witness": {"conflict_id": conflict, "phase_index": 0}}
        for category, conflict in (("protected_green_potential_envelope_conflict", "potential"),
                                   ("protected_green_movement_conflict", "confirmed"),
                                   ("conflicting_movements_share_signal_group", "confirmed"))],
        "conflict_graph": {"conflicts": [
            {"conflict_id": certainty, "certainty": certainty, "movement_a_id": "m1", "movement_b_id": "m2"}
            for certainty in ("confirmed", "potential")]}}


def _resolve(payload, pointer):
    for part in pointer.strip("/").split("/"):
        payload = payload[int(part)] if isinstance(payload, list) else payload[part]
    return payload


def test_rank_keeps_geometry_unknown_and_potential_distinct_from_model_effects():
    connection, safety = _connection(), _safety()
    review = rank_traffic_effects(connection, safety=safety, owners={"m1": ["j"], "m2": ["j"]})
    groups = review["ranked_groups"]
    assert [row["priority"] for row in groups] == sorted(row["priority"] for row in groups)
    assert groups[0]["category"] == "protected_green_movement_conflict"
    assert groups[0]["site_ids"] == ["j"]
    by_category = {row["category"]: row for row in groups}
    assert by_category["path_endpoint_gap"]["priority"] == 5
    assert by_category["path_endpoint_gap"]["original_severity"] == "structural_failures"
    assert by_category["new_unknown_rule"]["basis"] == "unresolved"
    assert by_category["protected_green_potential_envelope_conflict"]["priority"] == 4
    assert by_category["conflicting_movements_share_signal_group"]["priority"] == 4
    assert review["input_finding_count"] == review["grouped_finding_count"] == 7
    refs = [member for group in groups for member in group["members"]]
    assert len({(row["audit"], row["pointer"]) for row in refs}) == 7
    for member in refs:
        assert _resolve({"connection": connection, "safety": safety}[member["audit"]], member["pointer"])
    assert review["false_alarm_rate"] is None
    assert review["capacity_veh_per_hour"] is None
    assert review["route_effect"] == "not_measured"
    assert review["automatic_promotion_gate"] == "blocked"


def test_many_potential_findings_group_without_loss_or_promotion():
    safety = _safety()
    safety["findings"] = [safety["findings"][0]] * 2000
    report = rank_traffic_effects({}, safety=safety, owners={"m1": ["j"], "m2": ["j"]})
    assert report["review_group_count"] == 1
    assert report["ranked_groups"][0]["member_count"] == 2000
    assert len(report["ranked_groups"][0]["members"]) == 2000
    assert report["ranked_groups"][0]["priority"] == 4


def test_tls_global_list_counted_once_and_count_mismatch_rejected():
    connection = _connection()
    tls = {"structural_failures": ["tls_link_binding:controller_logic_missing:tls"]}
    connection["tls_link_binding_audit"] = tls
    connection["junctions"][0]["tls_link_binding_audit"] = tls
    connection["structural_failure_count"] += 1
    report = rank_traffic_effects(connection)
    assert report["input_finding_count"] == 5
    tls_group = next(row for row in report["ranked_groups"] if row["category"] == "controller_logic_missing")
    assert tls_group["site_ids"] == ["j"]
    connection["structural_failure_count"] += 1
    with pytest.raises(ValueError, match="counts"):
        rank_traffic_effects(connection)


def test_unlocated_findings_are_not_merged_into_one_physical_site():
    connection = {"structural_failure_count": 2, "tls_link_binding_audit": {"structural_failures": [
        "tls_link_binding:controller_logic_missing:first", "tls_link_binding:controller_logic_missing:second"]}}
    report = rank_traffic_effects(connection)
    assert report["review_group_count"] == 2
    assert all(not row["site_ids"] for row in report["ranked_groups"])


def test_parent_safety_matching_keeps_inherited_and_changed_witnesses_separate():
    safety = _safety()
    parent = {"findings": [safety["findings"][1]]}
    review = rank_traffic_effects({}, safety=safety, parent_safety=parent)
    assert review["parent_relation_counts"] == {"not_present_in_parent": 2, "present_in_parent": 1}
    assert review["ranked_groups"][0]["parent_relation"] == "present_in_parent"


def _network():
    return ET.fromstring('''<net>
    <edge id="in" from="a" to="j"><lane id="in_0" index="0" speed="10" length="10" shape="-10,0 0,0"/></edge>
    <edge id="out" from="j" to="b"><lane id="out_0" index="0" speed="10" length="10" shape="2,0 12,0"/></edge>
    <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" speed="10" length="2" shape="0,0 2,0"/></edge>
    <junction id="a" type="dead_end" x="-10" y="0" incLanes="" intLanes=""/>
    <junction id="j" type="priority" x="1" y="0" incLanes="in_0" intLanes=":j_0_0"/>
    <junction id="b" type="dead_end" x="12" y="0" incLanes="out_0" intLanes=""/>
    <connection from="in" fromLane="0" to="out" toLane="0" via=":j_0_0" dir="s"/>
    <connection from=":j_0" fromLane="0" to="out" toLane="0" dir="s"/>
    </net>''')


def _native_network(folder):
    nodes, edges, network = [folder / name for name in ("nodes.xml", "edges.xml", "base.net.xml")]
    nodes.write_text('<nodes><node id="a" x="-100" y="0"/><node id="j" x="0" y="0"/>'
        '<node id="b" x="100" y="0"/><node id="c" x="0" y="100"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="a" to="j"/><edge id="out" from="j" to="b"/>'
        '<edge id="branch" from="j" to="c"/></edges>', encoding="utf-8")
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges),
        "--precision", "6", "--offset.disable-normalization", "true", "-o", str(network)],
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return ET.parse(network).getroot()


def _native_access(path, mode):
    import traci
    before = file_sha256(path)
    label = f"review-{path.parent.name}-{path.stem}"
    with path.with_suffix(".stdout.log").open("w", encoding="utf-8") as log:
        traci.start([shutil.which("sumo"), "-n", str(path), "--no-step-log", "true",
            "--duration-log.disable", "true", "--error-log", str(path.with_suffix(".errors.log"))],
            label=label, stdout=log, numRetries=3)
        connection = traci.getConnection(label)
        try:
            link = next(row for row in connection.lane.getLinks("in_0", extended=True) if row[0] == "out_0")
            via = link[4]
            assert via and any(row[0] == "out_0" and not row[4]
                for row in connection.lane.getLinks(via, extended=True))
            chain = ["in_0", via, "out_0"]
            denied = {lane: list(connection.lane.getDisallowed(lane)) for lane in chain}
            record = {"admitted": all(mode not in denied[lane] for lane in chain),
                "chain": chain, "disallowed": denied, "native_version": connection.getVersion()[1]}
        finally:
            connection.close()
    assert file_sha256(path) == before
    return record


@pytest.mark.parametrize("alias,canonical", [("public_transport", "bus"), ("transport", "truck"), ("cityrail", "rail_urban")])
@pytest.mark.parametrize("selector", ["edge[@id='out']/lane", "edge[@id=':j_0']/lane"])
def test_class_aliases_do_not_create_access_changes(alias, canonical, selector):
    before, after = _network(), _network()
    before.set("version", "1.20")
    after.set("version", "1.20")
    before.find(selector).set("allow", alias)
    after.find(selector).set("allow", canonical)
    assert compare_model_access(before, after, vehicle_class=canonical) == []


@pytest.mark.parametrize("version,mode,legacy,modern", [
    ("1.3", "subway", "rail_urban", "rail_urban subway cable_car"),
    ("1.3", "cable_car", "rail_urban", "rail_urban subway cable_car"),
    ("1.2", "rail_fast", "bus", "bus rail_fast"),
])
@pytest.mark.parametrize("selector", ["edge[@id='out']/lane", "edge[@id=':j_0']/lane"])
def test_equivalent_legacy_denials_do_not_create_access_changes(version, mode, legacy, modern, selector):
    before, after = _network(), _network()
    before.set("version", version)
    after.set("version", "1.20")
    before.find(selector).set("disallow", legacy)
    after.find(selector).set("disallow", modern)
    assert compare_model_access(before, after, vehicle_class=mode) == []


@pytest.mark.parametrize("version,mode,denied", [("1.3", "subway", "rail_urban"),
    ("1.3", "cable_car", "rail_urban"), ("1.2", "rail_fast", "bus")])
def test_legacy_version_changes_retain_real_access_differences(version, mode, denied):
    before, after = _network(), _network()
    before.set("version", version)
    after.set("version", "1.20")
    for root in (before, after):
        root.find("edge[@id='out']/lane").set("disallow", denied)
    changes = compare_model_access(before, after, vehicle_class=mode)
    access = [row for row in changes if row["effect"] == "movement_access"]
    assert len(access) == 1
    assert access[0]["before_source_lanes"] == [] and access[0]["after_source_lanes"] == ["in_0"]


@pytest.mark.parametrize("alias,canonical", [("public_transport", "bus"), ("transport", "truck"), ("cityrail", "rail_urban")])
def test_requested_class_alias_uses_the_same_permissions(alias, canonical):
    before, after = _network(), _network()
    before.set("version", "1.20")
    after.set("version", "1.20")
    after.find("edge[@id='out']/lane").set("disallow", canonical)
    assert compare_model_access(before, after, vehicle_class=alias) == compare_model_access(before, after, vehicle_class=canonical)


def test_explicit_allow_precedence_does_not_create_a_false_access_change():
    before, after = _network(), _network()
    before.find("edge[@id='out']/lane").attrib.update(allow="bus", disallow="bus")
    after.find("edge[@id='out']/lane").set("allow", "bus")
    assert compare_model_access(before, after, vehicle_class="bus") == []


def test_ignoring_override_is_not_a_supported_review_class():
    before, after = _network(), _network()
    after.find("edge[@id='out']/lane").set("disallow", "all")
    with pytest.raises(ValueError, match="vehicle class"):
        compare_model_access(before, after, vehicle_class="ignoring")


def test_rail_fast_is_not_an_alias_of_rail_electric():
    before, after = _network(), _network()
    before.find("edge[@id='out']/lane").set("allow", "rail_fast")
    after.find("edge[@id='out']/lane").set("allow", "rail_electric")
    access = [row for row in compare_model_access(before, after, vehicle_class="rail_fast") if row["effect"] == "movement_access"]
    assert len(access) == 1 and access[0]["before_source_lanes"] == ["in_0"] and access[0]["after_source_lanes"] == []


@pytest.mark.parametrize("restriction", ["internal_lane", "internal_connection", "direct_connection", "target_lane"])
def test_compiled_access_uses_lane_permissions_and_not_connection_directives(restriction):
    before, after = _network(), _network()
    selectors = {"internal_lane": "edge[@id=':j_0']/lane", "target_lane": "edge[@id='out']/lane",
                 "direct_connection": "connection[@from='in']", "internal_connection": "connection[@from=':j_0']"}
    after.find(selectors[restriction]).set("disallow", "passenger")
    changes = compare_model_access(before, after)
    if restriction.endswith("connection"):
        assert changes == []
        return
    access = [row for row in changes if row["effect"] == "movement_access"]
    assert len(access) == 1
    assert access[0]["before_source_lanes"] == ["in_0"]
    assert access[0]["after_source_lanes"] == []
    assert not compare_model_access(before, after, vehicle_class="bus")
    assert access[0]["route_effect"] == "not_measured"


@pytest.mark.parametrize("selector", ["connection[@from='in']", "connection[@from=':j_0']"])
def test_legacy_connection_directives_do_not_change_compiled_access(selector):
    before, after = _network(), _network()
    before.set("version", "1.3")
    after.set("version", "1.20")
    for root in (before, after):
        root.find(selector).set("disallow", "rail_urban")
    assert compare_model_access(before, after, vehicle_class="subway") == []


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="Native SUMO is required.")
@pytest.mark.parametrize("kind,mode,versions,declarations", [
    ("out_lane", "bus", ("1.20", "1.20"), ({"allow": "public_transport"}, {"allow": "bus"})),
    ("out_lane", "subway", ("1.3", "1.20"), ({"disallow": "rail_urban"}, {"disallow": "rail_urban"})),
    ("out_lane", "subway", ("1.3", "1.20"), ({"disallow": "rail_urban"}, {"disallow": "rail_urban subway cable_car"})),
    ("out_lane", "rail_fast", ("1.2", "1.20"), ({"disallow": "bus"}, {"disallow": "bus"})),
    ("out_lane", "bus", ("1.20", "1.20"), ({"allow": "bus", "disallow": "bus"}, {"allow": "bus"})),
    ("via_lane", "passenger", ("1.20", "1.20"), ({}, {"disallow": "passenger"})),
    ("entry_connection", "passenger", ("1.20", "1.20"), ({}, {"disallow": "passenger"})),
    ("via_connection", "passenger", ("1.20", "1.20"), ({}, {"disallow": "passenger"})),
    ("via_connection", "subway", ("1.3", "1.20"), ({"disallow": "rail_urban"}, {"disallow": "rail_urban"})),
])
def test_review_access_matches_loaded_native_permissions(tmp_path, kind, mode, versions, declarations):
    from copy import deepcopy
    original = _native_network(tmp_path)
    via = original.find("connection[@from='in'][@to='out']").get("via")
    lane_edges = {lane.get("id"): edge.get("id") for edge in original.findall("edge") for lane in edge.findall("lane")}
    selectors = {"out_lane": "edge[@id='out']/lane", "via_lane": f"edge[@id='{lane_edges[via]}']/lane[@id='{via}']",
        "entry_connection": "connection[@from='in'][@to='out']",
        "via_connection": f"connection[@from='{lane_edges[via]}'][@to='out']"}
    roots, loaded = [], []
    for name, version, attributes in zip(("before", "after"), versions, declarations):
        root = deepcopy(original)
        root.set("version", version)
        target = root.find(selectors[kind])
        target.attrib.pop("allow", None)
        target.attrib.pop("disallow", None)
        target.attrib.update(attributes)
        path = tmp_path / f"{name}.net.xml"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        roots.append(root)
        loaded.append(_native_access(path, mode))
    access = [row for row in compare_model_access(*roots, vehicle_class=mode) if row["effect"] == "movement_access"]
    changed = loaded[0]["admitted"] != loaded[1]["admitted"]
    assert bool(access) == changed
    if changed:
        assert len(access) == 1
        assert access[0]["before_source_lanes"] == (["in_0"] if loaded[0]["admitted"] else [])
        assert access[0]["after_source_lanes"] == (["in_0"] if loaded[1]["admitted"] else [])


def test_lane_supply_and_unrelated_geometry_do_not_claim_capacity_or_route_loss():
    before, after = _network(), _network()
    after.find("edge[@id='in']/lane").set("shape", "-10,20 0,0")
    assert compare_model_access(before, after) == []
    after.find("edge[@id='out']/lane").set("allow", "bus")
    supply = next(row for row in compare_model_access(before, after) if row["effect"] == "lane_supply")
    assert (supply["before_lane_count"], supply["after_lane_count"]) == (1, 0)
    assert "capacity_proxy" in supply["basis"]


def test_width_speed_and_storage_length_are_proxies_with_unknown_defaults():
    before, after = _network(), _network()
    after.find("edge[@id='in']/lane").set("speed", "10.000")
    assert not compare_model_access(before, after)
    after.find("edge[@id='in']/lane").set("width", "3.5")
    change = compare_model_access(before, after)[0]
    assert change["category"] == "lane_operating_dimensions_changed"
    assert change["before"]["width"] is None
    assert change["after"]["width"] == 3.5
    after.find("edge[@id='in']/lane").set("width", "nan")
    with pytest.raises(ValueError, match="finite"):
        compare_model_access(before, after)


def test_internal_cycle_cannot_make_a_path_or_use_an_exterior_detour():
    before, after = _network(), _network()
    after.find("connection[@from=':j_0']").set("to", ":j_0")
    after.find("connection[@from=':j_0']").set("via", ":j_0_0")
    assert compare_model_access(before, after)[0]["after_source_lanes"] == []


def test_renamed_edges_require_correspondence_instead_of_claiming_access_loss():
    before, after = _network(), _network()
    after.find("edge[@id='out']").set("id", "renamed")
    for row in after.findall("connection"):
        row.set("to", "renamed")
    changes = compare_model_access(before, after)
    assert changes
    assert all(row["priority"] == 4 and row["effect"] == "unknown" for row in changes)


def test_common_connection_audit_includes_review_without_changing_its_status():
    root = _network()
    root.find("connection[@from='in']").set("via", "missing_internal_lane")
    report = audit_network_connection_mode(root)
    assert report["status"] == "fail"
    assert report["traffic_effect_review"]["input_finding_count"] == (
        report["review_finding_count"] + report["structural_failure_count"])
    assert report["traffic_effect_review"]["status"] == "blocked"
    assert report["automatic_promotion_gate"] == ("pass" if report["status"] == "pass" else "blocked")


def test_builder_binds_inputs_reports_and_every_member(tmp_path):
    candidate, parent = tmp_path / "candidate.net.xml", tmp_path / "parent.net.xml"
    ET.ElementTree(_network()).write(parent)
    root = _network()
    root.find("edge[@id='out']/lane").set("allow", "bus")
    ET.ElementTree(root).write(candidate)
    hashes = [file_sha256(row) for row in (candidate, parent)]
    report = build_network_traffic_effect_review(net_file=candidate, parent_net_file=parent, output_dir=tmp_path / "review")
    assert hashes == [file_sha256(row) for row in (candidate, parent)]
    assert report["parent_comparison"] == "shared_edge_and_lane_ids_only"
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    for identity in [manifest["report"], *manifest["audits"].values(), *manifest["inputs"].values()]:
        assert file_sha256(Path(identity["path"])) == identity["sha256"]
    for group in report["ranked_groups"]:
        for member in group["members"]:
            raw = json.loads(Path(report["audits"][member["audit"]]["path"]).read_text(encoding="utf-8"))
            assert _resolve(raw, member["pointer"])
    with pytest.raises(FileExistsError):
        build_network_traffic_effect_review(net_file=candidate, output_dir=tmp_path / "review")
    with pytest.raises(ValueError, match="input network"):
        build_network_traffic_effect_review(net_file=candidate, output_dir=tmp_path)
    with pytest.raises(ValueError, match="vehicle class"):
        build_network_traffic_effect_review(net_file=candidate, output_dir=tmp_path / "other", vehicle_class="unknown")


def test_cli_runs_and_rejects_existing_output(tmp_path, capsys):
    from astra_sumo.cli import main

    source = tmp_path / "source.net.xml"
    ET.ElementTree(_network()).write(source)
    args = ["network", "review-effects", str(source), str(tmp_path / "review"), "--json"]
    assert main(args) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "astra.traffic-effect-review/v1"
    assert payload["status"] == "blocked"
    assert main(args) == 3
    assert "new traffic review" in json.loads(capsys.readouterr().out)["error"]


def test_input_change_stops_before_writing_report(tmp_path, monkeypatch):
    from astra_sumo.corridor import conflict_graph

    candidate = tmp_path / "candidate.net.xml"
    ET.ElementTree(_network()).write(candidate)
    original = conflict_graph.audit_independent_movement_safety

    def mutate(snapshot):
        candidate.write_bytes(candidate.read_bytes() + b"\n")
        return original(snapshot)

    monkeypatch.setattr(conflict_graph, "audit_independent_movement_safety", mutate)
    with pytest.raises(ValueError, match="changed"):
        build_network_traffic_effect_review(net_file=candidate, output_dir=tmp_path / "review")
    assert not (tmp_path / "review").exists()
