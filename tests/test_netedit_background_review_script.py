from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path("plugins/torii-sumo/scripts/netedit_background_review.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("netedit_background_review", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_bound_bundle(tmp_path: Path, script):
    source = tmp_path / "source.net.xml"
    source.write_text(
        '<net><tlLogic id="old_tls"><phase duration="30" state="G"/>'
        '</tlLogic><junction id="old_j" type="traffic_light" x="0" y="0"/>'
        '<edge id="source_in" from="outside" to="old_j"><lane id="source_in_0"/>'
        '</edge><connection from="source_in" to="source_out" fromLane="0" '
        'toLane="0" tl="old_tls" linkIndex="0"/></net>',
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        '<net><tlLogic id="target_tls"><phase duration="30" state="G"/>'
        '</tlLogic><junction id="target_j" type="traffic_light" x="1" y="2" '
        'incLanes="candidate_in_0"/><edge id="candidate_in" from="outside" '
        'to="target_j"><lane id="candidate_in_0"/></edge><connection '
        'from="candidate_in" to="candidate_out" fromLane="0" toLane="0" '
        'tl="target_tls" linkIndex="0"/></net>',
        encoding="utf-8",
    )
    ownership = script.audit_tls_ownership_rebuild(
        source_net=source,
        candidate_net=candidate,
        target_source_junction_ids=("old_j",),
        target_candidate_junction_id="target_j",
        expected_controller_ids=("target_tls",),
        expected_controlled_connection_count=1,
        report_schema="torii.test-tls-ownership/v1",
    )
    nema_candidate = tmp_path / "nema.candidate.net.xml"
    nema_candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            '<tlLogic id="target_tls">',
            '<tlLogic id="target_tls" type="NEMA">',
        ),
        encoding="utf-8",
    )
    nema_ownership = script.audit_tls_ownership_rebuild(
        source_net=source,
        candidate_net=nema_candidate,
        target_source_junction_ids=("old_j",),
        target_candidate_junction_id="target_j",
        expected_controller_ids=("target_tls",),
        expected_controlled_connection_count=1,
        report_schema="torii.test-nema-tls-ownership/v1",
    )
    topology = tmp_path / "tls-topology.json"
    topology.write_text(
        json.dumps(
            {
                "automatic_promotion_gate": "blocked",
                "status": "candidate_ready_for_review",
                "candidate_net_file": str(nema_candidate),
                "candidate_sha256": script.file_sha256(nema_candidate),
                "standard_builder": {},
                "tls_ownership": {
                    "status": nema_ownership["status"],
                    "controller_ids": nema_ownership["candidate"]["target_controller_ids"],
                    "controlled_connection_count": nema_ownership["candidate"]["target_controlled_connection_count"],
                    "signal_group_count": nema_ownership["candidate"]["target_signal_group_count"],
                },
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "automatic_promotion_gate": "blocked",
                "source_net_file": str(source),
                "source_sha256": script.file_sha256(source),
                "candidate_net_file": str(candidate),
                "candidate_sha256": script.file_sha256(candidate),
                "tls_ownership": ownership,
                "tls_topology": {
                    "status": "candidate_ready_for_review",
                    "artifact_file": str(topology),
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "automatic_promotion_gate": "blocked",
                "artifacts": [
                    {"path": str(summary), "sha256": script.file_sha256(summary)},
                    {"path": str(source), "sha256": script.file_sha256(source)},
                    {
                        "path": str(candidate),
                        "sha256": script.file_sha256(candidate),
                    },
                    {
                        "path": str(nema_candidate),
                        "sha256": script.file_sha256(nema_candidate),
                    },
                    {
                        "path": str(topology),
                        "sha256": script.file_sha256(topology),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return source, candidate, summary, manifest


def _write_hamburg_bound_bundle(tmp_path: Path, script):
    source = tmp_path / "hamburg-source.net.xml"
    source.write_text('<net><junction id="source" type="priority" x="0" y="0"/></net>', encoding="utf-8")
    controller_by_node = {
        "0228": "controller_0228",
        "2421": "controller_2421",
        "2394": "controller_2394",
    }
    movement_counts = {"0228": 16, "2421": 9, "2394": 8}
    xml = ["<net>"]
    for node_index, (node_id, controller_id) in enumerate(controller_by_node.items()):
        count = movement_counts[node_id]
        xml.append(
            f'<tlLogic id="{controller_id}"><phase duration="30" state="{"r" * count}"/></tlLogic>'
        )
        xml.append(
            f'<junction id="{controller_id}" type="traffic_light" x="{node_index * 100}" '
            f'y="0" incLanes="{controller_id}_in_0"/>'
        )
        for link_index in range(count):
            xml.append(
                f'<connection from="{controller_id}_in_{link_index}" '
                f'to="{controller_id}_out_{link_index}" fromLane="0" toLane="0" '
                f'tl="{controller_id}" linkIndex="{link_index}"/>'
            )
    xml.append("</net>")
    candidate = tmp_path / "hamburg-candidate.net.xml"
    candidate.write_text("".join(xml), encoding="utf-8")

    official_assets = []
    for node_id in controller_by_node:
        for kind in ("map_xml", "ocit_xml"):
            asset = tmp_path / f"{node_id}-{kind}.xml"
            asset.write_text(f"<{kind} node=\"{node_id}\"/>", encoding="utf-8")
            official_assets.append(
                {
                    "node_id": node_id,
                    "kind": kind,
                    "path": str(asset),
                    "sha256": script.file_sha256(asset),
                }
            )
    stages = [
        {
            "status": "pass",
            "controller_id": f"HH_{node_id}",
            "native_teacher_replay": {
                "status": "pass",
                "junction_id": controller_id,
            },
        }
        for node_id, controller_id in (
            ("2421", controller_by_node["2421"]),
            ("2394", controller_by_node["2394"]),
            ("0228", controller_by_node["0228"]),
        )
    ]
    source_hash = script.file_sha256(source)
    candidate_hash = script.file_sha256(candidate)
    manifest = tmp_path / "hamburg-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_id": "torii.hamburg-official-tls-workflow.v1",
                "preset_id": "hamburg-sandtorkai-0228-2421-2394",
                "status": "pass",
                "claim_status": "official-tls-topology-ready",
                "stage": "complete",
                "automatic_promotion_gate": "blocked",
                "expected_vehicle_topology_movement_count": 33,
                "expected_primary_stream_count": 27,
                "source_net_file": str(source),
                "source_net_sha256_before": source_hash,
                "source_net_sha256_after": source_hash,
                "source_net_unchanged": True,
                "rebuilt_net_file": str(candidate),
                "official_asset_inventory": official_assets,
                "native_teacher_derivation": {"status": "pass"},
                "network_rebuild": {
                    "status": "pass",
                    "controller_count": 3,
                    "post_replay_compact_scope_tls_retirement_status": "pass",
                    "final_net_file": str(candidate),
                    "stage_reports": stages,
                },
                "native_geometry_continuity_audit": {"status": "pass"},
                "post_retirement_sumo_load_audit": {"status": "pass"},
                "effective_map_lane_binding_audit": {"status": "pass"},
                "official_movement_physical_endpoint_audit": {
                    "status": "pass",
                    "movement_count": 33,
                    "validated_movement_count": 33,
                    "unique_physical_endpoint_count": 33,
                    "replay_control_evidence": {"status": "pass"},
                },
                "primary_stream_tls_binding_audit": {"status": "pass"},
                "vehicle_topology_inventory_audit": {"status": "pass"},
                "ocit_group_validation": {"status": "pass"},
                "tld_observed_group_subset_audit": {"status": "pass"},
                "artifacts": [
                    {
                        "role": "rebuilt_net_file",
                        "path": str(candidate),
                        "sha256": candidate_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return source, candidate, manifest


def test_reads_target_and_builds_capture_requests(tmp_path: Path) -> None:
    script = _load_script()
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        '<net><junction id="j0" type="traffic_light" x="12.5" y="34.25" '
        'incLanes="e0_0 e1_0 e1_1" shape="11,33 13,33 13,35 11,35"/></net>',
        encoding="utf-8",
    )

    target = script.read_target_junction(net_file, "j0")
    requests = script.capture_requests(target)

    assert target.x == 12.5
    assert target.y == 34.25
    assert target.incoming_lanes == ("e0_0", "e1_0", "e1_1")
    assert target.junction_shape == "11,33 13,33 13,35 11,35"
    assert [request.mode for request in requests[:3]] == [
        "inspect",
        "tls",
        "connection",
    ]
    assert len(requests) == 3
    assert {request.selection_id for request in requests} == {"j0"}

    overview_requests = script.capture_requests(
        target,
        include_neutral_overview=True,
    )
    assert len(overview_requests) == 4
    assert overview_requests[0].name.endswith("-overview")
    assert overview_requests[0].selection_type == "none"
    assert overview_requests[0].selection_id == ""


def test_viewsettings_selection_and_mode_are_deterministic() -> None:
    script = _load_script()

    assert script.viewsettings_text((12.5, 34.25), zoom=500) == (
        "<viewsettings>\n"
        '  <scheme name="standard"/>\n'
        '  <viewport zoom="500" x="12.5" y="34.25" angle="0"/>\n'
        '  <delay value="100"/>\n'
        "</viewsettings>\n"
    )
    assert script.selection_text("junction", "j0") == "junction:j0\n"
    assert script.selection_text("lane", "e0_0") == "lane:e0_0\n"
    assert script.selection_text("none", "") == ""
    assert script.mode_key("inspect") == "CI"
    assert script.mode_key("connection") == "C"
    assert script.mode_key("tls") == "T"


def test_network_fit_view_uses_network_boundary_and_window_size(tmp_path: Path) -> None:
    script = _load_script()
    net_file = tmp_path / "corridor.net.xml"
    net_file.write_text(
        '<net><location convBoundary="0,0,1600,800"/>'
        '<junction id="a" x="0" y="0"/>'
        '<junction id="b" x="1600" y="800"/></net>',
        encoding="utf-8",
    )

    center, zoom = script.network_fit_view(net_file, "1400,1000")

    assert center == (800.0, 400.0)
    assert zoom == pytest.approx(90.0)


@pytest.mark.parametrize("scale", [0.1, 1.0, 10.0])
@pytest.mark.parametrize("size", ["1400,1000", "2800,2000"])
def test_overview_zoom_is_independent_of_world_scale_and_pixel_count(tmp_path, scale, size):
    script = _load_script()
    network = tmp_path / "network.net.xml"
    network.write_text(f'<net><location convBoundary="0,0,{1000*scale},{500*scale}"/></net>', encoding="utf-8")
    center, zoom = script.network_fit_view(network, size)
    assert center == (500 * scale, 250 * scale)
    assert zoom == pytest.approx(90.0)


def test_junction_auto_fit_uses_its_shape_and_bounded_connected_lane_context(tmp_path):
    script = _load_script()
    network = tmp_path / "network.net.xml"
    network.write_text('<net><location convBoundary="0,0,1000,500"/>'
        '<junction id="J" x="480" y="245" shape="450,230 550,230 550,270 450,270"/>'
        '<edge id="in" from="west" to="J"><lane shape="0,250 450,250" width="3.2"/></edge>'
        '<edge id="out" from="J" to="east"><lane shape="550,250 1000,250" width="3.2"/></edge></net>', encoding="utf-8")
    target = script.read_target_junction(network, "J")
    result = script.fitted_viewport(network, target=target)
    assert result["center"] == [500.0, 250.0]
    assert result["target_bounds"] == pytest.approx([418.4, 230.0, 581.6, 270.0])
    assert result["zoom"] == pytest.approx(100 * .9 * min(1000 / 163.2, 500 / 40))
    assert result["reference_bounds_basis"] == "convBoundary_estimate"
    assert result["runtime_grid_measured"] is False
    explicit = script.fitted_viewport(network, target=target, zoom=650)
    assert explicit["zoom"] == 650
    assert explicit["center"] == [480.0, 245.0]
    assert explicit["basis"] == "explicit_zoom_override"
    shifted = script.fitted_viewport(network, target=target, center=(400, 250))
    assert shifted["zoom"] < result["zoom"]
    half_width = 100 * 1000 / shifted["zoom"] / 2
    assert shifted["center"][0] - half_width <= shifted["target_bounds"][0]
    assert shifted["center"][0] + half_width >= shifted["target_bounds"][2]


@pytest.mark.parametrize("zoom", [0, -1, float("nan"), float("inf")])
def test_invalid_zoom_is_rejected_before_any_capture(tmp_path, zoom):
    script = _load_script()
    with pytest.raises(ValueError, match="positive, finite"):
        script.fitted_viewport(tmp_path / "not-read.net.xml", zoom=zoom)


def test_unspecified_cli_zoom_uses_automatic_fit(monkeypatch):
    script = _load_script()
    monkeypatch.setattr(script.sys, "argv", [str(SCRIPT), "--net-file", "candidate.net.xml", "--out-dir", "review"])
    assert script._args().zoom is None


def test_dpi_awareness_is_enabled_once_before_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script()
    calls: list[int] = []

    class FakeFunction:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    class FakeUser32:
        GetThreadDpiAwarenessContext = FakeFunction(lambda: script.ctypes.c_void_p(-2))
        AreDpiAwarenessContextsEqual = FakeFunction(lambda left, right: left.value == right.value)
        SetProcessDpiAwarenessContext = FakeFunction(
            lambda context: calls.append(context.value) or True
        )

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script.ctypes, "windll", type("Windll", (), {"user32": FakeUser32()})())
    script._DPI_AWARENESS = None

    assert script._enable_dpi_awareness()["context"] == "per-monitor-v2"
    assert script._enable_dpi_awareness()["context"] == "per-monitor-v2"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("hkl", "expected_status", "expected_chinese"),
    [
        (0x08040804, "blocked", True),
        (0x04090409, "pass", False),
    ],
)
def test_keyboard_layout_context_detects_chinese_input(
    monkeypatch: pytest.MonkeyPatch,
    hkl: int,
    expected_status: str,
    expected_chinese: bool,
) -> None:
    script = _load_script()

    class FakeUser32:
        def GetWindowThreadProcessId(self, hwnd, _):
            return 77

        def GetKeyboardLayout(self, thread_id):
            return hkl

        def GetKeyboardLayoutNameW(self, buffer):
            buffer.value = "00000409" if not expected_chinese else "00000804"
            return True

    class FakeGui:
        def GetForegroundWindow(self):
            return 42

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_windows_modules", lambda: (None, FakeGui(), None, None))
    monkeypatch.setattr(script.ctypes, "windll", type("Windll", (), {"user32": FakeUser32()})())

    result = script._keyboard_layout_context()

    assert result["status"] == expected_status
    assert result["is_chinese"] is expected_chinese
    assert result["thread_id"] == 77


def test_keyboard_layout_context_checks_target_window_not_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    observed_hwnds = []

    class FakeUser32:
        def GetWindowThreadProcessId(self, hwnd, _):
            observed_hwnds.append(hwnd)
            return 77

        def GetKeyboardLayout(self, _thread_id):
            return 0x04090409

        def GetKeyboardLayoutNameW(self, buffer):
            buffer.value = "00000409"
            return True

    class FakeGui:
        def GetForegroundWindow(self):
            return 42

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_windows_modules", lambda: (None, FakeGui(), None, None))
    monkeypatch.setattr(script.ctypes, "windll", type("Windll", (), {"user32": FakeUser32()})())

    result = script._keyboard_layout_context(99)

    assert result["status"] == "pass"
    assert result["target_hwnd"] == 99
    assert observed_hwnds == [99]


def test_background_review_switches_only_its_netedit_window_to_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    contexts = iter(
        [
            {"status": "blocked", "lang_id": "0x0804"},
            {"status": "pass", "lang_id": "0x0409"},
        ]
    )
    messages = []

    class FakeFunction:
        def __call__(self, *_args):
            return 0x04090409

    class FakeGui:
        def SendMessage(self, *args):
            messages.append(args)

    class FakeUser32:
        LoadKeyboardLayoutW = FakeFunction()

    monkeypatch.setattr(script, "_keyboard_layout_context", lambda _hwnd: next(contexts))
    monkeypatch.setattr(script, "_windows_modules", lambda: (None, FakeGui(), None, None))
    monkeypatch.setattr(script.ctypes, "windll", type("Windll", (), {"user32": FakeUser32()})())

    result = script._ensure_english_window_layout(99)

    assert result["status"] == "pass"
    assert result["changed_by_torii"] is True
    assert messages == [(99, 0x0050, 0, 0x04090409)]


def test_review_window_is_maximized_without_resize() -> None:
    script = _load_script()
    calls: list[tuple] = []

    class FakeGui:
        def ShowWindow(self, hwnd, command):
            calls.append(("show", hwnd, command))

        def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
            calls.append(("position", hwnd, insert_after, x, y, width, height, flags))

        def IsZoomed(self, hwnd):
            return True

    class FakeCon:
        HWND_BOTTOM = 9
        SWP_NOMOVE = 2
        SWP_NOSIZE = 1
        SWP_NOACTIVATE = 16

    result = script._maximize_review_window(42, FakeCon, FakeGui())

    assert result == {"status": "pass", "window_state": "maximized"}
    assert calls == [
        ("show", 42, script.SW_MAXIMIZE),
        ("position", 42, 9, 0, 0, 0, 0, 19),
    ]


def test_netedit_command_binds_view_selection_and_disables_registry_viewport() -> None:
    script = _load_script()

    command = script.build_netedit_command(
        netedit_binary="netedit",
        net_file=Path("candidate.net.xml"),
        view_file=Path("target.view.xml"),
        selection_file=Path("target.selection.txt"),
        additional_file=Path("review.add.xml"),
        window_size="1400,1000",
        window_pos="20,20",
    )

    assert command == [
        "netedit",
        "-s",
        "candidate.net.xml",
        "-g",
        "target.view.xml",
        "--selection-file",
        "target.selection.txt",
        "--registry-viewport",
        "false",
        "--window-size",
        "1400,1000",
        "--window-pos",
        "20,20",
        "--additional-files",
        "review.add.xml",
    ]


def test_candidate_identity_refuses_source_baseline(tmp_path: Path) -> None:
    script = _load_script()
    source, _, summary, manifest = _write_bound_bundle(tmp_path, script)
    payload = json.loads(summary.read_text(encoding="utf-8"))
    payload["candidate_net_file"] = str(source)
    payload["candidate_sha256"] = script.file_sha256(source)
    summary.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source baseline"):
        script.load_bound_candidate_identity(
            summary_file=summary,
            manifest_file=manifest,
        )


def test_candidate_identity_requires_exact_manifest_hash(tmp_path: Path) -> None:
    script = _load_script()
    _, candidate, summary, manifest = _write_bound_bundle(tmp_path, script)

    identity = script.load_bound_candidate_identity(
        summary_file=summary,
        manifest_file=manifest,
    )
    assert identity.candidate_file == candidate.resolve()
    assert identity.target_junction_id == "target_j"
    assert identity.tls_ownership_recheck["status"] == "pass"

    nema_identity = script.load_bound_candidate_identity(
        summary_file=summary,
        manifest_file=manifest,
        candidate_role="nema-topology",
    )
    assert nema_identity.candidate_role == "nema-topology"
    assert nema_identity.candidate_file.name == "nema.candidate.net.xml"
    assert nema_identity.candidate_evidence_file == (tmp_path / "tls-topology.json").resolve()

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for artifact in payload["artifacts"]:
        if Path(artifact["path"]).name == candidate.name:
            artifact["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Manifest hash mismatch"):
        script.load_bound_candidate_identity(
            summary_file=summary,
            manifest_file=manifest,
        )


def test_hamburg_identity_requires_all_hard_gates_and_exact_three_tls(tmp_path: Path) -> None:
    script = _load_script()
    _, candidate, manifest = _write_hamburg_bound_bundle(tmp_path, script)

    identity = script.load_bound_hamburg_official_identity(manifest_file=manifest)

    assert identity.candidate_file == candidate.resolve()
    assert [target.official_node_id for target in identity.targets] == ["0228", "2421", "2394"]
    assert identity.candidate_tls_recheck["status"] == "pass"
    assert identity.candidate_tls_recheck["controlled_connection_count"] == 33

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["automatic_promotion_gate"] = "allowed"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="automatic_promotion_gate"):
        script.load_bound_hamburg_official_identity(manifest_file=manifest)


@pytest.mark.parametrize(
    "gate_name",
    [
        "post_retirement_sumo_load_audit",
        "official_movement_physical_endpoint_audit",
    ],
)
def test_hamburg_identity_requires_final_runtime_and_movement_gates(
    tmp_path: Path,
    gate_name: str,
) -> None:
    script = _load_script()
    _, _, manifest = _write_hamburg_bound_bundle(tmp_path, script)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[gate_name] = {"status": "fail"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=rf"hard gate {gate_name} is not pass"):
        script.load_bound_hamburg_official_identity(manifest_file=manifest)


def test_hamburg_identity_requires_all_33_replayed_movement_endpoints(tmp_path: Path) -> None:
    script = _load_script()
    _, _, manifest = _write_hamburg_bound_bundle(tmp_path, script)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["official_movement_physical_endpoint_audit"]["validated_movement_count"] = 32
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="validated_movement_count must be 33"):
        script.load_bound_hamburg_official_identity(manifest_file=manifest)


def test_hamburg_identity_rejects_tampered_candidate_hash(tmp_path: Path) -> None:
    script = _load_script()
    _, candidate, manifest = _write_hamburg_bound_bundle(tmp_path, script)
    candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        script.load_bound_hamburg_official_identity(manifest_file=manifest)


def test_hamburg_identity_rejects_residual_osm_controller(tmp_path: Path) -> None:
    script = _load_script()
    _, candidate, manifest = _write_hamburg_bound_bundle(tmp_path, script)
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "</net>",
            '<tlLogic id="residual_osm_tls"><phase duration="30" state="r"/></tlLogic></net>',
        ),
        encoding="utf-8",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"][0]["sha256"] = script.file_sha256(candidate)
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly the three official controllers"):
        script.load_bound_hamburg_official_identity(manifest_file=manifest)


def test_hamburg_runner_plans_three_modes_for_each_junction_without_gui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    _, _, manifest = _write_hamburg_bound_bundle(tmp_path, script)
    calls = []

    def fake_capture_request(**kwargs):
        request = kwargs["request"]
        calls.append(request)
        return {
            "name": request.name,
            "mode": request.mode,
            "selection_type": request.selection_type,
            "selection_id": request.selection_id,
            "sha256": f"{len(calls):064x}",
            "print_window_result": 1,
            "render_quality": "pass",
            "foreground_unchanged": True,
            "foreground_context_restored": True,
            "mode_delivery": {"foreground_context_unchanged": True},
            "inspect_delivery": {"foreground_context_unchanged": True},
        }

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_capture_request", fake_capture_request)
    report = script.run_hamburg_background_review(
        manifest_file=manifest,
        output_dir=tmp_path / "review",
        netedit_binary="netedit",
        center=None,
        zoom=500,
        window_size="1400,1000",
        window_pos="20,20",
        settle_seconds=0,
    )

    assert report["status"] == "review_material_ready"
    assert report["capture_session_count"] == 9
    assert [request.mode for request in calls] == [
        "inspect",
        "tls",
        "connection",
    ] * 3
    assert {request.name.split("-")[1] for request in calls} == {"0228", "2421", "2394"}
    assert report["automatic_promotion_gate"] == "blocked"


@pytest.mark.parametrize("requested_zoom", [None, 500])
def test_direct_runner_is_hash_bound_and_non_promoting_without_gui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_zoom: float | None,
) -> None:
    script = _load_script()
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        '<net><junction id="owner:2394" type="priority" x="12" y="34" '
        'incLanes="in_0 in_1" shape="10,32 14,32 14,36 10,36"/></net>',
        encoding="utf-8",
    )
    expected_hash = script.file_sha256(candidate)
    calls = []
    captured_call_kwargs = []

    def fake_capture_request(**kwargs):
        request = kwargs["request"]
        calls.append(request)
        captured_call_kwargs.append(kwargs)
        return {
            "name": request.name,
            "mode": request.mode,
            "selection_type": request.selection_type,
            "selection_id": request.selection_id,
            "sha256": f"{len(calls):064x}",
            "print_window_result": 1,
            "render_quality": "pass",
            "foreground_unchanged": True,
            "foreground_context_restored": True,
            "mode_delivery": {"foreground_context_unchanged": True},
        }

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_capture_request", fake_capture_request)
    report = script.run_direct_background_review(
        net_file=candidate,
        expected_net_sha256=expected_hash,
        target_junction_id="owner:2394",
        output_dir=tmp_path / "review",
        netedit_binary="netedit",
        center=None,
        zoom=requested_zoom,
        window_size="1400,1000",
        window_pos="20,20",
        settle_seconds=0,
    )

    assert report["status"] == "review_material_ready"
    assert report["candidate_sha256_before"] == expected_hash
    assert report["candidate_sha256_after"] == expected_hash
    assert report["candidate_unchanged"] is True
    assert report["capture_session_count"] == 3
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["global_keyboard_or_mouse_input_used"] is False
    assert report["inspect_object_delivery"].startswith("junction selection only")
    assert all(call.selection_id == "owner:2394" for call in calls)
    assert all(call_kwargs["additional_file"] is None for call_kwargs in captured_call_kwargs)
    assert Path(report["report_file"]).name.startswith("review-")
    assert max(len(request.name) for request in calls) < 60
    assert [request.mode for request in calls] == ["inspect", "tls", "connection"]
    assert all(call_kwargs["zoom"] == (90 if requested_zoom is None else 500) for call_kwargs in captured_call_kwargs)
    assert report["viewport_fit"]["runtime_grid_measured"] is False


