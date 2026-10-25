"""Verifier doctor: SUMO and libsumo, the astra_sumo import path in-process and in a child, and the clone state.

The child is started as ``core/detector_demand_replay.py`` starts its SUMO driver: ``sys.executable -m <module>``
from a fresh working directory, so it imports astra_sumo through the venv rather than through the caller's path; ``-B``
keeps it from writing bytecode, which does not change what it imports. Commands run with captured output and write no
log files. The only directory doctor creates is that empty child working directory, under ``scratch_root`` when one
is given and otherwise under the clone's git-ignored ``outputs/``, never in the system temp directory; it is removed
afterwards, together with any scratch parent doctor had to create.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path

EXPECTED_SUMO = "1.27.1"
SUMO_DISTRIBUTIONS = ("libsumo", "eclipse-sumo")
PACKAGE_SRC = ("plugins", "astra-sumo", "src")
DEFAULT_SCRATCH = "outputs"
CHILD_ARGS = ("-B", "-m", "astra_sumo.twin.doctor")
_SHA = re.compile(r"[0-9a-f]{7,40}")


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path or ".").resolve())))


def _under(path: str | Path | None, root: Path) -> bool:
    if not path:
        return False
    target, base = _norm(path), _norm(root)
    return target == base or target.startswith(base.rstrip("\\/") + os.sep)


def _capture(command: list[str], *, cwd: Path | None = None, timeout_s: float = 60) -> tuple[int | None, str, str]:
    """Run a short command with captured output: no log files and no temp directory."""
    try:
        done = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout_s,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, "", str(error)
    return done.returncode, done.stdout or "", done.stderr or ""


def sumo_binary() -> str | None:
    spec = find_spec("sumo")
    if spec is not None and spec.origin:
        binary = Path(spec.origin).parent / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
        if binary.is_file():
            return str(binary)
    return shutil.which("sumo")


def repository_state(root: Path) -> dict:
    """HEAD commit and ``git status --porcelain`` cleanliness of a clone."""
    head_code, head, _ = _capture(["git", "-C", str(root), "rev-parse", "HEAD"], timeout_s=30)
    status_code, status, _ = _capture(["git", "-C", str(root), "status", "--porcelain"], timeout_s=60)
    lines = [line for line in status.splitlines() if line.strip()]
    return {"root": str(Path(root).resolve()), "commit": head.strip() if head_code == 0 and head.strip() else None,
            "clean": status_code == 0 and not lines, "porcelain_lines": len(lines)}


def commit_from_directory(root: Path) -> str | None:
    """The commit a verifier clone is named after (``<verifier root>/<sha>``), when its directory name is a sha."""
    name = Path(root).resolve().name.lower()
    return name if _SHA.fullmatch(name) else None


def foreign_sources(paths: list[str], own_src: Path) -> list[str]:
    """sys.path entries, other than the clone's own src, that hold an astra_sumo package."""
    return sorted({str(path) for path in paths if path and _norm(path) != _norm(own_src)
                   and (Path(path) / "astra_sumo" / "__init__.py").is_file()})


def _distributions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name in SUMO_DISTRIBUTIONS:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return found


def child_probe() -> dict:
    """What a driver child sees: its astra_sumo, its sys.path, its SUMO distributions and whether libsumo imports."""
    import astra_sumo

    try:
        import libsumo
    except ImportError as error:
        libsumo_file, libsumo_error = None, str(error)
    else:
        libsumo_file, libsumo_error = getattr(libsumo, "__file__", None), None
    return {"astra_sumo": astra_sumo.__file__, "sys_path": list(sys.path), "cwd": os.getcwd(),
            "executable": sys.executable, "libsumo": libsumo_file, "libsumo_error": libsumo_error,
            "distributions": _distributions()}


