import hashlib
import json
from pathlib import Path

import pytest

from torii_sumo.tools import intersection_tools
from torii_sumo.tools.intersection_tools import (
    sumo_intersection_archetype_classify,
    sumo_intersection_clean,
    sumo_intersection_model,
    sumo_intersection_scene_workflow,
    sumo_intersection_validate,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_sumo_intersection_archetype_classify_is_hash_bound_and_read_only() -> None:
    source = FIXTURES / "x4_signalized.osm.xml"
    before = source.read_bytes()

    report = sumo_intersection_archetype_classify(str(source), "1")

    expected_sha256 = hashlib.sha256(before).hexdigest()
    profile = report["archetype_profile"]
    assert report["status"] == "pass"
    assert report["source_file"] == str(source.resolve())
    assert report["source_sha256"] == expected_sha256
    assert report["traffic_side"] == "right"
    assert report["disposition"] == profile["disposition"]
    assert report["type_recognition"] == "pass"
    assert report["automatic_promotion_gate"] == "blocked"
    assert profile["source_evidence"] == {
        "sha256": expected_sha256,
        "media_type": "application/osm+xml",
        "content_format": "osm_xml",
    }
    assert profile["derived_alias"]["value"] == "X4"
    assert profile["classification_only"] is True
    assert profile["automatic_promotion_gate"] == "blocked"
    assert report["evidence_summary"]["physical_approach_count"] == 4
    assert report["evidence_summary"]["road_detail_unknown_count"] == len(
        profile["road_detail"]["unknown_road_arm_ids"]
    )
    assert report["evidence_artifacts"]["road_network_evidence"] is None
    assert report["evidence_summary"]["road_network_resolution"] == {
        "evidence_file_provided": False,
        "authoritative_evidence_used": False,
        "osm_fallback_used": True,
        "contradicted_resolution_used": False,
        "unknown_resolution_used": False,
        "road_arm_resolution_counts": {"osm_fallback": 4},
        "road_arm_resolution_by_id": {
            arm["road_arm_id"]: "osm_fallback"
            for arm in profile["road_detail"]["road_arms"]
        },
        "hash_bound_way_count": 0,
    }
    assert (
        report["evidence_artifacts"]["physical_cell"]["hypothesis_id"]
        == (profile["parent_physical_cell_hypothesis_id"])
    )
    assert (
        report["evidence_artifacts"]["topology_evidence"]["topology_evidence_id"]
        == profile["parent_topology_evidence_id"]
    )
    assert (
        report["evidence_artifacts"]["movement_hypotheses"]["hypothesis_set_id"]
        == profile["parent_movement_hypothesis_set_id"]
    )
    assert source.read_bytes() == before
    json.dumps(report)


def test_sumo_intersection_archetype_classify_consumes_hash_bound_road_network_evidence(
    tmp_path: Path,
) -> None:
    source = FIXTURES / "x4_signalized.osm.xml"
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = {
        "schema": "torii.road-detail-evidence-projection/v1",
        "status": "pass",
        "by_way_id": {
            "10": {
                "authority_category": "hvs",
                "network_role": "arterial",
                "functional_category": "HS III",
                "source_evidence_id": "relation-main",
                "source_relation_ids": ["relation-main"],
                "source_assignment_ids": ["assignment-main"],
                "source_sha256s": [source_sha256],
                "mapping_status": "pass",
            },
            "11": {
                "authority_category": "bezirksstrasse",
                "network_role": "collector",
                "functional_category": "ES IV",
                "source_evidence_id": "relation-minor",
                "source_relation_ids": ["relation-minor"],
                "source_assignment_ids": ["assignment-minor"],
                "source_sha256s": [source_sha256],
                "mapping_status": "pass",
            },
        },
        "conflicts": [],
        "excluded_relation_ids": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": "Classification evidence only.",
    }
    evidence_file = tmp_path / "road-network-evidence.json"
    evidence_bytes = json.dumps(evidence, sort_keys=True).encode("utf-8")
    evidence_file.write_bytes(evidence_bytes)

    report = sumo_intersection_archetype_classify(
        str(source),
        "1",
        road_network_evidence_file=str(evidence_file),
    )

    provenance = report["evidence_artifacts"]["road_network_evidence"]
    resolution = report["evidence_summary"]["road_network_resolution"]
    road_arms = report["archetype_profile"]["road_detail"]["road_arms"]
    assert provenance == {
        "source_file": str(evidence_file.resolve()),
        "source_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "schema": "torii.road-detail-evidence-projection/v1",
        "declared_status": "pass",
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "bound_osm_source_sha256": source_sha256,
        "hash_bound_way_ids": ["10", "11"],
        "mapping_status_excluded_way_ids": [],
        "unreferenced_by_local_osm_way_ids": [],
    }
    assert resolution["evidence_file_provided"] is True
    assert resolution["authoritative_evidence_used"] is True
    assert resolution["osm_fallback_used"] is False
    assert resolution["hash_bound_way_count"] == 2
    assert {arm["road_identity"]["resolution"] for arm in road_arms} == {
        "authoritative"
    }
    assert {arm["road_identity"]["authority_category"]["value"] for arm in road_arms} == {
        "hvs",
        "bezirksstrasse",
    }
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["archetype_profile"]["automatic_promotion_gate"] == "blocked"
    assert report["archetype_profile"]["source_evidence"]["road_network_evidence"] == {
        "schema": "torii.road-detail-evidence-projection/v1",
        "source_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "bound_osm_source_sha256": source_sha256,
    }


def test_sumo_intersection_archetype_classify_rejects_unbound_road_network_evidence(
    tmp_path: Path,
) -> None:
    evidence_file = tmp_path / "road-network-evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "schema": "torii.road-detail-evidence-projection/v1",
                "status": "pass",
                "by_way_id": {
                    "10": {
                        "authority_category": "hvs",
                        "network_role": "arterial",
                        "functional_category": "HS III",
                        "source_evidence_id": "relation-main",
                        "source_relation_ids": ["relation-main"],
                        "source_assignment_ids": ["assignment-main"],
                        "source_sha256s": ["0" * 64],
                        "mapping_status": "pass",
                    }
                },
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not bind local OSM way '10'"):
        sumo_intersection_archetype_classify(
            str(FIXTURES / "x4_signalized.osm.xml"),
            "1",
            road_network_evidence_file=str(evidence_file),
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (
            {
                "schema": "torii.road-detail-evidence-projection/v999",
                "status": "pass",
                "by_way_id": {},
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            },
            "schema",
        ),
        (
            {
                "schema": "torii.road-detail-evidence-projection/v1",
                "status": "pass",
                "by_way_id": [],
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            },
            "by_way_id",
        ),
    ],
)
def test_sumo_intersection_archetype_classify_rejects_invalid_road_network_evidence_shape(
    tmp_path: Path,
    payload: dict[str, object],
    error: str,
) -> None:
    evidence_file = tmp_path / "road-network-evidence.json"
    evidence_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        sumo_intersection_archetype_classify(
            str(FIXTURES / "x4_signalized.osm.xml"),
            "1",
            road_network_evidence_file=str(evidence_file),
        )