def test_direct_runner_rejects_unexpected_candidate_hash(tmp_path: Path) -> None:
    script = _load_script()
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        '<net><junction id="owner" type="priority" x="0" y="0"/></net>',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        script.run_direct_background_review(
            net_file=candidate,
            expected_net_sha256="0" * 64,
            target_junction_id="owner",
            output_dir=tmp_path / "review",
            netedit_binary="netedit",
            center=None,
            zoom=500,
            window_size="1400,1000",
            window_pos="20,20",
            settle_seconds=0,
        )


def test_direct_runner_auto_fits_only_neutral_overview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    candidate = tmp_path / "corridor.net.xml"
    candidate.write_text(
        '<net><location convBoundary="0,0,1600,800"/>'
        '<junction id="owner" type="traffic_light" x="100" y="200"/></net>',
        encoding="utf-8",
    )
    calls = []

    def fake_capture_request(**kwargs):
        calls.append(kwargs)
        return {
            "name": kwargs["request"].name,
            "mode": kwargs["request"].mode,
            "selection_type": kwargs["request"].selection_type,
            "selection_id": kwargs["request"].selection_id,
            "sha256": f"{len(calls):064x}",
            "print_window_result": 1,
            "render_quality": "pass",
            "foreground_unchanged": True,
            "foreground_context_restored": True,
            "mode_delivery": {"foreground_context_unchanged": True},
        }

    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_capture_request", fake_capture_request)
    report = script.run_direct_background_review(
        net_file=candidate,
        expected_net_sha256=script.file_sha256(candidate),
        target_junction_id="owner",
        output_dir=tmp_path / "review",
        netedit_binary="netedit",
        center=None,
        zoom=500,
        window_size="1400,1000",
        window_pos="20,20",
        settle_seconds=0,
        neutral_overview=True,
    )

    assert calls[0]["center"] == (800.0, 400.0)
    assert calls[0]["zoom"] == pytest.approx(90.0)
    assert all(call["center"] == (100.0, 200.0) for call in calls[1:])
    assert all(call["zoom"] == 500 for call in calls[1:])
    assert report["neutral_overview_view"]["zoom"] == pytest.approx(90.0)


