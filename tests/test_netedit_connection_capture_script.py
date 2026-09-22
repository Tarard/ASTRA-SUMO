from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest


SCRIPT = Path("plugins/torii-sumo/scripts/netedit_connection_capture.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("netedit_connection_capture", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_netedit_command_uses_view_selection_and_window_geometry() -> None:
    script = _load_script()

    window = script.NeteditWindow(
        name="tum",
        net_file=Path("teacher.net.xml"),
        view_file=Path("teacher.view.xml"),
        selection_file=Path("teacher.sel.xml"),
        window_pos="0,0",
    )

    assert script.build_netedit_command(
        "netedit",
        window,
        window_size="900,800",
    ) == [
        "netedit",
        "-s",
        "teacher.net.xml",
        "-g",
        "teacher.view.xml",
        "--selection-file",
        "teacher.sel.xml",
        "--window-size",
        "900,800",
        "--window-pos",
        "0,0",
    ]


def test_capture_plan_is_json_serializable() -> None:
    script = _load_script()

    windows = [
        script.NeteditWindow("tum", Path("tum.net.xml"), Path("tum.view.xml"), None, "0,0"),
        script.NeteditWindow(
            "candidate",
            Path("candidate.net.xml"),
            Path("candidate.view.xml"),
            Path("candidate.sel.xml"),
            "1000,0",
        ),
    ]

    plan = script.capture_plan(
        windows,
        screenshot_file=Path("out/shot.png"),
        window_size="1000,900",
        mode_method="menu",
    )

    assert plan["screenshot_file"] == "out/shot.png"
    assert plan["mode_method"] == "menu"
    assert [item["name"] for item in plan["windows"]] == ["tum", "candidate"]
    assert plan["windows"][1]["selection_file"] == "candidate.sel.xml"


def test_viewsettings_text_uses_center_and_zoom() -> None:
    script = _load_script()

    assert script.viewsettings_text("12.5,34.25", zoom=4500) == (
        '<viewsettings>\n'
        '  <viewport zoom="4500" x="12.5" y="34.25" angle="0"/>\n'
        '  <delay value="100"/>\n'
        '</viewsettings>\n'
    )


def test_choose_zoom_prefers_side_specific_value() -> None:
    script = _load_script()

    assert script.choose_zoom(9000, fallback=4500) == 9000
    assert script.choose_zoom(None, fallback=4500) == 4500


def test_split_screen_geometry_uses_left_and_right_halves() -> None:
    script = _load_script()

    assert script.split_screen_geometry("2048,1152") == {
        "window_size": "1024,1152",
        "left_pos": "0,0",
        "right_pos": "1024,0",
    }


def test_close_existing_command_targets_netedit_only() -> None:
    script = _load_script()

    assert script.close_existing_command("netedit.exe") == ["taskkill", "/IM", "netedit.exe", "/F"]


def test_connection_key_sequence_switches_chinese_ime_first() -> None:
    script = _load_script()

    assert script.connection_key_sequence() == ["shift", "c"]


def test_runtime_capture_is_blocked_in_favor_of_background_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script()
    monkeypatch.setattr(script, "_args", lambda: Namespace(dry_run=False))

    with pytest.raises(SystemExit, match="netedit_background_review.py"):
        script.main()
