import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.core.candidate_contracts import (
    build_review_decision_template,
    file_sha256,
)
from torii_sumo.core.corridor_edit_ledger import (
    build_corridor_edit_ledger,
    materialize_corridor_edit_variant,
    normalize_edit_operation,
    run_corridor_candidate_gates,
    _modal_addition_gate,
    _tls_logic_signature,
    validate_edit_ledger,
)


def _write_net(path: Path) -> None:
    path.write_text(
        """<net>
  <location netOffset="0.0,0.0" convBoundary="0.0,0.0,20.0,20.0" origBoundary="0.0,0.0,20.0,20.0"/>
  <junction id="a" type="priority" x="0" y="0"/>
  <junction id="b" type="priority" x="10" y="0"/>
  <junction id="c" type="priority" x="20" y="0"/>
  <edge id="ab" from="a" to="b" type="highway.secondary"><lane id="ab_0" index="0" speed="13.9" length="10" shape="0,0 10,0"/></edge>
  <edge id="bc" from="b" to="c" type="highway.secondary"><lane id="bc_0" index="0" speed="13.9" length="10" shape="10,0 20,0"/></edge>
  <edge id="rail" from="a" to="c" type="railway.rail"><lane id="rail_0" index="0" speed="20" length="20" shape="0,2 20,2"/></edge>
  <tlLogic id="tls" type="static" programID="0" offset="0"><phase duration="30" state="Gr"/></tlLogic>
  <connection from="ab" to="bc" fromLane="0" toLane="0" tl="tls" linkIndex="0"/>
</net>""",
        encoding="utf-8",
    )


def _write_distinct_candidate(source: Path, candidate: Path) -> None:
    root = ET.parse(source).getroot()
    ET.SubElement(root, "param", {"key": "torii.test-candidate", "value": "true"})
    ET.ElementTree(root).write(candidate, encoding="utf-8")


