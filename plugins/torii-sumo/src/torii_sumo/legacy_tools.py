"""Legacy tools loaded only for explicit MCP or CLI requests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core.hamburg_corridor_candidate import (
    bind_hamburg_corridor_tls_clusters,
    select_hamburg_corridor,
)
from .tools.environment_tools import sumo_get_environment, sumo_preflight
from .tools.demand_tools import (
    sumo_detector_count_audit,
    sumo_detector_count_constraints,
    sumo_detector_route_support,
)
from .tools.digital_twin_tools import (
    sumo_detector_route_sampler_calibrate,
    sumo_digital_twin_replay_validate,
    sumo_hamburg_2394_archetype_classify,
    sumo_hamburg_2394_compound_geometry_first_pass,
    sumo_hamburg_2394_tls_topology_materialize,
    sumo_hamburg_sandtorkai_corridor_geometry_materialize,
    sumo_hamburg_sandtorkai_mainline_scope_materialize,
    sumo_hamburg_sandtorkai_corridor_tls_materialize,
    sumo_hamburg_cached_detector_demand,
    sumo_hamburg_corridor_candidate_map_bindings,
    sumo_hamburg_corridor_candidate_signal_bindings,
    sumo_hamburg_sandtorkai_corridor_candidate_package,
    sumo_hamburg_sandtorkai_geometry_safe_digital_twin,
    sumo_hamburg_corridor_candidate_detector_demand,
    sumo_hamburg_official_tls_rebuild,
    sumo_hamburg_sandtorkai_digital_twin,
    sumo_hamburg_named_count_scope,
    sumo_hamburg_named_detector_bindings,
    sumo_hamburg_sandtorkai_signal_observations,
    sumo_hamburg_sandtorkai_named_replay,
    sumo_hamburg_sandtorkai_execution_plan,
    sumo_network_surface_overlap_audit,
    sumo_network_surface_overlap_comparison,
)
from .tools.evidence_tools import (
    sumo_collect_evidence,
    sumo_compare_outputs,
    sumo_config_pair_preflight,
)
from .tools.intersection_tools import (
    sumo_intersection_archetype_classify,
    sumo_intersection_clean,
    sumo_intersection_model,
    sumo_intersection_scene_workflow,
    sumo_intersection_validate,
    sumo_nema_four_way_reference_workflow,
)
from .tools.road_network_tools import (
    sumo_intersection_road_sumo_bind,
    sumo_road_semantic_bridge,
)
from .tools.signal_tools import sumo_signal_device_profile_classify
from .tools.osm_tools import (
    sumo_network_connection_mode_audit,
    sumo_network_connection_mode_calibration,
    sumo_network_connection_mode_regression_audit,
    sumo_network_connected_core,
    sumo_network_corridor_candidate_gates,
    sumo_network_corridor_edit_ledger,
    sumo_network_corridor_geometry_simplification_variant,
    sumo_network_corridor_materialize_variant,
    sumo_network_exact_semantic_regression_audit,
    sumo_network_junction_aggregation_variant,
    sumo_network_overlapping_junction_audit,
    sumo_network_reference_hierarchy_audit,
    sumo_network_reference_join_audit,
    sumo_network_reference_scope_audit,
    sumo_network_review_html,
    sumo_network_routeability_audit,
    sumo_network_routeability_probe,
    sumo_network_scope_pruning_variant,
    sumo_network_standard_nema_phase_binding,
    sumo_network_teacher_guided_junction_variant,
    sumo_network_teacher_guided_repair_queue,
    sumo_network_teacher_corridor_comparison,
    sumo_network_tls_aggregation_variant,
    sumo_network_tls_reference_cleanup_variant,
    sumo_network_tls_warning_parity,
    sumo_network_topology_audit,
    sumo_osm_build_network,
    sumo_osm_cleanup_workflow,
    sumo_osm_resolve_place,
    sumo_tls_audit,
    sumo_tls_multisource_review,
)
from .tools.run_tools import sumo_run_config, sumo_run_minimal_smoke
from .tools.netedit_tools import sumo_netedit_session
from .tools.workflow_tools import torii_auto_workflow


WORKFLOW_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "hamburg_corridor_bind_tls_clusters": bind_hamburg_corridor_tls_clusters,
    "hamburg_corridor_select": select_hamburg_corridor,
    "sumo_run_config": sumo_run_config,
    "sumo_run_minimal_smoke": sumo_run_minimal_smoke,
    "sumo_collect_evidence": sumo_collect_evidence,
    "sumo_osm_build_network": sumo_osm_build_network,
    "sumo_osm_cleanup_workflow": sumo_osm_cleanup_workflow,
    "sumo_tls_audit": sumo_tls_audit,
    "sumo_tls_multisource_review": sumo_tls_multisource_review,
    "sumo_network_connected_core": sumo_network_connected_core,
    "sumo_network_routeability_probe": sumo_network_routeability_probe,
    "sumo_network_routeability_audit": sumo_network_routeability_audit,
    "sumo_network_connection_mode_audit": sumo_network_connection_mode_audit,
    "sumo_network_connection_mode_calibration": sumo_network_connection_mode_calibration,
    "sumo_network_exact_semantic_regression_audit": sumo_network_exact_semantic_regression_audit,
    "sumo_network_overlapping_junction_audit": sumo_network_overlapping_junction_audit,
    "sumo_network_reference_join_audit": sumo_network_reference_join_audit,
    "sumo_network_reference_hierarchy_audit": sumo_network_reference_hierarchy_audit,
    "sumo_network_reference_scope_audit": sumo_network_reference_scope_audit,
    "sumo_network_tls_warning_parity": sumo_network_tls_warning_parity,
    "sumo_network_surface_overlap_audit": sumo_network_surface_overlap_audit,
    "sumo_network_surface_overlap_comparison": sumo_network_surface_overlap_comparison,
    "sumo_network_junction_aggregation_variant": sumo_network_junction_aggregation_variant,
    "sumo_network_scope_pruning_variant": sumo_network_scope_pruning_variant,
    "sumo_network_corridor_geometry_simplification_variant": sumo_network_corridor_geometry_simplification_variant,
    "sumo_network_corridor_edit_ledger": sumo_network_corridor_edit_ledger,
    "sumo_network_corridor_materialize_variant": sumo_network_corridor_materialize_variant,
    "sumo_network_corridor_candidate_gates": sumo_network_corridor_candidate_gates,
    "sumo_network_teacher_corridor_comparison": sumo_network_teacher_corridor_comparison,
    "sumo_network_tls_reference_cleanup_variant": sumo_network_tls_reference_cleanup_variant,
    "sumo_network_standard_nema_phase_binding": sumo_network_standard_nema_phase_binding,
    "sumo_network_teacher_guided_junction_variant": sumo_network_teacher_guided_junction_variant,
    "sumo_network_teacher_guided_repair_queue": sumo_network_teacher_guided_repair_queue,
    "sumo_network_tls_aggregation_variant": sumo_network_tls_aggregation_variant,
    "sumo_intersection_model": sumo_intersection_model,
    "sumo_intersection_clean": sumo_intersection_clean,
    "sumo_intersection_validate": sumo_intersection_validate,
    "sumo_nema_four_way_reference_workflow": sumo_nema_four_way_reference_workflow,
    "sumo_intersection_scene_workflow": sumo_intersection_scene_workflow,
    "sumo_road_semantic_bridge": sumo_road_semantic_bridge,
    "sumo_intersection_road_sumo_bind": sumo_intersection_road_sumo_bind,
    "sumo_detector_route_support": sumo_detector_route_support,
    "sumo_detector_count_constraints": sumo_detector_count_constraints,
    "sumo_detector_route_sampler_calibrate": sumo_detector_route_sampler_calibrate,
    "sumo_hamburg_sandtorkai_digital_twin": sumo_hamburg_sandtorkai_digital_twin,
    "sumo_hamburg_named_count_scope": sumo_hamburg_named_count_scope,
    "sumo_hamburg_named_detector_bindings": sumo_hamburg_named_detector_bindings,
    "sumo_hamburg_sandtorkai_signal_observations": sumo_hamburg_sandtorkai_signal_observations,
    "sumo_hamburg_sandtorkai_named_replay": sumo_hamburg_sandtorkai_named_replay,
    "sumo_hamburg_sandtorkai_execution_plan": sumo_hamburg_sandtorkai_execution_plan,
    "sumo_hamburg_2394_archetype_classify": sumo_hamburg_2394_archetype_classify,
    "sumo_hamburg_2394_compound_geometry_first_pass": sumo_hamburg_2394_compound_geometry_first_pass,
    "sumo_hamburg_2394_tls_topology_materialize": sumo_hamburg_2394_tls_topology_materialize,
    "sumo_hamburg_sandtorkai_corridor_geometry_materialize": sumo_hamburg_sandtorkai_corridor_geometry_materialize,
    "sumo_hamburg_sandtorkai_mainline_scope_materialize": sumo_hamburg_sandtorkai_mainline_scope_materialize,
    "sumo_hamburg_sandtorkai_corridor_tls_materialize": sumo_hamburg_sandtorkai_corridor_tls_materialize,
    "sumo_hamburg_cached_detector_demand": sumo_hamburg_cached_detector_demand,
    "sumo_hamburg_corridor_candidate_detector_demand": sumo_hamburg_corridor_candidate_detector_demand,
    "sumo_hamburg_corridor_candidate_map_bindings": sumo_hamburg_corridor_candidate_map_bindings,
    "sumo_hamburg_corridor_candidate_signal_bindings": sumo_hamburg_corridor_candidate_signal_bindings,
    "sumo_hamburg_sandtorkai_corridor_candidate_package": sumo_hamburg_sandtorkai_corridor_candidate_package,
    "sumo_hamburg_sandtorkai_geometry_safe_digital_twin": sumo_hamburg_sandtorkai_geometry_safe_digital_twin,
    "sumo_hamburg_official_tls_rebuild": sumo_hamburg_official_tls_rebuild,
    "sumo_digital_twin_replay_validate": sumo_digital_twin_replay_validate,
}


def register_legacy_mcp_tools(server: FastMCP) -> None:
    server.tool(description="Return Python and SUMO environment discovery evidence.")(sumo_get_environment)
    server.tool(description="Run SUMO environment preflight and return a construction-check report.")(sumo_preflight)
    server.tool(description="Inspect a baseline and variant .sumocfg pair for missing inputs and shared outputs.")(
        sumo_config_pair_preflight
    )
    server.tool(description="Run a bounded SUMO config with stdout and stderr captured.")(sumo_run_config)
    server.tool(description="Run a generated minimal SUMO smoke scenario when SUMO binaries are available.")(
        sumo_run_minimal_smoke
    )
    server.tool(description="Compare baseline and variant SUMO summary/tripinfo outputs.")(
        sumo_compare_outputs
    )
    server.tool(description="Write a JSON and Markdown evidence bundle.")(
        sumo_collect_evidence
    )
    server.tool(description="Control the single hash-bound NetEdit diagnostic session with open, observe, act, finalize, or abort. Open requires immutable source/candidate/output paths and source SHA; F7 additionally requires a frozen selection and declared junction identities. Observe returns viewport evidence and persisted XML state. Act accepts one whitelisted mouse or shortcut action plus the last screenshot SHA. Finalize saves and runs SUMO-load, surface, Connection Mode, identity, and evidence-integrity audits; abort closes the session. The source stays immutable and promotion is always blocked.")(
        sumo_netedit_session
    )
    server.tool(description="Execute an explicit host-LLM workflow_selection from the registered scenario catalog without keyword reclassification. Inspect-only and ask-first retain a plan. Without workflow_selection, retain the older natural-language router for compatibility. Discover scenarios with torii workflows --json.")(
        torii_auto_workflow
    )
    server.tool(description="Resolve an OSM place name to a candidate area, bbox, and OSM confirmation links.")(
        sumo_osm_resolve_place
    )
    server.tool(description="Read-only classification of one local OSM intersection into a hash-bound finite composable archetype; preserve physical-cell, topology, and movement evidence without joining nodes, rebuilding channelization, or binding traffic lights.")(
        sumo_intersection_archetype_classify
    )
    server.tool(description="Read-only road semantic bridge for frozen local OSM, SUMO, and Hamburg HH-SIB snapshots: generate auditable conflation/lineage evidence and pass-only reviewed road-network properties without downloading data, changing a network, generating SUMO files, or authorizing geometry, lane, TLS, demand, or simulation changes.")(
        sumo_road_semantic_bridge
    )
    server.tool(description="Read-only binding of a hash-bound intersection road-detail profile to the exact OSM-to-SUMO lineage in a frozen road-semantic bridge report; return review-only road-arm/edge and connection-intent evidence without generating or authorizing lane connections, geometry edits, traffic-light bindings, demand, or simulation changes.")(
        sumo_intersection_road_sumo_bind
    )
    server.tool(description="Read-only classification of one German OCIT-C supply snapshot into a source-hash-bound composable inventory of logical signal groups, physical heads, visual displays, and audible/tactile outputs; preserve unknowns and keep automatic lane, movement, controller, phase, and control binding blocked.")(
        sumo_signal_device_profile_classify
    )
    server.tool(description="Build a structured IntersectionIR from a local OSM intersection patch without compiling SUMO artifacts.")(
        sumo_intersection_model
    )
    server.tool(description="Compile a local OSM T3/X4 intersection patch into IntersectionIR, SUMO plain files, optional .net.xml, and validation artifacts.")(
        sumo_intersection_clean
    )
    server.tool(description="Validate a compiled IntersectionIR artifact with SUMO load evidence when available.")(
        sumo_intersection_validate
    )
    server.tool(description="Build a reusable four-way SUMO reference intersection with NEMA 8-phase dual-ring timing, channelized lanes, audit JSON, and optional SUMO smoke evidence.")(
        sumo_nema_four_way_reference_workflow
    )
    server.tool(description="Run the narrow Phase-1 synthetic passenger-only four-way TLS workflow with a defaulted NEMA reference controller, load/routeability checks, and artifact manifest; this is not an OSM or city-network workflow.")(
        sumo_intersection_scene_workflow
    )
    server.tool(description="Low-level helper only: download/reuse OSM for an already chosen bbox, filter road classes, and build a raw SUMO network with netconvert. Do not use this as the full user-facing workflow.")(
        sumo_osm_build_network
    )
    server.tool(description="Extract SUMO TLS audit candidates and cluster nearby physical intersections.")(
        sumo_tls_audit
    )
    server.tool(description="Create a human-review TLS evidence table with OSM, region-aware map links such as Amap/Gaode or Google Maps, Mapillary, KartaView, official inventory, signal-plan, and field-evidence fields.")(
        sumo_tls_multisource_review
    )
    server.tool(description="Extract the largest passenger component of a SUMO network into a connected-core .net.xml and report discarded fragments.")(
        sumo_network_connected_core
    )
    server.tool(description="Generate routeability probe routes and a bounded .sumocfg for named key roads.")(
        sumo_network_routeability_probe
    )
    server.tool(description="Run a completion-aware SUMO routeability audit, extending the horizon until all generated vehicles finish or max_end is reached.")(
        sumo_network_routeability_audit
    )
    server.tool(description="Reconstruct NetEdit Connection Mode directly from SUMO net.xml and fail closed on invalid lane/via/request/linkIndex bindings; emit a display-only additional.xml review layer so NetEdit is optional for flagged visual review only.")(
        sumo_network_connection_mode_audit
    )
    server.tool(description="Calibrate the Connection Mode endpoint tolerance from an immutable, hash-bound source baseline using serialized coordinate precision and lane scale; block gross source gaps instead of falling back to a global 2 m threshold.")(
        sumo_network_connection_mode_calibration
    )
    server.tool(description="Compare a source and candidate SUMO net.xml with a code-native Connection Mode differential gate; block new lane/via/request/TLS regressions and emit a display-only additional.xml plus a SHA-bound manifest.")(
        sumo_network_connection_mode_regression_audit
    )
    server.tool(description="Run the promotion-grade corridor regression gate with stable semantic entities, exact finding witnesses, explicit traffic side, independent movement-conflict safety, source immutability, and a hash-closed artifact manifest.")(
        sumo_network_exact_semantic_regression_audit
    )
    server.tool(description="Audit dense SUMO junction clusters, including physical cross/T approach-axis shape scoring for over-fragmented OSM topology.")(
        sumo_network_topology_audit
    )
    server.tool(description="Audit close overlapping top-level SUMO junctions while ignoring valid internal crossing and walkingarea layers.")(
        sumo_network_overlapping_junction_audit
    )
    server.tool(description="Mine joined-junction cases from a reference SUMO network and match them against fragmented candidate topology clusters.")(
        sumo_network_reference_join_audit
    )
    server.tool(description="Audit high-hierarchy road differences against a reference network, separating over-split corridors, out-of-scope roads, hierarchy mismatches, and link/slip-lane cases.")(
        sumo_network_reference_hierarchy_audit
    )
    server.tool(description="Compare a candidate SUMO network against a reference network by OSM road-type scope and flag over-included short detail fragments for review.")(
        sumo_network_reference_scope_audit
    )
    server.tool(description="Create a separate SUMO plain-nodes junction-join patch and review variant from topology or reference-join audit reports without overwriting the source network.")(
        sumo_network_junction_aggregation_variant
    )
    server.tool(description="Create a separate reference-scope pruning review variant from a reference scope audit without overwriting the source network.")(
        sumo_network_scope_pruning_variant
    )
    server.tool(description="Create a semantics-gated corridor geometry simplification variant by removing only reference-absent micro nodes with identical road, lane, TLS, crossing, and alias-normalized connection semantics.")(
        sumo_network_corridor_geometry_simplification_variant
    )
    server.tool(description="Create a reversible corridor edit ledger for human-style add/delete/merge, pedestrian, crossing, and ramp proposals with evidence, protected TLS/rail/bridge/tunnel constraints, review additional.xml, rollback, and manifest artifacts.")(
        sumo_network_corridor_edit_ledger
    )
    server.tool(description="Run promotion gates for a materialized corridor candidate from persisted, path-and-hash-bound netconvert and optional human-review evidence; verify SUMO load, routeability, TLS/rail/bridge/tunnel/modal preservation, topology, and emit a gate manifest.")(
        sumo_network_corridor_candidate_gates
    )
    server.tool(description="Materialize a separate review-only corridor variant from explicitly accepted ledger operations using netconvert destructive or additive plain XML inputs; preserve protected semantics and block invalid crossings.")(
        sumo_network_corridor_materialize_variant
    )
    server.tool(description="Compare one current OSM-derived corridor junction with a manually cleaned teacher cell; emit candidate-bound map evidence, a display-only SUMO overlay, structured review decision, HTML, and manifest without mutating either network.")(
        sumo_network_teacher_corridor_comparison
    )
    server.tool(description="Create a byte-minimal, reversible TLS-reference cleanup candidate for provably stale pedestrian-internal attributes while preserving implicit railway controllers; emit rollback, review additional.xml, and manifest artifacts.")(
        sumo_network_tls_reference_cleanup_variant
    )
    server.tool(description="Scan isolated standard three/four-way vehicle-only TLS junctions with a fail-closed NetEdit Connection Mode audit and, for one explicitly selected eligible junction, create a reversible classic NEMA movement-phase/linkIndex candidate with netconvert, SUMO-load, junction-collision-aware routeability, display-only additional.xml review, rollback, and manifest gates; joined, pedestrian, rail, turnaround, and ambiguous cases are fail-closed.")(
        sumo_network_standard_nema_phase_binding
    )
    server.tool(description="Build a diagnostic teacher-guided junction variant by replaying reference lane permissions, movements, pedestrian ring, and tlLogic onto candidate plain network files; requires NetEdit connection-mode review before adoption.")(
        sumo_network_teacher_guided_junction_variant
    )
    server.tool(description="Execute ready teacher-guided junction repair queue items against explicit plain node/edge/connection files; requires per-result NetEdit connection-mode review before adoption.")(
        sumo_network_teacher_guided_repair_queue
    )
    server.tool(description="Create a separate TLS cleanup review variant with one real SUMO junction set as TLS per physical TLS audit cluster.")(
        sumo_network_tls_aggregation_variant
    )
    server.tool(description="Compare mapped TUM/reference and candidate SUMO TLS warnings, separating inherited warnings from candidate-only regressions.")(
        sumo_network_tls_warning_parity
    )
    server.tool(description="Create an HTML human-review cockpit for a generated or partial SUMO network and available audit artifacts.")(
        sumo_network_review_html
    )
    server.tool(description="Build sanitized source/sink, route-candidate, and route-detector-incidence manifests for detector-constrained SUMO demand reconstruction.")(
        sumo_detector_route_support
    )
    server.tool(description="Aggregate expected detector counts into sanitized time-bin constraints and SUMO routeSampler edgeData.")(
        sumo_detector_count_constraints
    )
    server.tool(description="Compare expected detector counts against SUMO E1 detector output and report detector-fit metrics.")(
        sumo_detector_count_audit
    )
    server.tool(description="Build the reusable Am Sandtorkai three-intersection vehicle digital-twin package: Torii OSM cleanup, official Hamburg count and traffic-light API snapshots, same-location SUMO E1/E2 sensors, 15-minute edge constraints, routeSampler demand, and auditable TLS link events.")(
        sumo_hamburg_sandtorkai_digital_twin
    )
    server.tool(description="Fetch a declared official Hamburg detector scope for the named Am Sandtorkai corridor, select a complete warm-up plus Saturday two-hour window, and write auditable 15-minute SUMO count inputs; missing detectors and unknown directions remain explicit and block promotion.")(
        sumo_hamburg_named_count_scope
    )
    server.tool(description="Record and resume the hash-bound Am Sandtorkai execution plan; keep count acquisition separate from network-bound detector binding, verify downstream network SHA-256 and stage identities, derive the W5 capability summary automatically, and fail closed on stale or missing official evidence.")(
        sumo_hamburg_sandtorkai_execution_plan
    )
    server.tool(description="Build the reusable W4 Am Sandtorkai detector-constrained SUMO replay from the exact road, W2 signal-binding, W3a count-scope, and W3b detector-binding manifests; require approved aggregation semantics, consume hash-bound mappings/events, and block on teleports or collisions.")(
        sumo_hamburg_sandtorkai_named_replay
    )
    server.tool(description="Fetch official Hamburg primary-signal history for a fixed UTC window from the bound Am Sandtorkai streams, write time-zero/in-window SUMO TLS link events, and fail closed on missing or partial observations.")(
        sumo_hamburg_sandtorkai_signal_observations
    )
    server.tool(description="Classify Hamburg junction 2394 as a finite composable archetype from hash-bound official MAP lane/stop-line/movement evidence, official OCIT controller/Teilknoten evidence, and frozen SUMO owner geometry; emit only review-gated local join hints and never mutate the source network.")(
        sumo_hamburg_2394_archetype_classify
    )
    server.tool(description="Materialize only the explicitly accepted Hamburg 2394 compound-geometry first pass from a frozen source network and classification file; require both exact SHA-256 values and the accepted classification id, retire only the bounded legacy OSM TLS bindings, leave official TLS restoration not run, and keep automatic promotion blocked.")(
        sumo_hamburg_2394_compound_geometry_first_pass
    )
    server.tool(description="Materialize the hash-bound Hamburg 2394 static topology candidate: apply the proven five-connection removal and three-connection repair, bind one all-red HH_2394 placeholder across exactly three official signal-bearing owners, preserve two passive priority owners, run netconvert/SUMO/surface audits, and keep historical Saturday timing blocked.")(
        sumo_hamburg_2394_tls_topology_materialize
    )
    server.tool(description="Materialize the geometry-preserving Am Sandtorkai three-controller TLS candidate from official movement paths only after a hash-bound road-arm/SUMO connection-intent artifact for the exact source network covers every lane transition; add only missing MAP/OCIT transitions and keep historical timing blocked.")(
        sumo_hamburg_sandtorkai_corridor_tls_materialize
    )
    server.tool(description="Materialize the topology-aware Am Sandtorkai geometry-safe candidate: join only confirmed sub-groups, protect the official 0228 branch, trim inherited oversized junction faces, run SUMO/surface audits, and never overwrite the source network.")(
        sumo_hamburg_sandtorkai_corridor_geometry_materialize
    )
    server.tool(description="Materialize the bounded Am Sandtorkai mainline scope: keep the explicit 0228-2421-2394 backbone and short signal approaches, remove the upper-left 0228 branch, join the proven 2394 micro-junction pair, and emit a hash-bound NetEdit review candidate.")(
        sumo_hamburg_sandtorkai_mainline_scope_materialize
    )
    server.tool(description="Read-only complement to SUMO edge-overlap warnings: audit non-internal junction polygon overlaps and reconstructed external lane faces entering non-owner junctions; exclude expected owner endpoint contact and internal lanes, hash the source, and fail closed on findings or invalid geometry.")(
        sumo_network_surface_overlap_audit
    )
    server.tool(description="Compare baseline and candidate lane/junction surface-overlap audits for an explicit bounded junction focus; pass only with zero introduced and zero focus findings while preserving inherited out-of-scope defects and both global audit statuses.")(
        sumo_network_surface_overlap_comparison
    )
    server.tool(description="Resume Am Sandtorkai detector-demand construction from hash-pinned official TLS/MAP evidence and cached Hamburg count files; reuse the audited MAP-to-SUMO lane contract without downloading OSM or repeating nearest-lane matching.")(
        sumo_hamburg_cached_detector_demand
    )
    server.tool(description="Generate same-location E1/E2 sensors and routeSampler demand for the hash-bound geometry-preserving Am Sandtorkai TLS candidate; reuse frozen official MAP/count evidence while keeping the candidate's topology/surface review gate explicit.")(
        sumo_hamburg_corridor_candidate_detector_demand
    )
    server.tool(description="Recompute the hash-bound official MAP-to-SUMO lane binding contract on the exact geometry-preserving Am Sandtorkai corridor candidate before generating sensors or demand; never reuse pre-materialization nearest-edge assignments silently.")(
        sumo_hamburg_corridor_candidate_map_bindings
    )
    server.tool(description="Bind frozen official primary-signal metadata to the exact candidate controller links after MAP reprojection; preserve active/redundant status and keep historical signal replay blocked when observations are unavailable.")(
        sumo_hamburg_corridor_candidate_signal_bindings
    )
    server.tool(description="Run the geometry-preserving Am Sandtorkai candidate MAP reprojection, primary-signal binding, and detector-demand/routeSampler stages as one hash-bound review package; keep the blocked topology and historical-signal gates explicit.")(
        sumo_hamburg_sandtorkai_corridor_candidate_package
    )
    server.tool(description="Run the staged Am Sandtorkai geometry-safe digital-twin package: without an exact-candidate road-arm/SUMO binding it stops after the geometry review candidate; with the binding it may proceed to TLS, MAP/E1/E2, signal, and demand stages while retaining warm-up/history gates.")(
        sumo_hamburg_sandtorkai_geometry_safe_digital_twin
    )
    server.tool(description="Rebuild the Am Sandtorkai three-node SUMO traffic-light topology from a frozen network and cached official Hamburg MAP/OCIT assets; fail closed unless the TLD groups equal the complete OCIT motor-group inventory and all 27 primary signal streams bind as active or same-group redundant.")(
        sumo_hamburg_official_tls_rebuild
    )
    server.tool(description="Run Eclipse SUMO routeSampler against explicit candidate routes and multi-interval edge count constraints; report the resulting plausible detector-constrained route file and mismatch evidence, not a uniquely identified true OD matrix.")(
        sumo_detector_route_sampler_calibrate
    )
    server.tool(description="Run a passenger-link-complete official TLS event replay with SUMO E1/E2 sensors, then compare real counts to virtual nVehContrib bins and collect completion evidence.")(
        sumo_digital_twin_replay_validate
    )
