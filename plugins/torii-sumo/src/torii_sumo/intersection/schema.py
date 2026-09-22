from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from torii_sumo.road_semantics import classify_approach_mode_layer


class BBox(BaseModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class OSMNode(BaseModel):
    id: str
    lat: float
    lon: float
    x: float | None = None
    y: float | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class OSMWay(BaseModel):
    id: str
    node_refs: list[str]
    tags: dict[str, str] = Field(default_factory=dict)


class OSMRelation(BaseModel):
    id: str
    members: list[dict[str, str]] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


class PatchSeed(BaseModel):
    osm_node_id: str | None = None
    osm_way_id: str | None = None
    center_latlon: tuple[float, float] | None = None


class OSMPatch(BaseModel):
    nodes: dict[str, OSMNode]
    ways: dict[str, OSMWay]
    relations: dict[str, OSMRelation]
    bbox: BBox
    seed: PatchSeed | None = None


class IntersectionCore(BaseModel):
    core_id: str
    center_xy: tuple[float, float]
    center_latlon: tuple[float, float] | None = None
    core_osm_node_ids: list[str]
    core_way_ids: list[str]
    core_radius_m: float
    topology_type: Literal[
        "T3",
        "X4",
        "offset_X4",
        "dual_carriageway_X4",
        "slip_lane_X4",
        "roundabout",
        "complex",
        "unknown",
    ]
    internal_fragment_count: int
    short_internal_edge_count: int
    confidence: float


class Approach(BaseModel):
    approach_id: str
    role: str
    source_way_ids: list[str]
    corridor_extension_way_ids: list[str] = Field(default_factory=list)
    road_name: str | None = None
    highway_class: str
    bearing_to_core: float
    bearing_from_core: float
    endpoint_xy: tuple[float, float] | None = None
    source_shape_xy: list[tuple[float, float]] = Field(default_factory=list)
    incoming_lane_count: int
    outgoing_lane_count: int
    incoming_extra_lane_modes: list[set[str]] = Field(default_factory=list)
    outgoing_extra_lane_modes: list[set[str]] = Field(default_factory=list)
    incoming_edge_ids: list[str]
    outgoing_edge_ids: list[str]
    oneway: bool
    has_incoming_vehicle_flow: bool = True
    has_outgoing_vehicle_flow: bool = True
    direction_evidence: list[str] = Field(default_factory=list)
    allowed_modes: set[str]
    mode_layer: Literal["vehicle", "support", "fused_support_lane"] = "vehicle"
    is_vehicle_approach: bool = True
    is_support_only: bool = False
    fused_support_modes: list[set[str]] = Field(default_factory=list)
    turn_lanes_raw: str | None = None
    access_tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_mode_layer_fields(self) -> Approach:
        classification = classify_approach_mode_layer(
            self.allowed_modes,
            self.incoming_extra_lane_modes,
            self.outgoing_extra_lane_modes,
        )
        self.mode_layer = classification.mode_layer
        self.is_vehicle_approach = classification.is_vehicle_approach
        self.is_support_only = classification.is_support_only
        self.fused_support_modes = [set(modes) for modes in classification.fused_support_modes]
        return self


class RoadPairAngle(BaseModel):
    road_a_bearing_deg: float
    road_b_bearing_deg: float
    signed_delta_deg: float
    abs_delta_deg: float
    relation_class: Literal[
        "same_direction",
        "opposite_direction",
        "acute_merge",
        "right_angle",
        "obtuse_merge",
        "unknown",
    ]
    turn_angle_from_a_to_b_deg: float | None = None


class RoadPairDistance(BaseModel):
    endpoint_gap_m: float | None = None
    min_geometry_distance_m: float
    projected_intersection_xy: tuple[float, float] | None = None
    overlap_length_m: float
    overlap_ratio_a: float
    overlap_ratio_b: float
    crossing_point_inside_segments: bool
    nearest_point_a_xy: tuple[float, float] | None = None
    nearest_point_b_xy: tuple[float, float] | None = None


class RoadPairRelation(BaseModel):
    relation_id: str
    road_a_id: str
    road_b_id: str
    road_a_source_way_ids: list[str]
    road_b_source_way_ids: list[str]
    geometry_relation: Literal[
        "shared_node",
        "near_miss",
        "crossing_without_node",
        "overlap",
        "parallel",
        "diverging",
        "merging",
        "disjoint",
        "unknown",
    ]
    topology_relation: Literal[
        "connected",
        "disconnected",
        "incorrectly_connected",
        "duplicated",
        "ambiguous",
    ]
    expected_relation: Literal["should_connect", "should_not_connect", "maybe_connect", "unknown"]
    angle: RoadPairAngle
    distance: RoadPairDistance
    inferred_turn: Literal[
        "straight",
        "left",
        "right",
        "uturn",
        "crossing_conflict",
        "parallel_same_direction",
        "parallel_opposite_direction",
        "unknown",
    ]
    error_type: Literal[
        "none",
        "missing_connection",
        "wrong_connection",
        "topology_overlap",
        "geometry_near_miss",
        "duplicate_parallel_edge",
        "unjoined_physical_intersection",
        "false_internal_connector",
        "ambiguous",
    ]
    suggested_fix: Literal[
        "none",
        "join_nodes",
        "split_edge_at_crossing",
        "add_connection",
        "remove_connection",
        "merge_duplicate_edges",
        "preserve_separate_levels",
        "manual_review",
    ]
    severity: Literal["none", "diagnostic", "blocking", "manual_review"] = "none"
    confidence: float
    evidence: list[str]


class RoadPairRelationGraph(BaseModel):
    relations: list[RoadPairRelation]
    missing_connection_count: int
    wrong_connection_count: int
    overlap_conflict_count: int
    near_miss_count: int
    duplicate_parallel_count: int
    blocking_error_count: int


class Movement(BaseModel):
    movement_id: str
    from_approach_id: str
    to_approach_id: str
    road_pair_relation_id: str
    turn: Literal["right", "straight", "left", "uturn"]
    allowed: bool
    from_lane_indices: list[int]
    to_lane_indices: list[int]
    allowed_modes: set[str]
    evidence: list[str]
    confidence: float
    notes: list[str] = Field(default_factory=list)


class MovementMatrix(BaseModel):
    movements: list[Movement]
    legal_movement_count: int
    forbidden_movement_count: int
    inferred_movement_count: int
    restriction_blocked_count: int = 0
    restriction_warnings: list[str] = Field(default_factory=list)


class TLSPhase(BaseModel):
    phase_id: str
    duration: float
    state: str
    movement_ids: list[str]


class ControlModel(BaseModel):
    control_type: Literal[
        "traffic_light",
        "priority",
        "right_before_left",
        "allway_stop",
        "uncontrolled",
        "unknown",
    ]
    source: list[str]
    priority_approach_ids: list[str]
    tls_id: str | None = None
    phases: list[TLSPhase] = Field(default_factory=list)
    link_index_map: dict[str, int] = Field(default_factory=dict)
    confidence: float


class CompiledSUMOArtifacts(BaseModel):
    plain_node_file: str
    plain_edge_file: str
    plain_connection_file: str
    plain_type_file: str | None = None
    plain_tllogic_file: str | None = None
    net_file: str
    sumocfg_file: str | None = None
    netconvert_warnings: list[str] = Field(default_factory=list)
    custom_tllogic_applied: bool | None = None


class ValidationWarningRecord(BaseModel):
    message: str
    severity: Literal["diagnostic", "blocking", "manual_review"]
    source: Literal["netconvert", "sumo", "torii"]


class IntersectionValidation(BaseModel):
    status: Literal["pass", "blocked", "fail"]
    sumo_load_status: Literal["pass", "fail"]
    route_probe_status: Literal["pass", "fail", "skipped"]
    approach_count: int
    movement_count: int
    missing_movement_count: int
    forbidden_movement_count: int
    internal_fragment_count: int
    duplicate_junction_count: int
    disconnected_edge_count: int
    tls_linkindex_status: Literal["pass", "fail", "skipped"] = "skipped"
    approach_mode_counts: dict[str, int] = Field(default_factory=dict)
    vehicle_approach_count: int = 0
    vehicle_topology_type: str = "unknown"
    legal_movement_mode_counts: dict[str, int] = Field(default_factory=dict)
    forbidden_cross_mode_movement_count: int = 0
    warning_records: list[ValidationWarningRecord] = Field(default_factory=list)
    warning_count_by_severity: dict[str, int] = Field(default_factory=dict)
    blocking_error_count: int = 0
    warnings: list[str]


class IntersectionIR(BaseModel):
    schema_version: str = "intersection-ir/v1"
    intersection_id: str
    osm_patch: OSMPatch
    core: IntersectionCore
    approaches: list[Approach]
    road_pair_graph: RoadPairRelationGraph
    movement_matrix: MovementMatrix
    control: ControlModel
    compiled: CompiledSUMOArtifacts | None = None
    validation: IntersectionValidation | None = None
    claim_status: Literal[
        "raw-osm-parsed",
        "core-detected",
        "spatial-relations-built",
        "semantic-model-built",
        "sumo-plain-compiled",
        "sumo-net-compiled",
        "intersection-cleaned",
        "blocked",
        "failed",
    ]