def _aerial_sources(tmp_path, projection="+proj=utm +zone=32 +datum=WGS84 +units=m +no_defs"):
    from PIL import Image
    net = tmp_path / "aerial.net.xml"
    net.write_text(
        f'<net><location projParameter="{projection}" netOffset="-565000,-5932000" '
        'convBoundary="0,0,600,400"/><junction id="owner" type="priority" x="300" y="200" '
        'shape="295,195 305,195 305,205 295,205"/></net>', encoding="utf-8")
    aerial = tmp_path / "aerial & north.png"
    Image.new("RGB", (40, 20), (20, 130, 70)).save(aerial)
    return net, aerial, [565100, 5932100, 565500, 5932300]


def test_native_aerial_decal_uses_projection_offset_and_escaped_original_image(tmp_path):
    import xml.etree.ElementTree as ET
    script = _load_script()
    net, aerial, bbox = _aerial_sources(tmp_path)
    before = aerial.read_bytes()
    background = script.aerial_decal(net_file=net, aerial_image=aerial, bbox_epsg25832=bbox)
    node = ET.fromstring(script.viewsettings_text((300, 200), zoom=500, aerial_background=background)).find("decal")
    assert node.get("file") == str(aerial.resolve())
    assert float(node.get("centerX")) == pytest.approx(300, abs=0.001)
    assert float(node.get("centerY")) == pytest.approx(200, abs=0.001)
    assert float(node.get("width")) == pytest.approx(400, abs=0.001)
    assert float(node.get("height")) == pytest.approx(200, abs=0.001)
    assert node.get("rotation") == "0"
    assert node.get("screenRelative") == "false"
    assert float(node.get("layer")) < 0
    assert background["image"]["sha256"] == script.file_sha256(aerial)
    assert background["bbox_epsg25832"] == bbox
    assert aerial.read_bytes() == before


