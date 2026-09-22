from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.core.hamburg_compound_tls_acceptance import (
    audit_hamburg_compound_tls_acceptance,
    build_hamburg_compound_movement_smoke_binding,
)


def _fixture(tmp_path: Path, *, extra_series_control: bool = False) -> dict[str, Path | str]:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    manifest = tmp_path / "compound.json"
    smoke = tmp_path / "smoke.json"
    source.write_text("<net/>\n", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    root = ET.Element("net")
    movements = []
    groups = []
    stable_ids = []
    for node_id in ("2349", "2394"):
        controller = f"HH_{node_id}"
        ET.SubElement(root, "tlLogic", id=controller)
        shared_key = (f"{node_id}_in_shared", 0, f"{node_id}_out_shared", 0)
        group_links: dict[str, set[tuple[str, int, str, int]]] = {}
        for connection_number in range(1, 9):
            signal_group = "K1" if connection_number <= 2 else f"K{connection_number}"
            key = (
                shared_key
                if connection_number <= 2
                else (
                    f"{node_id}_in_{connection_number}",
                    0,
                    f"{node_id}_out_{connection_number}",
                    0,
                )
            )
            group_links.setdefault(signal_group, set()).add(key)
            movement_id = f"{node_id}:{connection_number}"
            stable_ids.append(movement_id)
            movements.append(
                {
                    "official_node_id": node_id,
                    "connection_id": str(connection_number),
                    "signal_group": signal_group,
                    "official_ingress_lane": f"in-{connection_number}",
                    "official_egress_lane": f"out-{connection_number}",
                    "path_lane_ids": [f"{key[0]}_0", f"{key[2]}_0"],
                    "selected_physical_links": [_key_row(key)],
                }
            )
        for link_index, (signal_group, keys) in enumerate(sorted(group_links.items())):
            groups.append(
                {
                    "official_node_id": node_id,
                    "signal_group": signal_group,
                    "tls_id": controller,
                    "link_index": link_index,
                    "physical_links": [_key_row(key) for key in sorted(keys)],
                }
            )
            for key in sorted(keys):
                _add_edge(root, key[0], f"{key[0]}_0")
                _add_edge(root, key[2], f"{key[2]}_0")
                ET.SubElement(
                    root,
                    "connection",
                    {
                        "from": key[0],
                        "to": key[2],
                        "fromLane": "0",
                        "toLane": "0",
                        "tl": controller,
                        "linkIndex": str(link_index),
                    },
                )
    if extra_series_control:
        movement = movements[0]
        old_out = str(movement["path_lane_ids"][-1])
        old_edge = old_out.rsplit("_", 1)[0]
        mid_edge = "2349_mid"
        final_edge = "2349_out_shared"
        movement["path_lane_ids"] = ["2349_in_shared_0", f"{mid_edge}_0", f"{final_edge}_0"]
        _add_edge(root, mid_edge, f"{mid_edge}_0")
        for connection in list(root.findall("connection")):
            if connection.attrib.get("from") == "2349_in_shared" and connection.attrib.get("to") == old_edge:
                connection.attrib["to"] = mid_edge
        ET.SubElement(
            root,
            "connection",
            {
                "from": mid_edge,
                "to": final_edge,
                "fromLane": "0",
                "toLane": "0",
                "tl": "legacy",
                "linkIndex": "0",
            },
        )
        ET.SubElement(root, "tlLogic", id="legacy")

    ET.indent(root, space="  ")
    candidate.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    payload = {
        "schema_id": "torii.hamburg-compound-official-tls/v1",
        "status": "topology_ready",
        "source": {"sha256": source_sha, "immutable": True},
        "tls_derivation": {
            "schema_id": "torii.official-tls-plan-derivation.v1",
            "status": "pass",
            "plan_id": "fixture",
            "movements": movements,
        },
        "network_rebuild": {
            "source_sha256_before": source_sha,
            "source_sha256_after": source_sha,
            "source_unchanged": True,
            "plan": {"groups": groups},
        },
        "artifacts": {"network": {"sha256": candidate_sha}},
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    smoke.write_text(
        json.dumps(
            {
                "status": "pass",
                "net_sha256": candidate_sha,
                "movement_count": 16,
                "stable_movement_ids": stable_ids,
                "checks": {
                    "all_expected_vehicles_arrived": True,
                    "source_immutable": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return {
        "source": source,
        "candidate": candidate,
        "manifest": manifest,
        "smoke": smoke,
        "source_sha": source_sha,
    }


def test_acceptance_allows_same_group_sharing_and_requires_fourteen_stoplines(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    report = audit_hamburg_compound_tls_acceptance(
        source_net_file=fixture["source"],
        candidate_net_file=fixture["candidate"],
        compound_tls_manifest=fixture["manifest"],
        expected_source_sha256=str(fixture["source_sha"]),
        movement_smoke_report=fixture["smoke"],
    )
    binding = build_hamburg_compound_movement_smoke_binding(fixture["manifest"])

    assert report["status"] == "pass"
    assert report["unique_physical_stopline_count"] == 14
    assert len(report["shared_same_group_stoplines"]) == 2
    assert len(binding["movement_records"]) == 16
    assert {row["stable_movement_id"] for row in binding["movement_records"]} == {
        f"{node}:{connection}" for node in ("2349", "2394") for connection in range(1, 9)
    }


def test_acceptance_blocks_a_second_control_later_on_one_movement_path(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, extra_series_control=True)

    report = audit_hamburg_compound_tls_acceptance(
        source_net_file=fixture["source"],
        candidate_net_file=fixture["candidate"],
        compound_tls_manifest=fixture["manifest"],
        expected_source_sha256=str(fixture["source_sha"]),
        movement_smoke_report=fixture["smoke"],
    )

    assert report["status"] == "blocked"
    assert report["checks"]["no_duplicate_series_controls"] == "blocked"
    assert report["series_control_errors"]


def _add_edge(root: ET.Element, edge_id: str, lane_id: str) -> None:
    if root.find(f"edge[@id='{edge_id}']") is not None:
        return
    edge = ET.SubElement(root, "edge", id=edge_id)
    ET.SubElement(edge, "lane", id=lane_id, index="0")


def _key_row(key: tuple[str, int, str, int]) -> dict[str, str | int]:
    return {
        "from_edge": key[0],
        "from_lane": key[1],
        "to_edge": key[2],
        "to_lane": key[3],
    }