def _persist_materialization_report(source: Path, candidate: Path, report_file: Path) -> dict[str, object]:
    report_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "netconvert",
        "--sumo-net-file",
        str(source.resolve()),
        "--output-file",
        str(candidate.resolve()),
    ]
    report: dict[str, object] = {
        "schema": "torii.corridor_materialization.v1",
        "status": "pass",
        "materialization_status": "variant_created_for_review",
        "candidate_variant_status": "review_only",
        "source_net_file": str(source.resolve()),
        "candidate_net_file": str(candidate.resolve()),
        "source_sha256": file_sha256(source),
        "candidate_sha256": file_sha256(candidate),
        "command": command,
        "command_result": {"status": "pass", "returncode": 0, "command": command},
        "report_file": str(report_file.resolve()),
    }
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _persist_review_decision(
    source: Path,
    candidate: Path,
    review_file: Path,
    *,
    semantic_allowances: dict[str, int] | None = None,
    tls_logic_allowances: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    decision = build_review_decision_template(
        source_net_file=source,
        candidate_net_file=candidate,
        semantic_allowances=semantic_allowances,
        tls_logic_allowances=tls_logic_allowances,
    )
    decision.update(
        {
            "status": "accepted",
            "rationale": "reviewed exact candidate deltas",
            "evidence": [{"kind": "manual_map_review", "result": "accepted"}],
            "rollback": {"action": "discard_candidate_and_restore_source"},
            "review_file": str(review_file.resolve()),
        }
    )
    review_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return decision


def _append_generated_edges_with_connections(source_root: ET.Element, additions_root: ET.Element) -> None:
    for edge in additions_root.findall("edge"):
        edge_id = str(edge.attrib["id"])
        source_root.append(edge)
        ET.SubElement(
            source_root,
            "connection",
            {"from": edge_id, "to": "bc", "fromLane": "0", "toLane": "0"},
        )


def _passing_routeability_audit(**kwargs: object) -> dict[str, object]:
    net_file = Path(str(kwargs["net_file"]))
    output_dir = Path(str(kwargs["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = str(kwargs.get("prefix", "routeability"))
    report_file = output_dir / f"{prefix}.json"
    manifest_file = output_dir / f"{prefix}.manifest.json"
    report: dict[str, object] = {
        "schema": "torii.routeability_audit.v2",
        "status": "pass",
        "routeability_status": "pass",
        "net_file": str(net_file.resolve()),
        "net_sha256": file_sha256(net_file),
        "report_file": str(report_file.resolve()),
        "manifest_file": str(manifest_file.resolve()),
    }
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest_file.write_text(
        json.dumps(
            {
                "schema": "torii.routeability_manifest.v2",
                "status": "pass",
                "net_file": str(net_file.resolve()),
                "net_sha256": file_sha256(net_file),
                "artifacts": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def _passing_topology_audit(**kwargs: object) -> dict[str, object]:
    net_file = Path(str(kwargs["net_file"]))
    output_dir = Path(str(kwargs["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = str(kwargs.get("prefix", "topology"))
    report_file = output_dir / f"{prefix}.json"
    manifest_file = output_dir / f"{prefix}.manifest.json"
    report: dict[str, object] = {
        "schema": "torii.topology_audit.v2",
        "status": "pass",
        "topology_fragmentation_status": "pass",
        "net_file": str(net_file.resolve()),
        "net_sha256": file_sha256(net_file),
        "suspicious_cluster_count": 0,
        "topology_canonical_cell_records": [],
        "report_file": str(report_file.resolve()),
        "manifest_file": str(manifest_file.resolve()),
    }
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest_file.write_text(
        json.dumps(
            {
                "schema": "torii.topology_manifest.v2",
                "status": "pass",
                "net_file": str(net_file.resolve()),
                "net_sha256": file_sha256(net_file),
                "artifacts": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def test_normalize_operation_promotes_edit_parameters() -> None:
    operation = normalize_edit_operation(
        {
            "id": "sidewalk-1",
            "operation": "add_pedestrian_facility",
            "from": "a",
            "to": "b",
            "type": "highway.footway",
            "rationale": "missing parallel pedestrian connection",
            "evidence": ["map review"],
            "rollback": {"action": "remove_candidate_addition"},
            "map_review_required": True,
            "review_question": "Is the sidewalk continuous?",
        }
    )

    assert operation["operation"] == "add_sidewalk"
    assert operation["params"]["type"] == "highway.footway"
    assert operation["constraints"]["preserve_tls"] is True
    assert operation["review_requirements"] == {
        "map_review_required": True,
        "review_question": "Is the sidewalk continuous?",
    }


def test_validation_requires_evidence_and_rollback() -> None:
    report = validate_edit_ledger(
        {
            "operations": [
                {"id": "delete-1", "operation": "delete_edge", "target_ids": ["edge"]}
            ]
        }
    )

    assert report["status"] == "fail"
    assert {item["code"] for item in report["errors"]} == {"missing_rationale", "missing_evidence", "missing_rollback"}


def test_build_writes_review_overlay_manifest_and_rollback(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    output_dir = tmp_path / "artifacts"
    _write_net(net_file)

    result = build_corridor_edit_ledger(
        net_file=net_file,
        output_dir=output_dir,
        include_auto_proposals=False,
        operations=[
            {
                "id": "add-crossing-b",
                "operation": "add_crossing",
                "params": {"node_id": "b", "crossing_edges": ["ab", "bc"]},
                "location": {"x": 10, "y": 0},
                "rationale": "provide a pedestrian crossing at the corridor junction",
                "evidence": [{"kind": "map_review", "source": "manual_review"}],
                "rollback": {"action": "remove_candidate_addition", "target_ids": ["add-crossing-b"]},
            }
        ],
    )

    assert result["status"] == "pass"
    assert result["candidate_variant_status"] == "review_only"
    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    rollback = json.loads(Path(result["rollback_file"]).read_text(encoding="utf-8"))
    overlay = ET.parse(result["review_overlay_file"]).getroot()
    assert manifest["candidate_variant_status"] == "review_only"
    assert rollback["safe_to_apply"] is False
    assert overlay.tag == "additional"
    assert overlay.find("poi") is not None
    assert len(manifest["artifacts"]) == 3


def test_protected_tls_and_rail_deletions_are_blocked(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)
    root = ET.parse(net_file).getroot()
    report = validate_edit_ledger(
        {
            "operations": [
                {
                    "id": "delete-tls",
                    "operation": "delete_edge",
                    "target_ids": ["ab"],
                    "rationale": "cleanup",
                    "evidence": ["topology"],
                    "rollback": {"action": "restore_source_edge"},
                },
                {
                    "id": "delete-rail",
                    "operation": "delete_edge",
                    "target_ids": ["rail"],
                    "rationale": "cleanup",
                    "evidence": ["topology"],
                    "rollback": {"action": "restore_source_edge"},
                },
            ]
        },
        root=root,
    )

    codes = {item["code"] for item in report["errors"]}
    assert report["status"] == "fail"
    assert "tls_controlled_target" in codes
    assert "rail_target" in codes


def test_merge_may_touch_remote_tls_boundary_but_not_local_tls_connection(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)
    root = ET.parse(net_file).getroot()
    root.find("connection").attrib.pop("tl", None)
    ET.SubElement(root, "connection", {"from": "bc", "to": "rail", "tl": "tls", "linkIndex": "0"})
    report = validate_edit_ledger(
        {
            "operations": [
                {
                    "id": "merge-boundary",
                    "operation": "merge_edges",
                    "target_ids": ["ab", "bc"],
                    "params": {"node_id": "b"},
                    "rationale": "remove a proven micro corridor node",
                    "evidence": ["straight lane-preserving local connection"],
                    "rollback": {"action": "restore_source_edges_and_junction"},
                }
            ]
        },
        root=root,
    )

    assert report["status"] == "pass"


def _accepted_operation(operation: str, target_ids: list[str], **kwargs: object) -> dict[str, object]:
    return {
        "id": f"accepted-{operation}",
        "operation": operation,
        "status": "accepted",
        "target_ids": target_ids,
        "rationale": "reviewed corridor correction",
        "evidence": ["corridor review"],
        "rollback": {"action": "restore_source"},
        **kwargs,
    }


def test_materializer_blocks_ledger_without_accepted_mutation(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    report = materialize_corridor_edit_variant(
        ledger={"source_net_file": str(net_file), "operations": []},
        output_dir=tmp_path / "materialized",
    )

    assert report["status"] == "blocked"
    assert report["materialization_status"] == "no_accepted_operations"
    assert not (tmp_path / "materialized" / "corridor_materialized_variant.net.xml").exists()
    assert Path(report["manifest_file"]).exists()


def test_materializer_materializes_accepted_sidewalk_as_plain_edge_candidate(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)
    output_dir = tmp_path / "materialized"

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                )
            ],
        },
        output_dir=output_dir,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["materialization_status"] == "variant_created_for_review"
    assert Path(report["candidate_net_file"]).exists()
    assert Path(report["accepted_review_additional_xml"]).exists()
    assert Path(report["map_review_evidence_file"]).exists()
    assert Path(report["candidate_review_html_file"]).exists()
    assert report["map_review_evidence_status"] == "pass"
    assert report["map_review_readiness_status"] == "not_required"
    map_evidence = json.loads(Path(report["map_review_evidence_file"]).read_text(encoding="utf-8"))
    assert map_evidence["candidate_sha256"] == report["candidate_sha256"]
    assert map_evidence["locations"][0]["proposal_id"] == "accepted-add_sidewalk"
    overlay = ET.parse(report["accepted_review_additional_xml"]).getroot()
    assert {element.tag for element in overlay.iter()} <= {"additional", "poi", "poly", "param"}
    poi_params = {
        item.attrib["key"]: item.attrib.get("value", "")
        for item in overlay.find("poi").findall("param")
    }
    assert poi_params["candidate_sha256"] == report["candidate_sha256"]
    review_html = Path(report["candidate_review_html_file"]).read_text(encoding="utf-8")
    assert report["candidate_sha256"] in review_html
    assert report["map_review_evidence_sha256"] in review_html
    assert "--edge-files" in report["command"]
    assert "--walkingareas" in report["command"]


def test_materializer_creates_separate_merge_variant_from_accepted_operation(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)
    output_dir = tmp_path / "materialized"
    source_before = net_file.read_text(encoding="utf-8")

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        candidate = Path(command[command.index("--output-file") + 1])
        root = ET.fromstring(source_before)
        for edge in list(root.findall("edge")):
            if edge.attrib.get("id") == "ab":
                root.remove(edge)
        for connection in list(root.findall("connection")):
            if "ab" in {connection.attrib.get("from"), connection.attrib.get("to")}:
                root.remove(connection)
        ET.ElementTree(root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [_accepted_operation("merge_edges", ["ab"])],
        },
        output_dir=output_dir,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["materialization_status"] == "variant_created_for_review"
    assert Path(report["candidate_net_file"]).exists()
    assert "--geometry.remove" in report["command"]
    assert "ab\n" not in Path(report["plan_file"]).read_text(encoding="utf-8")
    assert net_file.read_text(encoding="utf-8") == source_before


def test_materializer_creates_separate_delete_variant_from_accepted_operation(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)
    root = ET.parse(net_file).getroot()
    connection = root.find("connection")
    assert connection is not None
    root.remove(connection)
    ET.ElementTree(root).write(net_file, encoding="utf-8")
    output_dir = tmp_path / "materialized"

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        candidate = Path(command[command.index("--output-file") + 1])
        root = ET.parse(net_file).getroot()
        for edge in list(root.findall("edge")):
            if edge.attrib.get("id") == "bc":
                root.remove(edge)
        ET.ElementTree(root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [_accepted_operation("delete_edge", ["bc"])],
        },
        output_dir=output_dir,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["operation_family"] == "delete_edge"
    assert "--remove-edges.input-file" in report["command"]
    assert Path(report["candidate_net_file"]).exists()
    overlay = ET.parse(report["accepted_review_additional_xml"]).getroot()
    assert overlay.find("poi") is not None
    assert overlay.find("poly") is not None
    assert {element.tag for element in overlay.iter()} <= {"additional", "poi", "poly", "param"}
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    artifact_kinds = {item["kind"] for item in manifest["artifacts"]}
    assert "map_review_evidence" in artifact_kinds
    assert "accepted_review_additional_xml" in artifact_kinds
    assert "candidate_review_html" in artifact_kinds


def test_required_map_review_is_a_hash_bound_candidate_gate(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def materialize_runner(
        command: list[str], *, cwd: Path, timeout_seconds: float
    ) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    materialization = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "map_review": {"temporal_scope": "current", "target_date": ""},
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                    location={"x": 5, "y": 0, "lat": 48.766, "lon": 11.425},
                    review_requirements={
                        "map_review_required": True,
                        "review_question": "Is a continuous sidewalk visible here?",
                    },
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=materialize_runner,
    )

    assert materialization["status"] == "pass"
    assert materialization["map_review_readiness_status"] == "pass"
    assert materialization["map_review_required_location_ids"] == [
        "corridor_edit:accepted-add_sidewalk"
    ]

    candidate_file = Path(materialization["candidate_net_file"])
    blocked = run_corridor_candidate_gates(
        source_net_file=net_file,
        candidate_net_file=candidate_file,
        output_dir=tmp_path / "blocked-gates",
        materialization_report=materialization,
        routeability_audit_func=_passing_routeability_audit,
        topology_audit_func=_passing_topology_audit,
        command_runner=lambda *args, **kwargs: {"status": "pass", "returncode": 0},
    )
    assert blocked["status"] == "blocked"
    assert blocked["gates"]["review_contract"]["status"] == "blocked"

    review_file = tmp_path / "accepted-review.json"
    review = json.loads(
        Path(materialization["review_decision_template_file"]).read_text(encoding="utf-8")
    )
    review.update(
        {
            "status": "accepted",
            "rationale": "reviewed the exact candidate against current map evidence",
            "evidence": [{"kind": "manual_map_review", "result": "approved"}],
            "review_file": str(review_file.resolve()),
        }
    )
    map_decision = review["map_review"]["decisions"][0]
    map_decision.update(
        {
            "decision": "approved",
            "observed_facts": {
                "feature_presence": "A continuous sidewalk is visible.",
                "geometry_connectivity": "It connects both corridor endpoints.",
                "access_modes": "The facility is pedestrian-only.",
                "source_limitations": "Reviewed against current imagery only.",
            },
            "reviewer": "integration-test-reviewer",
            "reviewed_at": "2026-07-13T12:00:00+02:00",
        }
    )
    review_file.write_text(json.dumps(review, indent=2), encoding="utf-8")

    accepted = run_corridor_candidate_gates(
        source_net_file=net_file,
        candidate_net_file=candidate_file,
        output_dir=tmp_path / "accepted-gates",
        materialization_report=materialization,
        review_decision=review,
        routeability_audit_func=_passing_routeability_audit,
        topology_audit_func=_passing_topology_audit,
        command_runner=lambda *args, **kwargs: {"status": "pass", "returncode": 0},
    )

    assert accepted["status"] == "pass"
    assert accepted["map_review_status"] == "pass"
    assert all(gate["status"] == "pass" for gate in accepted["gates"].values())


def test_required_map_review_marks_unreviewable_location_and_blocks_promotion(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def materialize_runner(
        command: list[str], *, cwd: Path, timeout_seconds: float
    ) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    materialization = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                    review_requirements={"map_review_required": True},
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=materialize_runner,
    )

    assert materialization["status"] == "pass"
    assert materialization["map_review_readiness_status"] == "blocked"
    evidence = json.loads(Path(materialization["map_review_evidence_file"]).read_text(encoding="utf-8"))
    assert evidence["unavailable_required_location_ids"] == [
        "corridor_edit:accepted-add_sidewalk"
    ]
    assert evidence["google_maps_requires_time_confirmation"] == "yes"

    gates = run_corridor_candidate_gates(
        source_net_file=net_file,
        candidate_net_file=Path(materialization["candidate_net_file"]),
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        command_runner=lambda *args, **kwargs: {"status": "pass", "returncode": 0},
    )
    assert gates["status"] == "blocked"
    assert gates["gates"]["review_contract"]["status"] == "blocked"
    error_codes = {item["code"] for item in gates["gates"]["review_contract"]["errors"]}
    assert "map_review_evidence_not_ready" in error_codes


def test_tampered_map_review_evidence_blocks_materialization_contract(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def materialize_runner(
        command: list[str], *, cwd: Path, timeout_seconds: float
    ) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    materialization = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=materialize_runner,
    )
    evidence_file = Path(materialization["map_review_evidence_file"])
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    evidence["locations"][0]["review_question"] = "tampered after materialization"
    evidence_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    gates = run_corridor_candidate_gates(
        source_net_file=net_file,
        candidate_net_file=Path(materialization["candidate_net_file"]),
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        command_runner=lambda *args, **kwargs: {"status": "pass", "returncode": 0},
    )

    assert gates["status"] == "blocked"
    assert gates["gates"]["netconvert"]["status"] == "blocked"
    error_codes = {item["code"] for item in gates["gates"]["netconvert"]["errors"]}
    assert "map_review_evidence_sha256_mismatch" in error_codes


def test_review_overlay_rejects_runtime_side_effect_elements(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def materialize_runner(
        command: list[str], *, cwd: Path, timeout_seconds: float
    ) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    materialization = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=materialize_runner,
    )
    overlay_file = Path(materialization["accepted_review_additional_xml"])
    overlay = ET.parse(overlay_file)
    ET.SubElement(overlay.getroot(), "rerouter", {"id": "unsafe", "edges": "ab"})
    overlay.write(overlay_file, encoding="utf-8", xml_declaration=True)

    gates = run_corridor_candidate_gates(
        source_net_file=net_file,
        candidate_net_file=Path(materialization["candidate_net_file"]),
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        command_runner=lambda *args, **kwargs: {"status": "pass", "returncode": 0},
    )

    assert gates["status"] == "blocked"
    assert gates["gates"]["netconvert"]["status"] == "blocked"
    error_codes = {item["code"] for item in gates["gates"]["netconvert"]["errors"]}
    assert "review_overlay_contains_side_effect_elements" in error_codes


def test_materializer_blocks_crossing_when_netconvert_does_not_create_crossing_semantics(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        candidate = Path(command[command.index("--output-file") + 1])
        root = ET.parse(net_file).getroot()
        ET.SubElement(root, "param", {"key": "netconvert-ran", "value": "true"})
        ET.ElementTree(root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_crossing",
                    ["ab", "bc"],
                    params={"node_id": "b", "crossing_edges": ["ab", "bc"]},
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=fake_runner,
    )

    assert report["status"] == "blocked"
    assert report["materialization_status"] == "semantic_gate_failed"
    assert report["semantic_validation"]["crossing_shortfall"] == 1
    assert Path(report["candidate_net_file"]).exists()


def test_candidate_gates_require_bound_materialization_evidence_and_aggregate_passes(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    _write_distinct_candidate(source, candidate)
    materialization = _persist_materialization_report(
        source,
        candidate,
        tmp_path / "materialization.json",
    )

    def _command_runner(command, **_kwargs):
        return {"status": "pass", "command": command, "returncode": 0}

    def _routeability(**_kwargs):
        return _passing_routeability_audit(**_kwargs)

    def _topology(**_kwargs):
        return _passing_topology_audit(**_kwargs)

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        routeability_audit_func=_routeability,
        topology_audit_func=_topology,
        command_runner=_command_runner,
    )

    assert report["status"] == "pass"
    assert all(gate["status"] == "pass" for gate in report["gates"].values())
    assert Path(report["manifest_file"]).exists()


def test_candidate_gate_blocks_tls_phase_semantic_change(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    candidate.write_text(source.read_text(encoding="utf-8").replace('duration="30"', 'duration="31"'), encoding="utf-8")
    materialization = _persist_materialization_report(
        source,
        candidate,
        tmp_path / "materialization.json",
    )

    def _command_runner(command, **_kwargs):
        return {"status": "pass", "command": command, "returncode": 0}

    def _routeability(**_kwargs):
        return _passing_routeability_audit(**_kwargs)

    def _topology(**_kwargs):
        return _passing_topology_audit(**_kwargs)

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        routeability_audit_func=_routeability,
        topology_audit_func=_topology,
        command_runner=_command_runner,
    )

    assert report["status"] == "blocked"
    semantic = report["gates"]["tls_modal_rail_bridge_tunnel"]
    assert semantic["tls_logic_signature_status"] == "blocked"
    assert "tls" in semantic["tls_logic_signature_differences"]


def test_candidate_gate_accepts_only_explicit_tls_phase_review_allowance(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    candidate.write_text(source.read_text(encoding="utf-8").replace('duration="30"', 'duration="31"'), encoding="utf-8")
    expected_signature = _tls_logic_signature(ET.parse(candidate).getroot())
    materialization = _persist_materialization_report(
        source,
        candidate,
        tmp_path / "materialization.json",
    )
    review = _persist_review_decision(
        source,
        candidate,
        tmp_path / "review.json",
        tls_logic_allowances=expected_signature,
    )

    def _command_runner(command, **_kwargs):
        return {"status": "pass", "command": command, "returncode": 0}

    def _routeability(**_kwargs):
        return _passing_routeability_audit(**_kwargs)

    def _topology(**_kwargs):
        return _passing_topology_audit(**_kwargs)

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=materialization,
        review_decision=review,
        routeability_audit_func=_routeability,
        topology_audit_func=_topology,
        command_runner=_command_runner,
    )

    assert report["status"] == "pass"
    semantic = report["gates"]["tls_modal_rail_bridge_tunnel"]
    assert semantic["tls_logic_signature_status"] == "pass"
    assert "tls" in semantic["tls_logic_signature_accepted_changes"]
    assert semantic["tls_logic_signature_unexpected_changes"] == {}


def test_candidate_gate_requires_explicit_allowance_for_additive_pedestrian_semantics(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    root = ET.parse(source).getroot()
    ET.SubElement(root, "edge", {"id": ":b_c0", "function": "crossing"})
    ET.SubElement(root, "edge", {"id": ":b_w0", "function": "walkingarea"})
    ET.ElementTree(root).write(candidate, encoding="utf-8")
    materialization = _persist_materialization_report(
        source,
        candidate,
        tmp_path / "materialization.json",
    )
    review = _persist_review_decision(
        source,
        candidate,
        tmp_path / "review.json",
        semantic_allowances={"crossing_edge_count": 1, "walkingarea_edge_count": 1},
    )

    def _command_runner(command, **_kwargs):
        return {"status": "pass", "command": command, "returncode": 0}

    def _routeability(**_kwargs):
        return _passing_routeability_audit(**_kwargs)

    def _topology(**_kwargs):
        return _passing_topology_audit(**_kwargs)

    blocked = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "blocked_gates",
        materialization_report=materialization,
        routeability_audit_func=_routeability,
        topology_audit_func=_topology,
        command_runner=_command_runner,
    )
    allowed = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "allowed_gates",
        materialization_report=materialization,
        review_decision=review,
        routeability_audit_func=_routeability,
        topology_audit_func=_topology,
        command_runner=_command_runner,
    )

    assert blocked["status"] == "blocked"
    assert allowed["status"] == "pass"
    assert allowed["gates"]["tls_modal_rail_bridge_tunnel"]["disallowed_deltas"] == {}


def test_bicycle_alias_materializes_a_multimodal_plain_edge(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_bicycle",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.cycleway"},
                )
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    addition_root = ET.parse(Path(report["plan_file"])).getroot()
    edge = addition_root.find("edge")
    assert edge is not None
    assert edge.attrib["allow"] == "bicycle pedestrian"


def test_anonymous_operation_id_is_content_addressed_and_deterministic() -> None:
    raw = {
        "operation": "add_sidewalk",
        "from": "a",
        "to": "b",
        "evidence": ["map review"],
    }
    canonical = json.dumps(
        raw,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = f"operation-{hashlib.sha256(canonical).hexdigest()[:16]}"

    assert normalize_edit_operation(raw)["id"] == expected


def test_materializer_supports_one_compatible_additive_family(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    _write_net(net_file)

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        ET.SubElement(source_root, "edge", {"id": ":b_c0", "function": "crossing"})
        ET.SubElement(source_root, "edge", {"id": ":b_w0", "function": "walkingarea"})
        candidate = Path(command[command.index("--output-file") + 1])
        ET.ElementTree(source_root).write(candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                ),
                _accepted_operation(
                    "add_crossing",
                    ["ab", "bc"],
                    params={"node_id": "b", "crossing_edges": ["ab", "bc"]},
                ),
            ],
        },
        output_dir=tmp_path / "materialized",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["operation_family"] == "additive"
    assert "--edge-files" in report["command"]
    assert "--connection-files" in report["command"]
    assert report["review_required"] is True
    assert Path(report["review_decision_template_file"]).is_file()


def test_materializer_removes_stale_candidate_before_running_netconvert(tmp_path: Path) -> None:
    net_file = tmp_path / "source.net.xml"
    output_dir = tmp_path / "materialized"
    output_dir.mkdir()
    _write_net(net_file)
    stale_candidate = output_dir / "corridor_materialized_variant.net.xml"
    stale_candidate.write_text("<net><stale/></net>", encoding="utf-8")

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict[str, object]:
        assert not stale_candidate.exists()
        source_root = ET.parse(net_file).getroot()
        additions_root = ET.parse(Path(command[command.index("--edge-files") + 1])).getroot()
        _append_generated_edges_with_connections(source_root, additions_root)
        ET.ElementTree(source_root).write(stale_candidate, encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}

    report = materialize_corridor_edit_variant(
        ledger={
            "source_net_file": str(net_file),
            "operations": [
                _accepted_operation(
                    "add_sidewalk",
                    [],
                    params={"from": "a", "to": "b", "type": "highway.footway"},
                )
            ],
        },
        output_dir=output_dir,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["stale_candidate_removed"] is True
    assert "<stale" not in stale_candidate.read_text(encoding="utf-8")


def test_candidate_gate_rejects_status_only_materialization_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    _write_distinct_candidate(source, candidate)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("runtime gates must not run after a failed evidence contract")

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report={"status": "pass"},
        routeability_audit_func=should_not_run,
        topology_audit_func=should_not_run,
        command_runner=should_not_run,
    )

    assert report["status"] == "blocked"
    error_codes = {item["code"] for item in report["gates"]["netconvert"]["errors"]}
    assert "materialization_schema_mismatch" in error_codes
    assert "materialization_evidence_file_required" in error_codes
    assert report["gates"]["sumo_load"]["execution_status"] == "not_run"
    assert Path(report["manifest_file"]).is_file()


def test_candidate_gate_rejects_identical_or_stale_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    identical = tmp_path / "identical.net.xml"
    _write_net(source)
    identical.write_bytes(source.read_bytes())
    evidence = _persist_materialization_report(source, identical, tmp_path / "materialization.json")

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=identical,
        output_dir=tmp_path / "gates",
        materialization_report=evidence,
    )

    identity_codes = {item["code"] for item in report["gates"]["artifact_identity"]["errors"]}
    assert report["status"] == "blocked"
    assert "candidate_identical_to_source" in identity_codes

    distinct = tmp_path / "distinct.net.xml"
    _write_distinct_candidate(source, distinct)
    stale_evidence = _persist_materialization_report(source, distinct, tmp_path / "stale.json")
    stale_evidence["candidate_sha256"] = "0" * 64
    Path(stale_evidence["report_file"]).write_text(json.dumps(stale_evidence), encoding="utf-8")
    stale_report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=distinct,
        output_dir=tmp_path / "stale_gates",
        materialization_report=stale_evidence,
    )
    stale_codes = {item["code"] for item in stale_report["gates"]["netconvert"]["errors"]}
    assert "materialization_candidate_sha256_mismatch" in stale_codes


def test_candidate_gate_persists_invalid_xml_failure(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    candidate.write_text("<net><broken>", encoding="utf-8")
    evidence = _persist_materialization_report(source, candidate, tmp_path / "materialization.json")

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=evidence,
    )

    assert report["status"] == "blocked"
    assert report["gates"]["xml_parse"]["status"] == "blocked"
    assert Path(report["report_file"]).is_file()
    assert Path(report["manifest_file"]).is_file()


def test_review_allowance_must_equal_the_exact_candidate_delta(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    root = ET.parse(source).getroot()
    ET.SubElement(root, "edge", {"id": ":b_c0", "function": "crossing"})
    ET.ElementTree(root).write(candidate, encoding="utf-8")
    evidence = _persist_materialization_report(source, candidate, tmp_path / "materialization.json")
    overbroad_review = _persist_review_decision(
        source,
        candidate,
        tmp_path / "review.json",
        semantic_allowances={"crossing_edge_count": 2},
    )

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=evidence,
        review_decision=overbroad_review,
    )

    semantic_gate = report["gates"]["tls_modal_rail_bridge_tunnel"]
    assert report["status"] == "blocked"
    assert semantic_gate["mismatched_allowances"] == {"crossing_edge_count": 2}


def test_routeability_requires_both_report_and_routeability_pass_status(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source)
    _write_distinct_candidate(source, candidate)
    evidence = _persist_materialization_report(source, candidate, tmp_path / "materialization.json")

    report = run_corridor_candidate_gates(
        source_net_file=source,
        candidate_net_file=candidate,
        output_dir=tmp_path / "gates",
        materialization_report=evidence,
        command_runner=lambda command, **_kwargs: {
            "status": "pass",
            "returncode": 0,
            "command": command,
        },
        routeability_audit_func=lambda **_kwargs: {
            "status": "pass",
            "routeability_status": "incomplete",
        },
        topology_audit_func=lambda **_kwargs: {
            "status": "pass",
            "topology_fragmentation_status": "pass",
        },
    )

    assert report["status"] == "blocked"
    assert report["gates"]["routeability"]["status"] == "blocked"


def test_modal_addition_follows_sumo_internal_walkingarea_connections() -> None:
    source = ET.fromstring(
        """<net>
  <edge id="p0" from="a" to="b"><lane id="p0_0" index="0" allow="pedestrian"/></edge>
  <edge id=":b_w0" function="walkingarea"><lane id=":b_w0_0" index="0" allow="pedestrian"/></edge>
  <connection from="p0" to=":b_w0" fromLane="0" toLane="0"/>
</net>"""
    )
    candidate = ET.fromstring(
        """<net>
  <edge id="p0" from="a" to="b"><lane id="p0_0" index="0" allow="pedestrian"/></edge>
  <edge id="p1" from="b" to="c"><lane id="p1_0" index="0" allow="pedestrian"/></edge>
  <edge id=":b_w0" function="walkingarea"><lane id=":b_w0_0" index="0" allow="pedestrian"/></edge>
  <edge id=":c_w0" function="walkingarea"><lane id=":c_w0_0" index="0" allow="pedestrian"/></edge>
  <connection from="p0" to=":b_w0" fromLane="0" toLane="0"/>
  <connection from=":b_w0" to="p1" fromLane="0" toLane="0"/>
  <connection from="p1" to=":c_w0" fromLane="0" toLane="0"/>
</net>"""
    )

    report = _modal_addition_gate(
        source,
        candidate,
        [
            {
                "edge_id": "p1",
                "operation": "add_sidewalk",
                "allow": "pedestrian",
            }
        ],
    )

    assert report["status"] == "pass"
    assert report["checked_additions"][0]["modal_connection_count"] == 2
