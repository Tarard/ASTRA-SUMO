from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.corridor.enums import GateStatus
from torii_sumo.corridor.plainxml_normalization import normalize_osm_plainxml_bundle
from torii_sumo.corridor.schema import build_plainxml_normalization_report_schema


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _write_bundle(
    prefix: Path,
    *,
    closed_roundabout: bool = True,
    first_node_type: str = "priority",
    roundabout_records: str = "",
    turnaround: bool = False,
    compliant_policy: bool = True,
) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    nodes = [
        f'<node id="n0" x="0" y="0" type="{first_node_type}"/>',
        '<node id="n1" x="10" y="0" type="priority"/>',
        '<node id="n2" x="5" y="10" type="priority"/>',
    ]
    edges = [
        '<edge id="e0" from="n0" to="n1"><param key="junction" value="roundabout"/></edge>',
        '<edge id="e1" from="n1" to="n2"><param key="junction" value="roundabout"/></edge>',
    ]
    if closed_roundabout:
        edges.append(
            '<edge id="e2" from="n2" to="n0"><param key="junction" value="roundabout"/></edge>'
        )
    if turnaround:
        edges.append('<edge id="reverse" from="n1" to="n0"/>')

    (Path(f"{prefix}.nod.xml")).write_text(
        "<nodes>" + "".join(nodes) + "</nodes>", encoding="utf-8"
    )
    (Path(f"{prefix}.edg.xml")).write_text(
        "<edges>" + "".join(edges) + roundabout_records + "</edges>",
        encoding="utf-8",
    )
    connections = '<connection from="e0" to="reverse" fromLane="0" toLane="0"/>' if turnaround else ""
    (Path(f"{prefix}.con.xml")).write_text(
        f"<connections>{connections}</connections>", encoding="utf-8"
    )
    (Path(f"{prefix}.tll.xml")).write_text("<tlLogics/>", encoding="utf-8")
    (Path(f"{prefix}.typ.xml")).write_text("<types/>", encoding="utf-8")
    policy = (
        """
<output><plain-output.lanes value="true"/></output>
<processing><roundabouts.guess value="false"/><lefthand value="true"/></processing>
<junctions><no-turnarounds value="true"/></junctions>
<random_number><seed value="42"/></random_number>
"""
        if compliant_policy
        else "<output><plain-output.lanes value=\"true\"/></output>"
    )
    (Path(f"{prefix}.netccfg")).write_text(
        f"<netconvertConfiguration>{policy}</netconvertConfiguration>", encoding="utf-8"
    )


def test_normalization_makes_safe_roundabout_replays_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first" / "plain"
    second = tmp_path / "second" / "plain"
    _write_bundle(
        first,
        first_node_type="right_before_left",
        roundabout_records=(
            '<roundabout nodes="n0" edges="e0"/>'
            '<roundabout nodes="n1 n2" edges="e1 e2"/>'
        ),
    )
    _write_bundle(second, roundabout_records="")
    first_source = {suffix: Path(f"{first}{suffix}").read_bytes() for suffix in (".nod.xml", ".edg.xml")}
    second_source = {suffix: Path(f"{second}{suffix}").read_bytes() for suffix in (".nod.xml", ".edg.xml")}

    first_report = normalize_osm_plainxml_bundle(
        first,
        tmp_path / "first" / "normalized",
        tmp_path / "first" / "normalization.json",
    )
    second_report = normalize_osm_plainxml_bundle(
        second,
        tmp_path / "second" / "normalized",
        tmp_path / "second" / "normalization.json",
    )

    assert first_report.status is GateStatus.PASS
    assert second_report.status is GateStatus.PASS
    assert first_report.output_bundle_sha256 == second_report.output_bundle_sha256
    assert first_report.canonical_roundabout_records[0].edge_ids == ("e0", "e1", "e2")
    assert first_report.canonical_roundabout_records[0].node_ids == ("n0", "n1", "n2")
    assert [change.node_id for change in first_report.node_type_changes] == ["n0"]
    assert second_report.node_type_changes == ()
    assert "<roundabout nodes=\"n0 n1 n2\" edges=\"e0 e1 e2\"" in Path(
        f"{tmp_path / 'first' / 'normalized'}.edg.xml"
    ).read_text(encoding="utf-8")
    for suffix in (".nod.xml", ".edg.xml", ".con.xml", ".tll.xml", ".typ.xml"):
        assert Path(f"{tmp_path / 'first' / 'normalized'}{suffix}").read_bytes() == Path(
            f"{tmp_path / 'second' / 'normalized'}{suffix}"
        ).read_bytes()
    assert first_source == {
        suffix: Path(f"{first}{suffix}").read_bytes() for suffix in first_source
    }
    assert second_source == {
        suffix: Path(f"{second}{suffix}").read_bytes() for suffix in second_source
    }


