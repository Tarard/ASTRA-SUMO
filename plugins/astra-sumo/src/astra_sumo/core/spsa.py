"""First-order, two-sided SPSA from Spall (1998), equations 5 and 6."""

import math

import numpy as np


def calibrate_spsa(objective, initial, *, lower, upper, a, A, c, alpha, gamma, iterations, seed,
                   on_iteration=None):
    """Use explicit experiment settings and exactly two loss calls per update.

    The caller supplies the simulation and records its raw outputs. A final
    simulation at the returned vector is separate from gradient estimation.
    Bounds apply before loss calls as in Antoniou et al. (2015), section 2.2.
    """
    theta = np.asarray(initial, dtype=float).copy()
    low, high = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if (theta.ndim != 1 or not theta.size or low.shape != theta.shape or high.shape != theta.shape
            or not np.all(np.isfinite(theta)) or not np.all(np.isfinite(low))
            or np.any(np.isnan(high)) or np.any(high == -np.inf)
            or np.any(low >= high) or np.any(theta < low) or np.any(theta > high)):
        raise ValueError("Supply a finite initial vector inside explicit, nonempty parameter bounds.")
    if (any(not math.isfinite(v) or v <= 0 for v in (a, c, alpha, gamma))
            or not math.isfinite(A) or A < 0
            or not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0
            or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
        raise ValueError("Supply positive gains, exponents and iterations, and nonnegative A and seed.")
    rng = np.random.default_rng(seed)
    history = []
    for k in range(1, iterations + 1):
        ak, ck = a / (A + k) ** alpha, c / k ** gamma
        delta = rng.choice((-1, 1), size=theta.size)
        plus, minus = np.clip(theta + ck * delta, low, high), np.clip(theta - ck * delta, low, high)
        yplus = float(objective(plus.copy(), f"iteration-{k:04d}-plus"))
        yminus = float(objective(minus.copy(), f"iteration-{k:04d}-minus"))
        if not math.isfinite(yplus) or not math.isfinite(yminus):
            raise ValueError("Both perturbation losses must be finite. Preserve failed simulation outputs.")
        gradient = (yplus - yminus) / (2 * ck * delta)
        following = np.clip(theta - ak * gradient, low, high)
        if not np.all(np.isfinite(gradient)) or not np.all(np.isfinite(following)):
            raise ValueError("SPSA arithmetic exceeded finite parameter values. Preserve the loss pair.")
        row = {"iteration": k, "theta": theta.tolist(), "ak": ak, "ck": ck,
               "delta": delta.tolist(), "plus": plus.tolist(), "minus": minus.tolist(),
               "loss_plus": yplus, "loss_minus": yminus, "gradient": gradient.tolist(),
               "next_theta": following.tolist()}
        history.append(row)
        if on_iteration is not None:
            on_iteration(row)
        theta = following
    return {"theta": theta.tolist(), "history": history, "loss_evaluations": 2 * iterations,
            "stopping_reason": "declared_iteration_count", "final_vector_remeasured": False}
