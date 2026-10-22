"""Account for departure-to-detector lags in one completed native replay."""

from collections import Counter, defaultdict
import math

import numpy as np


def propose_native_count_retiming(*, requests, passages, measurements, max_moves_per_field=8,
                                 max_shift_s=900, margin_s=30, step_s=.1):
    """Move a bounded number of observed surplus passages toward deficient bins.

    This is a proposal from one replay. A new native run must measure its effect.
    Missing passages are not assigned a time or a zero response.
    """
    if (not isinstance(max_moves_per_field, int) or isinstance(max_moves_per_field, bool)
            or max_moves_per_field < 1 or not all(math.isfinite(v) and v > 0
            for v in (max_shift_s, margin_s, step_s))):
        raise ValueError("Use positive finite limits and an integer move limit.")
    requested = {row["vehicle_id"]: row for row in requests}
    if (not requested or len(requested) != len(requests)
            or any(not identity or not row["detector_id"] for identity, row in requested.items())):
        raise ValueError("Use distinct explicit vehicle requests.")
    if any(not math.isfinite(row["requested_depart_s"]) or row["requested_depart_s"] < 0
           for row in requests):
        raise ValueError("Use finite nonnegative requested departures.")
    bins = {}
    for row in measurements:
        field, begin, end = row["detector_id"], float(row["begin"]), float(row["end"])
        wanted, measured = row["expected_total"], row["measured_nVehContrib"]
        key = field, begin, end
        if (key in bins or not math.isfinite(begin) or not math.isfinite(end)
                or not 0 <= begin < end or 2 * margin_s >= end - begin
                or any(value is None or value == "" or not math.isfinite(float(value))
                       or float(value) < 0 or int(float(value)) != float(value)
                       for value in (wanted, measured))):
            raise ValueError("Retain unique complete native E1 intervals and integer targets.")
        bins[key] = int(float(wanted)) - int(float(measured))
    if not bins:
        raise ValueError("Retain every measured target interval.")
    begin_window, end_window = min(key[1] for key in bins), max(key[2] for key in bins)
    observed, events, seen = Counter(), [], set()
    for row in passages:
        identity, field = row["vehicle_id"], row["detector_id"]
        time = float(row["notification_time_s"])
        key = field, float(row["interval_begin_s"]), float(row["interval_end_s"])
        if (identity not in requested or (identity, field) in seen or not math.isfinite(time)
                or time < requested[identity]["requested_depart_s"] or not key[1] <= time < key[2]
                or field != requested[identity]["detector_id"]):
            raise ValueError("Match distinct native notifications to their declared vehicle and field.")
        seen.add((identity, field))
        if key in bins:
            observed[key] += 1
        elif begin_window <= time < end_window:
            raise ValueError("A passage inside the fitting window has no target interval.")
        events.append((identity, field, time, key))
    if any(observed[key] != int(float(row["measured_nVehContrib"]))
           for row in measurements
           for key in [(row["detector_id"], float(row["begin"]), float(row["end"]))]):
        raise ValueError("Reconcile all native identities with nVehContrib before proposing changes.")
    proposals, moved = [], set()
    # Keep the initialization requests fixed. Unobserved vehicles remain unchanged.
    for field in sorted({key[0] for key in bins}):
        for _ in range(max_moves_per_field):
            candidates = []
            for identity, event_field, time, source_key in events:
                depart = requested[identity]["requested_depart_s"]
                if (event_field != field or identity in moved or depart < begin_window
                        or time < begin_window or (source_key in bins and bins[source_key] >= 0)):
                    continue
                for key, deficit in bins.items():
                    if key[0] != field or deficit <= 0:
                        continue
                    wanted = min(max(time, key[1] + margin_s), key[2] - margin_s)
                    shift = round((wanted - time) / step_s) * step_s
                    proposed = round(depart + shift, 9)
                    if (not shift or abs(shift) > max_shift_s + 1e-8 or proposed < begin_window
                            or not key[1] <= time + shift < key[2]):
                        continue
                    candidates.append((abs(shift), key[1], identity, proposed, shift, source_key, key))
            if not candidates:
                break
            _, _, identity, proposed, shift, source_key, target_key = min(candidates)
            moved.add(identity)
            bins[target_key] -= 1
            if source_key in bins:
                bins[source_key] += 1
            proposals.append({"vehicle_id": identity, "detector_id": field,
                              "source_depart_s": requested[identity]["requested_depart_s"],
                              "proposed_depart_s": proposed, "shift_s": shift,
                              "source_interval": list(source_key), "target_interval": list(target_key)})
    return {"proposals": proposals, "changed_vehicles": len(proposals),
            "max_moves_per_field": max_moves_per_field, "max_shift_s": max_shift_s,
            "formal_result": False, "new_simulations": 0,
            "claim_boundary": "Bounded proposal from measured surplus and later native passages. Old notification lags do not predict changed queues. Missing passages stay unknown. Measure every target again and reject non-improving or unsafe candidates."}


