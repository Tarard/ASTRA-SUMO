from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


_ATOMIC_TEMP_PREFIX = ".torii-"
_ATOMIC_TEMP_SUFFIX = ".tmp"


def _open_atomic_temporary_file(
    parent: Path,
    *,
    mode: str,
    encoding: str | None = None,
    newline: str | None = None,
) -> Any:
    """Open a short, target-independent temporary file beside an artifact."""

    options: dict[str, Any] = {
        "mode": mode,
        "dir": parent,
        "prefix": _ATOMIC_TEMP_PREFIX,
        "suffix": _ATOMIC_TEMP_SUFFIX,
        "delete": False,
    }
    if encoding is not None:
        options["encoding"] = encoding
    if newline is not None:
        options["newline"] = newline
    return tempfile.NamedTemporaryFile(**options)


def relative_or_absolute_path(path: Path, start: Path) -> str:
    """Return a portable relative path, falling back across Windows drives."""

    target = path.resolve()
    base = start.resolve()
    try:
        return Path(os.path.relpath(target, start=base)).as_posix()
    except ValueError:
        return target.as_posix()


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
) -> None:
    """Serialize JSON completely before atomically replacing the destination."""
    text = json.dumps(
        payload,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
    )
    write_text_atomic(path, text, encoding="utf-8")


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write a text artifact through a same-directory temporary file."""
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with _open_atomic_temporary_file(
            destination.parent,
            mode="w",
            encoding=encoding,
            newline="",
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            # The original exception remains authoritative; a later run can
            # safely ignore a uniquely named temporary artifact.
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def copy_file_atomic(source: Path, destination: Path) -> None:
    """Copy a binary artifact before atomically replacing the destination."""

    source_path = source.resolve(strict=True)
    destination_path = destination.resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with (
            source_path.open("rb") as source_handle,
            _open_atomic_temporary_file(
                destination_path.parent,
                mode="wb",
            ) as destination_handle,
        ):
            temporary_path = Path(destination_handle.name)
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
