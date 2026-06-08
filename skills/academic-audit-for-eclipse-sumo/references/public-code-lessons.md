# Public Code Lessons

Use public repositories for evaluation structure and failure-pattern awareness. Do not copy code, claims, or repository-specific assumptions.

## SUMO-RL

Repository: https://github.com/LucasAlegre/sumo-rl

Reusable lessons:

- State, action, and reward definitions must be explicit.
- `delta_time`, yellow time, and min-green constraints shape controller behavior.
- Queue, density, waiting time, and phase indicators are common observation components.
- Evaluation metrics must be distinguished from RL rewards; a dense training reward is not automatically a publishable performance metric.
- GUI and parallel/libsumo modes can have different operational constraints.
- A learning environment is not automatically a fair academic benchmark without seeds, scenarios, baselines, and statistics.

## RESCO

Repository: https://github.com/Pi-Star-Lab/RESCO

Reusable lessons:

- Use real-world scenarios and scenario separation.
- Include static baselines such as Fixed Time, Max Pressure, and Max Wave where appropriate.
- State, reward, and action configurations should be reported.
- Benchmark claims need seeds, variance, and reproducible commands.
- Route completion, throughput, and backlog handling must be the same across controllers.

## LibSignal

Project: https://darl-libsignal.github.io/

Reusable lessons:

- Cross-simulator comparison requires unified state, reward, and metric definitions.
- A controller that performs well in one simulator is not automatically validated in another.
- Dataset and simulator transformations are part of the experimental method, not background detail.
- Multiple baselines and datasets reduce benchmark narrowness.
- Unified metrics should include travel time, queue, delay, and throughput definitions, not just model names.

## sumoITScontrol

Repository: https://github.com/DerKevinRiehl/sumoITScontrol

Reusable lessons:

- Keep control context objects separate from controller algorithms.
- Distinguish fixed-cycle and flexible-cycle Max Pressure.
- Report controller parameters such as min green, max green, yellow, measurement period, and cycle duration.
- Variance-aware benchmarking is stronger than single-run demos.
- For coordinated control, record travel-time or offset assumptions.

## Use Pattern

When learning from public code:

1. Identify the evaluation structure.
2. Extract baseline classes and metrics.
3. Check how scenario, seed, and output handling are recorded.
4. Avoid copying implementation code.
5. Do not import performance claims unless the same experimental gates are reproduced.
