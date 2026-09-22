import json
import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.core.teacher_corridor import build_teacher_corridor_comparison


ROOT = Path(__file__).resolve().parents[1]
NETWORKS = ROOT / "examples" / "02_one_prompt_osm_network" / "networks"


def test_ingolstadt_teacher_corridor_emits_bounded_review_package(tmp_path: Path) -> None:
    osm_file = tmp_path / "current.osm.xml"
    osm_file.write_text(
        """<osm version="0.6">
  <node id="267517510" lat="48.7726731" lon="11.4259610"/>
  <node id="crossing" lat="48.7727000" lon="11.4259700"><tag k="highway" v="crossing"/><tag k="crossing" v="traffic_signals"/></node>
  <node id="signal" lat="48.7726800" lon="11.4259800"><tag k="highway" v="traffic_signals"/></node>
  <way id="148050448"><nd ref="267517510"/><nd ref="crossing"/><tag k="highway" v="residential"/></way>
</osm>""",
        encoding="utf-8",
    )
    result = build_teacher_corridor_comparison(
        teacher_net_file=NETWORKS / "tum_ingolstadt_center_reference.net.xml",
        candidate_net_file=NETWORKS / "torii_5_5_reference_visual_detail_tls_aggregated.net.xml",
        junction_id="267517510",
        output_dir=tmp_path,
        prefix="ingolstadt_267517510",
        map_temporal_scope="current",
        osm_file=osm_file,
    )

    assert result["status"] == "pass"
    assert result["teacher_transfer_status"] == "review_required"
    assert result["promotion_decision"] == "blocked_review_required"
    assert result["comparison"]["approach_edge_equivalence_applied"] is True
    assert result["comparison"]["mismatch_fields"] == [
        "control_type",
        "has_tls",
        "internal_function_counts",
        "movement_signature_counts",
    ]
    assert result["teacher_pattern"]["internal_function_counts"] == {
        "crossing": 3,
        "internal": 12,
        "walkingarea": 3,
    }
    assert result["candidate_pattern"]["internal_function_counts"] == {
        "crossing": 0,
        "internal": 14,
        "walkingarea": 0,
    }
    assert result["map_review_readiness_status"] == "pass"
    assert result["osm_corridor_evidence"]["explicit_crossing_node_count"] == 1
    assert result["osm_corridor_evidence"]["traffic_signal_node_count"] == 1
    crossing_recommendation = next(
        item for item in result["recommendations"] if item["action"] == "plan_pedestrian_crossings"
    )
    assert crossing_recommendation["evidence_status"] == "supported_by_current_osm"

    map_evidence = json.loads(Path(result["map_review_evidence_file"]).read_text(encoding="utf-8"))
    assert map_evidence["locations"][0]["google_maps_url"].startswith(
        "https://www.google.com/maps/"
    )
    overlay = ET.parse(result["review_overlay_file"]).getroot()
    assert {element.tag for element in overlay.iter()} <= {"additional", "poi", "poly", "param"}
    assert len(overlay.findall("poly")) == 4

    decision = json.loads(
        Path(result["review_decision_template_file"]).read_text(encoding="utf-8")
    )
    assert decision["teacher_sha256"] == result["teacher_sha256"]
    assert decision["candidate_sha256"] == result["candidate_sha256"]
    assert decision["map_review_evidence_sha256"] == result["map_review_evidence_sha256"]
    review_html = Path(result["review_html_file"]).read_text(encoding="utf-8")
    assert result["teacher_sha256"] in review_html
    assert result["candidate_sha256"] in review_html

    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    artifact_paths = {item["path"] for item in manifest["artifacts"]}
    assert str(Path(result["map_review_evidence_file"]).resolve()) in artifact_paths
    assert str(Path(result["review_overlay_file"]).resolve()) in artifact_paths
    assert str(Path(result["review_html_file"]).resolve()) in artifact_paths


def test_teacher_corridor_blocks_when_candidate_junction_is_absent(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        "<net><junction id=\"other\" type=\"priority\" x=\"0\" y=\"0\"/></net>",
        encoding="utf-8",
    )

    result = build_teacher_corridor_comparison(
        teacher_net_file=NETWORKS / "tum_ingolstadt_center_reference.net.xml",
        candidate_net_file=candidate,
        junction_id="267517510",
        output_dir=tmp_path / "result",
    )

    assert result["status"] == "blocked"
    assert "no comparable" in result["error"]
    assert Path(result["report_file"]).exists()
    assert Path(result["manifest_file"]).exists()
