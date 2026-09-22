"""Source-neutral road-network semantics and source adapters.

This package keeps road identity/property evidence separate from OSM import
scope, intersection recognition, and SUMO materialization policy.
"""

from .contracts import (
    CanonicalRoadLink,
    ConflationEvidence,
    ConflationRelation,
    RoadCorridor,
    RoadObjectRef,
    RoadPropertyAssignment,
    build_conflation_relation,
    project_road_detail_evidence,
)
from .conflation import (
    build_osm_subset_derivation_relations,
    build_osm_sumo_lineage_relations,
    generate_official_osm_conflation_candidates,
)

__all__ = [
    "CanonicalRoadLink",
    "ConflationEvidence",
    "ConflationRelation",
    "RoadCorridor",
    "RoadObjectRef",
    "RoadPropertyAssignment",
    "build_conflation_relation",
    "build_osm_subset_derivation_relations",
    "build_osm_sumo_lineage_relations",
    "generate_official_osm_conflation_candidates",
    "project_road_detail_evidence",
]