def native_departure_windows(*, requests, passages, max_shift_s=900, guard_s=90, step_s=.1):
    """Intersect target windows under frozen native passage lags.

    This proposes request times only. Changed queues require another native run.
    """
    if (not math.isfinite(max_shift_s) or max_shift_s < 0
            or not math.isfinite(guard_s) or guard_s < 0):
        raise ValueError("Use finite nonnegative shift and margin limits.")
    if (not math.isfinite(step_s) or step_s < .001
            or abs(step_s * 1000 - round(step_s * 1000)) > 1e-6):
        raise ValueError("Use a positive simulation step in whole milliseconds.")
    step_ms = round(step_s * 1000)
    wanted = {}
    for request in requests:
        identity, depart, targets = (request[k] for k in ("vehicle_id", "requested_depart_s", "targets"))
        if (not isinstance(identity, str) or not identity or identity in wanted
                or not math.isfinite(depart) or depart < 0 or not targets):
            raise ValueError("Use unique vehicles, finite nonnegative departures and explicit targets.")
        fields = set()
        for target in targets:
            field, begin, end = (target[k] for k in ("detector_id", "begin_s", "end_s"))
            if (not isinstance(field, str) or not field or field in fields
                    or not math.isfinite(begin) or not math.isfinite(end)
                    or not 0 <= begin < end or 2 * guard_s >= end - begin):
                raise ValueError("Use distinct fields and finite target windows wider than both margins.")
            fields.add(field)
        wanted[identity] = request
    if not wanted:
        raise ValueError("Retain explicit requested vehicles.")
    observed = {}
    for passage in passages:
        identity, field, time = (passage[k] for k in ("vehicle_id", "detector_id", "notification_time_s"))
        key = identity, field
        if (identity not in wanted or field not in {t["detector_id"] for t in wanted[identity]["targets"]}
                or key in observed or not math.isfinite(time)
                or time < math.floor(wanted[identity]["requested_depart_s"] * 1000 + .5) / 1000 - 1e-6):
            raise ValueError("Use one native notification per declared vehicle and field after its request.")
        observed[key] = time
    rows = []
    budget_steps = math.floor(max_shift_s / step_s + 1e-9)
    for identity, request in wanted.items():
        depart_ms = math.floor(request["requested_depart_s"] * 1000 + .5)
        missing = [t["detector_id"] for t in request["targets"] if (identity, t["detector_id"]) not in observed]
        row = {**request, "native_requested_depart_s": depart_ms / 1000,
            "observed_target_passages": len(request["targets"]) - len(missing),
            "missing_fields": missing, "joint_depart_min_s": None, "joint_depart_max_s": None,
            "proposed_depart_s": None, "proposed_shift_s": None}
        if missing:
            row["status"] = "missing_native_passage"
        else:
            # Whole-step shifts retain the source request's phase within a simulation step.
            lower = max(math.ceil((t["begin_s"] + guard_s - observed[identity, t["detector_id"]]) / step_s - 1e-8)
                        for t in request["targets"])
            upper = min(math.ceil((t["end_s"] - guard_s - observed[identity, t["detector_id"]]) / step_s - 1e-8) - 1
                        for t in request["targets"])
            row.update(joint_depart_min_s=(depart_ms + lower * step_ms) / 1000,
                       joint_depart_max_s=(depart_ms + upper * step_ms) / 1000)
            start, stop = max(lower, math.ceil(-depart_ms / step_ms), -budget_steps), min(upper, budget_steps)
            if lower > upper:
                row["status"] = "incompatible_target_windows"
            elif depart_ms + upper * step_ms < 0:
                row["status"] = "before_simulation_start"
            elif start > stop:
                row["status"] = "shift_budget_exceeded"
            else:
                shift_ms = min(max(0, start), stop) * step_ms
                row.update(status="feasible_frozen_lags", proposed_depart_s=(depart_ms + shift_ms) / 1000,
                           proposed_shift_s=shift_ms / 1000)
        rows.append(row)
    return {"rows": rows, "status_counts": dict(Counter(r["status"] for r in rows)),
        "model_vehicles": len(rows), "target_field_visits": sum(len(r["targets"]) for r in rows),
        "observed_target_passages": len(observed), "max_shift_s": max_shift_s, "guard_s": guard_s, "step_s": step_s,
        "formal_result": False, "new_simulations": 0, "changed_demand_response_verified": False,
        "claim_boundary": "Frozen native notification lags only. Missing passages stay unknown. Shared vehicles must satisfy all declared target windows. These intervals do not prove feasibility or infeasibility after queues, insertion or lane choices change."}


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
