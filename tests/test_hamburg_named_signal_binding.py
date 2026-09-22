from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_named_signal_binding import (
    HamburgSignalBindingError,
    _load_compound_tls_manifest,
    materialize_hamburg_named_signal_binding,
)


def _write_inputs(tmp_path: Path, *, mismatch: bool = False) -> tuple[Path, dict[str, Path], list[Path]]:
    net = tmp_path / "candidate.net.xml"
    net.write_text(
        """<net>
  <tlLogic id="hh-map-2349" type="static" programID="structural-all-red" offset="0"/>
  <tlLogic id="hh-map-2394" type="static" programID="structural-all-red" offset="0"/>
  <connection from="a" to="b" fromLane="0" toLane="0" tl="hh-map-2349" linkIndex="0"/>
  <connection from="c" to="d" fromLane="1" toLane="0" tl="hh-map-2394" linkIndex="2"/>
</net>\n""",
        encoding="utf-8",
    )
    manifests: dict[str, Path] = {}
    streams: list[dict[str, object]] = []
    for node_id, connection_id, ingress, egress, link_index, group, physical_key in (
        ("2349", "2", "2", "11", 0, "K1", ("a", 0, "b", 0)),
        ("2394", "1", "10", "9", 2, "K6", ("c", 1, "d", 0)),
    ):
        manifest = tmp_path / f"{node_id}.manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "node_id": node_id,
                    "controller_id": f"hh-map-{node_id}",
                    "movements": [
                        {
                            "connection_id": connection_id,
                            "ingress_lane_id": "wrong" if mismatch and node_id == "2349" else ingress,
                            "egress_lane_id": egress,
                            "link_index": link_index,
                            "topology_control_key": (
                                "P_K1__S_NONE" if node_id == "2349" else "P_K6__S_K7"
                            ),
                            "from_edge": physical_key[0],
                            "from_lane": physical_key[1],
                            "to_edge": physical_key[2],
                            "to_lane": physical_key[3],
                            "primary_motor_groups": [group],
                            "secondary_motor_groups": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        manifests[node_id] = manifest
        streams.append(
            {
                "stream_id": 100 + int(node_id),
                "node_id": node_id,
                "connection_id": connection_id,
                "ingress_lane_id": ingress,
                "egress_lane_id": egress,
                "signal_group": group,
                "layer_name": "primary_signal",
                "lane_type": "KFZ",
            }
        )
    stream_file = tmp_path / "streams.json"
    stream_file.write_text(json.dumps({"schema": "test", "streams": streams}), encoding="utf-8")
    return net, manifests, [stream_file]


def test_official_streams_bind_to_controller_link_indices(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["status"] == "partial"
    assert report["execution_gate"] == "pass"
    assert report["automatic_promotion_gate"] == "blocked"
    assert len(json.loads(Path(report["binding_artifact"]["path"]).read_text(encoding="utf-8"))["bindings"]) == 2
    assert report["node_reports"]["2349"]["bound_count"] == 1
    assert report["node_reports"]["2349"]["official_movement_physical_key_parity"]["status"] == "pass"


def test_compound_tls_evidence_binds_final_osm_connections(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    net.write_text(
        """<net>
  <tlLogic id="HH_2349" type="static" programID="structural-all-red" offset="0"/>
  <tlLogic id="HH_2394" type="static" programID="structural-all-red" offset="0"/>
  <connection from="osm-a" to="osm-b" fromLane="0" toLane="0" tl="HH_2349" linkIndex="0"/>
  <connection from="osm-c" to="osm-d" fromLane="1" toLane="0" tl="HH_2394" linkIndex="2"/>
</net>\n""",
        encoding="utf-8",
    )
    compound = tmp_path / "compound.manifest.json"
    compound.write_text(
        json.dumps(
            {
                "schema_id": "torii.hamburg-compound-official-tls/v1",
                "status": "topology_ready",
                "tls_derivation": {
                    "schema_id": "torii.official-tls-plan-derivation.v1",
                    "status": "pass",
                    "movements": [
                        {
                            "official_node_id": "2349",
                            "connection_id": "2",
                            "signal_group": "P_K1__S_NONE",
                            "official_ingress_lane": "2",
                            "official_egress_lane": "11",
                            "selected_physical_links": [
                                {"from_edge": "osm-a", "from_lane": 0, "to_edge": "osm-b", "to_lane": 0},
                            ],
                        },
                        {
                            "official_node_id": "2394",
                            "connection_id": "1",
                            "signal_group": "P_K6__S_K7",
                            "official_ingress_lane": "10",
                            "official_egress_lane": "9",
                            "selected_physical_links": [
                                {"from_edge": "osm-c", "from_lane": 1, "to_edge": "osm-d", "to_lane": 0}
                            ],
                        },
                    ],
                },
                "network_rebuild": {
                    "plan": {
                        "groups": [
                            {
                                "official_node_id": "2349",
                                "signal_group": "P_K1__S_NONE",
                                "tls_id": "HH_2349",
                                "link_index": 0,
                                "physical_links": [
                                    {"from_edge": "osm-a", "from_lane": 0, "to_edge": "osm-b", "to_lane": 0},
                                ],
                            },
                            {
                                "official_node_id": "2394",
                                "signal_group": "P_K6__S_K7",
                                "tls_id": "HH_2394",
                                "link_index": 2,
                                "physical_links": [
                                    {"from_edge": "osm-c", "from_lane": 1, "to_edge": "osm-d", "to_lane": 0}
                                ],
                            },
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "compound-out",
        compound_tls_manifest=compound,
    )

    assert report["execution_gate"] == "pass"
    assert report["source"]["compound_tls_manifest"]["path"] == str(compound.resolve())
    parity = report["node_reports"]["2349"]["official_movement_physical_key_parity"]
    assert (parity["evidence_mode"], parity["expected_count"], parity["candidate_count"]) == (
        "compound_osm_tls_plan",
        1,
        1,
    )
    bindings = json.loads(Path(report["binding_artifact"]["path"]).read_text(encoding="utf-8"))["bindings"]
    binding = next(row for row in bindings if row["node_id"] == "2349")
    assert (binding["controller_id"], binding["link_index"], len(binding["candidate_connections"])) == (
        "HH_2349",
        0,
        1,
    )

    manifest = json.loads(manifests["2349"].read_text(encoding="utf-8"))
    manifest["movements"][0]["topology_control_key"] = "P_WRONG__S_NONE"
    manifests["2349"].write_text(json.dumps(manifest), encoding="utf-8")
    blocked = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "compound-identity-mismatch",
        compound_tls_manifest=compound,
    )
    assert blocked["execution_gate"] == "blocked"
    assert any(
        error["code"] == "compound_tls_movement_identity_mismatch"
        and error["mismatches"] == ["topology_control_key"]
        for error in blocked["errors"]
    )


def test_compound_tls_rejects_one_movement_bound_to_two_physical_links(
    tmp_path: Path,
) -> None:
    compound = tmp_path / "compound.manifest.json"
    links = [
        {"from_edge": "a", "from_lane": 0, "to_edge": "b", "to_lane": 0},
        {"from_edge": "b", "from_lane": 0, "to_edge": "c", "to_lane": 0},
    ]
    compound.write_text(
        json.dumps(
            {
                "schema_id": "torii.hamburg-compound-official-tls/v1",
                "status": "topology_ready",
                "tls_derivation": {
                    "schema_id": "torii.official-tls-plan-derivation.v1",
                    "status": "pass",
                    "movements": [
                        {
                            "official_node_id": "2349",
                            "connection_id": "2",
                            "signal_group": "K1",
                            "official_ingress_lane": "1",
                            "official_egress_lane": "2",
                            "selected_physical_links": links,
                        }
                    ],
                },
                "network_rebuild": {
                    "plan": {
                        "groups": [
                            {
                                "official_node_id": "2349",
                                "signal_group": "K1",
                                "tls_id": "HH_2349",
                                "link_index": 0,
                                "physical_links": links,
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HamburgSignalBindingError, match="exactly one physical stop-line"):
        _load_compound_tls_manifest(compound)


def test_mismatched_lane_identity_blocks_binding(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path, mismatch=True)
    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["status"] == "blocked"
    assert report["execution_gate"] == "blocked"
    assert any(error["code"] == "stream_movement_mismatch" for error in report["errors"])


def test_missing_2403_is_explicit_non_promoting_partial(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
        required_node_ids=("2349", "2394", "2403"),
    )

    assert report["missing_official_signal_node_ids"] == ["2403"]
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["gates"]["2403_official_signal_asset"] == "blocked"


def test_extra_controlled_physical_connection_blocks_exact_parity(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    text = net.read_text(encoding="utf-8").replace(
        "</net>",
        '<connection from="extra" to="out" fromLane="0" toLane="0" '
        'tl="hh-map-2349" linkIndex="0"/>\n</net>',
    )
    net.write_text(text, encoding="utf-8")

    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["execution_gate"] == "blocked"
    parity = report["node_reports"]["2349"]["official_movement_physical_key_parity"]
    assert parity["status"] == "blocked"
    assert parity["unexpected_physical_keys"] == [
        {"from": "extra", "fromLane": 0, "to": "out", "toLane": 0}
    ]


def test_duplicate_official_connection_id_blocks_binding(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    manifest = json.loads(manifests["2349"].read_text(encoding="utf-8"))
    manifest["movements"][0]["connection_id"] = "movement-a"
    manifest["movements"].append(dict(manifest["movements"][0]))
    manifests["2349"].write_text(json.dumps(manifest), encoding="utf-8")
    streams = json.loads(stream_files[0].read_text(encoding="utf-8"))
    streams["streams"][0]["connection_id"] = "movement-a"
    stream_files[0].write_text(json.dumps(streams), encoding="utf-8")

    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["execution_gate"] == "blocked"
    error = next(error for error in report["errors"] if error["code"] == "duplicate_official_connection_id")
    assert error["connection_ids"] == ["movement-a"]


def test_legacy_manifest_without_candidate_physical_key_is_blocked(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    manifest = json.loads(manifests["2349"].read_text(encoding="utf-8"))
    for key in ("from_edge", "from_lane", "to_edge", "to_lane"):
        manifest["movements"][0].pop(key)
    manifests["2349"].write_text(json.dumps(manifest), encoding="utf-8")

    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["execution_gate"] == "blocked"
    assert any(error["code"] == "official_movement_physical_key_missing" for error in report["errors"])


def test_same_candidate_physical_connection_at_extra_link_index_blocks_binding(tmp_path: Path) -> None:
    net, manifests, stream_files = _write_inputs(tmp_path)
    text = net.read_text(encoding="utf-8").replace(
        "</net>",
        '<connection from="a" to="b" fromLane="0" toLane="0" '
        'tl="hh-map-2349" linkIndex="1"/>\n</net>',
    )
    net.write_text(text, encoding="utf-8")

    report = materialize_hamburg_named_signal_binding(
        net_file=net,
        intersection_manifests=manifests,
        signal_stream_files=stream_files,
        output_dir=tmp_path / "out",
    )

    assert report["execution_gate"] == "blocked"
    parity = report["node_reports"]["2349"]["official_movement_physical_key_parity"]
    assert parity["candidate_count"] == 2
    assert parity["candidate_unique_count"] == 1
    assert parity["duplicate_physical_connections"] == [
        {
            "from": "a",
            "fromLane": 0,
            "to": "b",
            "toLane": 0,
            "multiplicity": 2,
            "link_indices": [0, 1],
        }
    ]
