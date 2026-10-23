# Hamburg ordinary SPSA

Accepted exact calibration: False.
Stopping reason: declared_iteration_count. Completed updates: 1.

Counts use native E1 nVehContrib. All 130 fields and 40 derived station rows are retained.
Unsafe results have diagnostic errors only. Fixed route/lane/time templates remain priors.

[Settings](settings.json), [evaluations](evaluations.json), [result](result.json).

Runner: plugins/astra-sumo/scripts/run_hamburg_od_spsa.py. Scenario: C:\Users\huqio\Documents\ASTRA-SUMO\outputs\hamburg-od-baseline-20261022-v1.
Analysis: plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py. Raw results: C:\Users\huqio\Documents\ASTRA-SUMO\outputs\hamburg-od-spsa-standard-20261022-v2.

| Evaluation | Diagnostic MAE | Max error | Collisions | Teleports | Waiting | In network | Healthy |
|---|---:|---:|---:|---:|---:|---:|---|
| initial | 14.923077 | 60 | 3 | 3 | 976 | 424 | False |
| iteration-0001-plus | 15.615385 | 72 | 5 | 5 | 1211 | 365 | False |
| iteration-0001-minus | 17.676923 | 67 | 2 | 2 | 1519 | 396 | False |
| final | 15.646154 | 62 | 6 | 6 | 1212 | 407 | False |

Initial native evidence reused from: C:\Users\huqio\Documents\ASTRA-SUMO\outputs\hamburg-od-initial-native-20261022-v1.
[Input and native correspondence](initial-reuse.json).
