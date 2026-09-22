from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple


class NeteditWindow(NamedTuple):
    name: str
    net_file: Path
    view_file: Path | None
    selection_file: Path | None
    window_pos: str


def build_netedit_command(netedit_binary: str, window: NeteditWindow, *, window_size: str) -> list[str]:
    open_arg = "--sumocfg-file" if window.net_file.suffix.lower() == ".sumocfg" else "-s"
    command = [netedit_binary, open_arg, str(window.net_file)]
    if window.view_file is not None:
        command += ["-g", str(window.view_file)]
    if window.selection_file is not None:
        command += ["--selection-file", str(window.selection_file)]
    command += ["--window-size", window_size, "--window-pos", window.window_pos]
    return command


def close_existing_command(process_name: str) -> list[str]:
    return ["taskkill", "/IM", process_name, "/F"]


def connection_key_sequence() -> list[str]:
    return ["shift", "c"]


def _json_path(path: Path) -> str:
    return path.as_posix()


def capture_plan(
    windows: list[NeteditWindow],
    *,
    screenshot_file: Path,
    window_size: str,
    mode_method: str,
    netedit_binary: str = "netedit",
) -> dict[str, Any]:
    return {
        "screenshot_file": _json_path(screenshot_file),
        "window_size": window_size,
        "mode_method": mode_method,
        "windows": [
            {
                "name": window.name,
                "net_file": _json_path(window.net_file),
                "view_file": "" if window.view_file is None else _json_path(window.view_file),
                "selection_file": "" if window.selection_file is None else _json_path(window.selection_file),
                "window_pos": window.window_pos,
                "command": build_netedit_command(netedit_binary, window, window_size=window_size),
            }
            for window in windows
        ],
    }


def _parse_point(value: str) -> tuple[int, int]:
    left, right = value.split(",", 1)
    return int(left), int(right)


def split_screen_geometry(screen_size: str) -> dict[str, str]:
    width, height = _parse_point(screen_size)
    half = width // 2
    return {
        "window_size": f"{half},{height}",
        "left_pos": "0,0",
        "right_pos": f"{half},0",
    }


def _screen_size() -> str:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return f"{user32.GetSystemMetrics(0)},{user32.GetSystemMetrics(1)}"
    return "2000,1000"


def viewsettings_text(center: str, *, zoom: int) -> str:
    x, y = center.split(",", 1)
    return (
        "<viewsettings>\n"
        f'  <viewport zoom="{zoom}" x="{x}" y="{y}" angle="0"/>\n'
        '  <delay value="100"/>\n'
        "</viewsettings>\n"
    )


def choose_zoom(side_zoom: int | None, *, fallback: int) -> int:
    return fallback if side_zoom is None else side_zoom


def _generated_view(out_dir: Path, name: str, center: str | None, zoom: int) -> Path | None:
    if center is None:
        return None
    view_file = out_dir / f"{name}.view.xml"
    view_file.write_text(viewsettings_text(center, zoom=zoom), encoding="utf-8")
    return view_file


def _windows_for_pid(pid: int) -> list[dict[str, Any]]:
    user32 = ctypes.windll.user32
    found: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd: int, _: int) -> bool:
        proc_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            rect = ctypes.wintypes.RECT()
            user32.GetWindowTextW(hwnd, title, length + 1)
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append(
                {
                    "hwnd": int(hwnd),
                    "title": title.value,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                }
            )
        return True

    user32.EnumWindows(enum_proc, 0)
    return found


def _move_windows(pids: list[int], windows: list[NeteditWindow], *, window_size: str) -> dict[str, Any]:
    user32 = ctypes.windll.user32
    width, height = _parse_point(window_size)
    moved: dict[str, Any] = {}
    for pid, window in zip(pids, windows):
        netedit_windows = _windows_for_pid(pid)
        if not netedit_windows:
            moved[str(pid)] = []
            continue
        x, y = _parse_point(window.window_pos)
        hwnd = netedit_windows[0]["hwnd"]
        user32.ShowWindow(hwnd, 9)
        user32.MoveWindow(hwnd, x, y, width, height, True)
        moved[str(pid)] = _windows_for_pid(pid)
    return moved


def _raise_windows(pids: list[int]) -> None:
    user32 = ctypes.windll.user32
    for pid in pids:
        netedit_windows = _windows_for_pid(pid)
        if not netedit_windows:
            continue
        hwnd = netedit_windows[0]["hwnd"]
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002)


