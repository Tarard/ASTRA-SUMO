# Source-relative OCIT program timelines

The [OCIT-C specification](https://www.ocit.org/media/ocit-c_lsa_versorgungsdaten_v2.0_a01.pdf), page 51, defines switching times as transition starts.
The target image follows the group's transition sequence. A transition can cross the cycle boundary.
The specification leaves an omitted group's behavior undefined.

The shared [reader](../../plugins/astra-sumo/src/astra_sumo/core/ocit_c_programs.py) expands standard transitions and constant images.
It preserves native image codes and each group's declared securing states.
Dark release and an always-dark group remain distinct. The reader assigns no SUMO green or road permissions.
It retains `SignalzeitenVersatz` separately, without clock alignment.
Program selection uses the source reference name. Outstation numbers remain separate because exports can reuse them or omit them.
Its explicit modeling assumption repeats the listed cycle and holds each destination until the next transition.

Unknown rows and named transitions remain visible per group. Other groups retain their timelines.
This reader does not select weekday programs, infer actual field operation, or resolve primary/secondary control expressions.
It provides source evidence for later timing construction.
The [tests](../../tests/test_ocit_c_programs.py) include fractional boundaries, overlapping transitions, dark groups, and all five frozen Ring1 sites.

Route preparation also needs declared scope boundaries.
The existing route-support tool now accepts `boundary_zones_file`, including cuts where retained roads still meet.
It reports unresolved references and uses loaded passenger-lane permissions.
Road incidence cannot separate multiple detector lanes on one road. Actual E1 responses remain necessary for count fitting.

The [native counter fixture](../../benchmarks/hamburg_ring1_count_probe_20261021_v1/README.md) retains an unfitted 500-vehicle cohort.
It distinguishes complete E1 contributions from early departures and preserves a separate failed short-entry test.
The shared response reader reconciles event identities against periodic `nVehContrib` counts before constructing a proposal.
These identities are simulator instrumentation. The real observations remain aggregate field counts.
Prototype demand proposals preserve source departure-position selectors and require native replay after traffic changes.
