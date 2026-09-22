from __future__ import annotations

from enum import Enum


class AutomationAction(str, Enum):
    AUTO_REPAIR = "auto-repair"
    SUGGEST = "suggest"
    REVIEW = "review"
    BLOCK = "block"


class GateStatus(str, Enum):
    NOT_RUN = "not-run"
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"
    BLOCKED = "blocked"


class TrafficSide(str, Enum):
    RIGHT = "right"
    LEFT = "left"
    UNKNOWN = "unknown"


class ScopeMembership(str, Enum):
    TARGET = "target"
    GUARD = "guard"
    BOUNDARY = "boundary"
    OUTSIDE = "outside"


class FindingSeverity(str, Enum):
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"
    STRUCTURAL = "structural"
    SAFETY = "safety"


class HypothesisType(str, Enum):
    SPLIT_SHARED_CONTROLLER = "split-shared-controller"
    MERGE_PHYSICAL_CELL = "merge-physical-cell"
    PARTIAL_INTERNAL_REPAIR = "partial-internal-repair"


class CandidateStatus(str, Enum):
    PLANNED = "planned"
    MATERIALIZED = "materialized"
    STRUCTURALLY_VERIFIED = "structurally-verified"
    SAFETY_VERIFIED = "safety-verified"
    DIFFERENTIALLY_VERIFIED = "differentially-verified"
    RUNTIME_VERIFIED = "runtime-verified"
    REVIEW_PENDING = "review-pending"
    AUTO_CERTIFIED = "auto-certified"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DeltaAction(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class ReviewDecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    INVALIDATED = "invalidated"


class WorkflowStage(str, Enum):
    INGESTED = "INGESTED"
    CANONICALIZED = "CANONICALIZED"
    FINDINGS_READY = "FINDINGS_READY"
    HYPOTHESES_READY = "HYPOTHESES_READY"
    CANDIDATE_PLANNED = "CANDIDATE_PLANNED"
    MATERIALIZED = "MATERIALIZED"
    STRUCTURALLY_VERIFIED = "STRUCTURALLY_VERIFIED"
    SAFETY_VERIFIED = "SAFETY_VERIFIED"
    DIFFERENTIALLY_VERIFIED = "DIFFERENTIALLY_VERIFIED"
    RUNTIME_VERIFIED = "RUNTIME_VERIFIED"
    REVIEW_PENDING = "REVIEW_PENDING"
    AUTO_CERTIFIED = "AUTO_CERTIFIED"
    BLOCKED = "BLOCKED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class QualityDimensionName(str, Enum):
    TOPOLOGY = "topology"
    GEOMETRY = "geometry"
    LANE = "lane"
    MOVEMENT = "movement"
    MODE = "mode"
    RIGHT_OF_WAY = "rightOfWay"
    TLS = "TLS"
    SAFETY = "safety"
    PROVENANCE = "provenance"
    REVIEWABILITY = "reviewability"


class ArtifactRole(str, Enum):
    OSM_SOURCE = "osm-source"
    SOURCE_NET = "source-net"
    TEACHER_NET = "teacher-net"
    CANDIDATE_NET = "candidate-net"
    PLAN = "plan"
    REPORT = "report"
    REVIEW_JSON = "review-json"
    REVIEW_HTML = "review-html"
    DISPLAY_OVERLAY = "display-only-additional"
    MANIFEST = "manifest"
    ROLLBACK = "rollback"
    TOOLCHAIN_LOCK = "toolchain-lock"
    BENCHMARK_SPEC = "benchmark-spec"


class EvidenceSourceType(str, Enum):
    OSM = "osm"
    TEACHER = "teacher"
    SUMO_TOOL = "sumo-tool"
    HUMAN_MAP_OBSERVATION = "human-map-observation"
    BENCHMARK_GOLD = "benchmark-gold"
    RUNTIME = "runtime"


class EvidenceReliability(str, Enum):
    PRIMARY = "primary"
    CORROBORATED = "corroborated"
    SECONDARY = "secondary"
    UNKNOWN = "unknown"