def test_normalization_blocks_open_roundabout_and_removes_stale_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source" / "plain"
    output = tmp_path / "candidate" / "normalized"
    _write_bundle(source, closed_roundabout=False)
    stale = Path(f"{output}.nod.xml")
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")

    report = normalize_osm_plainxml_bundle(
        source,
        output,
        tmp_path / "candidate" / "normalization.json",
    )

    assert report.status is GateStatus.BLOCKED
    assert "roundabout_component_not_directed_simple_cycle" in report.blockers
    assert report.output_bundle_sha256 is None
    assert report.rebuild_arguments == ()
    assert not stale.exists()
    assert all(artifact.output_path is None for artifact in report.artifacts)


def test_normalization_preserves_and_records_upstream_turnarounds(tmp_path: Path) -> None:
    source = tmp_path / "source" / "plain"
    _write_bundle(source, turnaround=True)

    report = normalize_osm_plainxml_bundle(
        source,
        tmp_path / "candidate" / "normalized",
        tmp_path / "candidate" / "normalization.json",
    )

    assert report.status is GateStatus.PASS
    assert report.turnaround_connection_signatures == (
        "from=e0|fromLane=0|to=reverse|toLane=0",
    )
    assert '<connection from="e0" to="reverse"' in Path(
        f"{tmp_path / 'candidate' / 'normalized'}.con.xml"
    ).read_text(encoding="utf-8")


def test_normalization_accepts_unique_permission_partitioned_cycles(tmp_path: Path) -> None:
    source = tmp_path / "source" / "plain"
    _write_bundle(source)
    edge_path = Path(f"{source}.edg.xml")
    edge_path.write_text(
        """<edges>
<edge id="motor0" from="n0" to="n1" disallow="pedestrian"><param key="junction" value="roundabout"/></edge>
<edge id="motor1" from="n1" to="n2" disallow="pedestrian"><param key="junction" value="roundabout"/></edge>
<edge id="motor2" from="n2" to="n0" disallow="pedestrian"><param key="junction" value="roundabout"/></edge>
<edge id="walk0" from="n1" to="n0" allow="pedestrian"><param key="junction" value="roundabout"/></edge>
<edge id="walk1" from="n2" to="n1" allow="pedestrian"><param key="junction" value="roundabout"/></edge>
<edge id="walk2" from="n0" to="n2" allow="pedestrian"><param key="junction" value="roundabout"/></edge>
<roundabout nodes="n0" edges="motor0 walk0"/>
</edges>""",
        encoding="utf-8",
    )

    report = normalize_osm_plainxml_bundle(
        source,
        tmp_path / "candidate" / "normalized",
        tmp_path / "candidate" / "normalization.json",
    )

    assert report.status is GateStatus.PASS
    assert len(report.roundabout_components) == 1
    component = report.roundabout_components[0]
    assert component.directed_simple_cycle is False
    assert component.decomposition_method == "permission_partitioned_directed_cycles"
    assert [cycle.edge_ids for cycle in component.canonical_cycles] == [
        ("motor0", "motor1", "motor2"),
        ("walk0", "walk1", "walk2"),
    ]
    assert len({cycle.permission_partition_sha256 for cycle in component.canonical_cycles}) == 2


def test_normalization_blocks_an_unlocked_upstream_generation_policy(tmp_path: Path) -> None:
    source = tmp_path / "source" / "plain"
    _write_bundle(source, compliant_policy=False)

    report = normalize_osm_plainxml_bundle(
        source,
        tmp_path / "candidate" / "normalized",
        tmp_path / "candidate" / "normalization.json",
    )

    assert report.status is GateStatus.BLOCKED
    assert {
        "upstream_no_turnarounds_not_enabled",
        "upstream_roundabout_guessing_not_disabled",
        "upstream_seed_missing_or_invalid",
    }.issubset(report.blockers)


def test_plainxml_normalization_schema_is_current() -> None:
    expected = json.dumps(
        build_plainxml_normalization_report_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert (
        REPOSITORY_ROOT
        / "schemas"
        / "torii.corridor.plainxml-normalization-report.v1.schema.json"
    ).read_text(encoding="utf-8") == expected
