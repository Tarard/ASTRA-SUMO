from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


def capture_code_producer_state(repository_root: Path) -> dict[str, Any]:
    """Capture the exact Git producer state without requiring network access.

    A clean revision/tree is sufficient for formal evidence.  Dirty executions
    remain traceable through a content-derived worktree-state hash, but the v4
    authoritative verifier rejects them as non-formal evidence.
    """

    root = repository_root.resolve(strict=True)
    top_level = Path(
        _git_text(root, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if top_level != root:
        raise ValueError(
            f"Repository root mismatch: requested={root}, git={top_level}."
        )
    status = _git_bytes(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    unstaged = _git_bytes(root, "diff", "--binary")
    staged = _git_bytes(root, "diff", "--binary", "--cached", "HEAD")
    untracked = _git_bytes(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    state_digest = hashlib.sha256()
    for label, payload in (
        (b"status\0", status),
        (b"unstaged\0", unstaged),
        (b"staged\0", staged),
        (b"untracked\0", untracked),
    ):
        state_digest.update(label)
        state_digest.update(payload)
    for raw_path in sorted(item for item in untracked.split(b"\0") if item):
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative
        state_digest.update(b"untracked-file\0")
        state_digest.update(raw_path)
        state_digest.update(b"\0")
        state_digest.update(_file_sha256(path).encode("ascii"))

    try:
        repository_url = _git_text(root, "remote", "get-url", "origin")
    except ValueError:
        repository_url = "unconfigured"
    payload = {
        "repository_url": repository_url,
        "revision": _git_text(root, "rev-parse", "HEAD"),
        "tree_revision": _git_text(root, "rev-parse", "HEAD^{tree}"),
        "branch": _git_text(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_clean": not bool(status.strip()),
        "worktree_state_sha256": state_digest.hexdigest(),
        "untracked_file_count": len(
            [item for item in untracked.split(b"\0") if item]
        ),
    }
    identity = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": "torii.code-producer-state/v1",
        "producer_state_id": f"producer-{hashlib.sha256(identity).hexdigest()[:20]}",
        **payload,
    }


def _git_text(root: Path, *arguments: str) -> str:
    return _git_bytes(root, *arguments).decode("utf-8").strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise ValueError(
            f"Git identity command failed ({' '.join(arguments)}): "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return completed.stdout


def _file_sha256(path: Path) -> str:
    # ``git ls-files --others`` may report an untracked nested worktree or
    # Windows directory junction as one path.  Git itself does not inspect the
    # nested contents, so producer identity must hash a stable directory marker
    # instead of trying to open the directory as a regular file.
    if not path.is_file():
        return hashlib.sha256(b"untracked-directory\0").hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
