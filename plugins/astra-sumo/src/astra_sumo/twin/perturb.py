"""Keyed, deterministic perturbations of a frozen explicit-vehicle demand (M2 robustness families).

``depart_jitter``: u_v = HMAC-SHA256(key, vehicle_id) mapped to U[-0.5, 0.5) s and added to the scheduled depart.
A draw depends only on the key and the vehicle id, so a parent and a candidate evaluated with one key share every
vehicle's offset (common random numbers), and a vehicle an operation adds gets its own offset from its id.

``tie_order``: inside each group of vehicles with an equal scheduled depart, the file order (SUMO's insertion order for
equal departure times) is replaced by the order of HMAC-SHA256(key, vehicle_id). Departure times are unchanged.

Neither family changes vehicle ids, routes, stops, types or lane intentions: every vehicle element keeps its exact
bytes except its depart token (jitter) or its position in the file (order). The output stays sorted by depart, stable
on the input order, as SUMO's incremental route loader expects.

Keys are explicit bytes supplied by the caller. A ``DrawKey`` records its label and the SHA-256 of the key; this
module never writes the key itself. ``DrawKey.public`` derives a key from its label and marks it public: such a key
is reproducible by anyone and suits a calibration panel, not a held-out check.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, Decimal
from xml.sax.saxutils import unescape

JITTER_HALF_WIDTH_S = Decimal("0.5")
JITTER_PLACES = 12
PUBLIC_KEY_DOMAIN = b"astra-twin/public-draw-key/v1\x00"
_TWO_64 = Decimal(2) ** 64
_VEHICLE = re.compile(rb"<vehicle\b[^>]*>.*?</vehicle>", re.S)
_START = re.compile(rb"<vehicle\b[^>]*>")
_DEPART = re.compile(rb'\sdepart="([^"]*)"')
_ID = re.compile(rb'\sid="([^"]*)"')
_TOKEN = re.compile(r"^\d+(?:\.(\d+))?$")
_GENERATORS = re.compile(rb"<(?:vehicle|flow|trip|person|personFlow|container|containerFlow)\b")


class DemandFormatError(ValueError):
    """The demand is not a plain explicit-vehicle route file this module can rewrite byte-exactly."""


@dataclass(frozen=True)
class DrawKey:
    label: str
    key: bytes
    public: bool = False

    def __post_init__(self) -> None:
        if not self.label or not self.key:
            raise ValueError("A draw key needs a nonempty label and nonempty key bytes.")

    @classmethod
    def from_hex(cls, label: str, key_hex: str) -> DrawKey:
        return cls(label, bytes.fromhex(key_hex))

    @classmethod
    def public_key(cls, label: str) -> DrawKey:
        """A key derived from its label alone: reproducible by anyone, so never a secret or held-out key."""
        return cls(label, hashlib.sha256(PUBLIC_KEY_DOMAIN + label.encode("utf-8")).digest(), public=True)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.key).hexdigest()

    def record(self) -> dict:
        return {"label": self.label, "key_sha256": self.fingerprint, "public": self.public,
                "derivation": "sha256(domain || label)" if self.public else "caller-supplied bytes"}


@dataclass(frozen=True)
class VehicleElement:
    vehicle_id: str
    depart: Decimal
    token: str
    data: bytes
    token_span: tuple[int, int]

    @property
    def places(self) -> int:
        fraction = _TOKEN.match(self.token)[1]
        return len(fraction) if fraction else 0

    def with_depart(self, value: Decimal, places: int) -> VehicleElement:
        if value < 0:
            raise ValueError(f"{self.vehicle_id} would depart at {value} s, before 0")
        token = format_depart(value, places)
        if token == self.token:
            return self
        start, end = self.token_span
        data = self.data[:start] + token.encode("ascii") + self.data[end:]
        return replace(self, depart=Decimal(token), token=token, data=data, token_span=(start, start + len(token)))


@dataclass(frozen=True)
class Demand:
    head: bytes
    vehicles: tuple[VehicleElement, ...]
    separator: bytes
    tail: bytes

    def to_bytes(self) -> bytes:
        return self.head + self.separator.join(v.data for v in self.vehicles) + self.tail

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def by_id(self) -> dict[str, VehicleElement]:
        return {v.vehicle_id: v for v in self.vehicles}


def format_depart(value: Decimal, places: int) -> str:
    return f"{value.quantize(Decimal(1).scaleb(-places)):f}" if places else f"{value.quantize(Decimal(1)):f}"


def parse_demand(data: bytes) -> Demand:
    """Split a route file into header, vehicle elements and trailer, checked against an XML parse of the same bytes."""
    matches = list(_VEHICLE.finditer(data))
    if not matches:
        raise DemandFormatError("The demand has no vehicle elements.")
    head, tail = data[:matches[0].start()], data[matches[-1].end():]
    separators = {data[a.end():b.start()] for a, b in zip(matches, matches[1:])}
    if len(separators) > 1 or any(s.strip() for s in separators):
        raise DemandFormatError("Vehicle elements must be separated by one uniform whitespace run.")
    if _GENERATORS.search(head) or _GENERATORS.search(tail) or tail.strip() != b"</routes>":
        raise DemandFormatError("Only a header, explicit vehicles and the closing routes tag are supported.")
    vehicles = []
    for match in matches:
        element = match[0]
        start_tag = _START.match(element)[0]
        departs, names = list(_DEPART.finditer(start_tag)), list(_ID.finditer(start_tag))
        if len(departs) != 1 or len(names) != 1 or b"<vehicle" in element[1:]:
            raise DemandFormatError(f"Vehicle element at byte {match.start()} needs exactly one id and one depart.")
        token = departs[0][1].decode("ascii")
        if not _TOKEN.match(token):
            raise DemandFormatError(f"Depart {token!r} is not a plain decimal time in seconds.")
        vehicles.append(VehicleElement(unescape(names[0][1].decode("utf-8"), {"&quot;": '"', "&apos;": "'"}),
                                       Decimal(token), token, element, departs[0].span(1)))
    demand = Demand(head, tuple(vehicles), separators.pop() if separators else b"", tail)
    root = ET.fromstring(data)
    parsed = [(row.get("id"), row.get("depart")) for row in root.findall("vehicle")]
    if (demand.to_bytes() != data or parsed != [(v.vehicle_id, v.token) for v in vehicles]
            or len({v.vehicle_id for v in vehicles}) != len(vehicles)
            or any(row.tag not in {"vehicle", "vType", "route"} for row in root)):
        raise DemandFormatError("The byte-level split does not reproduce the XML parse (ids, departs or order).")
    return demand


def with_departs(demand: Demand, departs: dict[str, Decimal], places: int | None = None) -> Demand:
    """Replace the depart of the named vehicles and re-sort by depart, stable on the current order.

    ``places`` fixes the decimals of each rewritten token; by default a token keeps its own number of decimals.
    """
    unknown = set(departs) - {v.vehicle_id for v in demand.vehicles}
    if unknown:
        raise KeyError(f"Unknown vehicle ids: {sorted(unknown)[:3]}")
    rewritten = [v.with_depart(departs[v.vehicle_id], v.places if places is None else max(places, v.places))
                 if v.vehicle_id in departs else v for v in demand.vehicles]
    order = sorted(range(len(rewritten)), key=lambda index: (rewritten[index].depart, index))
    return replace(demand, vehicles=tuple(rewritten[index] for index in order))


def keyed_unit(key: bytes, vehicle_id: str) -> Decimal:
    """The first 64 bits of HMAC-SHA256(key, vehicle_id) as an exact fraction in [0, 1)."""
    digest = hmac.new(key, vehicle_id.encode("utf-8"), hashlib.sha256).digest()
    return Decimal(int.from_bytes(digest[:8], "big")) / _TWO_64


def jitter_offset(key: bytes, vehicle_id: str, half_width_s: Decimal = JITTER_HALF_WIDTH_S) -> Decimal:
    """U[-w, w) on a 1e-12 s grid, floored so the offset never reaches +w."""
    value = (keyed_unit(key, vehicle_id) * 2 - 1) * half_width_s
    return value.quantize(Decimal(1).scaleb(-JITTER_PLACES), rounding=ROUND_FLOOR)


def _order_change(before: Demand, after: Demand) -> int:
    return sum(a.vehicle_id != b.vehicle_id for a, b in zip(before.vehicles, after.vehicles))


def apply_jitter(demand: Demand, key: DrawKey,
                 half_width_s: Decimal = JITTER_HALF_WIDTH_S) -> tuple[Demand, dict]:
    offsets = {v.vehicle_id: jitter_offset(key.key, v.vehicle_id, half_width_s) for v in demand.vehicles}
    result = with_departs(demand, {v.vehicle_id: v.depart + offsets[v.vehicle_id] for v in demand.vehicles},
                          places=JITTER_PLACES)
    values = sorted(offsets.values())
    return result, {"family": "depart_jitter", "key": key.record(), "half_width_s": str(half_width_s),
                    "grid_s": f"1e-{JITTER_PLACES}", "vehicles": len(values),
                    "offset_min_s": str(values[0]), "offset_max_s": str(values[-1]),
                    "offset_mean_s": str((sum(values) / len(values)).quantize(Decimal("1e-6"))),
                    "positions_changed": _order_change(demand, result),
                    "base_sha256": demand.sha256, "sha256": result.sha256}


def apply_tie_order(demand: Demand, key: DrawKey) -> tuple[Demand, dict]:
    """Permute the file order inside each group of vehicles with an equal depart; departs are unchanged."""
    groups: dict[Decimal, list[int]] = {}
    for index, vehicle in enumerate(demand.vehicles):
        groups.setdefault(vehicle.depart, []).append(index)
    order: list[int] = []
    for indices in sorted(groups.values(), key=lambda rows: demand.vehicles[rows[0]].depart):
        order += sorted(indices, key=lambda i: (keyed_unit(key.key, demand.vehicles[i].vehicle_id), i))
    result = replace(demand, vehicles=tuple(demand.vehicles[i] for i in order))
    ties = [rows for rows in groups.values() if len(rows) > 1]
    changed = sum(1 for rows in ties if [result.vehicles[i] for i in rows] != [demand.vehicles[i] for i in rows])
    return result, {"family": "tie_order", "key": key.record(), "tie_groups": len(ties),
                    "tied_vehicles": sum(map(len, ties)), "groups_reordered": changed,
                    "positions_changed": _order_change(demand, result),
                    "base_sha256": demand.sha256, "sha256": result.sha256}


FAMILIES = {"depart_jitter": apply_jitter, "tie_order": apply_tie_order}


def perturb(demand: Demand, family: str, key: DrawKey) -> tuple[Demand, dict]:
    if family not in FAMILIES:
        raise ValueError(f"Unknown perturbation family {family!r}; use one of {sorted(FAMILIES)}")
    return FAMILIES[family](demand, key)
