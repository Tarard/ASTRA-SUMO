"""Account for departure-to-detector lags in one completed native replay."""

from collections import defaultdict
import math

import numpy as np


def native_temporal_feedback(*, requests, trips, passages, measurements, bin_seconds=900):
    """Retain requested demand, including vehicles that never entered.

    Transfer fractions describe this replay. They are neither derivatives nor
    predictions for changed demand. Unobserved cells have no estimated response.
    """
    if not math.isfinite(bin_seconds) or bin_seconds <= 0:
        raise ValueError("Use a positive finite departure interval.")
    requested = {r["vehicle_id"]: r for r in requests}
    inserted = {r["vehicle_id"]: r for r in trips}
    if (not requested or len(requested) != len(requests) or len(inserted) != len(trips)
            or not inserted.keys() <= requested.keys()):
        raise ValueError("Use unique requested vehicles and matching inserted vehicles.")
    if any(not math.isfinite(r["requested_depart_s"]) or r["requested_depart_s"] < 0 for r in requests):
        raise ValueError("Use finite nonnegative requested departure times.")
    # SUMO 1.27.1 TIME2STEPS stores requested times in milliseconds.
    native_departures = {v: math.floor(r["requested_depart_s"] * 1000 + .5) / 1000 for v, r in requested.items()}
    row_keys = [(r["detector_id"], float(r["begin"]), float(r["end"])) for r in measurements]
    if not row_keys or len(set(row_keys)) != len(row_keys):
        raise ValueError("Use unique detector intervals.")
    if any(not math.isfinite(a) or not math.isfinite(b) or not 0 <= a < b for _, a, b in row_keys):
        raise ValueError("Use finite ordered detector intervals.")
    measured = []
    for row in measurements:
        value = row.get("measured_nVehContrib")
        if value in (None, "", "None"):
            raise ValueError("A missing detector measurement has no estimated temporal response.")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or numeric != int(numeric):
            raise ValueError("Use nonnegative integer native E1 counts.")
        measured.append(int(numeric))
    keys = sorted({(r["plan_id"], math.floor(native_departures[v] / bin_seconds)) for v, r in requested.items()})
    index = {key: i for i, key in enumerate(keys)}
    vehicle_columns = {v: index[r["plan_id"], math.floor(native_departures[v] / bin_seconds)]
                       for v, r in requested.items()}
    denominators = np.bincount(list(vehicle_columns.values()), minlength=len(keys))
    numerators = np.zeros((len(row_keys), len(keys)), dtype=np.int64)
    row_index = {key: i for i, key in enumerate(row_keys)}
    detector_ids = {key[0] for key in row_keys}
    insertion_delays, network_delays = defaultdict(list), defaultdict(list)
    for identity, trip in inserted.items():
        delay = trip["actual_depart_s"] - native_departures[identity]
        if not math.isfinite(delay) or delay < -1e-6:
            raise ValueError("Actual insertion must follow the requested departure.")
        insertion_delays[vehicle_columns[identity]].append(max(0, delay))
    passed, inside, outside_counts = set(), set(), defaultdict(int)
    for passage in passages:
        identity = passage["vehicle_id"]
        if identity not in inserted or passage["detector_id"] not in detector_ids:
            raise ValueError("A native passage must match an inserted vehicle and a declared detector.")
        delay = passage["notification_time_s"] - inserted[identity]["actual_depart_s"]
        if not math.isfinite(delay) or delay < -1e-6:
            raise ValueError("A detector passage must follow actual insertion.")
        column = vehicle_columns[identity]
        network_delays[column].append(max(0, delay))
        passed.add(identity)
        key = (passage["detector_id"], float(passage["interval_begin_s"]), float(passage["interval_end_s"]))
        if key in row_index:
            numerators[row_index[key], column] += 1
            inside.add(identity)
        else:
            outside_counts[column] += 1
    if not np.array_equal(numerators.sum(axis=1), measured):
        raise ValueError("Native identities must reconcile with every periodic nVehContrib row.")
    columns = []
    for i, (plan, period) in enumerate(keys):
        members = {v for v, c in vehicle_columns.items() if c == i}

        def median(values):
            return float(np.median(values)) if values else None

        columns.append({"plan_id": plan, "requested_depart_begin_s": period * bin_seconds,
            "requested_depart_end_s": (period + 1) * bin_seconds, "requested_vehicles": int(denominators[i]),
            "inserted_vehicles": len(members & inserted.keys()),
            "not_inserted_vehicles": len(members - inserted.keys()),
            "inserted_without_complete_passage": len((members & inserted.keys()) - passed),
            "vehicles_with_observed_window_passage": len(members & inside),
            "vehicles_with_only_outside_window_passage": len((members & passed) - inside),
            "observed_window_passages": int(numerators[:, i].sum()),
            "outside_window_passages": outside_counts[i],
            "median_insertion_delay_s": median(insertion_delays[i]),
            "median_inserted_to_passage_delay_s": median(network_delays[i])})
    totals = {key: sum(r[key] for r in columns) for key in (
        "requested_vehicles", "inserted_vehicles", "not_inserted_vehicles", "inserted_without_complete_passage",
        "vehicles_with_observed_window_passage", "vehicles_with_only_outside_window_passage",
        "observed_window_passages", "outside_window_passages")}
    assert totals["requested_vehicles"] == sum(totals[key] for key in (
        "not_inserted_vehicles", "inserted_without_complete_passage", "vehicles_with_observed_window_passage",
        "vehicles_with_only_outside_window_passage"))
    first_observation = min(a for _, a, _ in row_keys)
    return {"numerators": numerators, "denominators": denominators, "requested_time_resolution_s": .001,
        "first_observation_begin_s": first_observation,
        "requested_before_first_observation": sum(t < first_observation for t in native_departures.values()),
        "fractions": numerators / denominators, "columns": columns, "row_keys": row_keys,
        "measured_counts": np.asarray(measured, dtype=np.int64), "totals": totals,
        "claim_boundary": "Same-replay accounting only. Requested vehicles form the denominator. Outside-window passages are not zero observations. Unsampled cells and causal effects of changed demand remain unknown."}
