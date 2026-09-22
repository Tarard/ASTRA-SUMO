from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Sequence
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic


PW_RENDERFULLCONTENT = 2
SW_SHOWNOACTIVATE = 4
_ALLOWED_MODIFIER_KEYS = {0x10, 0x11, 0x12}  # shift, control, alt
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_DPI_AWARENESS_PER_MONITOR = 2


class _MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class _KeyInput(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("mi", _MouseInput), ("ki", _KeyInput))


class _Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (("type", wintypes.DWORD), ("value", _InputUnion))


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    )


def _which_netedit(name: str) -> str | None:
    # A wheel's console launcher has a different PID from the native GUI window.
    from sumolib import checkBinary
    return shutil.which(checkBinary(name) if name == "netedit" else name)


def _netedit_input_mode(input_file: Path | str) -> str:
    """Return the Netedit input mode for a supported SUMO file."""
    path = Path(input_file)
    lowered_name = path.name.lower()
    if lowered_name.endswith(".net.xml"):
        return "net"
    if lowered_name.endswith(".sumocfg"):
        return "sumocfg"
    raise ValueError(f"unsupported Netedit input type: {path}; expected .net.xml or .sumocfg")


def _build_netedit_open_command(
    input_file: Path | str,
    *,
    netedit_binary: str = "netedit",
    gui_settings_file: Path | str | None = None,
    selection_file: Path | str | None = None,
    window_size: str | None = None,
    window_pos: str | None = None,
) -> list[str]:
    """Build the argument list used to open an existing SUMO network in Netedit.

    ``--sumo-net-file`` (``-s``) is the Netedit option for an existing
    ``.net.xml``.  ``-n`` is intentionally never used because it means plain
    node input for a network build, not a compiled SUMO network.
    """
    path = Path(input_file)
    open_mode = _netedit_input_mode(path)
    if not path.is_file():
        raise FileNotFoundError(f"Netedit input file not found: {path}")

    optional_paths = (
        ("GUI settings", None if gui_settings_file is None else Path(gui_settings_file)),
        ("selection", None if selection_file is None else Path(selection_file)),
    )
    for label, optional_path in optional_paths:
        if optional_path is not None and not optional_path.is_file():
            raise FileNotFoundError(f"Netedit {label} file not found: {optional_path}")

    command = [
        str(netedit_binary),
        "--sumocfg-file" if open_mode == "sumocfg" else "--sumo-net-file",
        str(path),
    ]
    if gui_settings_file is not None:
        command += ["-g", str(gui_settings_file)]
    if selection_file is not None:
        command += ["--selection-file", str(selection_file)]
    if window_size:
        command += ["--window-size", window_size]
    if window_pos:
        command += ["--window-pos", window_pos]
    return command


def _failure_report(
    input_file: Path,
    *,
    netedit_binary: str | None,
    warning: str,
    status: str = "fail",
    netedit_status: str = "failed",
) -> dict[str, Any]:
    return {
        "status": status,
        "claim_status": "construction-invalid" if status == "fail" else "diagnostic-demo",
        "netedit_status": netedit_status,
        "netedit_binary": netedit_binary,
        "netedit_process_id": None,
        "netedit_window_title": "",
        "netedit_network_file": str(input_file),
        "netedit_input_file": str(input_file),
        "command": [],
        "warnings": [warning],
    }


def launch_netedit(
    net_file: Path | str,
    *,
    gui_settings_file: Path | str | None = None,
    selection_file: Path | str | None = None,
    window_size: str | None = None,
    window_pos: str | None = None,
    netedit_binary: str = "netedit",
    detach_console: bool = False,
    which_func: Callable[[str], str | None] = _which_netedit,
    popen_func: Callable[..., Any] = subprocess.Popen,
    platform_name: str = os.name,
) -> dict[str, Any]:
    """Open a SUMO network/config in Netedit without waiting for the GUI."""
    input_file = Path(net_file)
    try:
        open_mode = _netedit_input_mode(input_file)
    except ValueError as exc:
        return _failure_report(input_file, netedit_binary=netedit_binary, warning=str(exc))
    if not input_file.is_file():
        return _failure_report(
            input_file,
            netedit_binary=netedit_binary,
            warning=f"network file not found: {input_file}",
        )

    resolved_binary = which_func(netedit_binary)
    if resolved_binary is None:
        return _failure_report(
            input_file,
            netedit_binary=None,
            warning="netedit binary not found",
            status="blocked",
            netedit_status="unavailable",
        )

    try:
        command = _build_netedit_open_command(
            input_file,
            netedit_binary=resolved_binary,
            gui_settings_file=gui_settings_file,
            selection_file=selection_file,
            window_size=window_size,
            window_pos=window_pos,
        )
    except (FileNotFoundError, ValueError) as exc:
        return _failure_report(input_file, netedit_binary=resolved_binary, warning=str(exc))

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    detached = bool(detach_console and platform_name == "nt")
    if detached:
        popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0x00000200,
        )
    try:
        process = popen_func(command, **popen_kwargs)
    except OSError as exc:
        report = _failure_report(
            input_file,
            netedit_binary=resolved_binary,
            warning=f"{type(exc).__name__}: {exc}",
        )
        report.update(
            {
                "netedit_open_mode": open_mode,
                "netedit_gui_settings_file": str(gui_settings_file) if gui_settings_file is not None else "",
                "netedit_selection_file": str(selection_file) if selection_file is not None else "",
                "netedit_window_size": window_size or "",
                "netedit_window_pos": window_pos or "",
                "netedit_detached": detached,
                "command": command,
            }
        )
        return report

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "netedit_status": "opened",
        "netedit_binary": resolved_binary,
        "netedit_process_id": process.pid,
        "netedit_window_title": "",
        "netedit_network_file": str(input_file),
        "netedit_input_file": str(input_file),
        "netedit_open_mode": open_mode,
        "netedit_gui_settings_file": str(gui_settings_file) if gui_settings_file is not None else "",
        "netedit_selection_file": str(selection_file) if selection_file is not None else "",
        "netedit_window_size": window_size or "",
        "netedit_window_pos": window_pos or "",
        "netedit_detached": detached,
        "command": command,
        "warnings": [],
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _windows_modules() -> tuple[Any, Any, Any, Any]:
    try:
        import win32con
        import win32gui
        import win32process
        import win32ui
    except ImportError as exc:  # pragma: no cover - Windows runtime guard
        raise RuntimeError("pywin32 is required for a NetEdit target session") from exc
    return win32con, win32gui, win32process, win32ui


