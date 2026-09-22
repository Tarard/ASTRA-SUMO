from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import TrafficSide
from .ids import stable_id


class BenchmarkDimension(ContractModel):
    name: str
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_dimension(self) -> BenchmarkDimension:
        if not self.values or len(set(self.values)) != len(self.values):
            raise ValueError("Benchmark dimensions require unique values.")
        return self


class BenchmarkFamily(ContractModel):
    family_id: str
    layer: Literal["synthetic", "sumo-official", "real-osm"]
    status: Literal["planned", "fixture-ready", "gold-ready"]
    required_tags: tuple[str, ...]
    traffic_sides: tuple[TrafficSide, ...]
    required_faults: tuple[str, ...] = ()
    expected_claim_boundary: str


class BenchmarkSpecV1(ContractModel):
    schema_id: str = "torii.corridor.benchmark/v1"
    benchmark_id: StableToken
    release: Literal["v1"] = "v1"
    frozen: Literal[True] = True
    research_plan_path: str
    dimensions: tuple[BenchmarkDimension, ...]
    families: tuple[BenchmarkFamily, ...]
    split_policy: str
    threshold_policy: str

    def identity_payload(self) -> dict[str, object]:
        return {
            "release": self.release,
            "frozen": self.frozen,
            "research_plan_path": self.research_plan_path,
            "dimensions": [dimension.model_dump(mode="json") for dimension in self.dimensions],
            "families": [family.model_dump(mode="json") for family in self.families],
            "split_policy": self.split_policy,
            "threshold_policy": self.threshold_policy,
        }

    @model_validator(mode="after")
    def validate_spec(self) -> BenchmarkSpecV1:
        expected = stable_id("manifest", self.identity_payload())
        if self.benchmark_id != expected:
            raise ValueError("benchmark_id does not match the frozen benchmark payload.")
        names = [dimension.name for dimension in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("Benchmark dimension names must be unique.")
        layers = {family.layer for family in self.families}
        if layers != {"synthetic", "sumo-official", "real-osm"}:
            raise ValueError("Benchmark v1 must contain all three benchmark layers.")
        if not any(TrafficSide.LEFT in family.traffic_sides for family in self.families):
            raise ValueError("Benchmark v1 must include left-hand traffic.")
        return self


class BenchmarkLock(ContractModel):
    benchmark_path: str
    benchmark_sha256: Sha256
    toolchain_lock_path: str
    toolchain_lock_sha256: Sha256
    research_plan_path: str
    research_plan_sha256: Sha256
