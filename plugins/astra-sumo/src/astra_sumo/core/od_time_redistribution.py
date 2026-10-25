"""Propose a bounded departure-period redistribution for one observed OD pair."""

import math
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def propose_od_time_redistribution(*, response, counts, measured, targets, od_ids, max_per_cell, max_moved):
    """Use replay proportions as a proposal, never as causal derivatives or new measurements."""
    matrix = np.asarray(response, dtype=float)
    counts, measured, targets = [np.asarray(values, dtype=float) for values in (counts, measured, targets)]
    if (matrix.ndim != 2 or counts.ndim != 1 or measured.ndim != 1 or targets.shape != measured.shape
            or matrix.shape != (len(measured), len(counts)) or len(od_ids) != len(counts)
            or not counts.size or not measured.size
            or any(not isinstance(key, str) or not key for key in od_ids)
            or any(not np.all(np.isfinite(values)) or np.any(values < 0) for values in (matrix, counts, measured, targets))
            or any(np.any(values != np.rint(values)) for values in (counts, measured, targets))):
        raise ValueError("Use finite complete integer counts and a matching nonnegative response matrix.")
    if np.any(counts <= 0):
        raise ValueError("Use only sampled positive-request departure cells; keep unsampled cells unchanged.")
    if not np.allclose(matrix @ counts, measured, rtol=0, atol=1e-7):
        raise ValueError("Replay proportions must reconcile with every supplied native measurement.")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (max_per_cell, max_moved)):
        raise ValueError("Use positive integer limits for each cell and the moved population.")
    baseline = float(np.abs(measured - targets).sum())
    best = None
    incomplete = []
    rows = len(measured)
    for group in sorted(set(od_ids)):
        indices = np.array([i for i, key in enumerate(od_ids) if key == group])
        n = len(indices)
        if n < 2:
            continue
        local = matrix[:, indices]
        identity = np.eye(n)
        equality = np.hstack([local, np.zeros((rows, n)), -np.eye(rows), np.eye(rows)])
        absolute = np.vstack([np.hstack([identity, -identity]), np.hstack([-identity, -identity])])
        absolute = np.hstack([absolute, np.zeros((2 * n, 2 * rows))])
        total = np.concatenate([np.ones(n), np.zeros(n + 2 * rows)])[None, :]
        moved = np.concatenate([np.zeros(n), np.ones(n), np.zeros(2 * rows)])[None, :]
        constraints = [LinearConstraint(equality, targets - measured, targets - measured),
            LinearConstraint(absolute, -np.inf, 0), LinearConstraint(total, 0, 0), LinearConstraint(moved, 0, 2 * max_moved)]
        lower = np.concatenate([-np.minimum(counts[indices], max_per_cell), np.zeros(n + 2 * rows)])
        upper = np.concatenate([np.full(n, max_per_cell), np.full(n + 2 * rows, np.inf)])
        objective = np.concatenate([np.zeros(n), np.full(n, 1e-6), np.ones(2 * rows)])
        solved = milp(objective, integrality=np.concatenate([np.ones(n), np.zeros(n + 2 * rows)]),
            bounds=Bounds(lower, upper), constraints=constraints, options={"time_limit": 2.0})
        if not solved.success:
            incomplete.append(group)
            continue
        delta = np.rint(solved.x[:n]).astype(int)
        if (delta.sum() != 0 or np.abs(delta).sum() > 2 * max_moved
                or np.any(np.abs(delta) > max_per_cell) or np.any(counts[indices] + delta < 0)):
            raise RuntimeError("The proposed integer redistribution violates its bounds.")
        prediction = measured + local @ delta
        error = float(np.abs(prediction - targets).sum())
        population = int(np.abs(delta).sum() // 2)
        if error >= baseline - 1e-7 or best is not None and (error, population, group) >= best[0]:
            continue
        updated = counts.copy()
        updated[indices] += delta
        best = ((error, population, group), {"status": "candidate", "counts": updated.astype(int).tolist(),
            "od_id": group, "changed_indices": indices[delta != 0].tolist(), "delta": (updated - counts).astype(int).tolist(),
            "moved_vehicles": population, "predicted_total_absolute_error": error,
            "predicted_counts": prediction.tolist()})
    result = best[1] if best else {"status": "review_required" if incomplete else "no_effect", "counts": counts.astype(int).tolist(),
        "moved_vehicles": 0, "predicted_total_absolute_error": baseline}
    result.update(baseline_total_absolute_error=baseline, observation_rows=rows,
        max_per_cell=max_per_cell, max_moved=max_moved, formal_result=False,
        incomplete_od_searches=incomplete,
        claim_boundary="Same-replay proportions guide one bounded OD timing proposal. OD totals remain fixed. Changed queues and native counts are unknown until a new full native evaluation.")
    return result


def propose_measured_unit_step(*, prior, trial, od_ids, before, after, targets):
    """Use a measured coupled count change for the smallest integer step on that direction."""
    prior, trial, before, after, targets = [np.asarray(values, dtype=float) for values in (prior, trial, before, after, targets)]
    if (prior.ndim != 1 or trial.shape != prior.shape or len(od_ids) != len(prior)
            or any(not isinstance(key, str) or not key for key in od_ids)
            or before.ndim != 1 or not before.size or after.shape != before.shape or targets.shape != before.shape
            or any(not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values != np.rint(values))
                   for values in (prior, trial, before, after, targets))):
        raise ValueError("Use matching finite nonnegative integer demand and complete native count vectors.")
    delta = (trial - prior).astype(int)
    changed = np.flatnonzero(delta)
    if not len(changed) or delta.sum() != 0 or len({od_ids[i] for i in changed}) != 1:
        raise ValueError("Use one OD's measured nonzero timing redistribution with unchanged total demand.")
    divisor = math.gcd(*(abs(int(delta[i])) for i in changed))
    unit = delta // divisor
    baseline = float(np.abs(before - targets).sum())
    best = None
    for sign in (-1, 1):
        counts = prior + sign * unit
        prediction = before + sign * (after - before) / divisor
        if np.any(counts < 0) or np.any(prediction < 0):
            continue
        error = float(np.abs(prediction - targets).sum())
        if error < baseline - 1e-7 and (best is None or error < best['predicted_total_absolute_error']):
            best = {'status': 'candidate', 'counts': counts.astype(int).tolist(),
                'fraction_of_measured_change': sign / divisor, 'delta': (sign * unit).tolist(),
                'moved_vehicles': int(np.abs(unit).sum() // 2), 'predicted_counts': prediction.tolist(),
                'predicted_total_absolute_error': error}
    result = best if best is not None else {'status': 'no_effect', 'counts': prior.astype(int).tolist(),
        'moved_vehicles': 0, 'predicted_total_absolute_error': baseline}
    result.update(formal_result=False, observation_rows=len(before), baseline_total_absolute_error=baseline,
        claim_boundary='One measured action supplies a coupled local secant, not a validated demand derivative. The smallest integer step still needs full native measurement and all safety/backlog checks.')
    return result