def _activate_connection_mode(
    pids: list[int],
    *,
    method: str,
    menu_click: tuple[int, int],
    connection_click: tuple[int, int],
) -> None:
    if method == "none":
        return
    try:
        import pyautogui
    except ImportError as exc:
        raise SystemExit("pyautogui is required unless --mode-method none is used") from exc

    user32 = ctypes.windll.user32
    for pid in pids:
        netedit_windows = _windows_for_pid(pid)
        if not netedit_windows:
            continue
        window = netedit_windows[0]
        hwnd = window["hwnd"]
        left, top, *_ = window["rect"]
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        if method in {"key", "both"}:
            pyautogui.click(left + 220, top + 180)
            time.sleep(0.1)
            for key in connection_key_sequence():
                pyautogui.press(key)
                time.sleep(0.1)
            time.sleep(0.2)
        if method in {"menu", "both"}:
            pyautogui.click(left + menu_click[0], top + menu_click[1])
            time.sleep(0.2)
            pyautogui.click(left + connection_click[0], top + connection_click[1])
            time.sleep(0.4)


def _screenshot(path: Path) -> None:
    try:
        import pyautogui
    except ImportError as exc:
        raise SystemExit("pyautogui is required to capture screenshots") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    pyautogui.screenshot().save(path)


def _existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open NetEdit review windows, enter connection mode, and screenshot them.")
    parser.add_argument("--left-net", required=True)
    parser.add_argument("--right-net", required=True)
    parser.add_argument("--left-view")
    parser.add_argument("--right-view")
    parser.add_argument("--left-center")
    parser.add_argument("--right-center")
    parser.add_argument("--zoom", type=int, default=4500)
    parser.add_argument("--left-zoom", type=int)
    parser.add_argument("--right-zoom", type=int)
    parser.add_argument("--left-selection")
    parser.add_argument("--right-selection")
    parser.add_argument("--left-name", default="tum")
    parser.add_argument("--right-name", default="candidate")
    parser.add_argument("--left-pos", default="0,0")
    parser.add_argument("--right-pos", default="1000,0")
    parser.add_argument("--window-size", default="1000,1000")
    parser.add_argument("--split-screen", action="store_true")
    parser.add_argument("--screen-size")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--screenshot-name", default="netedit_connection_review.png")
    parser.add_argument("--status-name", default="netedit_connection_review.json")
    parser.add_argument("--netedit-binary", default="netedit")
    parser.add_argument("--mode-method", choices=["none", "key", "menu", "both"], default="key")
    parser.add_argument("--close-existing-netedit", action="store_true")
    parser.add_argument("--menu-click", default="45,12")
    parser.add_argument("--connection-click", default="90,246")
    parser.add_argument("--settle-seconds", type=float, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if not args.dry_run:
        raise SystemExit(
            "Foreground NetEdit screenshots are disabled. Use "
            "plugins/torii-sumo/scripts/netedit_background_review.py; "
            "interactive viewing must use the Torii CLI launcher."
        )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_file = out_dir / args.screenshot_name
    status_file = out_dir / args.status_name
    netedit_binary = shutil.which(args.netedit_binary) or args.netedit_binary
    if args.split_screen:
        geometry = split_screen_geometry(args.screen_size or _screen_size())
        args.window_size = geometry["window_size"]
        args.left_pos = geometry["left_pos"]
        args.right_pos = geometry["right_pos"]
    left_view = _generated_view(
        out_dir,
        args.left_name,
        args.left_center,
        choose_zoom(args.left_zoom, fallback=args.zoom),
    ) or _existing_path(args.left_view)
    right_view = _generated_view(
        out_dir,
        args.right_name,
        args.right_center,
        choose_zoom(args.right_zoom, fallback=args.zoom),
    ) or _existing_path(args.right_view)
    windows = [
        NeteditWindow(args.left_name, Path(args.left_net), left_view, _existing_path(args.left_selection), args.left_pos),
        NeteditWindow(
            args.right_name,
            Path(args.right_net),
            right_view,
            _existing_path(args.right_selection),
            args.right_pos,
        ),
    ]
    plan = capture_plan(
        windows,
        screenshot_file=screenshot_file,
        window_size=args.window_size,
        mode_method=args.mode_method,
        netedit_binary=netedit_binary,
    )
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0

    if sys.platform != "win32":
        raise SystemExit("NetEdit window automation is currently Windows-only")

    if args.close_existing_netedit:
        subprocess.run(close_existing_command("netedit.exe"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(1.0)

    processes = [
        subprocess.Popen(item["command"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for item in plan["windows"]
    ]
    pids = [process.pid for process in processes]
    for _ in range(80):
        if all(_windows_for_pid(pid) for pid in pids):
            break
        time.sleep(0.25)
    moved = _move_windows(pids, windows, window_size=args.window_size)
    time.sleep(args.settle_seconds)
    _activate_connection_mode(
        pids,
        method=args.mode_method,
        menu_click=_parse_point(args.menu_click),
        connection_click=_parse_point(args.connection_click),
    )
    time.sleep(args.settle_seconds)
    _raise_windows(pids)
    _screenshot(screenshot_file)
    status = {**plan, "pids": pids, "windows_after_move": moved}
    status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