@pytest.mark.parametrize("bbox", [[1, 2, 1, 5], [565100, 5932100, float("nan"), 5932300],
                                  [True, 5932100, 565500, 5932300], [9.9, 53.5, 10.1, 53.6]])
def test_native_aerial_decal_rejects_invalid_source_bounds(tmp_path, bbox):
    script = _load_script()
    net, aerial, _ = _aerial_sources(tmp_path)
    with pytest.raises(ValueError):
        script.aerial_decal(net_file=net, aerial_image=aerial, bbox_epsg25832=bbox)


@pytest.mark.parametrize("projection", ["!", "EPSG:4326", "+proj=utm +zone=33 +datum=WGS84 +units=m +no_defs"])
def test_native_aerial_decal_rejects_missing_geographic_and_rotated_network_frames(tmp_path, projection):
    script = _load_script()
    net, aerial, bbox = _aerial_sources(tmp_path, projection)
    with pytest.raises(ValueError):
        script.aerial_decal(net_file=net, aerial_image=aerial, bbox_epsg25832=bbox)


def test_native_aerial_decal_rejects_exif_rotated_pixels(tmp_path):
    from PIL import Image
    script = _load_script()
    net, _, bbox = _aerial_sources(tmp_path)
    aerial = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 3
    Image.new("RGB", (40, 20)).save(aerial, exif=exif)
    with pytest.raises(ValueError, match="orientation"):
        script.aerial_decal(net_file=net, aerial_image=aerial, bbox_epsg25832=bbox)


