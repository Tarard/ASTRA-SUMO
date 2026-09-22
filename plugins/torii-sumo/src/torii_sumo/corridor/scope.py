from __future__ import annotations

import math

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import TrafficSide
from .ids import require_stable_id


class BoundaryPort(ContractModel):
    boundary_port_id: StableToken
    center_xy: tuple[float, float]
    tangent_xy: tuple[float, float]
    normal_xy: tuple[float, float]
    lane_role_ids: tuple[StableToken, ...]
    lane_widths_m: tuple[float, ...]
    mode_permissions: dict[str, frozenset[str]]
    source_anchor_refs: tuple[str, ...]
    source_geometry_sha256: Sha256
    traffic_side: TrafficSide
    sidewalk: bool = False
    bicycle: bool = False
    rail: bool = False
    layer: int = 0
    bridge: bool = False
    tunnel: bool = False

    @model_validator(mode="after")
    def validate_port(self) -> BoundaryPort:
        require_stable_id(self.boundary_port_id, kind="port")
        if not self.lane_role_ids:
            raise ValueError("Boundary ports require at least one lane role.")
        if len(self.lane_role_ids) != len(self.lane_widths_m):
            raise ValueError("lane_role_ids and lane_widths_m must have equal length.")
        if len(set(self.lane_role_ids)) != len(self.lane_role_ids):
            raise ValueError("Boundary port lane roles must be unique and ordered.")
        for lane_role_id in self.lane_role_ids:
            require_stable_id(lane_role_id, kind="lane_role")
        if any(width <= 0 or not math.isfinite(width) for width in self.lane_widths_m):
            raise ValueError("Boundary port lane widths must be finite and positive.")
        if set(self.mode_permissions) != set(self.lane_role_ids):
            raise ValueError("mode_permissions must cover each lane role exactly.")
        _validate_unit_vector(self.tangent_xy, "tangent_xy")
        _validate_unit_vector(self.normal_xy, "normal_xy")
        dot = sum(a * b for a, b in zip(self.tangent_xy, self.normal_xy, strict=True))
        if abs(dot) > 1e-3:
            raise ValueError("Boundary port tangent and normal must be orthogonal.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("A frozen boundary port requires an explicit traffic side.")
        return self


class ScopeSpec(ContractModel):
    scope_id: StableToken
    physical_cell_ids: frozenset[StableToken]
    target_entity_ids: frozenset[StableToken]
    guard_entity_ids: frozenset[StableToken] = Field(default_factory=frozenset)
    closure_rules: tuple[str, ...]
    boundary_ports: tuple[BoundaryPort, ...]
    traffic_side: TrafficSide

    @model_validator(mode="after")
    def validate_scope(self) -> ScopeSpec:
        require_stable_id(self.scope_id, kind="scope")
        if not self.target_entity_ids:
            raise ValueError("ScopeSpec requires at least one target entity.")
        overlap = self.target_entity_ids & self.guard_entity_ids
        if overlap:
            raise ValueError(f"Target and guard entities must be disjoint: {sorted(overlap)}")
        for cell_id in self.physical_cell_ids:
            require_stable_id(cell_id, kind="cell")
        for entity_id in self.target_entity_ids | self.guard_entity_ids:
            require_stable_id(entity_id)
        port_ids = [port.boundary_port_id for port in self.boundary_ports]
        if len(port_ids) != len(set(port_ids)):
            raise ValueError("Boundary ports must be unique inside a scope.")
        if not self.closure_rules:
            raise ValueError("ScopeSpec requires explicit closure rules.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Automatic scope construction requires explicit traffic side.")
        return self


def _validate_unit_vector(vector: tuple[float, float], name: str) -> None:
    if not all(math.isfinite(value) for value in vector):
        raise ValueError(f"{name} must be finite.")
    norm = math.hypot(*vector)
    if abs(norm - 1.0) > 1e-3:
        raise ValueError(f"{name} must be a unit vector.")
