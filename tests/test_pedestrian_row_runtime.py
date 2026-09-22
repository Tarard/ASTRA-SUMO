from __future__ import annotations

from pathlib import Path

from torii_sumo.core.artifact_io import write_text_atomic
from torii_sumo.corridor.enums import GateStatus
from torii_sumo.corridor.pedestrian_row_runtime import (
    build_row_runtime_probe_from_outputs,
)


_SHA256 = "a" * 64


def _base_files(root: Path) -> tuple[Path, Path, Path, Path]:
    net = root / "case.net.xml"
    route = root / "case.rou.xml"
    tripinfo = root / "tripinfo.xml"
    collisions = root / "collisions.xml"
    write_text_atomic(net, "<net/>")
    write_text_atomic(route, "<routes/>")
    write_text_atomic(
        tripinfo,
        '<tripinfos><tripinfo id="veh"/><personinfo id="ped"/></tripinfos>',
    )
    write_text_atomic(collisions, "<collisions/>")
    return net, route, tripinfo, collisions


def _probe(
    root: Path,
    *,
    fcd: str,
    collision_xml: str = "<collisions/>",
):
    net, route, tripinfo, collisions = _base_files(root)
    fcd_path = root / "trace.fcd.xml"
    write_text_atomic(fcd_path, fcd)
    write_text_atomic(collisions, collision_xml)
    return build_row_runtime_probe_from_outputs(
        net_file=net,
        route_file=route,
        fcd_file=fcd_path,
        tripinfo_file=tripinfo,
        collision_file=collisions,
        crossing_edge_id=":C_c3",
        vehicle_internal_lane_id=":C_10_0",
        arrival_schedule="simultaneous",
        pedestrian_depart_s=0.0,
        vehicle_depart_s=0.5,
        vehicle_speed_mps=13.9,
        sumo_binary_sha256=_SHA256,
        sumo_version="1.27.1",
        command_status="pass",
        command_stderr="",
    )


def test_runtime_probe_observes_vehicle_yield_without_claiming_real_truth(
    tmp_path: Path,
) -> None:
    probe = _probe(
        tmp_path,
        fcd="""<fcd-export>
  <timestep time="0.8"><person id="ped" edge=":C_c3" speed="1.2"/><vehicle id="veh" lane="WC_1" speed="0"/></timestep>
  <timestep time="0.9"><person id="ped" edge=":C_c3" speed="1.2"/><vehicle id="veh" lane="WC_1" speed="0"/></timestep>
  <timestep time="1.0"><person id="ped" edge=":C_c3" speed="1.2"/><vehicle id="veh" lane="WC_1" speed="0"/></timestep>
  <timestep time="1.1"><person id="ped" edge=":C_c3" speed="1.2"/><vehicle id="veh" lane=":C_10_0" speed="2"/></timestep>
</fcd-export>""",
    )

    assert probe.observed_behavior == "vehicle-yielded"
    assert probe.vehicle_stopped_before_conflict_s == 0.3
    assert probe.pedestrian_stopped_before_crossing_s == 0.0
    assert probe.runtime_status is GateStatus.PASS
    assert probe.proves_real_world_priority is False


def test_runtime_probe_observes_pedestrian_yield(tmp_path: Path) -> None:
    probe = _probe(
        tmp_path,
        fcd="""<fcd-export>
  <timestep time="0.8"><person id="ped" edge=":C_w2" speed="0"/><vehicle id="veh" lane=":C_10_0" speed="2"/></timestep>
  <timestep time="0.9"><person id="ped" edge=":C_w2" speed="0"/><vehicle id="veh" lane="CE_1" speed="3"/></timestep>
  <timestep time="1.0"><person id="ped" edge=":C_w2" speed="0"/><vehicle id="veh" lane="CE_1" speed="4"/></timestep>
  <timestep time="1.1"><person id="ped" edge=":C_c3" speed="1.2"/><vehicle id="veh" lane="CE_1" speed="5"/></timestep>
</fcd-export>""",
    )

    assert probe.observed_behavior == "pedestrian-yielded"
    assert probe.pedestrian_stopped_before_crossing_s == 0.3
    assert probe.vehicle_stopped_before_conflict_s == 0.0
    assert probe.runtime_status is GateStatus.PASS


def test_runtime_collision_is_never_a_pass(tmp_path: Path) -> None:
    probe = _probe(
        tmp_path,
        fcd="""<fcd-export>
  <timestep time="0.8"><person id="ped" edge=":C_c3" speed="1"/><vehicle id="veh" lane=":C_10_0" speed="2"/></timestep>
</fcd-export>""",
        collision_xml='<collisions><collision time="0.8"/></collisions>',
    )

    assert probe.observed_behavior == "unsafe-overlap"
    assert probe.collision_count == 1
    assert probe.runtime_status is GateStatus.BLOCKED