def test_sumo_intersection_archetype_classify_hashes_and_parses_one_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot.osm.xml"
    original_bytes = (FIXTURES / "x4_signalized.osm.xml").read_bytes()
    source.write_bytes(original_bytes)
    original_parser = intersection_tools.parse_osm_xml_bytes

    def replace_file_after_read(
        source_bytes: bytes,
        *,
        gzip_compressed: bool = False,
    ):
        source.write_text("<osm version='0.6'/>", encoding="utf-8")
        return original_parser(
            source_bytes,
            gzip_compressed=gzip_compressed,
        )

    monkeypatch.setattr(
        intersection_tools,
        "parse_osm_xml_bytes",
        replace_file_after_read,
    )

    report = sumo_intersection_archetype_classify(str(source), "1")

    assert report["source_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert report["archetype_profile"]["derived_alias"]["value"] == "X4"


@pytest.mark.parametrize("traffic_side", ["center", "", "RIGHT-HAND"])
def test_sumo_intersection_archetype_classify_rejects_invalid_traffic_side(
    traffic_side: str,
) -> None:
    with pytest.raises(ValueError, match="traffic_side"):
        sumo_intersection_archetype_classify(
            str(FIXTURES / "x4_signalized.osm.xml"),
            "1",
            traffic_side=traffic_side,
        )


def test_sumo_intersection_archetype_classify_rejects_missing_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="existing local file"):
        sumo_intersection_archetype_classify(str(tmp_path / "missing.osm.xml"), "1")


