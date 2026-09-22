from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.corridor.enums import GateStatus, TrafficSide
from torii_sumo.corridor.held_out_corpus_contracts import (
    GeographicBbox,
    HeldOutCityExtract,
    HeldOutCorpusMachineManifest,
    HeldOutCorridorSelection,
    HeldOutMachineArtifactIdentity,
)
from torii_sumo.corridor.held_out_corpus_runner import (
    _segment_intersects_bbox,
    crop_city_extract,
    download_city_extract,
)
from torii_sumo.corridor.held_out_corridor_runner import (
    _classify_case,
    _connection_audit_tolerance,
)
from torii_sumo.corridor.held_out_corpus_contracts import HeldOutCorpusSpec
from torii_sumo.corridor.held_out_corpus_preregistration import (
    build_preregistered_held_out_corpus,
)
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.schema import (
    build_held_out_corpus_schema,
    build_held_out_corpus_machine_manifest_schema,
    build_held_out_corpus_machine_report_schema,
    build_held_out_corpus_snapshot_report_schema,
    build_held_out_machine_run_identity_schema,
)
from torii_sumo.corridor.run_identity import (
    CodeProducerIdentity,
    HeldOutMachineRunIdentity,
    RuntimeExecutableIdentity,
    RuntimeSupportFileIdentity,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def _selection() -> HeldOutCorridorSelection:
    bbox = GeographicBbox(west=-0.1, south=-0.1, east=0.1, north=0.1)
    payload = {
        "corridor_key": "test-crossing",
        "city_source_id": "test-city",
        "center_lat": 0.0,
        "center_lon": 0.0,
        "bbox": bbox.model_dump(mode="json", by_alias=True),
        "morphology": "multimodal",
        "preregistered_feature_targets": ("pedestrian", "rail", "bridge"),
    }
    return HeldOutCorridorSelection(
        selection_id=stable_id("scope", payload),
        **payload,
        label="Synthetic crossing corridor",
        selection_basis="Unit-test reference-complete crop.",
    )


def test_crop_keeps_segment_crossing_bbox_node_tags_and_closed_restriction(
    tmp_path: Path,
) -> None:
    source = tmp_path / "city.osm.xml"
    source.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="0" lon="-0.2"/>
  <node id="2" lat="0" lon="0.2"><tag k="highway" v="traffic_signals"/></node>
  <node id="3" lat="0.05" lon="-0.05"/>
  <node id="4" lat="0.05" lon="0.05"/>
  <node id="5" lat="0.5" lon="0.5"/>
  <node id="6" lat="0.6" lon="0.6"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/><tag k="sidewalk" v="both"/>
    <tag k="bridge" v="yes"/>
  </way>
  <way id="12">
    <nd ref="3"/><nd ref="4"/><tag k="railway" v="tram"/>
  </way>
  <way id="11">
    <nd ref="5"/><nd ref="6"/><tag k="highway" v="secondary"/>
  </way>
  <relation id="20">
    <member type="way" ref="10" role="from"/>
    <member type="way" ref="12" role="to"/>
    <member type="node" ref="3" role="via"/>
    <tag k="type" v="restriction"/><tag k="restriction" v="no_left_turn"/>
  </relation>
  <relation id="21">
    <member type="way" ref="10" role="from"/>
    <member type="way" ref="11" role="to"/>
    <member type="node" ref="2" role="via"/>
    <tag k="type" v="restriction"/><tag k="restriction" v="no_right_turn"/>
  </relation>
</osm>
""",
        encoding="utf-8",
    )

    snapshots = crop_city_extract(
        source,
        city_extract_sha256=file_sha256(source),
        selections=(_selection(),),
        output_dir=tmp_path / "crops",
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.status is GateStatus.PASS
    assert snapshot.selected_way_count == 2
    assert snapshot.selected_restriction_count == 1
    assert snapshot.reference_complete is True
    assert snapshot.observed_feature_counts["pedestrian"] >= 1
    assert snapshot.observed_feature_counts["rail"] == 1
    assert snapshot.observed_feature_counts["bridge"] == 1
    root = ET.parse(snapshot.path).getroot()
    assert {way.attrib["id"] for way in root.findall("way")} == {"10", "12"}
    assert {relation.attrib["id"] for relation in root.findall("relation")} == {
        "20"
    }
    signal = next(node for node in root.findall("node") if node.attrib["id"] == "2")
    assert {(tag.attrib["k"], tag.attrib["v"]) for tag in signal.findall("tag")} == {
        ("highway", "traffic_signals")
    }


def test_crop_closes_a_touched_osm_roundabout_component(tmp_path: Path) -> None:
    source = tmp_path / "roundabout-city.osm.xml"
    source.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="0" lon="-0.2"/>
  <node id="2" lat="0" lon="0.2"/>
  <node id="3" lat="0.2" lon="0.2"/>
  <node id="4" lat="0.2" lon="-0.2"/>
  <node id="5" lat="0.5" lon="0.5"/>
  <node id="6" lat="0.6" lon="0.6"/>
  <way id="100"><nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="tertiary"/><tag k="junction" v="roundabout"/>
    <tag k="sidewalk" v="both"/>
  </way>
  <way id="101"><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="tertiary"/><tag k="junction" v="roundabout"/>
  </way>
  <way id="102"><nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="tertiary"/><tag k="junction" v="roundabout"/>
  </way>
  <way id="103"><nd ref="4"/><nd ref="1"/>
    <tag k="highway" v="tertiary"/><tag k="junction" v="roundabout"/>
  </way>
  <way id="200"><nd ref="5"/><nd ref="6"/>
    <tag k="highway" v="tertiary"/><tag k="junction" v="roundabout"/>
  </way>
</osm>
""",
        encoding="utf-8",
    )
    base = _selection()
    payload = base.model_dump(mode="json", by_alias=True)
    payload["preregistered_feature_targets"] = ("pedestrian",)
    payload["selection_id"] = stable_id(
        "scope",
        {
            key: payload[key]
            for key in (
                "corridor_key",
                "city_source_id",
                "center_lat",
                "center_lon",
                "bbox",
                "morphology",
                "preregistered_feature_targets",
            )
        },
    )
    selection = HeldOutCorridorSelection.model_validate(payload)

    snapshot = crop_city_extract(
        source,
        city_extract_sha256=file_sha256(source),
        selections=(selection,),
        output_dir=tmp_path / "roundabout-crops",
    )[0]

    assert snapshot.status is GateStatus.PASS
    assert snapshot.selected_way_count == 4
    root = ET.parse(snapshot.path).getroot()
    assert {way.attrib["id"] for way in root.findall("way")} == {
        "100",
        "101",
        "102",
        "103",
    }