def _probe_child(scratch: Path) -> tuple[dict, list[str]]:
    """Run the child probe from a new empty directory under ``scratch``; remove it and any scratch parent created."""
    created = [path for path in (scratch, *scratch.parents) if not path.exists()]
    command = [sys.executable, *CHILD_ARGS]
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="astra-twin-doctor-", dir=scratch) as directory:
            code, stdout, stderr = _capture(command, cwd=Path(directory), timeout_s=120)
    finally:
        for path in created:
            with suppress(OSError):
                path.rmdir()
    try:
        return json.loads(stdout), command
    except ValueError:
        return {"error": stderr or f"exit code {code}", "sys_path": [], "astra_sumo": None, "libsumo": None,
                "distributions": {}}, command


def run_doctor(repository_root: Path, *, forbidden_paths: list[str] | None = None, expected_commit: str | None = None,
               scratch_root: Path | None = None) -> dict:
    """Every check must be true. Without ``expected_commit`` the commit is read from the clone's directory name."""
    import astra_sumo

    root = Path(repository_root).resolve()
    own_src = root.joinpath(*PACKAGE_SRC)
    binary = sumo_binary()
    version_line, version = None, None
    if binary:
        _, stdout, _ = _capture([binary, "--version"], timeout_s=30)
        version_line = next((line for line in stdout.splitlines() if "SUMO" in line), None)
        match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", version_line or "")
        version = match[1] if match else None
    scratch = Path(scratch_root).resolve() if scratch_root is not None else root / DEFAULT_SCRATCH
    child, command = _probe_child(scratch)
    source = "--expect-commit" if expected_commit else None
    if not expected_commit:
        expected_commit = commit_from_directory(root)
        source = "clone directory name" if expected_commit else None
    expected = expected_commit.lower() if expected_commit else None
    forbidden = {_norm(path) for path in forbidden_paths or []}
    child_path = [path for path in child.get("sys_path", []) if path]
    foreign = {"in_process": foreign_sources(sys.path, own_src), "child": foreign_sources(child_path, own_src)}
    distributions = child.get("distributions") or {}
    state = repository_state(root)
    checks = {
        "sumo_version": version == EXPECTED_SUMO,
        "sumo_binary_in_venv": _under(binary, Path(sys.prefix)),
        "libsumo_import": bool(child.get("libsumo")),
        "sumo_distributions": all(distributions.get(name) == EXPECTED_SUMO for name in SUMO_DISTRIBUTIONS),
        "astra_sumo_in_process_under_clone": _under(astra_sumo.__file__, root),
        "astra_sumo_child_under_clone": _under(child.get("astra_sumo"), root),
        "no_foreign_astra_sumo_on_sys_path": not foreign["in_process"],
        "no_foreign_astra_sumo_on_child_sys_path": not foreign["child"],
        "forbidden_absent_from_sys_path": not forbidden & {_norm(p) for p in sys.path if p},
        "forbidden_absent_from_child_sys_path": not forbidden & {_norm(p) for p in child_path},
        "head_matches_expected": bool(expected and _SHA.fullmatch(expected) and state["commit"]
                                      and state["commit"].startswith(expected)),
        "porcelain_clean": state["clean"]}
    passed = all(value is True for value in checks.values())
    return {"schema": "astra.twin-doctor/v1", "status": "pass" if passed else "blocked",
            "sumo": {"binary": binary, "version_line": version_line, "version": version, "expected": EXPECTED_SUMO,
                     "venv": sys.prefix, "child_distributions": distributions},
            "python": {"executable": sys.executable, "version": sys.version.split()[0]},
            "astra_sumo": {"in_process": astra_sumo.__file__, "child": child.get("astra_sumo"),
                           "child_command": command[1:], "child_cwd": child.get("cwd"),
                           "child_scratch_root": str(scratch),
                           "child_scratch_source": "--scratch-root" if scratch_root is not None
                           else f"the clone's git-ignored {DEFAULT_SCRATCH}/", "child_libsumo": child.get("libsumo"),
                           "child_libsumo_error": child.get("libsumo_error"), "child_error": child.get("error"),
                           "foreign_sources": foreign, "forbidden_paths": sorted(forbidden_paths or [])},
            "clone": {**state, "expected_commit": expected,
                      "expected_commit_source": source or "none: pass --expect-commit or run from a clone directory "
                                                          "named after its commit"},
            "checks": checks, "pass": passed}


if __name__ == "__main__":
    print(json.dumps(child_probe()))