def test_sumo_intersection_archetype_classify_counts_roundabout_ring_gates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "four-entry-roundabout.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <bounds minlat="-0.003" minlon="-0.003" maxlat="0.003" maxlon="0.003"/>
  <node id="10" lat="0.001" lon="0"/><node id="11" lat="0" lon="0.001"/>
  <node id="12" lat="-0.001" lon="0"/><node id="13" lat="0" lon="-0.001"/>
  <node id="20" lat="0.002" lon="0"/><node id="21" lat="0" lon="0.002"/>
  <node id="22" lat="-0.002" lon="0"/><node id="23" lat="0" lon="-0.002"/>
  <way id="100"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="10"/>
    <tag k="highway" v="secondary"/><tag k="junction" v="roundabout"/><tag k="oneway" v="yes"/>
  </way>
  <way id="200"><nd ref="20"/><nd ref="10"/><tag k="highway" v="secondary"/><tag k="name" v="North"/></way>
  <way id="201"><nd ref="21"/><nd ref="11"/><tag k="highway" v="secondary"/><tag k="name" v="East"/></way>
  <way id="202"><nd ref="22"/><nd ref="12"/><tag k="highway" v="secondary"/><tag k="name" v="South"/></way>
  <way id="203"><nd ref="23"/><nd ref="13"/><tag k="highway" v="secondary"/><tag k="name" v="West"/></way>
</osm>""",
        encoding="utf-8",
    )

    report = sumo_intersection_archetype_classify(str(source), "10")
    profile = report["archetype_profile"]

    assert profile["derived_alias"]["value"] == "roundabout"
    assert profile["arm_model"]["arm_count"] == 4
    assert profile["arm_model"]["entry_count"] == 4
    assert profile["arm_model"]["exit_count"] == 4
    assert profile["dimensions"]["cell_structure"]["value"] == "ring_group"
    assert profile["dimensions"]["movement_graph_status"]["value"] == "unknown"
    assert profile["semantic_arm_evidence"]["source"] == (
        "explicit_roundabout_ring_boundary"
    )


def test_sumo_intersection_model_returns_json_compatible_ir_summary(
    tmp_path: Path,
) -> None:
    report = sumo_intersection_model(
        str(FIXTURES / "t3_priority.osm.xml"), str(tmp_path)
    )

    assert report["status"] == "pass"
    assert report["intersection_id"] == "core_1"
    assert report["approach_mode_counts"] == {"passenger": 3}
    assert report["vehicle_approach_count"] == 3
    assert report["vehicle_topology_type"] == "T3"
    assert report["legal_movement_mode_counts"] == {"passenger": 6}
    assert report["forbidden_cross_mode_movement_count"] == 0
    assert Path(report["intersection_ir_file"]).exists()
    json.dumps(report)


def test_sumo_intersection_model_reports_next_phase_fields(tmp_path: Path) -> None:
    result = sumo_intersection_model(
        str(FIXTURES / "x4_signalized.osm.xml"), str(tmp_path)
    )

    assert result["restriction_warning_count"] == 0
    assert result["custom_tllogic_applied"] is None
    assert result["direction_blocked_approach_count"] == 0


def test_sumo_intersection_validate_reports_next_phase_fields(tmp_path: Path) -> None:
    clean_result = sumo_intersection_clean(
        str(FIXTURES / "x4_signalized.osm.xml"),
        str(tmp_path / "clean"),
        compile_net=False,
    )

    result = sumo_intersection_validate(
        clean_result["intersection_ir_file"], str(tmp_path / "validate")
    )

    assert result["restriction_warning_count"] == 0
    assert result["custom_tllogic_applied"] is True
    assert result["direction_blocked_approach_count"] == 0


def test_sumo_intersection_clean_wraps_clean_intersection(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_clean(**kwargs):
        assert kwargs["osm_file"] == FIXTURES / "t3_priority.osm.xml"
        assert kwargs["output_dir"] == tmp_path
        assert kwargs["seed"] is None
        assert kwargs["compile_net"] is False
        return {"status": "blocked", "intersection_id": "core_1"}

    monkeypatch.setattr(intersection_tools, "clean_intersection", fake_clean)

    report = sumo_intersection_clean(
        str(FIXTURES / "t3_priority.osm.xml"),
        str(tmp_path),
        compile_net=False,
    )

    assert report == {"status": "blocked", "intersection_id": "core_1"}


def test_sumo_intersection_scene_workflow_delegates_with_path_and_options(
    monkeypatch, tmp_path: Path
) -> None:
    calls = {}

    def fake_workflow(prompt, output_dir, prefix, launch_netedit_after_build):
        calls.update(
            prompt=prompt,
            output_dir=output_dir,
            prefix=prefix,
            launch_netedit_after_build=launch_netedit_after_build,
        )
        return {"status": "pass"}

    monkeypatch.setattr(
        intersection_tools, "run_intersection_scene_workflow", fake_workflow
    )

    report = sumo_intersection_scene_workflow(
        "Make a four-way traffic-light intersection",
        str(tmp_path),
        prefix="demo",
        launch_netedit_after_build=True,
    )

    assert report == {"status": "pass"}
    assert calls == {
        "prompt": "Make a four-way traffic-light intersection",
        "output_dir": tmp_path,
        "prefix": "demo",
        "launch_netedit_after_build": True,
    }