def test_download_reuses_only_provider_verified_content(tmp_path: Path) -> None:
    payload = b"frozen-city-extract"
    target = tmp_path / "city.osm.pbf"
    target.write_bytes(payload)
    source = HeldOutCityExtract(
        source_id="test-city-20260711",
        city_group="test-city",
        traffic_side=TrafficSide.RIGHT,
        pbf_url="https://example.invalid/test.osm.pbf",
        checksum_url="https://example.invalid/CHECKSUM.txt",
        provider_md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        expected_content_length_bytes=len(payload),
        expected_last_modified_http="Sat, 11 Jul 2026 00:00:00 GMT",
        expected_etag='"test-etag"',
    )

    result = download_city_extract(
        source,
        destination=target,
        user_agent="unit-test",
        timeout_seconds=1,
    )

    assert result.status is GateStatus.PASS
    assert result.source_reused is True
    assert result.sha256 == hashlib.sha256(payload).hexdigest()


def test_segment_crossing_bbox_is_selected_without_an_internal_vertex() -> None:
    bbox = GeographicBbox(west=-0.1, south=-0.1, east=0.1, north=0.1)
    assert _segment_intersects_bbox((-0.2, 0.0), (0.2, 0.0), bbox)
    assert not _segment_intersects_bbox((-0.2, 0.2), (0.2, 0.2), bbox)