@pytest.mark.parametrize("change_image", [False, True])
def test_direct_capture_carries_and_rechecks_aerial_identity(tmp_path, monkeypatch, change_image):
    script = _load_script()
    net, aerial, bbox = _aerial_sources(tmp_path)
    calls = []
    def capture(**kwargs):
        calls.append(kwargs)
        request = kwargs["request"]
        if change_image:
            aerial.write_bytes(aerial.read_bytes() + b"changed")
        return dict(name=request.name, mode=request.mode, selection_type=request.selection_type,
                    sha256=f"{len(calls):064x}", print_window_result=1, render_quality="pass",
                    foreground_unchanged=True, foreground_context_restored=True,
                    mode_delivery={"foreground_context_unchanged": True})
    monkeypatch.setattr(script.sys, "platform", "win32")
    monkeypatch.setattr(script, "_capture_request", capture)
    report = script.run_direct_background_review(
        net_file=net, expected_net_sha256=script.file_sha256(net), target_junction_id="owner",
        output_dir=tmp_path / "capture", netedit_binary="netedit", center=None, zoom=500,
        window_size="1400,1000", window_pos="20,20", settle_seconds=0,
        aerial_image=aerial, aerial_bbox_epsg25832=bbox)
    assert all(call["aerial_background"]["image"]["path"] == str(aerial.resolve()) for call in calls)
    assert report["aerial_background"]["image_unchanged"] is not change_image
    assert report["status"] == ("blocked" if change_image else "review_material_ready")


def test_aerial_cli_is_direct_only_and_requires_paired_arguments(tmp_path, monkeypatch):
    script = _load_script()
    monkeypatch.setattr(script.sys, "argv", [str(SCRIPT), "--summary", "s.json", "--out-dir", "review",
                                            "--aerial-image", "image.png"])
    with pytest.raises(SystemExit, match="only valid with --net-file"):
        script.main()
    net, aerial, _ = _aerial_sources(tmp_path)
    with pytest.raises(ValueError, match="together"):
        script.run_direct_background_review(
            net_file=net, expected_net_sha256=script.file_sha256(net), target_junction_id="owner",
            output_dir=tmp_path / "capture", netedit_binary="netedit", center=None, zoom=500,
            window_size="1400,1000", window_pos="20,20", settle_seconds=0, aerial_image=aerial)
