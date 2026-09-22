import hashlib
import json
from pathlib import Path

from torii_sumo.core.map_review import (
    build_map_review_decision_binding,
    build_map_review_evidence,
    validate_map_review_decisions,
    validate_map_review_evidence,
)


def _write_net(path: Path, *, marker: str) -> None:
    path.write_text(
        f'''<net>
  <location netOffset="0,0" convBoundary="0,0,10,10" origBoundary="0,0,10,10" projParameter="!"/>
  <junction id="a" type="priority" x="0" y="0"/>
  <param key="marker" value="{marker}"/>
</net>''',
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_location() -> dict[str, object]:
    return {
        "proposal_id": "merge-42",
        "operation": "merge_edges",
        "map_review_required": True,
        "location": {"x": 10.0, "y": 20.0, "lat": 48.765, "lon": 11.423},
        "geometry_source": "source_net",
    }


def test_map_evidence_builds_candidate_bound_human_review_links(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source, marker="source")
    _write_net(candidate, marker="candidate")

    evidence = build_map_review_evidence(
        source_net_file=source,
        candidate_net_file=candidate,
        candidate_sha256=_sha256(candidate),
        locations=[_required_location()],
        temporal_scope="current",
    )

    assert evidence["status"] == "pass"
    assert evidence["review_readiness_status"] == "pass"
    assert evidence["required_location_ids"] == ["corridor_edit:merge-42"]
    location = evidence["locations"][0]
    assert location["coordinate"]["coordinate_status"] == "explicit_wgs84"
    assert location["google_maps_url"].startswith("https://www.google.com/maps/")
    assert location["regional_map_provider"] == "Google Maps"
    assert location["candidate_sha256"] == _sha256(candidate)


def test_required_map_review_blocks_when_time_scope_or_projection_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source, marker="source")
    _write_net(candidate, marker="candidate")

    evidence = build_map_review_evidence(
        source_net_file=source,
        candidate_net_file=candidate,
        candidate_sha256=_sha256(candidate),
        locations=[
            {
                "proposal_id": "delete-1",
                "operation": "delete_edge",
                "map_review_required": True,
                "location": {"x": 4, "y": 5},
            }
        ],
        temporal_scope="unspecified",
    )

    assert evidence["review_readiness_status"] == "blocked"
    assert evidence["unavailable_required_location_ids"] == ["corridor_edit:delete-1"]
    assert evidence["google_maps_requires_time_confirmation"] == "yes"
    assert evidence["locations"][0]["google_maps_url"] == ""

    forged = json.loads(json.dumps(evidence))
    forged["review_readiness_status"] = "pass"
    forged["unavailable_required_location_ids"] = []
    forged["missing_provider_required_location_ids"] = []
    forged["google_maps_requires_time_confirmation"] = "no"
    validation = validate_map_review_evidence(
        forged,
        source_net_file=source,
        candidate_net_file=candidate,
    )
    assert validation["status"] == "blocked"
    error_codes = {item["code"] for item in validation["errors"]}
    assert "map_review_readiness_status_mismatch" in error_codes
    assert "map_review_time_confirmation_mismatch" in error_codes


def test_map_review_decision_requires_structured_observation_and_exact_binding(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    evidence_file = tmp_path / "map-review.json"
    _write_net(source, marker="source")
    _write_net(candidate, marker="candidate")
    evidence = build_map_review_evidence(
        source_net_file=source,
        candidate_net_file=candidate,
        candidate_sha256=_sha256(candidate),
        locations=[_required_location()],
        temporal_scope="current",
    )
    evidence_file.write_text(json.dumps(evidence), encoding="utf-8")
    evidence_sha256 = _sha256(evidence_file)

    contract = validate_map_review_evidence(
        evidence,
        source_net_file=source,
        candidate_net_file=candidate,
        evidence_file=evidence_file,
        evidence_sha256=evidence_sha256,
    )
    assert contract["status"] == "pass"

    decision = build_map_review_decision_binding(
        evidence,
        evidence_file=evidence_file,
        evidence_sha256=evidence_sha256,
    )
    incomplete = validate_map_review_decisions(
        decision,
        evidence=evidence,
        evidence_file=evidence_file,
        evidence_sha256=evidence_sha256,
        candidate_net_file=candidate,
    )
    assert incomplete["status"] == "blocked"
    assert "map_review_decision_not_approved" in {item["code"] for item in incomplete["errors"]}
    assert "map_review_observed_fact_required" in {item["code"] for item in incomplete["errors"]}

    decision_item = decision["decisions"][0]
    decision_item.update(
        {
            "decision": "approved",
            "observed_facts": {
                "feature_presence": "Current map shows one physical junction.",
                "geometry_connectivity": "All modeled approaches meet continuously.",
                "access_modes": "Road access is consistent with the candidate.",
                "source_limitations": "Current imagery; no historical claim is made.",
            },
            "reviewer": "human-reviewer",
            "reviewed_at": "2026-07-13T18:00:00+02:00",
        }
    )
    accepted = validate_map_review_decisions(
        decision,
        evidence=evidence,
        evidence_file=evidence_file,
        evidence_sha256=evidence_sha256,
        candidate_net_file=candidate,
    )
    assert accepted["status"] == "pass"

    decision_item["candidate_sha256"] = "0" * 64
    stale = validate_map_review_decisions(
        decision,
        evidence=evidence,
        evidence_file=evidence_file,
        evidence_sha256=evidence_sha256,
        candidate_net_file=candidate,
    )
    assert stale["status"] == "blocked"
    assert "map_review_decision_candidate_hash_mismatch" in {
        item["code"] for item in stale["errors"]
    }


def test_optional_map_review_does_not_become_an_automatic_hard_gate(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_net(source, marker="source")
    _write_net(candidate, marker="candidate")
    evidence = build_map_review_evidence(
        source_net_file=source,
        candidate_net_file=candidate,
        candidate_sha256=_sha256(candidate),
        locations=[
            {
                "proposal_id": "safe-addition",
                "operation": "add_sidewalk",
                "map_review_required": False,
                "location": {"x": 4, "y": 5},
            }
        ],
    )

    assert evidence["review_readiness_status"] == "not_required"
    result = validate_map_review_decisions(
        None,
        evidence=evidence,
        evidence_file=None,
        evidence_sha256="",
        candidate_net_file=candidate,
    )
    assert result["status"] == "pass"