def test_invalid_selection_identity_is_rejected() -> None:
    payload = _selection().model_dump(mode="json", by_alias=True)
    payload["center_lat"] = 0.01
    with pytest.raises(ValueError, match="selection_id"):
        HeldOutCorridorSelection.model_validate(payload)


def test_held_out_corpus_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.held-out-corpus.v1.schema.json": (
            build_held_out_corpus_schema()
        ),
        "torii.corridor.held-out-corpus-snapshot-report.v1.schema.json": (
            build_held_out_corpus_snapshot_report_schema()
        ),
        "torii.corridor.held-out-corpus-machine-report.v2.schema.json": (
            build_held_out_corpus_machine_report_schema()
        ),
        "torii.corridor.held-out-machine-run-identity.v1.schema.json": (
            build_held_out_machine_run_identity_schema()
        ),
        "torii.corridor.held-out-corpus-machine-manifest.v2.schema.json": (
            build_held_out_corpus_machine_manifest_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected


def test_held_out_run_identity_is_content_derived_and_fail_closed() -> None:
    producer = CodeProducerIdentity(
        repository_url="https://github.com/Tarard/Torii-SUMO.git",
        revision="a" * 40,
        tree_revision="b" * 40,
        branch="codex/test",
        working_tree_clean=True,
    )
    runtime_tools = (
        RuntimeExecutableIdentity(
            name="netconvert",
            path="C:/SUMO/netconvert.exe",
            version="1.27.1",
            sha256="c" * 64,
        ),
    )
    support_files = (
        RuntimeSupportFileIdentity(
            name="osmNetconvert.typ.xml",
            path="C:/SUMO/osmNetconvert.typ.xml",
            sha256="d" * 64,
        ),
    )
    identity = HeldOutMachineRunIdentity.build(
        producer=producer,
        entrypoint="plugins/torii-sumo/scripts/build_held_out_corridor_evidence.py",
        toolchain_id=stable_id("toolchain", {"fixture": 1}),
        toolchain_lock_sha256="e" * 64,
        platform_name="Windows-test",
        python_version="3.12.10",
        runtime_dependencies={"torii-sumo": "1.0.2"},
        runtime_tools=runtime_tools,
        support_files=support_files,
        selected_corridor_keys=("alpha", "beta"),
        timeout_seconds=180.0,
        blinding_seed_sha256="f" * 64,
    )

    assert identity.run_identity_id == stable_id(
        "toolchain",
        identity.identity_payload(),
    )
    tampered = identity.model_dump(mode="json", by_alias=True)
    tampered["timeout_seconds"] = 181.0
    with pytest.raises(ValueError, match="run_identity_id"):
        HeldOutMachineRunIdentity.model_validate(tampered)


def test_machine_manifest_requires_provenance_artifact_closure() -> None:
    producer = CodeProducerIdentity(
        repository_url="https://github.com/Tarard/Torii-SUMO.git",
        revision="a" * 40,
        tree_revision="b" * 40,
        branch="codex/test",
        working_tree_clean=True,
    )
    toolchain_path = "C:/repo/toolchain.lock.json"
    identity_path = "C:/output/run-identity.json"
    artifacts = (
        HeldOutMachineArtifactIdentity(
            path=toolchain_path,
            sha256="c" * 64,
        ),
        HeldOutMachineArtifactIdentity(
            path=identity_path,
            sha256="d" * 64,
        ),
    )
    payload = {
        "corpus_id": stable_id("manifest", {"fixture": "corpus"}),
        "evidence_build_status": GateStatus.BLOCKED,
        "toolchain_lock_path": toolchain_path,
        "toolchain_lock_sha256": "c" * 64,
        "run_identity_id": stable_id("toolchain", {"fixture": "run"}),
        "run_identity_path": identity_path,
        "run_identity_sha256": "d" * 64,
        "producer": producer,
        "artifacts": artifacts,
    }

    manifest = HeldOutCorpusMachineManifest(**payload)

    assert len(manifest.artifacts) == 2
    payload["artifacts"] = artifacts[:1]
    with pytest.raises(ValueError, match="does not close provenance artifacts"):
        HeldOutCorpusMachineManifest(**payload)


def test_real_held_out_corpus_is_preregistered_and_deterministic() -> None:
    corpus_path = BENCHMARK_DIR / "held_out_corpus.v1.json"
    policy_path = BENCHMARK_DIR / "held_out_review_preregistration.v1.json"
    benchmark_path = BENCHMARK_DIR / "benchmark.v1.json"
    corpus = HeldOutCorpusSpec.model_validate_json(
        corpus_path.read_text(encoding="utf-8")
    )
    regenerated = build_preregistered_held_out_corpus(
        held_out_review_policy_file=policy_path,
        parent_benchmark_file=benchmark_path,
    )

    assert corpus == regenerated
    assert corpus.held_out_review_policy_sha256 == file_sha256(policy_path)
    assert len(corpus.corridors) == 30
    assert len(corpus.city_extracts) == 6
    assert {source.traffic_side for source in corpus.city_extracts} == {
        TrafficSide.RIGHT,
        TrafficSide.LEFT,
    }
    assert len({case.morphology for case in corpus.corridors}) >= 6
    assert {
        feature
        for case in corpus.corridors
        for feature in case.preregistered_feature_targets
    } >= {"pedestrian", "bicycle", "ramp", "rail", "bridge", "tunnel"}


def test_safety_coverage_gap_is_ambiguous_not_a_claimed_defect() -> None:
    label, categories, _passed, unresolved = _classify_case(
        build_report={"status": "pass"},
        load_report={"status": "pass"},
        connection_report={"status": "pass"},
        calibration_status=GateStatus.PASS,
        safety_status=GateStatus.BLOCKED,
        safety_categories=("controlled_link_outside_independent_conflict_model",),
        reproducibility_status=GateStatus.PASS,
        applicability=SimpleNamespace(decision="out-of-domain", findings=()),
        routeability={"status": "pass"},
    )

    assert label == "ambiguous"
    assert "controlled_link_outside_independent_conflict_model" in categories
    assert "independent_safety" in unresolved


def test_confirmed_protected_green_conflict_is_a_machine_defect() -> None:
    label, _categories, _passed, _unresolved = _classify_case(
        build_report={"status": "pass"},
        load_report={"status": "pass"},
        connection_report={"status": "pass"},
        calibration_status=GateStatus.PASS,
        safety_status=GateStatus.BLOCKED,
        safety_categories=("protected_green_movement_conflict",),
        reproducibility_status=GateStatus.PASS,
        applicability=SimpleNamespace(decision="in-domain", findings=()),
        routeability={"status": "pass"},
    )

    assert label == "defect"


def test_empty_movement_path_permission_is_a_machine_defect() -> None:
    label, categories, _passed, _unresolved = _classify_case(
        build_report={"status": "pass"},
        load_report={"status": "pass"},
        connection_report={"status": "pass"},
        calibration_status=GateStatus.PASS,
        safety_status=GateStatus.BLOCKED,
        safety_categories=("movement_path_permission_empty",),
        reproducibility_status=GateStatus.PASS,
        applicability=SimpleNamespace(decision="in-domain", findings=()),
        routeability={"status": "pass"},
    )

    assert label == "defect"
    assert "movement_path_permission_empty" in categories


def test_nonreproducible_network_is_a_machine_defect() -> None:
    label, categories, _passed, unresolved = _classify_case(
        build_report={"status": "pass"},
        load_report={"status": "pass"},
        connection_report={"status": "pass"},
        calibration_status=GateStatus.PASS,
        safety_status=GateStatus.PASS,
        safety_categories=(),
        reproducibility_status=GateStatus.BLOCKED,
        applicability=SimpleNamespace(decision="in-domain", findings=()),
        routeability={"status": "pass"},
    )

    assert label == "defect"
    assert "normalized_net_replay_mismatch" in categories
    assert "reproducibility" in unresolved


def test_connection_audit_uses_source_calibration_when_available() -> None:
    tolerance, source = _connection_audit_tolerance(
        SimpleNamespace(endpoint_tolerance_m=0.038742)
    )

    assert tolerance == 0.038742
    assert source == "source_baseline_calibration"


def test_blocked_calibration_has_diagnostic_only_fallback() -> None:
    tolerance, source = _connection_audit_tolerance(
        SimpleNamespace(endpoint_tolerance_m=None)
    )

    assert tolerance == 2.0
    assert source == "diagnostic_fallback_due_blocked_calibration"


def test_sydney_probe_evidence_remains_fail_closed() -> None:
    evidence = json.loads(
        (
            BENCHMARK_DIR / "evidence" / "sydney_probe_20260714.v1.json"
        ).read_text(encoding="utf-8")
    )
    corpus = HeldOutCorpusSpec.model_validate_json(
        (BENCHMARK_DIR / "held_out_corpus.v1.json").read_text(encoding="utf-8")
    )

    assert evidence["corpus_id"] == corpus.corpus_id
    assert len(evidence["corridor_snapshots"]) == 5
    assert all(item["reference_complete"] for item in evidence["corridor_snapshots"])
    assert evidence["harbour_bridge_machine_evidence"]["machine_label"] == (
        "ambiguous"
    )
    assert evidence["harbour_bridge_machine_evidence"]["connection_mode"][
        "structural_failure_count"
    ] == 0
    replay = evidence["harbour_bridge_machine_evidence"]["netconvert_replay"]
    assert replay["status"] == "pass"
    assert replay["reproducible_semantics"] is True
    assert replay["primary_normalized_sha256"] == replay[
        "replay_normalized_sha256"
    ]
    assert evidence["harbour_bridge_machine_evidence"]["independent_safety"][
        "status"
    ] == "blocked"
    broad_phase = evidence["harbour_bridge_machine_evidence"][
        "independent_safety"
    ]["broad_phase"]
    assert broad_phase["name"] == "aabb-sweep-v1"
    assert broad_phase["evaluated_geometry_pair_count"] < broad_phase[
        "geometry_pair_count"
    ]
    assert broad_phase[
        "conflicts_exactly_equal_to_saved_exhaustive_result"
    ] is True
    assert evidence["review_state"]["reviewer_visible_html_prepared"] is True
    assert evidence["review_state"]["display_only_overlay_validated"] is True
    assert evidence["review_state"]["human_review_decision_count"] == 0
    assert evidence["review_state"]["automatic_promotion_gate"] == "blocked"
    assert evidence["claims_not_supported"]


def test_full_held_out_snapshot_evidence_closes_identity_not_model_quality() -> None:
    evidence = json.loads(
        (
            BENCHMARK_DIR
            / "evidence"
            / "held_out_corpus_snapshot_20260714.v1.json"
        ).read_text(encoding="utf-8")
    )
    corpus_path = BENCHMARK_DIR / "held_out_corpus.v1.json"
    corpus = HeldOutCorpusSpec.model_validate_json(
        corpus_path.read_text(encoding="utf-8")
    )

    assert evidence["corpus_id"] == corpus.corpus_id
    assert evidence["corpus_spec_sha256"] == file_sha256(corpus_path)
    assert evidence["status"] == "pass"
    assert evidence["blockers"] == []
    closure = evidence["identity_closure"]
    assert closure["city_extract_count"] == len(corpus.city_extracts) == 6
    assert closure["corridor_count"] == len(corpus.corridors) == 30
    assert closure["reference_complete_corridor_count"] == 30
    assert closure["unconfirmed_preregistered_feature_case_count"] == 0
    assert closure["manifest_hash_failure_count"] == 0
    assert {item["source_id"] for item in evidence["city_extracts"]} == {
        source.source_id for source in corpus.city_extracts
    }
    assert all(
        item["provider_identity_matched"] for item in evidence["city_extracts"]
    )
    assert "All 30 SUMO networks are correctly modeled" in evidence[
        "claims_not_supported"
    ]
