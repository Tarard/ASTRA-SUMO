from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from astra_sumo.cli import main
from astra_sumo.core.motion_review import build_motion_review_request_schema, render_motion_review

NET = """<net>
  <location netOffset="{offset}" convBoundary="0,0,200,100" origBoundary="0,0,1,1" projParameter="!"/>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" speed="13.9" length="90" width="3.2" shape="0,50 90,50"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" speed="13.9" length="90" width="3.2" shape="110,50 200,50"/></edge>
  <edge id="walk" from="a" to="j"><lane id="walk_0" index="0" allow="pedestrian" speed="2" length="90" width="2" shape="0,56 90,56"/></edge>
  <tlLogic id="j" type="static" programID="0" offset="0"><phase duration="5" state="G"/><phase duration="5" state="r"/></tlLogic>
  <junction id="j" type="traffic_light" x="100" y="50" incLanes="in_0" intLanes="" shape="90,45 110,45 110,55 90,55"/>
  <connection from="in" to="out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s" state="O"/>
</net>"""


def _artifact(path: Path) -> dict[str, str]:
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _fixture(tmp_path: Path, *, second_offset: str = "0.00,0.00", seconds: range = range(10, 14)) -> Path:
    panels = []
    for name, offset, stopped in (("plain", "0.00,0.00", True), ("astra", second_offset, False)):
        net = tmp_path / f"{name}.net.xml"
        net.write_text(NET.format(offset=offset), encoding="utf-8")
        fcd = tmp_path / f"{name}.fcd.xml"
        fcd.write_text("<fcd-export>" + "".join(
            f'<timestep time="{second}.00">'
            f'<vehicle id="v" x="{60 if stopped else 60 + 5 * (second - 10)}" y="50" angle="90" type="car" speed="{0 if stopped else 5}"/>'
            f'<vehicle id="b" x="30" y="50" angle="90" type="bike_bicycle" speed="4"/>'
            f'<person id="p" x="40" y="56" angle="90" type="ped" speed="1"/>'
            f'<vehicle id="far" x="900" y="900" angle="0" type="car" speed="0"/></timestep>' for second in seconds) + "</fcd-export>",
            encoding="utf-8")
        states = tmp_path / f"{name}.tls.xml"
        states.write_text("<tlsStates>" + "".join(
            f'<tlsState time="{second}.00" id="j" programID="0" phase="0" state="{"G" if second < 12 else "r"}"/>'
            for second in range(0, 20)) + "</tlsStates>", encoding="utf-8")
        panels.append({"title": name, "net": _artifact(net), "fcd": _artifact(fcd), "tls_states": _artifact(states)})
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema": "astra.motion-review-request/v1", "panels": panels, "bbox": [0, 0, 200, 100],
                                   "begin_s": 10, "end_s": 13, "pixels_per_metre": 2.0}), encoding="utf-8")
    return request


def test_motion_review_draws_each_second_of_both_runs_and_binds_inputs(tmp_path: Path) -> None:
    report = render_motion_review(request_file=_fixture(tmp_path), output_dir=tmp_path / "review")

    assert report["status"] == "measured" and report["view"]["frame_count"] == 4
    plain, astra = report["panels"]
    # The vehicle outside the view is not counted; the stopped car is told apart from the moving one.
    assert plain["objects_in_view"] == {"car": {"min": 1, "max": 1}, "bicycle": {"min": 1, "max": 1},
                                        "pedestrian": {"min": 1, "max": 1}}
    assert (plain["stopped_cars_in_view"]["max"], astra["stopped_cars_in_view"]["max"]) == (1, 0)
    assert plain["signals_in_view"] == 1
    with Image.open(tmp_path / "review" / "motion.gif") as gif:
        assert gif.n_frames == 4 and gif.size == (2 * 400 + 6, 200 + 46)
    saved = json.loads((tmp_path / "review" / "motion-review.json").read_text(encoding="utf-8"))
    assert saved["artifacts"]["motion.gif"]["sha256"] == hashlib.sha256((tmp_path / "review" / "motion.gif").read_bytes()).hexdigest()
    assert set(saved["inputs"]) == {"request", "panel0.net", "panel0.fcd", "panel0.tls_states",
                                    "panel1.net", "panel1.fcd", "panel1.tls_states"}


def test_motion_review_refuses_changed_inputs_missing_seconds_and_other_frames(tmp_path: Path) -> None:
    request = _fixture(tmp_path)
    with pytest.raises(ValueError, match="new review output directory"):
        render_motion_review(request_file=request, output_dir=tmp_path)
    (tmp_path / "plain.fcd.xml").write_text("<fcd-export/>", encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        render_motion_review(request_file=request, output_dir=tmp_path / "changed")

    gap = tmp_path / "gap"
    gap.mkdir()
    with pytest.raises(ValueError, match="no FCD time step for second 13"):
        render_motion_review(request_file=_fixture(gap, seconds=range(10, 13)), output_dir=gap / "review")
    shifted = tmp_path / "shifted"
    shifted.mkdir()
    with pytest.raises(ValueError, match="different network offsets"):
        render_motion_review(request_file=_fixture(shifted, second_offset="25.00,0.00"), output_dir=shifted / "review")
    assert not (gap / "review").exists() and not (shifted / "review").exists()


def test_motion_review_cli_and_schema(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["review", "motion", str(_fixture(tmp_path)), str(tmp_path / "result"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["status"] == "measured"
    schema = Path(__file__).resolve().parents[1] / "schemas/product/astra.motion-review-request.v1.schema.json"
    assert json.loads(schema.read_text(encoding="utf-8")) == build_motion_review_request_schema()
