"""Realize OD-period counts by retiming selected existing requests only."""

from collections import defaultdict
import math

import numpy as np

from .route_sensor_matrix import _exact_nonnegative_integer_array


def propose_stable_od_retiming(*, requests, cells, target, frozen_before_s=3600):
    """Select donor quantiles, then pair same-OD requests and receiver time quantiles.

    This returns departure proposals for apply_native_count_retiming. It does
    not regenerate vehicle IDs, route/lane templates or unchanged requests.
    """
    counts = _exact_nonnegative_integer_array(target, "target")
    if counts.shape != (len(cells),) or not cells or not requests:
        raise ValueError("Use nonempty OD cells and explicit requests, with one target per cell.")
    if not math.isfinite(frozen_before_s) or frozen_before_s < 0:
        raise ValueError("Use a finite nonnegative initialization boundary.")
    by_od, bounds = defaultdict(list), []
    for index, cell in enumerate(cells):
        try:
            od = cell["origin"], cell["destination"]
            begin, end = float(cell["begin_s"]), float(cell["end_s"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Declare each OD and its finite departure interval.") from error
        if (any(not isinstance(v, str) or not v.strip() for v in od)
                or not math.isfinite(begin) or not math.isfinite(end) or not 0 <= begin < end
                or begin < frozen_before_s < end):
            raise ValueError("Use named OD cells with ordered intervals and a whole-cell initialization boundary.")
        by_od[od].append(index)
        bounds.append((begin, end))
    for indices in by_od.values():
        ordered = sorted(indices, key=lambda i: bounds[i])
        if any(bounds[a][1] > bounds[b][0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Departure intervals for one OD must not overlap.")
    members, seen = defaultdict(list), set()
    for row in requests:
        identity, index, depart = row["vehicle_id"], row["od_cell"], row["requested_depart_s"]
        if (not isinstance(identity, str) or not identity or identity in seen
                or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(cells)
                or not math.isfinite(depart) or depart < 0):
            raise ValueError("Use unique explicit IDs, valid cell indices and finite nonnegative departures.")
        native_depart = math.floor(depart * 1000 + .5) / 1000
        if not bounds[index][0] <= native_depart < bounds[index][1]:
            raise ValueError("Every native-millisecond departure must belong to its declared cell.")
        seen.add(identity)
        members[index].append(row)
    prior = [len(members[i]) for i in range(len(cells))]
    for index, (_, end) in enumerate(bounds):
        if end <= frozen_before_s and counts[index] != prior[index]:
            raise ValueError("Keep every initialization cell and its requests fixed.")
    od_totals, proposals = [], []
    for od, indices in sorted(by_od.items()):
        total = sum(prior[i] for i in indices)
        if total != sum(int(counts[i]) for i in indices):
            raise ValueError("Keep the total number of requests for each OD unchanged.")
        od_totals.append(dict(origin=od[0], destination=od[1], vehicles=total))
        donors, slots = [], []
        for index in indices:
            rows = sorted(members[index], key=lambda r: (r["requested_depart_s"], r["vehicle_id"]))
            number = int(counts[index]) - prior[index]
            if number < 0:
                take = -number
                for j in range(take):
                    rank = (2*j + 1) * len(rows) // (2*take)
                    donors.append((rows[rank], index, rank))
            elif number > 0:
                if not rows:
                    raise ValueError("An added receiver flow needs its own observed empirical departure prior.")
                positions = (np.arange(len(rows)) + .5) / len(rows)
                times = [r["requested_depart_s"] for r in rows]
                for j in range(number):
                    quantile = (j + .5) / number
                    depart = round(float(np.interp(quantile, positions, times)), 9)
                    native_depart = math.floor(depart * 1000 + .5) / 1000
                    if not bounds[index][0] <= native_depart < bounds[index][1]:
                        raise ValueError("A receiver quantile left its declared native-millisecond interval.")
                    slots.append((depart, index, j, quantile))
        donors.sort(key=lambda r: (r[0]["requested_depart_s"], r[0]["vehicle_id"]))
        slots.sort()
        # Sorted pairing minimizes unnecessary time crossings for these selected endpoints.
        for (row, source, rank), (depart, receiver, slot, quantile) in zip(donors, slots, strict=True):
            old = row["requested_depart_s"]
            proposals.append(dict(vehicle_id=row["vehicle_id"], source_depart_s=old,
                proposed_depart_s=depart, shift_s=depart-old, source_cell=source, target_cell=receiver,
                donor_empirical_rank=rank, receiver_slot=slot, receiver_quantile=quantile,
                native_shift_s=(math.floor(depart*1000+.5)-math.floor(old*1000+.5))/1000))
    proposals.sort(key=lambda r: (r["source_depart_s"], r["vehicle_id"]))
    actual = prior.copy()
    for row in proposals:
        actual[row["source_cell"]] -= 1
        actual[row["target_cell"]] += 1
    l1 = sum(abs(int(counts[i])-prior[i]) for i in range(len(cells)))
    assert actual == counts.tolist() and len(proposals)*2 == l1
    assert len({r["vehicle_id"] for r in proposals}) == len(proposals)
    shifts = [abs(r["shift_s"]) for r in proposals]
    return dict(status="proposal", proposals=proposals, prior=prior, target=counts.tolist(),
        changed_vehicles=len(proposals), l1_cell_change=l1, vehicle_count=len(requests), od_totals=od_totals,
        initial_requests_changed=sum(math.floor(r["source_depart_s"]*1000+.5)/1000 < frozen_before_s for r in proposals),
        max_absolute_shift_s=max(shifts, default=0), median_absolute_shift_s=float(np.median(shifts)) if shifts else 0,
        selection="Equal empirical donor ranks; receiver empirical time quantiles; sorted pairing within each OD.",
        native_runs=0, formal_result=False,
        claim_boundary="Same OD-period counts with a different empirical realization. Only explicit departures are proposed; apply to preserved original requests. Frozen C/q predictions are not verified for this realization. Native remeasurement is required.")
