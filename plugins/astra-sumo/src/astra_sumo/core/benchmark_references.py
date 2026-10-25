"""Read frozen benchmark packet files through their exact raw-SHA byte references.

A packet keeps ``byte-references.json`` at its root. Its aliases are keyed by packet-relative paths such as
``cases/<case>/prepared/network.net.xml`` and point at a target inside the packet (scope ``packet``) or inside the
repository (scope ``repository``). Tracked files may be stored as ``<name>.gz`` or ``<name>.gz.gz``; the returned
bytes are always the raw (decompressed) bytes whose SHA-256 the alias declares.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REFERENCE_FILE = "byte-references.json"
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


def read_tracked_file(path: Path) -> tuple[bytes, Path]:
    """Return the raw bytes of ``path``, read directly or through its tracked ``.gz`` / ``.gz.gz`` form."""
    found = find_tracked_file(path)
    if found is None:
        raise FileNotFoundError(f"No tracked file for {path}")
    data = found[0].read_bytes()
    for _ in range(found[1]):
        data = gzip.decompress(data)
    return data, found[0]


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
        data, tracked = read_tracked_file(packet_root / name)
    except FileNotFoundError:
        pass
    else:
        return ResolvedReference(name.as_posix(), "direct", name.as_posix(), tracked,
                                 hashlib.sha256(data).hexdigest(), data)
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
        data, tracked = inner.data, inner.tracked_path
    elif row["scope"] == "repository":
        data, tracked = read_tracked_file(repository_root / target)
    else:
        raise BenchmarkReferenceError(f"Unknown alias scope {row['scope']!r} for {name.as_posix()}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["sha256"]:
        raise BenchmarkReferenceError(f"{name.as_posix()} resolved to sha256 {digest}, declared {row['sha256']}")
    return ResolvedReference(name.as_posix(), row["scope"], target.as_posix(), tracked, digest, data)


def resolve_packet_file(packet_root: Path, relative: str, *, repository_root: Path | None = None) -> bytes:
    """Return the raw bytes of a packet-relative file; aliases must match their declared raw SHA-256."""
    return resolve_packet_reference(packet_root, relative, repository_root=repository_root).data


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

    def read(self, relative: str) -> bytes:
        return self.resolve(relative).data

    def read_json(self, relative: str) -> object:
        return json.loads(self.read(relative))

    def read_csv(self, relative: str) -> list[dict[str, str]]:
        return csv_rows(self.read(relative))

    def tracked_name(self, reference: ResolvedReference | Path) -> str:
        """Repository-relative POSIX name of a tracked file, or of the one a reference was read from."""
        path = reference.tracked_path if isinstance(reference, ResolvedReference) else Path(reference)
        try:
            return path.resolve().relative_to(self.repository_root).as_posix()
        except ValueError:
            return path.as_posix()
