# Ring1 fragility check: owner-delegated decisions before any dynamic run

Written 2026-10-26, after the round-2 pre-registration and before any candidate SUMO run of the Calibrate scenario.
The owner delegated these decisions. They answer the "owner decision needed" rows of the pre-registration
(`ring1-fragility-prereg-2026-10-26.md`, sha256 `7fe443eb2cd79812424f61a028e5f112a88a96834af66373b4589ee96dd92d64`;
its hash manifest `PREREG.sha256`, sha256 `7b7144c6ea4ba30a3100ca248285a8b547f012d36d81d9962c9ca5c76dda9464`, binds
the candidate nets, controls, goals and the code tree `2562999b19ce09fc612190e5e0b43bce1e331e41`).

| Question | Decision | Reason |
|---|---|---|
| S01 metric reading | Accept the arm-neutral metric: "wrong lane" is judged by the parent's usable links on both arms. The own-links count stays secondary. | A candidate that removes the wrong-lane standstill must show fewer standstills under one fixed definition; judging by the candidate's own links would erase the reference events by definition. |
| Spend runs on F1 | No. F1 is G0-rejected (netconvert reshapes 7 untouched internal paths at LSA535_part0), and its target, the link-5 trap, never occurs in the 46 reference draws because the lane-section hold masks it. | Its 16 runs could only test link-7 emergency braking, which F1 does not target. |
| Spend runs on F3 | No. The verified-parent chain stops at its G0-rejected step 1. | Same as F1. |
| Spend runs on F2 | Yes, the 16 planned runs on keys `fragility/F2/k1..k16` (spares k17-k19 for crashed or timed-out runs only). Every result is labelled `diagnostic_only`, as pre-registered, because F2 is `dynamic_hard` at L3 (collisions at :260305243_0_1 on routeability seeds 7 and 97). | F2 targets the S01 gridlock root and its precursor occurs in 22 of 46 reference draws, so the run answers whether the fix removes the precursor and whether calibration holds, even though F2 cannot be accepted. |
| F2 added-link state | Accept `minor_where_yielding` (the added link copies the sibling's column with `g` where the request matrix makes it yield). | This matches netconvert's own program for that link and avoids a priority green that the junction logic contradicts. |
| Health rule for candidate gridlocks | Keep the conservative A2 rule; report gridlocks against the reference rate (1 in 46) as well. | No outcome-informed relaxation. |

What this run can show: whether adding the lane-1 straight link removes the S01 wrong-lane standstills under the
parent's own definition, and whether the M2 calibration guards detect a regression. What it cannot show: an accepted
fix (F2 fails L3), a gridlock-rate reduction (gridlocks are too rare for 16 draws), or anything about field correctness.
