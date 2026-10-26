"""Content-derived stable ids for twin profiles, so diffs line up across rounds."""

from __future__ import annotations

import hashlib
import re

_HAMBURG_DETECTOR = re.compile(r"^hh_(\d+)_Z_(\d+)_\d+$")


def short_hash(*parts: str, length: int = 8) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:length]


def detector_alias(detector_id: str) -> str:
    """``hh_0119_Z_13_34018`` -> ``0119Z13``; other ids are kept unchanged."""
    match = _HAMBURG_DETECTOR.match(detector_id)
    return f"{match[1]}Z{match[2]}" if match else detector_id


def row_id(detector_id: str, begin_s: float) -> str:
    return f"R:{detector_alias(detector_id)}@{float(begin_s):g}"


def element_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def hotspot_id(kind: str, anchor: str) -> str:
    return f"H:{kind}:{anchor}"


def coanchor_hotspot_id(anchor_ids: list[str]) -> str:
    return f"H:LOC:co:{short_hash(*sorted(anchor_ids))}"


def candidate_id(net_sha256: str, control_sha256: str, demand_sha256: str, additional_sha256: str) -> str:
    return "c-" + short_hash(net_sha256, control_sha256, demand_sha256, additional_sha256, length=12)


def goal_reference(goal_id: str, goal_bytes: bytes) -> str:
    return f"{goal_id}@{hashlib.sha256(goal_bytes).hexdigest()[:8]}"


def cas_reference(sha256: str) -> str:
    return f"cas:sha256:{sha256}"


def connection_lanes(connection) -> tuple[str, str]:
    """``(<from>_<fromLane>, <to>_<toLane>)`` of one ``<connection>`` element."""
    return (f"{connection.get('from')}_{connection.get('fromLane')}",
            f"{connection.get('to')}_{connection.get('toLane')}")


def connection_key(connection) -> str:
    """``<from>_<fromLane>><to>_<toLane>``: the order-free name of a connection in every twin id."""
    return ">".join(connection_lanes(connection))
