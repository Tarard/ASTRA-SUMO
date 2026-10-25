"""Deterministic scenario operations on a frozen demand: the M2 known-answer family ``cell_depart_shift``.

An OD-period cell is the ``od_cell`` that ``lane-intentions.json`` assigns to each vehicle. The packet's cells are
indexed period-major, ``cell = period * od_pairs + pair`` (22 pairs x 14 periods for the Ring1 case), which
``cell_table`` checks against the departures instead of assuming it. ``cell_depart_shift`` adds one shift to every
departure of one cell. Every other vehicle element keeps its exact bytes and relative order, and the result is
re-sorted by depart (stable), so the operation is a pure function of the demand bytes, the cell and the shift.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal

from .perturb import Demand, with_departs


def od_cells(intentions: bytes) -> dict[str, int]:
    return {row["vehicle_id"]: int(row["od_cell"]) for row in json.loads(intentions)["vehicles"]}


def intended_fields(intentions: bytes) -> dict[str, str]:
    return {row["vehicle_id"]: row["field_id"] for row in json.loads(intentions)["vehicles"]}


def cell_table(demand: Demand, intentions: bytes, *, od_pairs: int, periods: int,
               period_s: float | None = None) -> dict[int, dict]:
    """Per cell: pair, period, vehicles, fields and depart range; the index layout is checked, never assumed."""
    cells, fields = od_cells(intentions), intended_fields(intentions)
    if set(cells) != {v.vehicle_id for v in demand.vehicles}:
        raise ValueError("Lane intentions and demand must name the same vehicles.")
    members: dict[int, list] = defaultdict(list)
    for vehicle in demand.vehicles:
        members[cells[vehicle.vehicle_id]].append(vehicle)
    table = {}
    for cell, rows in sorted(members.items()):
        if not 0 <= cell < od_pairs * periods:
            raise ValueError(f"Cell {cell} lies outside {od_pairs} pairs x {periods} periods.")
        pair, period = cell % od_pairs, cell // od_pairs
        first, last = min(v.depart for v in rows), max(v.depart for v in rows)
        if period_s is not None and not (period * period_s <= first and last < (period + 1) * period_s):
            raise ValueError(f"Cell {cell} departs outside period {period} of {period_s:g} s.")
        table[cell] = {"cell": cell, "pair": pair, "period": period, "vehicles": len(rows),
                       "fields": sorted({fields[v.vehicle_id] for v in rows}),
                       "depart_s": [str(first), str(last)]}
    entries = defaultdict(set)
    for cell, rows in members.items():
        entries[table[cell]["pair"]].update(ET.fromstring(v.data).find("route").get("edges").split()[0] for v in rows)
    if any(len(edges) != 1 for edges in entries.values()):
        raise ValueError("A pair index must keep one entry edge across periods; the cell layout is not period-major.")
    return table


def cell_depart_shift(demand: Demand, intentions: bytes, *, cell: int, shift_s: Decimal | int | str,
                      horizon_s: float | None = None, od_pairs: int | None = None) -> tuple[Demand, dict]:
    """Shift every departure of one OD-period cell by ``shift_s``; all other vehicles are byte-identical."""
    shift = Decimal(str(shift_s))
    cells = od_cells(intentions)
    members = [v for v in demand.vehicles if cells.get(v.vehicle_id) == cell]
    if not members:
        raise ValueError(f"Cell {cell} has no vehicles.")
    new = {v.vehicle_id: v.depart + shift for v in members}
    if min(new.values()) < 0 or (horizon_s is not None and max(new.values()) >= Decimal(str(horizon_s))):
        raise ValueError(f"Shifting cell {cell} by {shift} s leaves departures outside [0, {horizon_s}).")
    places = max(0, -shift.as_tuple().exponent)
    result = with_departs(demand, new, places=places)
    survivors_before = [v for v in demand.vehicles if v.vehicle_id not in new]
    survivors_after = [v for v in result.vehicles if v.vehicle_id not in new]
    identical = [a.data for a in survivors_before] == [b.data for b in survivors_after]
    if not identical:
        raise AssertionError("cell_depart_shift changed a vehicle outside its cell.")
    fields = intended_fields(intentions)
    record = {"op": "cell_depart_shift", "cell": cell, "shift_s": str(shift), "vehicles": len(members),
              "fields": sorted({fields[v.vehicle_id] for v in members}),
              "depart_s": [str(min(v.depart for v in members)), str(max(v.depart for v in members))],
              "survivors": len(survivors_after), "survivors_byte_identical_in_order": identical,
              "base_sha256": demand.sha256, "sha256": result.sha256}
    if od_pairs:
        record.update(pair=cell % od_pairs, period=cell // od_pairs)
    return result, record


def cell_depart_shifts(base: Demand, intentions: bytes, shifts: dict[int, Decimal | int | str], *,
                       horizon_s: float | None = None) -> tuple[Demand, dict]:
    """Canonical composition: each cell's net shift applied to the base in one stable re-sort of the base order.

    Applying ops one after another is not equivalent when vehicles of different cells share a depart (153 of the
    parent's 180 tie groups): a shift and its reversal leave the tie in another file order, which SUMO inserts
    differently. Here a zero net shift on every cell returns the base bytes exactly.
    """
    net = {int(cell): Decimal(str(value)) for cell, value in shifts.items() if Decimal(str(value)) != 0}
    cells = od_cells(intentions)
    unknown = sorted(set(net) - set(cells.values()))
    if unknown:
        raise ValueError(f"Cells without vehicles: {unknown[:5]}")
    new = {v.vehicle_id: v.depart + net[cells[v.vehicle_id]] for v in base.vehicles if cells[v.vehicle_id] in net}
    if new and (min(new.values()) < 0 or (horizon_s is not None and max(new.values()) >= Decimal(str(horizon_s)))):
        raise ValueError(f"The net shifts leave departures outside [0, {horizon_s}).")
    places = max([0] + [-value.as_tuple().exponent for value in net.values()])
    result = with_departs(base, new, places=places) if new else base
    return result, {"op": "cell_depart_shifts", "net_shift_s": {str(c): str(v) for c, v in sorted(net.items())},
                    "vehicles": len(new), "base_sha256": base.sha256, "sha256": result.sha256}


def parse_op(text: str) -> tuple[str, dict]:
    """``cell_depart_shift:<cell>:<shift_s>`` -> (name, arguments)."""
    name, _, rest = text.partition(":")
    if name != "cell_depart_shift":
        raise ValueError(f"Unknown scenario op {name!r}; M2 supports cell_depart_shift:<cell>:<shift_s>")
    cell, _, shift = rest.partition(":")
    if not cell.isdigit() or not shift:
        raise ValueError("Use cell_depart_shift:<cell>:<shift_s>, e.g. cell_depart_shift:0:900")
    return name, {"cell": int(cell), "shift_s": shift}
