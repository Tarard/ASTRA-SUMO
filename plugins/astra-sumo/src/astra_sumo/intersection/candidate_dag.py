from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Any

from .archetype_profile import CLASSIFIER_VERSION, SCHEMA_ID


_TOPOLOGY_HYPOTHESES = (
    "preserve_split_shared_controller",
    "merge_physical_cell",
    "partial_internal_repair",
)


def build_candidate_hypothesis_dag(
    physical_cell: dict[str, Any],
    movement_hypotheses: dict[str, Any],
    *,
    archetype_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build reversible, mutually exclusive candidate plans.

    This is a planning artifact. It deliberately does not write PlainXML or a
    SUMO net and never selects a topology or movement variant automatically.
    """

    semantic_classes = _semantic_equivalence_classes(movement_hypotheses)
    physical_cell_id = str(physical_cell["hypothesis_id"])
    movement_set_id = str(movement_hypotheses["hypothesis_set_id"])
    if movement_hypotheses.get("parent_physical_cell_hypothesis_id") != physical_cell_id:
        raise ValueError("movement_hypotheses parent_physical_cell_hypothesis_id does not match the physical cell")
    scope = _scope_record(physical_cell)
    evidence_node = {
        "node_id": physical_cell_id,
        "node_kind": "physical_cell_hypothesis",
        "status": physical_cell["disposition"],
        "source_junction_ids": scope["source_junction_ids"],
        "boundary_port_ids": scope["boundary_port_ids"],
        "physical_approach_ids": scope["physical_approach_ids"],
    }
    movement_set_node = {
        "node_id": movement_set_id,
        "node_kind": "movement_hypothesis_set",
        "status": movement_hypotheses.get("disposition", "review"),
        "variant_ids": sorted(str(item["variant_id"]) for item in movement_hypotheses.get("variants", ())),
        "automatic_promotion_gate": "blocked",
    }

    archetype_node = None
    topology_reference_node = None
    archetype_classification_id = None
    topology_evidence_id = None
    if archetype_profile is not None:
        if archetype_profile.get("parent_physical_cell_hypothesis_id") != physical_cell_id:
            raise ValueError("archetype_profile parent_physical_cell_hypothesis_id does not match the physical cell")
        if archetype_profile.get("parent_movement_hypothesis_set_id") != movement_set_id:
            raise ValueError(
                "archetype_profile parent_movement_hypothesis_set_id does not match the movement hypotheses"
            )
        if archetype_profile.get("classification_only") is not True:
            raise ValueError("archetype_profile must be classification_only")
        if archetype_profile.get("generation_status") != "pass":
            raise ValueError("archetype_profile generation_status must be pass")
        if archetype_profile.get("automatic_promotion_gate") != "blocked":
            raise ValueError("archetype_profile automatic_promotion_gate must remain blocked")
        if archetype_profile.get("schema") != SCHEMA_ID:
            raise ValueError(f"archetype_profile schema must be {SCHEMA_ID}")
        if archetype_profile.get("classifier_version") != CLASSIFIER_VERSION:
            raise ValueError(f"archetype_profile classifier_version must be {CLASSIFIER_VERSION}")
        profile_payload = {key: value for key, value in archetype_profile.items() if key != "classification_id"}
        expected_classification_id = f"intersection-classification-{_stable_digest(profile_payload)[:20]}"
        if archetype_profile.get("classification_id") != expected_classification_id:
            raise ValueError("archetype_profile classification_id does not match its canonical content")
        archetype_classification_id = str(archetype_profile["classification_id"])
        raw_topology_evidence_id = archetype_profile.get("parent_topology_evidence_id")
        if not raw_topology_evidence_id:
            raise ValueError("archetype_profile must bind a topology evidence id")
        topology_evidence_id = str(raw_topology_evidence_id)
        topology_reference_node = {
            "node_id": topology_evidence_id,
            "node_kind": "topology_evidence_reference",
            "status": "hash_bound_reference",
            "automatic_promotion_gate": "blocked",
        }
        archetype_node = {
            "node_id": archetype_classification_id,
            "node_kind": "intersection_archetype_profile",
            "status": archetype_profile.get("disposition", "review"),
            "canonical_identity": archetype_profile.get("canonical_identity", {}),
            "derived_alias": archetype_profile.get("derived_alias", {}),
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
        }

    class_nodes = []
    candidate_nodes = []
    edges = [
        {
            "from_node_id": physical_cell_id,
            "to_node_id": movement_set_id,
            "relation": "movement_hypotheses_depend_on_physical_cell",
        }
    ]
    if archetype_node is not None:
        edges.extend(
            [
                {
                    "from_node_id": physical_cell_id,
                    "to_node_id": topology_evidence_id,
                    "relation": "topology_evidence_depends_on_physical_cell",
                },
                {
                    "from_node_id": topology_evidence_id,
                    "to_node_id": archetype_classification_id,
                    "relation": "archetype_depends_on_topology_evidence",
                },
                {
                    "from_node_id": movement_set_id,
                    "to_node_id": archetype_classification_id,
                    "relation": "archetype_depends_on_movement_hypotheses",
                },
            ]
        )
    exclusion_groups = []
    for semantic_class in semantic_classes:
        class_node = {
            "node_id": semantic_class["semantic_class_id"],
            "node_kind": "movement_semantic_class",
            **semantic_class,
        }
        class_nodes.append(class_node)
        edges.append(
            {
                "from_node_id": movement_set_id,
                "to_node_id": semantic_class["semantic_class_id"],
                "relation": "movement_semantics_derive_from_movement_hypotheses",
            }
        )
        topology_candidate_ids = []
        for topology_hypothesis in _TOPOLOGY_HYPOTHESES:
            candidate = _candidate_node(
                physical_cell,
                semantic_class=semantic_class,
                topology_hypothesis=topology_hypothesis,
                scope=scope,
                nested_restriction_ids=movement_hypotheses["nested_restriction_ids"],
                archetype_profile=archetype_profile,
            )
            candidate_nodes.append(candidate)
            topology_candidate_ids.append(candidate["candidate_id"])
            edges.append(
                {
                    "from_node_id": semantic_class["semantic_class_id"],
                    "to_node_id": candidate["candidate_id"],
                    "relation": "candidate_depends_on_movement_semantics",
                }
            )
            if archetype_classification_id is not None:
                edges.append(
                    {
                        "from_node_id": archetype_classification_id,
                        "to_node_id": candidate["candidate_id"],
                        "relation": "candidate_strategy_is_hash_bound_to_archetype",
                    }
                )
        exclusion_groups.append(
            {
                "group_id": (f"topology-choice-{semantic_class['semantic_class_id']}"),
                "reason": "one materialized candidate cannot use multiple physical-topology hypotheses",
                "candidate_ids": sorted(topology_candidate_ids),
                "selection_status": "unselected",
            }
        )

    if len(semantic_classes) > 1:
        exclusion_groups.append(
            {
                "group_id": f"movement-choice-{movement_hypotheses['hypothesis_set_id']}",
                "reason": "non-equivalent lane-movement semantic classes are mutually exclusive",
                "semantic_class_ids": sorted(item["semantic_class_id"] for item in semantic_classes),
                "selection_status": "unselected",
            }
        )

    payload = {
        "schema": "torii.intersection-candidate-dag/v1",
        "workflow_state": "HYPOTHESES_READY",
        "generation_status": "pass",
        "automatic_promotion_gate": "blocked",
        "selected_candidate_id": None,
        "parent_physical_cell_hypothesis_id": physical_cell_id,
        "parent_movement_hypothesis_set_id": movement_set_id,
        "parent_topology_evidence_id": topology_evidence_id,
        "parent_archetype_classification_id": archetype_classification_id,
        "scope": scope,
        "nodes": [
            evidence_node,
            movement_set_node,
            *([topology_reference_node] if topology_reference_node is not None else []),
            *([archetype_node] if archetype_node is not None else []),
            *class_nodes,
            *candidate_nodes,
        ],
        "edges": edges,
        "mutual_exclusion_groups": exclusion_groups,
        "semantic_equivalence_class_count": len(semantic_classes),
        "candidate_count": len(candidate_nodes),
        "review_ready_candidate_ids": sorted(
            item["candidate_id"] for item in candidate_nodes if item["candidate_status"] == "review_required"
        ),
        "blocked_candidate_ids": sorted(
            item["candidate_id"] for item in candidate_nodes if item["candidate_status"] == "blocked"
        ),
        "materializer_capabilities": {
            "plainxml_join_patch": "available_for_merge_hypothesis",
            "netconvert_internal_connection_regeneration": "available_after_candidate_selection",
            "obsolete_tls_owner_retirement": "available_through_scoped_netconvert_rebuild",
            "custom_tls_topology": "blocked_until_movement_and_conflict_closure",
            "field_timing_reconstruction": "out_of_scope",
            "direct_generic_netxml_edit": "prohibited",
        },
        "rollback_model": (
            "Candidates are immutable derived artifacts. Every operation rolls "
            "back by restoring the content-addressed parent source, never by "
            "mutating the source in place."
        ),
        "claim_boundary": (
            "This DAG proves candidate lineage, exclusivity, declared operations, "
            "and rollback intent. It does not prove which topology is physically "
            "correct and does not authorize materialization or TLS phases."
        ),
    }
    return {
        **payload,
        "candidate_dag_id": f"candidate-dag-{_stable_digest(payload)[:20]}",
    }


def _semantic_equivalence_classes(
    movement_hypotheses: dict[str, Any],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for variant in movement_hypotheses["variants"]:
        groups[str(variant["semantic_signature"])].append(variant)
    records = []
    for semantic_signature, variants in sorted(groups.items()):
        variant_ids = sorted(item["variant_id"] for item in variants)
        unresolved_reasons = sorted({reason for variant in variants for reason in variant["unresolved_reasons"]})
        payload = {
            "semantic_signature": semantic_signature,
            "movement_variant_ids": variant_ids,
            "methods": sorted(item["method"] for item in variants),
            "atomic_movement_count": variants[0]["atomic_movement_count"],
            "movement_family_count": variants[0]["movement_family_count"],
            "lane_coverage_status": variants[0]["lane_coverage"]["status"],
            "unresolved_reasons": unresolved_reasons,
        }
        records.append(
            {
                "semantic_class_id": (f"movement-class-{_stable_digest(payload)[:16]}"),
                **payload,
            }
        )
    return records


def _candidate_node(
    physical_cell: dict[str, Any],
    *,
    semantic_class: dict[str, Any],
    topology_hypothesis: str,
    scope: dict[str, Any],
    nested_restriction_ids: list[str],
    archetype_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    archetype_classification_id = str(archetype_profile["classification_id"]) if archetype_profile is not None else None
    blockers = list(semantic_class["unresolved_reasons"])
    blockers.extend(f"physical_cell:{item}" for item in physical_cell["risks"])
    if nested_restriction_ids:
        blockers.append("nested_turn_restrictions_not_resolved_to_lane_paths")
    review_requirements = _topology_review_requirements(topology_hypothesis)
    if archetype_profile is not None:
        recognition = archetype_profile.get("decision_capabilities", {}).get(
            "type_recognition",
            "review_required",
        )
        if recognition != "pass":
            review_requirements.append(f"archetype_type_recognition:{recognition}")
        review_requirements.extend(f"archetype_profile:{item}" for item in archetype_profile.get("review_reasons", ()))
    operation_specs = _operation_specs(
        topology_hypothesis,
        scope=scope,
        semantic_class=semantic_class,
        archetype_classification_id=archetype_classification_id,
    )
    identity_payload = {
        "physical_cell_hypothesis_id": physical_cell["hypothesis_id"],
        "semantic_class_id": semantic_class["semantic_class_id"],
        "topology_hypothesis": topology_hypothesis,
    }
    if archetype_classification_id is not None:
        identity_payload["archetype_classification_id"] = archetype_classification_id
    candidate_id = f"candidate-{_stable_digest(identity_payload)[:20]}"
    return {
        "node_id": candidate_id,
        "node_kind": "candidate_variant",
        "candidate_id": candidate_id,
        **identity_payload,
        "candidate_status": "blocked" if blockers else "review_required",
        "automatic_promotion_gate": "blocked",
        "materialization_status": "not_materialized",
        "scope": scope,
        "declared_operations": operation_specs,
        "operation_count": len(operation_specs),
        "blockers": sorted(set(blockers)),
        "review_requirements": sorted(set(review_requirements)),
        "preconditions": [
            "source artifact path and sha256 are bound",
            *(
                ["accepted archetype classification id and canonical identity still match"]
                if archetype_classification_id is not None
                else []
            ),
            "boundary ports and lane cardinality still match this hypothesis",
            (
                "movement semantic class is selected by review, certified evidence, "
                "or a preregistered experiment that remains promotion-blocked"
            ),
            "topology hypothesis is selected independently of controller membership",
            (
                "affected multimodal and controller owner closures are inventoried; "
                "unresolved closures block promotion but may be materialized only "
                "inside a preregistered falsification experiment"
            ),
        ],
        "expected_postconditions": _postconditions(topology_hypothesis),
        "rollback": {
            "strategy": "restore_content_addressed_parent_artifact",
            "source_mutation": False,
            "inverse_operation_ids": [item["inverse_operation"]["operation_id"] for item in reversed(operation_specs)],
        },
    }


def _operation_specs(
    topology_hypothesis: str,
    *,
    scope: dict[str, Any],
    semantic_class: dict[str, Any],
    archetype_classification_id: str | None,
) -> list[dict[str, Any]]:
    shared = {
        "source_junction_ids": scope["source_junction_ids"],
        "boundary_port_ids": scope["boundary_port_ids"],
        "movement_semantic_signature": semantic_class["semantic_signature"],
    }
    if archetype_classification_id is not None:
        shared["archetype_classification_id"] = archetype_classification_id
    if topology_hypothesis == "preserve_split_shared_controller":
        specs = [
            ("assert_preserved_physical_nodes", "assertion", shared),
            ("retire_obsolete_source_tls_owners", "plainxml_tls_scope", shared),
            ("bind_shared_controller_after_conflict_closure", "plainxml_tls_scope", shared),
        ]
    elif topology_hypothesis == "merge_physical_cell":
        specs = [
            ("join_physical_cell_nodes", "plainxml_node_join", shared),
            ("regenerate_internal_lane_connections", "netconvert_rebuild", shared),
            ("retire_obsolete_source_tls_owners", "netconvert_rebuild", shared),
            ("bind_merged_controller_after_conflict_closure", "plainxml_tls_scope", shared),
        ]
    else:
        specs = [
            ("assert_preserved_physical_nodes", "assertion", shared),
            ("rebuild_scoped_internal_connections", "plainxml_connection_scope", shared),
            ("rebuild_request_foes_from_independent_conflicts", "netconvert_rebuild", shared),
            ("rebind_controller_groups_after_conflict_closure", "plainxml_tls_scope", shared),
        ]
    return [
        _operation_record(
            name,
            operation_type=operation_type,
            payload=payload,
        )
        for name, operation_type, payload in specs
    ]


def _operation_record(
    name: str,
    *,
    operation_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "name": name,
        "operation_type": operation_type,
        "payload": payload,
    }
    operation_id = f"operation-{_stable_digest(identity)[:16]}"
    return {
        "operation_id": operation_id,
        **identity,
        "precondition_mode": "fail_closed",
        "inverse_operation": {
            "operation_id": f"inverse-{operation_id}",
            "operation_type": "restore_content_addressed_parent_artifact",
            "source_mutation": False,
        },
    }


def _scope_record(physical_cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_junction_ids": sorted(map(str, physical_cell["proposed_source_junction_ids"])),
        "geometry_shape_node_ids": sorted(map(str, physical_cell["geometry_shape_node_ids"])),
        "boundary_port_ids": sorted(item["boundary_port_id"] for item in physical_cell["raw_boundary_ports"]),
        "physical_approach_ids": sorted(item["physical_approach_id"] for item in physical_cell["physical_approaches"]),
        "scope_closure": [
            "physical_cell_members",
            "internal_geometry_shape_nodes",
            "boundary_ports",
            "movement_paths",
            "controller_owners",
        ],
    }


def _topology_review_requirements(topology_hypothesis: str) -> list[str]:
    if topology_hypothesis == "preserve_split_shared_controller":
        return [
            "prove multiple stop lines or conflict cells justify preserved nodes",
            "bind the complete shared-controller owner closure",
        ]
    if topology_hypothesis == "merge_physical_cell":
        return [
            "prove one connected physical conflict envelope",
            "prove no independent storage, crossing, access, rail, or grade separation",
            "prove fixed boundary ports remain unchanged after regeneration",
        ]
    return [
        "identify exact broken movement/request/TLS witnesses",
        "prove external junction topology and boundary ports need no change",
    ]


def _postconditions(topology_hypothesis: str) -> list[str]:
    common = [
        "all declared lane movements have traceable internal paths",
        "independent conflict graph agrees with request/foes and TLS groups",
        "outside-scope stable semantic diff is empty",
        "SUMO load and routeability smoke pass",
    ]
    if topology_hypothesis == "merge_physical_cell":
        return [
            "one target physical junction replaces only declared source members",
            "all obsolete source TLS owner identities are absent",
            *common,
        ]
    return [
        "declared physical junction identities remain unchanged",
        *common,
    ]


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