def _wait_for_netedit_window(pid: int, timeout_seconds: float) -> int:
    _, win32gui, win32process, _ = _windows_modules()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        windows: list[int] = []

        def collect(hwnd: int, _: Any) -> bool:
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid == pid and win32gui.IsWindowVisible(hwnd):
                windows.append(hwnd)
            return True

        win32gui.EnumWindows(collect, None)
        if windows:
            return windows[0]
        time.sleep(0.1)
    raise TimeoutError(f"NetEdit process {pid} did not create a visible window")


def _require_owned_window(hwnd: int, pid: int) -> None:
    _, win32gui, win32process, _ = _windows_modules()
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("NetEdit target window is no longer valid")
    _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
    if int(window_pid) != int(pid):
        raise RuntimeError(f"window {hwnd} belongs to process {window_pid}, not NetEdit process {pid}")


def _capture_target_window(hwnd: int, destination: Path) -> dict[str, Any]:
    _, win32gui, _, win32ui = _windows_modules()
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    window_width, window_height = right - left, bottom - top
    client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(hwnd)
    client_width = client_right - client_left
    client_height = client_bottom - client_top
    client_origin_x, client_origin_y = win32gui.ClientToScreen(hwnd, (0, 0))
    crop_left = client_origin_x - left
    crop_top = client_origin_y - top
    crop_right = crop_left + client_width
    crop_bottom = crop_top + client_height
    if window_width <= 0 or window_height <= 0 or client_width <= 0 or client_height <= 0:
        raise RuntimeError("NetEdit target window has no drawable area")
    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, window_width, window_height)
    memory_dc.SelectObject(bitmap)
    try:
        printer = ctypes.windll.user32.PrintWindow
        printer.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        printer.restype = wintypes.BOOL
        result = printer(hwnd, memory_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bitmap.GetInfo()
        full_window_image = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bitmap.GetBitmapBits(True),
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
        if not (
            0 <= crop_left < crop_right <= full_window_image.width
            and 0 <= crop_top < crop_bottom <= full_window_image.height
        ):
            raise RuntimeError(
                "NetEdit client rectangle is outside the captured top-level window "
                f"(window={full_window_image.width}x{full_window_image.height}, "
                f"client_crop={crop_left},{crop_top},{crop_right},{crop_bottom})"
            )
        image = full_window_image.crop((crop_left, crop_top, crop_right, crop_bottom))
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
    return {
        "print_window_result": int(result),
        "coordinate_space": "netedit_top_level_client",
        "width": client_width,
        "height": client_height,
        "top_level_window_width": window_width,
        "top_level_window_height": window_height,
        "client_crop_in_window": [crop_left, crop_top, crop_right, crop_bottom],
        "window_dpi": _window_dpi(hwnd),
        "sha256": _file_sha256(destination),
    }


def _client_size(hwnd: int) -> tuple[int, int]:
    _, win32gui, _, _ = _windows_modules()
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return right - left, bottom - top


def _compare_viewport_images(
    previous_file: Path,
    current_file: Path,
    *,
    protected_points: Sequence[tuple[int, int]] = (),
    protected_radius: int = 24,
) -> dict[str, Any]:
    """Allow minor editor animation while protecting actual click/drag targets."""

    from PIL import Image, ImageChops

    with Image.open(previous_file) as previous_image, Image.open(current_file) as current_image:
        previous = previous_image.convert("RGB")
        current = current_image.convert("RGB")
    if previous.size != current.size:
        return {
            "status": "fail",
            "reason": "viewport dimensions changed",
            "previous_size": list(previous.size),
            "current_size": list(current.size),
        }
    difference = ImageChops.difference(previous, current)
    red, green, blue = difference.split()
    maximum_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = maximum_channel.histogram()
    pixel_count = previous.width * previous.height
    changed_pixel_count = pixel_count - histogram[0]
    changed_fraction = changed_pixel_count / pixel_count
    mean_max_channel_delta = sum(index * count for index, count in enumerate(histogram)) / pixel_count
    point_checks = []
    protected_points_unchanged = True
    for x, y in protected_points:
        box = (
            max(0, x - protected_radius),
            max(0, y - protected_radius),
            min(previous.width, x + protected_radius + 1),
            min(previous.height, y + protected_radius + 1),
        )
        unchanged = maximum_channel.crop(box).getbbox() is None
        point_checks.append({"point": [x, y], "box": list(box), "unchanged": unchanged})
        protected_points_unchanged = protected_points_unchanged and unchanged
    global_drift_allowed = changed_fraction <= 0.01 and mean_max_channel_delta <= 2.0
    return {
        "status": "pass" if global_drift_allowed and protected_points_unchanged else "fail",
        "reason": (
            "bounded editor animation only"
            if global_drift_allowed and protected_points_unchanged
            else "viewport or protected action target changed"
        ),
        "previous_size": list(previous.size),
        "current_size": list(current.size),
        "changed_pixel_count": changed_pixel_count,
        "changed_pixel_fraction": changed_fraction,
        "mean_max_channel_delta": mean_max_channel_delta,
        "maximum_changed_pixel_fraction": 0.01,
        "maximum_mean_channel_delta": 2.0,
        "protected_radius_px": protected_radius,
        "protected_point_checks": point_checks,
    }


def _enable_per_monitor_dpi_awareness() -> dict[str, Any]:
    """Use physical pixels for screenshots, client coordinates, and SendInput."""

    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is None:
        raise RuntimeError("Per-Monitor-V2 DPI awareness is unavailable")
    setter.argtypes = [wintypes.HANDLE]
    setter.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    enabled = bool(setter(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    error = int(ctypes.get_last_error())
    if not enabled and error not in {0, 5}:
        raise RuntimeError(f"unable to enable Per-Monitor-V2 DPI awareness (Win32 error {error})")
    context_getter = getattr(user32, "GetThreadDpiAwarenessContext", None)
    awareness_getter = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)
    context_equal = getattr(user32, "AreDpiAwarenessContextsEqual", None)
    if context_getter is None or awareness_getter is None or context_equal is None:
        raise RuntimeError("unable to verify the active DPI awareness context")
    context_getter.argtypes = []
    context_getter.restype = wintypes.HANDLE
    awareness_getter.argtypes = [wintypes.HANDLE]
    awareness_getter.restype = ctypes.c_int
    context_equal.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    context_equal.restype = wintypes.BOOL
    current_context = context_getter()
    awareness = int(awareness_getter(current_context))
    is_per_monitor_v2 = bool(
        context_equal(current_context, _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    )
    if awareness != _DPI_AWARENESS_PER_MONITOR or not is_per_monitor_v2:
        raise RuntimeError(
            "NetEdit input requires Per-Monitor-V2 DPI awareness; "
            f"the active thread reports awareness={awareness}, PMv2={is_per_monitor_v2}"
        )
    return {
        "status": "enabled_or_already_configured",
        "mode": "per_monitor_v2",
        "verified_thread_awareness": awareness,
        "verified_per_monitor_v2_context": is_per_monitor_v2,
        "already_configured": not enabled,
        "win32_error": error,
    }


def _window_dpi(hwnd: int) -> int:
    getter = getattr(ctypes.windll.user32, "GetDpiForWindow", None)
    if getter is None:
        return 96
    getter.argtypes = [wintypes.HWND]
    getter.restype = wintypes.UINT
    return max(1, int(getter(hwnd)))


def _wait_for_file_stable(path: Path, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[int, int, str] | None = None
    while time.monotonic() < deadline:
        stat = path.stat()
        current = (stat.st_mtime_ns, stat.st_size, _file_sha256(path))
        if current == previous:
            return {"mtime_ns": current[0], "size": current[1], "sha256": current[2]}
        previous = current
        time.sleep(0.1)
    raise TimeoutError(f"NetEdit candidate did not stabilize after save: {path}")


def _gui_focus(hwnd: int) -> int:
    _, _, win32process, _ = _windows_modules()
    thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)
    info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
    getter = ctypes.windll.user32.GetGUIThreadInfo
    getter.argtypes = [wintypes.DWORD, ctypes.POINTER(_GuiThreadInfo)]
    getter.restype = wintypes.BOOL
    if not getter(thread_id, ctypes.byref(info)):
        raise RuntimeError("unable to inspect NetEdit GUI focus")
    return int(info.hwndFocus or 0)


def _focus_canvas(hwnd: int) -> int:
    _, win32gui, _, _ = _windows_modules()
    canvases: list[int] = []

    def collect(child: int, _: Any) -> bool:
        if win32gui.GetClassName(child) == "FXGLCanvas":
            canvases.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, collect, None)
    return canvases[0] if canvases else hwnd


def _set_foreground_and_focus(hwnd: int, focus_hwnd: int) -> None:
    _, win32gui, win32process, _ = _windows_modules()
    user32 = ctypes.windll.user32
    current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
    foreground = int(win32gui.GetForegroundWindow())
    foreground_thread = int(win32process.GetWindowThreadProcessId(foreground)[0]) if foreground else 0
    target_thread = int(win32process.GetWindowThreadProcessId(hwnd)[0])
    attached: list[int] = []
    for thread_id in {foreground_thread, target_thread}:
        if thread_id and thread_id != current_thread and user32.AttachThreadInput(current_thread, thread_id, True):
            attached.append(thread_id)
    try:
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetFocus(focus_hwnd)
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, False)


def _activate_target_window(hwnd: int, pid: int) -> dict[str, Any]:
    _, win32gui, _, _ = _windows_modules()
    _require_owned_window(hwnd, pid)
    previous_foreground = int(win32gui.GetForegroundWindow())
    previous_focus = _gui_focus(previous_foreground) if previous_foreground else 0
    previous_cursor = tuple(int(value) for value in win32gui.GetCursorPos())
    context = {
        "previous_foreground_hwnd": previous_foreground,
        "previous_focus_hwnd": previous_focus,
        "previous_cursor": list(previous_cursor),
    }
    try:
        focus = _focus_canvas(hwnd)
        _set_foreground_and_focus(hwnd, focus)
        foreground = int(win32gui.GetForegroundWindow())
        observed_focus = _gui_focus(hwnd)
        if foreground != hwnd or not (observed_focus == hwnd or win32gui.IsChild(hwnd, observed_focus)):
            raise RuntimeError("NetEdit did not acquire exact foreground and keyboard focus")
    except Exception:
        restored = _restore_input_context(context)
        if not restored["restored"]:
            raise RuntimeError("NetEdit activation failed and the previous input context was not restored")
        raise
    return {
        **context,
        "target_foreground_hwnd": foreground,
        "target_focus_hwnd": observed_focus,
    }


def _restore_input_context(context: dict[str, Any]) -> dict[str, Any]:
    _, win32gui, _, _ = _windows_modules()
    cursor = tuple(context["previous_cursor"])
    ctypes.windll.user32.SetCursorPos(*cursor)
    previous = int(context["previous_foreground_hwnd"])
    focus = int(context["previous_focus_hwnd"])
    if previous and win32gui.IsWindow(previous):
        if not focus or not (focus == previous or win32gui.IsChild(previous, focus)):
            focus = previous
        _set_foreground_and_focus(previous, focus)
    restored_foreground = int(win32gui.GetForegroundWindow())
    restored_cursor = tuple(int(value) for value in win32gui.GetCursorPos())
    restored_focus = _gui_focus(previous) if previous and win32gui.IsWindow(previous) else 0
    focus_restored = not previous or restored_focus == focus or (
        focus == previous and (restored_focus == previous or win32gui.IsChild(previous, restored_focus))
    )
    return {
        "foreground_hwnd": restored_foreground,
        "focus_hwnd": restored_focus,
        "cursor": list(restored_cursor),
        "restored": (
            (not previous or restored_foreground == previous)
            and focus_restored
            and restored_cursor == cursor
        ),
    }


def _send_inputs(inputs: list[_Input]) -> int:
    array = (_Input * len(inputs))(*inputs)
    sender = ctypes.windll.user32.SendInput
    sender.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    sender.restype = wintypes.UINT
    sent = int(sender(len(array), array, ctypes.sizeof(_Input)))
    if sent != len(array):
        raise RuntimeError(f"SendInput delivered {sent}/{len(array)} events")
    return sent


def _release_partial_input(action: dict[str, Any]) -> int:
    """Best-effort release for a partially delivered key chord or mouse drag."""

    if action["type"] == "key":
        releases = [_key_input(action["virtual_key"], up=True)]
        releases += [_key_input(value, up=True) for value in reversed(tuple(action["modifier_keys"]))]
    else:
        _, win32gui, _, _ = _windows_modules()
        screen_x, screen_y = win32gui.GetCursorPos()
        releases = [_mouse_input(screen_x, screen_y, _MOUSEEVENTF_LEFTUP)]
    array = (_Input * len(releases))(*releases)
    sender = ctypes.windll.user32.SendInput
    sender.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    sender.restype = wintypes.UINT
    return int(sender(len(array), array, ctypes.sizeof(_Input)))


def _assert_physical_input_idle(action: dict[str, Any]) -> dict[str, Any]:
    """Refuse injection while user-held keys could change the action meaning."""

    getter = ctypes.windll.user32.GetAsyncKeyState
    getter.argtypes = [ctypes.c_int]
    getter.restype = ctypes.c_short
    keys = range(1, 0xFF)
    pressed = sorted(key for key in keys if int(getter(key)) & 0x8000)
    if pressed:
        raise RuntimeError(
            "physical keyboard or mouse input is active; release these virtual keys before retrying: "
            + ", ".join(f"0x{key:02X}" for key in pressed)
        )
    return {
        "status": "idle",
        "checked_virtual_key_range": [1, 0xFE],
        "requested_action_type": action["type"],
    }


def _key_input(virtual_key: int, *, up: bool = False) -> _Input:
    return _Input(
        type=_INPUT_KEYBOARD,
        ki=_KeyInput(
            wVk=virtual_key,
            wScan=0,
            dwFlags=_KEYEVENTF_KEYUP if up else 0,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _mouse_input(screen_x: int, screen_y: int, flags: int) -> _Input:
    user32 = ctypes.windll.user32
    virtual_left = int(user32.GetSystemMetrics(76))
    virtual_top = int(user32.GetSystemMetrics(77))
    virtual_width = max(1, int(user32.GetSystemMetrics(78)) - 1)
    virtual_height = max(1, int(user32.GetSystemMetrics(79)) - 1)
    absolute_x = round((screen_x - virtual_left) * 65535 / virtual_width)
    absolute_y = round((screen_y - virtual_top) * 65535 / virtual_height)
    return _Input(
        type=_INPUT_MOUSE,
        mi=_MouseInput(
            dx=absolute_x,
            dy=absolute_y,
            mouseData=0,
            dwFlags=flags | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _perform_real_input(
    hwnd: int,
    pid: int,
    action: dict[str, Any],
    *,
    post_input_seconds: float = 0.2,
) -> dict[str, Any]:
    _, win32gui, _, _ = _windows_modules()
    physical_input = _assert_physical_input_idle(action)
    context = _activate_target_window(hwnd, pid)
    try:
        if int(win32gui.GetForegroundWindow()) != hwnd:
            raise RuntimeError("NetEdit lost foreground before SendInput")
        if action["type"] == "key":
            modifiers = tuple(action["modifier_keys"])
            inputs = [_key_input(value) for value in modifiers]
            inputs += [_key_input(action["virtual_key"]), _key_input(action["virtual_key"], up=True)]
            inputs += [_key_input(value, up=True) for value in reversed(modifiers)]
        else:
            start = win32gui.ClientToScreen(hwnd, (action["x"], action["y"]))
            inputs = [_mouse_input(*start, _MOUSEEVENTF_MOVE), _mouse_input(*start, _MOUSEEVENTF_LEFTDOWN)]
            if action["type"] == "drag":
                end = win32gui.ClientToScreen(hwnd, (action["to_x"], action["to_y"]))
                inputs.append(_mouse_input(*end, _MOUSEEVENTF_MOVE))
            inputs.append(_mouse_input(*(end if action["type"] == "drag" else start), _MOUSEEVENTF_LEFTUP))
        try:
            sent = _send_inputs(inputs)
        except Exception:
            _release_partial_input(action)
            raise
        time.sleep(max(0.05, post_input_seconds))
        foreground_after = int(win32gui.GetForegroundWindow())
        focus_after = _gui_focus(hwnd)
        if foreground_after != hwnd or not (focus_after == hwnd or win32gui.IsChild(hwnd, focus_after)):
            raise RuntimeError("NetEdit lost foreground or focus before consuming the injected input")
    finally:
        restored = _restore_input_context(context)
    if not restored["restored"]:
        raise RuntimeError("previous input context was not restored after NetEdit action")
    return {
        "send_input_event_count": sent,
        "physical_input_preflight": physical_input,
        "focus_context": context,
        "target_foreground_after_input": foreground_after,
        "target_focus_after_input": focus_after,
        "restore": restored,
    }


class NeteditTargetSession:
    """Hash-bound, target-window-only NetEdit edit session for Windows."""

    def __init__(
        self,
        source_net_file: Path | str,
        candidate_net_file: Path | str,
        output_dir: Path | str,
        *,
        expected_source_sha256: str,
        netedit_binary: str = "netedit",
        gui_settings_file: Path | str | None = None,
        selection_file: Path | str | None = None,
        target_source_junction_ids: Sequence[str] = (),
        target_candidate_junction_ids: Sequence[str] = (),
        window_size: str = "1400,1000",
        window_pos: str = "20,20",
        settle_seconds: float = 0.5,
        window_timeout_seconds: float = 20.0,
        platform_name: str = sys.platform,
        which_func: Callable[[str], str | None] = _which_netedit,
        popen_func: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.source = Path(source_net_file).resolve()
        self.candidate = Path(candidate_net_file).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.expected_source_sha256 = expected_source_sha256
        self.netedit_binary = netedit_binary
        self.gui_settings_file = None if gui_settings_file is None else Path(gui_settings_file).resolve()
        self.selection_file = None if selection_file is None else Path(selection_file).resolve()
        self.gui_settings_snapshot_file: Path | None = None
        self.selection_snapshot_file: Path | None = None
        self.target_source_junction_ids = tuple(
            dict.fromkeys(str(value).strip() for value in target_source_junction_ids if str(value).strip())
        )
        self.target_candidate_junction_ids = tuple(
            dict.fromkeys(str(value).strip() for value in target_candidate_junction_ids if str(value).strip())
        )
        self.window_size = window_size
        self.window_pos = window_pos
        self.settle_seconds = settle_seconds
        self.window_timeout_seconds = window_timeout_seconds
        self.platform_name = platform_name
        self.which_func = which_func
        self.popen_func = popen_func
        self.state = "created"
        self.process: Any | None = None
        self.hwnd = 0
        self.command: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.initial_candidate_sha256 = ""
        self.initial_selection_sha256 = ""
        self.preloaded_artifacts: list[dict[str, Any]] = []
        self.global_input_used = False
        self.foreground_activation_used = False
        self.dpi_awareness: dict[str, Any] = {}
        self.report_file = self.output_dir / "netedit-target-session.json"

    def open(self) -> dict[str, Any]:
        if self.state != "created":
            raise RuntimeError(f"cannot open NetEdit session from state {self.state}")
        if self.platform_name != "win32":
            raise RuntimeError("NetEdit target sessions are Windows-only")
        self.dpi_awareness = _enable_per_monitor_dpi_awareness()
        if not self.source.is_file() or not self.source.name.lower().endswith(".net.xml"):
            raise ValueError(f"source must be an existing .net.xml file: {self.source}")
        if self.candidate == self.source:
            raise ValueError("candidate must differ from source")
        if self.candidate.exists():
            raise FileExistsError(f"candidate already exists: {self.candidate}")
        source_hash = _file_sha256(self.source)
        if source_hash != self.expected_source_sha256:
            raise ValueError(
                f"source SHA-256 mismatch: expected {self.expected_source_sha256}, observed {source_hash}"
            )
        source_root = ET.parse(self.source).getroot()
        source_junction_ids = {
            str(junction.get("id"))
            for junction in source_root.findall("junction")
            if junction.get("id")
        }
        missing_source_scope = sorted(
            set(self.target_source_junction_ids) - source_junction_ids
        )
        if missing_source_scope:
            raise ValueError(
                "target_source_junction_ids are absent from the source network: "
                + ", ".join(missing_source_scope)
            )
        executable = self.which_func(self.netedit_binary)
        if executable is None:
            raise FileNotFoundError("netedit binary not found")
        if Path(executable).stem.lower() != "netedit":
            raise ValueError(f"target executable is not NetEdit: {executable}")
        for label, path in (("GUI settings", self.gui_settings_file), ("selection", self.selection_file)):
            if path is not None and not path.is_file():
                raise FileNotFoundError(f"NetEdit {label} file not found: {path}")
        if self.output_dir.exists():
            if not self.output_dir.is_dir():
                raise NotADirectoryError(f"NetEdit session output is not a directory: {self.output_dir}")
            if any(self.output_dir.iterdir()):
                raise FileExistsError(
                    f"NetEdit session output directory must be fresh and empty: {self.output_dir}"
                )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        preload_dir = self.output_dir / "preloaded"
        preload_dir.mkdir(parents=True, exist_ok=True)
        for role, source_path, snapshot_name in (
            ("gui_settings", self.gui_settings_file, "gui-settings.xml"),
            ("selection", self.selection_file, "selection.txt"),
        ):
            if source_path is None:
                continue
            source_artifact_hash = _file_sha256(source_path)
            snapshot_path = preload_dir / snapshot_name
            if snapshot_path.exists():
                raise FileExistsError(f"NetEdit preload snapshot already exists: {snapshot_path}")
            shutil.copy2(source_path, snapshot_path)
            snapshot_hash = _file_sha256(snapshot_path)
            if snapshot_hash != source_artifact_hash:
                raise RuntimeError(f"NetEdit {role} snapshot does not match its source bytes")
            if role == "gui_settings":
                self.gui_settings_snapshot_file = snapshot_path
            else:
                self.selection_snapshot_file = snapshot_path
                self.initial_selection_sha256 = snapshot_hash
            self.preloaded_artifacts.append(
                {
                    "role": role,
                    "source_path": str(source_path),
                    "snapshot_path": str(snapshot_path),
                    "sha256": snapshot_hash,
                }
            )
        self.candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source, self.candidate)
        self.initial_candidate_sha256 = _file_sha256(self.candidate)
        if self.initial_candidate_sha256 != self.expected_source_sha256:
            self.candidate.unlink(missing_ok=True)
            raise RuntimeError("candidate copy does not match the immutable source SHA-256")
        self.command = _build_netedit_open_command(
            self.candidate,
            netedit_binary=executable,
            gui_settings_file=self.gui_settings_snapshot_file,
            selection_file=self.selection_snapshot_file,
            window_size=self.window_size,
            window_pos=self.window_pos,
        )
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        startup_info_class = getattr(subprocess, "STARTUPINFO", None)
        if startup_info_class is not None:
            startup_info = startup_info_class()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = SW_SHOWNOACTIVATE
            popen_kwargs["startupinfo"] = startup_info
        self.process = self.popen_func(self.command, **popen_kwargs)
        try:
            self.hwnd = _wait_for_netedit_window(self.process.pid, self.window_timeout_seconds)
            _require_owned_window(self.hwnd, self.process.pid)
            render_context = _activate_target_window(self.hwnd, self.process.pid)
            self.foreground_activation_used = True
            time.sleep(self.settle_seconds)
            render_restore = _restore_input_context(render_context)
            if not render_restore["restored"]:
                raise RuntimeError("foreground context was not restored after NetEdit initial render")
            self.state = "open"
            return self._record_step(
                "open",
                {
                    "window_title": self._window_title(),
                    "render_activation": render_context,
                    "render_restore": render_restore,
                },
            )
        except Exception:
            self._stop_process()
            self.state = "aborted"
            raise

    def observe(self, label: str = "observe") -> dict[str, Any]:
        self._require_open()
        return self._record_step("observe", {"label": label})

    def act(self, action: dict[str, Any]) -> dict[str, Any]:
        self._require_open()
        expected_screenshot = action.get("expected_screenshot_sha256")
        current_screenshot = self.steps[-1]["screenshot_sha256"] if self.steps else ""
        if expected_screenshot != current_screenshot:
            raise ValueError("action requires the exact latest screenshot SHA-256")
        kind = action.get("type")
        if kind in {"click", "drag"}:
            x, y = action.get("x"), action.get("y")
            width, height = _client_size(self.hwnd)
            self._validate_point(x, y, width, height, label=kind)
            normalized = {"type": kind, "x": x, "y": y}
            if kind == "drag":
                to_x, to_y = action.get("to_x"), action.get("to_y")
                self._validate_point(to_x, to_y, width, height, label="drag destination")
                normalized.update({"to_x": to_x, "to_y": to_y})
        elif kind == "key":
            virtual_key = action.get("virtual_key")
            modifiers = tuple(action.get("modifier_keys", ()))
            if not isinstance(virtual_key, int) or isinstance(virtual_key, bool) or not 1 <= virtual_key <= 0xFE:
                raise ValueError("virtual_key must be an integer from 1 to 254")
            if any(not isinstance(value, int) or value not in _ALLOWED_MODIFIER_KEYS for value in modifiers):
                raise ValueError("modifier_keys may contain only Shift, Control, or Alt")
            normalized = {"type": "key", "virtual_key": virtual_key, "modifier_keys": list(modifiers)}
        else:
            raise ValueError("action type must be 'click', 'drag', or 'key'")
        protected_points = (
            ((normalized["x"], normalized["y"]),)
            if kind == "click"
            else (
                (normalized["x"], normalized["y"]),
                (normalized["to_x"], normalized["to_y"]),
            )
            if kind == "drag"
            else ()
        )
        selection_lock = (
            self._verify_f7_selection_lock()
            if kind == "key" and normalized["virtual_key"] == 0x76
            else None
        )
        preflight = self._verify_current_viewport(
            expected_screenshot,
            label="act",
            protected_points=protected_points,
            exact_pixels=kind == "key",
        )
        try:
            delivery = _perform_real_input(
                self.hwnd,
                self.process.pid,
                normalized,
                post_input_seconds=self.settle_seconds,
            )
        except Exception as exc:
            if "physical keyboard or mouse input is active" not in str(exc):
                self.global_input_used = True
            raise
        self.global_input_used = True
        time.sleep(self.settle_seconds)
        try:
            return self._record_step(
                "act",
                {
                    **normalized,
                    "selection_lock": selection_lock,
                    "preflight_observation": preflight,
                    "delivery": delivery,
                },
            )
        except Exception as exc:
            self._stop_process()
            self.state = "aborted"
            self._persist_report()
            raise RuntimeError(
                "post-input evidence capture failed; NetEdit session was aborted"
            ) from exc

    def finalize(self, *, expected_screenshot_sha256: str) -> dict[str, Any]:
        self._require_open()
        current_screenshot = self.steps[-1]["screenshot_sha256"] if self.steps else ""
        if expected_screenshot_sha256 != current_screenshot:
            raise ValueError("finalize requires the exact latest screenshot SHA-256")
        save_action = {"type": "key", "virtual_key": ord("S"), "modifier_keys": [0x11]}
        preflight = self._verify_current_viewport(
            expected_screenshot_sha256,
            label="finalize",
            exact_pixels=True,
        )
        try:
            try:
                save = _perform_real_input(
                    self.hwnd,
                    self.process.pid,
                    save_action,
                    post_input_seconds=self.settle_seconds,
                )
            except Exception as exc:
                if "physical keyboard or mouse input is active" not in str(exc):
                    self.global_input_used = True
                raise
            self.global_input_used = True
            time.sleep(self.settle_seconds)
            stable_file = _wait_for_file_stable(self.candidate)
            if _file_sha256(self.source) != self.expected_source_sha256:
                raise RuntimeError("source network changed during NetEdit finalize")
            ET.parse(self.candidate)
            self._record_step(
                "finalize",
                {
                    "preflight_observation": preflight,
                    "save_shortcut": save,
                    "stable_candidate": stable_file,
                },
            )
        except Exception as exc:
            if "physical keyboard or mouse input is active" in str(exc):
                raise
            self._stop_process()
            self.state = "aborted"
            self._persist_report()
            raise RuntimeError(
                "post-save validation or evidence capture failed; NetEdit session was aborted"
            ) from exc
        self._stop_process()
        self.state = "finalized"
        return self._persist_report()

    def abort(self, reason: str = "caller_aborted") -> dict[str, Any]:
        observation_error = ""
        try:
            self._require_open()
            self._record_step("abort", {"reason": reason})
        except Exception as exc:
            observation_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._stop_process()
            self.state = "aborted"
        report = self._persist_report()
        if observation_error:
            report["abort_observation_error"] = observation_error
            write_json_atomic(self.report_file, {key: value for key, value in report.items() if key != "report_file"})
        return report

    @staticmethod
    def _validate_point(x: Any, y: Any, width: int, height: int, *, label: str) -> None:
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValueError(f"{label} coordinates must be integers")
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"{label} ({x}, {y}) is outside NetEdit client area {width}x{height}")

    def _verify_f7_selection_lock(self) -> dict[str, Any]:
        """Bind junction aggregation to the immutable preloaded selection and declared scope."""

        if self.selection_snapshot_file is None:
            raise ValueError("F7 requires a preloaded immutable NetEdit selection file")
        current_hash = _file_sha256(self.selection_snapshot_file)
        if not self.initial_selection_sha256 or current_hash != self.initial_selection_sha256:
            raise RuntimeError("NetEdit selection file changed after the session opened")
        prior_actions = [step for step in self.steps if step.get("kind") == "act"]
        if prior_actions:
            raise ValueError("F7 must be the first edit action in a NetEdit target session")
        selected_junction_ids = sorted(
            {
                line.split(":", 1)[1].strip()
                for line in self.selection_snapshot_file.read_text(encoding="utf-8-sig").splitlines()
                if line.strip().startswith("junction:") and line.split(":", 1)[1].strip()
            }
        )
        expected_junction_ids = sorted(self.target_source_junction_ids)
        if not expected_junction_ids or selected_junction_ids != expected_junction_ids:
            raise ValueError(
                "F7 selection junction IDs must exactly equal target_source_junction_ids "
                f"(selected={selected_junction_ids}, declared={expected_junction_ids})"
            )
        return {
            "status": "pass",
            "selection_file": str(self.selection_snapshot_file),
            "selection_sha256": current_hash,
            "selected_junction_ids": selected_junction_ids,
            "declared_source_junction_ids": expected_junction_ids,
            "first_edit_action": True,
        }

    def _require_open(self) -> None:
        if self.state != "open" or self.process is None:
            raise RuntimeError(f"NetEdit session is not open (state={self.state})")
        poll = getattr(self.process, "poll", None)
        if poll is not None and poll() is not None:
            raise RuntimeError("NetEdit process has exited")
        _require_owned_window(self.hwnd, self.process.pid)

    def _window_title(self) -> str:
        _, win32gui, _, _ = _windows_modules()
        return str(win32gui.GetWindowText(self.hwnd))

    def _record_step(self, kind: str, detail: dict[str, Any]) -> dict[str, Any]:
        if self.process is None:
            raise RuntimeError("NetEdit process is unavailable")
        _require_owned_window(self.hwnd, self.process.pid)
        source_hash = _file_sha256(self.source)
        if source_hash != self.expected_source_sha256:
            raise RuntimeError("source network changed during NetEdit session")
        step_number = len(self.steps)
        screenshot = self.output_dir / f"{step_number:03d}-{kind}.png"
        if screenshot.exists():
            raise FileExistsError(f"session screenshot already exists: {screenshot}")
        capture = self._capture_viewport(screenshot)
        step = {
            "index": step_number,
            "kind": kind,
            "detail": detail,
            "source_sha256": source_hash,
            "candidate_sha256": _file_sha256(self.candidate),
            "screenshot_file": str(screenshot),
            "screenshot_sha256": capture["sha256"],
            "capture": capture,
        }
        self.steps.append(step)
        self._persist_report()
        return step

    def _capture_viewport(self, screenshot: Path) -> dict[str, Any]:
        capture = _capture_target_window(self.hwnd, screenshot)
        if (
            capture["print_window_result"] != 1
            or capture["width"] < 640
            or capture["height"] < 480
        ):
            raise RuntimeError(
                "NetEdit observation is not a usable rendered client viewport "
                f"(PrintWindow={capture['print_window_result']}, "
                f"size={capture['width']}x{capture['height']})"
            )
        if capture.get("coordinate_space", "netedit_top_level_client") != "netedit_top_level_client":
            raise RuntimeError("NetEdit screenshot and action coordinates are not in the same client space")
        return capture

    def _verify_current_viewport(
        self,
        expected_sha256: str,
        *,
        label: str,
        protected_points: Sequence[tuple[int, int]] = (),
        exact_pixels: bool = False,
    ) -> dict[str, Any]:
        source_hash = _file_sha256(self.source)
        if source_hash != self.expected_source_sha256:
            raise RuntimeError("source network changed before NetEdit action")
        screenshot = self.output_dir / f"{len(self.steps):03d}-{label}-preflight.png"
        if screenshot.exists():
            raise FileExistsError(f"session preflight screenshot already exists: {screenshot}")
        capture = self._capture_viewport(screenshot)
        previous_screenshot = Path(self.steps[-1]["screenshot_file"])
        if _file_sha256(previous_screenshot) != expected_sha256:
            raise RuntimeError("previous NetEdit screenshot artifact no longer matches its recorded SHA-256")
        comparison = (
            {
                "status": "pass" if capture["sha256"] == expected_sha256 else "fail",
                "reason": (
                    "live viewport exactly matches the recorded screenshot"
                    if capture["sha256"] == expected_sha256
                    else "live viewport changed after the recorded screenshot"
                ),
                "exact_pixels_required": True,
                "previous_screenshot_sha256": expected_sha256,
                "current_screenshot_sha256": capture["sha256"],
            }
            if exact_pixels
            else _compare_viewport_images(
                previous_screenshot,
                screenshot,
                protected_points=protected_points,
            )
        )
        if comparison["status"] != "pass":
            raise ValueError(
                "NetEdit viewport or protected action target changed after the last observation; "
                "observe again before acting"
            )
        return {
            "expected_previous_screenshot_sha256": expected_sha256,
            "screenshot_file": str(screenshot),
            **capture,
            "viewport_comparison": comparison,
        }

    def _stop_process(self) -> None:
        if self.process is None:
            return
        if self.hwnd:
            try:
                _require_owned_window(self.hwnd, self.process.pid)
                win32con, win32gui, _, _ = _windows_modules()
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        try:
            self.process.wait(timeout=3.0)
        except Exception:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
                except Exception:
                    pass

    def _persist_report(self) -> dict[str, Any]:
        source_hash = _file_sha256(self.source)
        candidate_hash = _file_sha256(self.candidate) if self.candidate.is_file() else ""
        candidate_xml_parseable = False
        if candidate_hash:
            try:
                ET.parse(self.candidate)
                candidate_xml_parseable = True
            except ET.ParseError:
                pass
        report = {
            "schema": "torii.netedit-target-session/v1",
            "status": self.state,
            "source_net_file": str(self.source),
            "source_sha256_expected": self.expected_source_sha256,
            "source_sha256_observed": source_hash,
            "source_unchanged": source_hash == self.expected_source_sha256,
            "candidate_net_file": str(self.candidate),
            "candidate_sha256_initial": self.initial_candidate_sha256,
            "candidate_sha256": candidate_hash,
            "candidate_changed": bool(candidate_hash and candidate_hash != self.initial_candidate_sha256),
            "candidate_xml_parseable": candidate_xml_parseable,
            "semantic_edit_status": (
                "unverified_candidate_delta"
                if candidate_hash and candidate_hash != self.initial_candidate_sha256
                else "no_candidate_byte_delta"
            ),
            "semantic_edit_success": False,
            "command": self.command,
            "window_size": self.window_size,
            "window_pos": self.window_pos,
            "preloaded_artifacts": self.preloaded_artifacts,
            "declared_edit_scope": {
                "target_source_junction_ids": list(self.target_source_junction_ids),
                "target_candidate_junction_ids": list(self.target_candidate_junction_ids),
                "fixed_before_gui_edit": True,
            },
            "netedit_process_id": None if self.process is None else self.process.pid,
            "netedit_window_handle": self.hwnd,
            "steps": self.steps,
            "global_keyboard_or_mouse_input_used": self.global_input_used,
            "foreground_activation_used": self.foreground_activation_used,
            "dpi_awareness": self.dpi_awareness,
            "automatic_promotion_gate": "blocked",
            "claim_boundary": "GUI edits are diagnostic candidate evidence and never authorize promotion.",
        }
        write_json_atomic(self.report_file, report)
        return {**report, "report_file": str(self.report_file)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open an existing SUMO network in Netedit.")
    parser.add_argument("input_file", type=Path, help="A compiled .net.xml or a .sumocfg file")
    parser.add_argument("--netedit-binary", default="netedit")
    parser.add_argument("--gui-settings-file", type=Path)
    parser.add_argument("--selection-file", type=Path)
    parser.add_argument("--window-size")
    parser.add_argument("--window-pos")
    parser.add_argument(
        "--detach-console",
        action="store_true",
        help="Detach the non-blocking Netedit GUI process from this console on Windows",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    launch_func: Callable[..., dict[str, Any]] = launch_netedit,
) -> int:
    args = _parser().parse_args(argv)
    report = launch_func(
        args.input_file,
        netedit_binary=args.netedit_binary,
        gui_settings_file=args.gui_settings_file,
        selection_file=args.selection_file,
        window_size=args.window_size,
        window_pos=args.window_pos,
        detach_console=args.detach_console,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    raise SystemExit(main())
