"""First-order, two-sided SPSA from Spall (1998), equations 5 and 6."""

import math

import numpy as np


class InitialSPSAGainUnavailable(ValueError):
    """The first loss pair cannot determine the requested initial-move gain."""


def calibrate_spsa(objective, initial, *, lower, upper, a, A, c, alpha, gamma, iterations, seed,
                   on_iteration=None, initial_step=None, evaluate_pair=None):
    """Use explicit experiment settings and two loss evaluations per update.

    The caller supplies the simulation and records its raw outputs. A final
    simulation at the returned vector is separate from gradient estimation.
    Bounds apply before loss calls as in Antoniou et al. (2015), section 2.2.
    An optional evaluate_pair((plus, name), (minus, name)) replaces both
    objective calls and returns (loss_plus, loss_minus). It owns concurrency;
    the algorithm supplies independent vector copies and retains result order.
    """
    theta = np.asarray(initial, dtype=float).copy()
    low, high = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if (theta.ndim != 1 or not theta.size or low.shape != theta.shape or high.shape != theta.shape
            or not np.all(np.isfinite(theta)) or not np.all(np.isfinite(low))
            or np.any(np.isnan(high)) or np.any(high == -np.inf)
            or np.any(low >= high) or np.any(theta < low) or np.any(theta > high)):
        raise ValueError("Supply a finite initial vector inside explicit, nonempty parameter bounds.")
    if (any(not math.isfinite(v) or v <= 0 for v in (c, alpha, gamma))
            or (a is not None and (not math.isfinite(a) or a <= 0))
            or (a is None and (initial_step is None or not math.isfinite(initial_step) or initial_step <= 0))
            or (a is not None and initial_step is not None)
            or not math.isfinite(A) or A < 0
            or not isinstance(iterations, int) or isinstance(iterations, bool) or iterations <= 0
            or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0):
        raise ValueError("Supply positive gains, exponents and iterations, and nonnegative A and seed.")
    rng = np.random.default_rng(seed)
    history = []
    for k in range(1, iterations + 1):
        ck = c / k ** gamma
        delta = rng.choice((-1, 1), size=theta.size)
        plus, minus = np.clip(theta + ck * delta, low, high), np.clip(theta - ck * delta, low, high)
        if evaluate_pair is None:
            yplus = float(objective(plus.copy(), f"iteration-{k:04d}-plus"))
            yminus = float(objective(minus.copy(), f"iteration-{k:04d}-minus"))
        else:
            losses = evaluate_pair((plus.copy(), f"iteration-{k:04d}-plus"),
                                   (minus.copy(), f"iteration-{k:04d}-minus"))
            try:
                yplus, yminus = losses
            except (TypeError, ValueError) as error:
                raise ValueError("The pair evaluator must return exactly two losses in plus/minus order.") from error
            yplus, yminus = float(yplus), float(yminus)
        if not math.isfinite(yplus) or not math.isfinite(yminus):
            raise ValueError("Both perturbation losses must be finite. Preserve failed simulation outputs.")
        gradient = (yplus - yminus) / (2 * ck * delta)
        if a is None:
            magnitude = float(np.max(np.abs(gradient)))
            if magnitude == 0:
                raise InitialSPSAGainUnavailable("The first SPSA gradient is zero. Its loss pair cannot determine gain a.")
            # Spall (1998), Implementation, p. 820: set a from the desired initial move.
            a = initial_step * (A + 1) ** alpha / magnitude
        ak = a / (A + k) ** alpha
        following = np.clip(theta - ak * gradient, low, high)
        if not np.all(np.isfinite(gradient)) or not np.all(np.isfinite(following)):
            raise ValueError("SPSA arithmetic exceeded finite parameter values. Preserve the loss pair.")
        row = {"iteration": k, "theta": theta.tolist(), "a": a, "ak": ak, "ck": ck,
               "delta": delta.tolist(), "plus": plus.tolist(), "minus": minus.tolist(),
               "loss_plus": yplus, "loss_minus": yminus, "gradient": gradient.tolist(),
               "next_theta": following.tolist()}
        history.append(row)
        if on_iteration is not None:
            on_iteration(row)
        theta = following
    return {"theta": theta.tolist(), "history": history, "loss_evaluations": 2 * iterations,
            "a": a, "stopping_reason": "declared_iteration_count", "final_vector_remeasured": False}
