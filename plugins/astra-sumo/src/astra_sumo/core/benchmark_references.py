"""Read frozen benchmark packet files through their exact raw-SHA byte references.

A packet keeps ``byte-references.json`` at its root. Its aliases are keyed by packet-relative paths such as
``cases/<case>/prepared/network.net.xml`` and point at a target inside the packet (scope ``packet``) or inside the
repository (scope ``repository``). Tracked files may be stored as ``<name>.gz`` or ``<name>.gz.gz``; the returned
bytes are always the raw (decompressed) bytes whose SHA-256 the alias declares. A file tracked in the packet itself
(scope ``direct``) is pinned instead by the SHA-256 of its tracked bytes in the packet's ``manifest.json``. The
manifest lives in the packet, so ``PacketCase.committed`` also compares the tracked bytes read with the blob that the
repository's HEAD commit holds at the same path: a trust root outside the packet.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath

REFERENCE_FILE = "byte-references.json"
MANIFEST_FILE = "manifest.json"
LOCAL_ONLY_FILE = "local-only-large-traces.json"
_MAX_ALIAS_DEPTH = 8


class BenchmarkReferenceError(ValueError):
    """A packet reference is unsafe, undeclared or does not match its declared SHA-256."""


@dataclass(frozen=True)
class ResolvedReference:
    relative: str
    scope: str
    target: str
    tracked_path: Path
    sha256: str
    data: bytes
    tracked_sha256: str  # of the tracked file's bytes exactly as read, before any gzip layer is removed
    tracked_blob: str  # git blob id (SHA-1) of those same tracked bytes, as ``git hash-object --no-filters`` gives it


def git_blob_id(data: bytes) -> str:
    """The SHA-1 git blob id of ``data`` exactly as given (no clean filter, no line-ending conversion)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def head_blob_ids(repository_root: Path, paths: Iterable[str]) -> dict[str, str]:
    """Repository-relative path -> blob id at the repository's HEAD, from one ``git ls-tree`` call.

    A path HEAD does not hold, a directory without a git repository or a failed git call yields no entry, so the
    caller treats the path as not committed.
    """
    names = sorted(set(paths))
    if not names:
        return {}
    try:
        done = subprocess.run(["git", "-C", str(repository_root), "ls-tree", "--full-tree", "-z", "HEAD", "--", *names],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if done.returncode != 0:
        return {}
    blobs = {}
    for record in done.stdout.split(b"\0"):
        meta, _, path = record.partition(b"\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == b"blob":
            blobs[path.decode("utf-8", errors="replace")] = fields[2].decode("ascii")
    return {name: blobs[name] for name in names if name in blobs}


def safe_relative(name: str | PurePosixPath) -> PurePosixPath:
    text = str(name).replace("\\", "/")
    path = PurePosixPath(text)
    if not text or text == "." or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise BenchmarkReferenceError(f"Unsafe relative path: {name!r}")
    return path


def find_tracked_file(path: Path) -> tuple[Path, int] | None:
    """The tracked form of ``path`` (itself, ``.gz`` or ``.gz.gz``) and its number of gzip layers."""
    for suffix, layers in (("", 0), (".gz", 1), (".gz.gz", 2)):
        candidate = path.with_name(path.name + suffix)
        if candidate.is_file():
            return candidate, layers
    return None


def _read_tracked(path: Path) -> tuple[bytes, Path, str, str]:
    """Raw bytes, tracked path, and the SHA-256 and git blob id of the tracked bytes exactly as read."""
    found = find_tracked_file(path)
    if found is None:
        raise FileNotFoundError(f"No tracked file for {path}")
    tracked = found[0].read_bytes()
    data = tracked
    for _ in range(found[1]):
        data = gzip.decompress(data)
    return data, found[0], hashlib.sha256(tracked).hexdigest(), git_blob_id(tracked)


def read_tracked_file(path: Path) -> tuple[bytes, Path]:
    """Return the raw bytes of ``path``, read directly or through its tracked ``.gz`` / ``.gz.gz`` form."""
    data, tracked, _, _ = _read_tracked(path)
    return data, tracked


def load_byte_reference_document(packet_root: Path) -> dict:
    return json.loads((Path(packet_root) / REFERENCE_FILE).read_text(encoding="utf-8"))


def load_byte_references(packet_root: Path) -> dict[str, dict[str, str]]:
    return load_byte_reference_document(packet_root)["aliases"]


def csv_rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def resolve_packet_reference(packet_root: Path, relative: str, *, repository_root: Path | None = None,
                             aliases: dict[str, dict[str, str]] | None = None, _depth: int = 0) -> ResolvedReference:
    """Resolve a packet-relative file: a tracked packet file first, otherwise its declared byte reference."""
    packet_root = Path(packet_root).resolve()
    repository_root = Path(repository_root).resolve() if repository_root is not None else packet_root.parents[1]
    name = safe_relative(relative)
    try:
        data, tracked, tracked_sha, tracked_blob = _read_tracked(packet_root / name)
    except FileNotFoundError:
        pass
    else:
        return ResolvedReference(name.as_posix(), "direct", name.as_posix(), tracked,
                                 hashlib.sha256(data).hexdigest(), data, tracked_sha, tracked_blob)
    if _depth >= _MAX_ALIAS_DEPTH:
        raise BenchmarkReferenceError(f"Alias chain too deep at {name.as_posix()}")
    aliases = load_byte_references(packet_root) if aliases is None else aliases
    row = aliases.get(name.as_posix())
    if row is None:
        raise FileNotFoundError(f"{name.as_posix()} is neither tracked in the packet nor declared in {REFERENCE_FILE}")
    target = safe_relative(row["target"])
    if row["scope"] == "packet":
        inner = resolve_packet_reference(packet_root, target.as_posix(), repository_root=repository_root,
                                         aliases=aliases, _depth=_depth + 1)
        data, tracked, tracked_sha, tracked_blob = inner.data, inner.tracked_path, inner.tracked_sha256, inner.tracked_blob
    elif row["scope"] == "repository":
        data, tracked, tracked_sha, tracked_blob = _read_tracked(repository_root / target)
    else:
        raise BenchmarkReferenceError(f"Unknown alias scope {row['scope']!r} for {name.as_posix()}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise BenchmarkReferenceError(f"{name.as_posix()} resolved to sha256 {digest}, declared {row['sha256']}")
    return ResolvedReference(name.as_posix(), row["scope"], target.as_posix(), tracked, digest, data, tracked_sha,
                             tracked_blob)


def resolve_packet_file(packet_root: Path, relative: str, *, repository_root: Path | None = None) -> bytes:
    """Return the raw bytes of a packet-relative file; aliases must match their declared raw SHA-256."""
    return resolve_packet_reference(packet_root, relative, repository_root=repository_root).data


def local_only_row(data: bytes, relative: str) -> dict | None:
    """The ``local-only-large-traces.json`` row of a packet-relative file whose bytes are not in the repository."""
    return next((row for row in json.loads(data)["files"] if row.get("relative") == relative), None)


class PacketCase:
    """Read ``cases/<case>/...`` files of one packet, loading its byte references once."""

    def __init__(self, packet_root: Path, case: str, *, repository_root: Path | None = None) -> None:
        self.packet_root = Path(packet_root).resolve()
        self.repository_root = (Path(repository_root).resolve() if repository_root is not None
                                else self.packet_root.parents[1])
        self.case = safe_relative(case).as_posix()
        document = load_byte_reference_document(self.packet_root)
        self.aliases: dict[str, dict[str, str]] = document["aliases"]
        self.rule = document.get("rule")

    def key(self, relative: str) -> str:
        return f"cases/{self.case}/{safe_relative(relative).as_posix()}"

    def resolve(self, relative: str) -> ResolvedReference:
        return resolve_packet_reference(self.packet_root, self.key(relative), repository_root=self.repository_root,
                                        aliases=self.aliases)

    def resolve_root(self, relative: str) -> ResolvedReference:
        """Resolve a packet-root file such as ``local-only-large-traces.json`` (not under ``cases/<case>``)."""
        return resolve_packet_reference(self.packet_root, relative, repository_root=self.repository_root,
                                        aliases=self.aliases)

    def read(self, relative: str) -> bytes:
        return self.resolve(relative).data

    def read_json(self, relative: str) -> object:
        return json.loads(self.read(relative))

    def read_csv(self, relative: str) -> list[dict[str, str]]:
        return csv_rows(self.read(relative))

    @cached_property
    def manifest(self) -> dict[str, str]:
        """Packet-relative tracked path -> SHA-256 of its tracked bytes, from ``manifest.json`` (empty without one)."""
        path = self.packet_root / MANIFEST_FILE
        if not path.is_file():
            return {}
        return {row["path"]: row["sha256"] for row in json.loads(path.read_text(encoding="utf-8"))["files"]}

    def manifest_sha256(self, tracked: Path) -> str | None:
        """The manifest SHA-256 of a tracked file inside the packet, or None when the manifest does not list it."""
        try:
            key = Path(tracked).resolve().relative_to(self.packet_root).as_posix()
        except ValueError:
            return None
        return self.manifest.get(key)

    def pinned(self, reference: ResolvedReference) -> bool:
        """Whether the bytes read are pinned: an alias by its declared raw SHA-256, which ``resolve`` checks, and a
        file tracked in the packet by the SHA-256 that ``manifest.json`` records for exactly the bytes read."""
        return reference.scope != "direct" or self.manifest_sha256(reference.tracked_path) == reference.tracked_sha256

    def committed(self, references: Iterable[ResolvedReference]) -> dict[str, bool]:
        """Repository-relative tracked path -> whether the tracked bytes read equal the blob HEAD holds at that path.

        This binds files the packet manifest alone pins (a coherent edit of a file and its manifest row passes the
        manifest) to the commit the verifier records. The comparison is byte-exact, so a checkout whose filters change
        the tracked bytes fails closed, as does a path HEAD does not hold or a root without a git repository.
        """
        names = {self.tracked_name(reference): reference.tracked_blob for reference in references}
        head = head_blob_ids(self.repository_root, names)
        return {name: head.get(name) == blob for name, blob in names.items()}

    def tracked_name(self, reference: ResolvedReference | Path) -> str:
        """Repository-relative POSIX name of a tracked file, or of the one a reference was read from."""
        path = reference.tracked_path if isinstance(reference, ResolvedReference) else Path(reference)
        try:
            return path.resolve().relative_to(self.repository_root).as_posix()
        except ValueError:
            return path.as_posix()
