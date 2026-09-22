"""Stable contract wrappers for the reduced default MCP surface.

These wrappers deliberately expose fewer knobs than the legacy tools.  They
keep external URLs, executable paths, and most output locations under Torii's
control and return one structured result shape.  They call the same business
functions as the legacy MCP tools and the ``torii`` CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .tools.demand_tools import sumo_detector_count_audit
from .tools.environment_tools import sumo_preflight
from .tools.evidence_tools import sumo_compare_outputs, sumo_config_pair_preflight
from .tools.intersection_tools import sumo_intersection_archetype_classify
from .tools.netedit_tools import (
    NeteditAction,
    NeteditObjectType,
    sumo_netedit_session,
)
from .tools.osm_tools import (
    sumo_network_connection_mode_audit,
    sumo_network_connection_mode_regression_audit,
    sumo_network_overlapping_junction_audit,
    sumo_network_review_html,
    sumo_network_topology_audit,
    sumo_osm_resolve_place,
)
from .tools.signal_tools import sumo_signal_device_profile_classify

NetworkAuditProfile = Literal["quick", "standard"]
NetworkCompareProfile = Literal["standard"]
NeteditCloseMode = Literal["finalize", "abort"]
TrafficSide = Literal["left", "right"]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class ToriiArtifact(BaseModel):
    role: str | None = None
    path: str | None = None
    sha256: str | None = None
    media_type: str | None = None


class ToriiToolResult(BaseModel):
    """Common result envelope returned by every default MCP tool."""

    status: str
    summary: str
    claim_status: str | None = None
    findings: list[Any] = Field(default_factory=list)
    artifacts: list[ToriiArtifact] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


def _result(
    raw: dict[str, Any],
    *,
    summary: str,
    status: str | None = None,
    claim_status: str | None = None,
    next_actions: list[str] | None = None,
) -> ToriiToolResult:
    selected_status = status or str(raw.get("status") or "unknown")
    selected_claim = claim_status or raw.get("claim_status")
    return ToriiToolResult(
        status=selected_status,
        summary=summary,
        claim_status=selected_claim,
        findings=raw.get("findings") if isinstance(raw.get("findings"), list) else [],
        artifacts=[
            ToriiArtifact(
                role=item.get("role"),
                path=item.get("path"),
                sha256=item.get("sha256"),
                media_type=item.get("media_type"),
            )
            for item in raw.get("artifacts", [])
            if isinstance(item, dict)
        ],
        next_actions=next_actions or [],
        payload=raw,
    )


def _as_path_list(items: list[str]) -> list[str]:
    return [str(Path(item).expanduser().resolve()) for item in items]


def torii_preflight() -> ToriiToolResult:
    raw = sumo_preflight()
    summary = "SUMO environment preflight completed."
    if raw.get("sumo_home"):
        summary += f" SUMO_HOME={raw['sumo_home']}"
    return _result(
        raw,
        summary=summary,
        next_actions=(
            []
            if raw.get("status") == "pass"
            else ["Install or repair the missing SUMO/Python components shown in payload."]
        ),
    )


def torii_config_inspect(
    baseline_config: Annotated[
        str,
        Field(description="Path to the baseline .sumocfg file; Torii does not modify it."),
    ],
    variant_config: Annotated[
        str,
        Field(description="Path to the variant .sumocfg file; Torii does not modify it."),
    ],
) -> ToriiToolResult:
    raw = sumo_config_pair_preflight(
        baseline_config=baseline_config,
        variant_config=variant_config,
    )
    return _result(
        raw,
        summary="Configuration pair preflight completed.",
        next_actions=(
            ["Fix the missing inputs or shared outputs reported in payload before running a comparison."]
            if raw.get("status") not in {"pass", "ok"}
            else ["Run torii run compare against the inspected configuration pair."]
        ),
    )


def torii_run_compare(
    baseline_summary: Annotated[
        str | None,
        Field(description="Optional baseline summary.xml path."),
    ] = None,
    baseline_tripinfo: Annotated[
        str | None,
        Field(description="Optional baseline tripinfo.xml path."),
    ] = None,
    variant_summary: Annotated[
        str | None,
        Field(description="Optional variant summary.xml path."),
    ] = None,
    variant_tripinfo: Annotated[
        str | None,
        Field(description="Optional variant tripinfo.xml path."),
    ] = None,
) -> ToriiToolResult:
    raw = sumo_compare_outputs(
        baseline_summary=baseline_summary,
        baseline_tripinfo=baseline_tripinfo,
        variant_summary=variant_summary,
        variant_tripinfo=variant_tripinfo,
    )
    return _result(
        raw,
        summary="Baseline and variant outputs were compared.",
        next_actions=["Review comparison gates before promoting any candidate."],
    )


def torii_place_resolve(
    place_name: Annotated[str, Field(description="Place name to resolve through OSM Nominatim.")],
    limit: Annotated[int, Field(ge=1, description="Maximum candidate areas to return.")] = 1,
) -> ToriiToolResult:
    raw = sumo_osm_resolve_place(
        place_name=place_name,
        limit=limit,
        nominatim_url="https://nominatim.openstreetmap.org/search",
        timeout_seconds=30.0,
    )
    return _result(
        raw,
        summary=f"Resolved OSM place {place_name!r}.",
        next_actions=["Confirm the resolved area before starting network construction."],
    )


def torii_intersection_classify(
    osm_file: Annotated[str, Field(description="Path to the immutable local OSM XML input.")],
    seed_osm_node_id: Annotated[str, Field(description="OSM node ID at the intersection center.")],
    traffic_side: Annotated[
        TrafficSide,
        Field(description="Traffic side used to interpret lane order."),
    ] = "right",
    road_network_evidence_file: Annotated[
        str | None,
        Field(description="Optional path to a reviewed road-network evidence JSON file."),
    ] = None,
) -> ToriiToolResult:
    raw = sumo_intersection_archetype_classify(
        osm_file=osm_file,
        seed_osm_node_id=seed_osm_node_id,
        traffic_side=traffic_side,
        road_network_evidence_file=road_network_evidence_file,
    )
    return _result(
        raw,
        summary=f"Classified OSM intersection at node {seed_osm_node_id!r}.",
        next_actions=["Use the returned archetype evidence to choose a bounded intersection workflow."],
    )


def torii_signal_classify(
    ocit_file: Annotated[str, Field(description="Path to the immutable OCIT-C supply file.")],
    expected_node_id: Annotated[
        str | None,
        Field(description="Optional controller node ID that the supply must contain."),
    ] = None,
) -> ToriiToolResult:
    raw = sumo_signal_device_profile_classify(
        ocit_file=ocit_file,
        expected_node_id=expected_node_id,
    )
    return _result(
        raw,
        summary="Classified the OCIT-C signal device inventory.",
        next_actions=["Keep automatic lane, movement, controller, and phase binding blocked until reviewed."],
    )


def torii_network_audit(
    net_file: Annotated[str, Field(description="Path to the local SUMO .net.xml input.")],
    output_dir: Annotated[
        str,
        Field(description="Directory where audit reports are created or replaced."),
    ],
    profile: Annotated[
        NetworkAuditProfile,
        Field(
            description=(
                "quick runs topology only; standard runs topology, Connection Mode, "
                "and overlap checks. Neither runs SUMO routeability."
            )
        ),
    ] = "standard",
    prefix: Annotated[str, Field(description="Filename prefix for generated audit reports.")] = (
        "network_audit"
    ),
) -> ToriiToolResult:
    if profile not in {"quick", "standard"}:
        raise ValueError("network audit profile must be quick or standard")
    topology = sumo_network_topology_audit(
        net_file=net_file,
        output_dir=output_dir,
        prefix=f"{prefix}_topology",
    )
    if profile == "quick":
        raw = topology
        summary = "Quick network topology audit completed."
    else:
        connection = sumo_network_connection_mode_audit(
            net_file=net_file,
            output_dir=output_dir,
            prefix=f"{prefix}_connection",
        )
        overlap = sumo_network_overlapping_junction_audit(
            net_file=net_file,
            output_dir=output_dir,
            prefix=f"{prefix}_overlap",
        )
        existing_overlap_artifacts = overlap.get("artifacts")
        overlap_artifacts = (
            list(existing_overlap_artifacts)
            if isinstance(existing_overlap_artifacts, list)
            else []
        )
        for field, role, media_type in (
            ("groups_file", "overlapping-junction-groups", "text/csv"),
            ("summary_file", "overlapping-junction-audit", "application/json"),
        ):
            if isinstance(overlap.get(field), str):
                overlap_artifacts.append(
                    {
                        "role": role,
                        "path": overlap[field],
                        "media_type": media_type,
                    }
                )
        overlap_groups = overlap.get("overlapping_junction_groups")
        if not isinstance(overlap_groups, list):
            overlap_groups = []
        overlap = {**overlap, "artifacts": overlap_artifacts}
        if overlap.get("status") == "pass" and overlap_groups:
            overlap["status"] = "review_required"
            overlap["findings"] = overlap_groups
        reports = (topology, connection, overlap)
        statuses = [str(report.get("status") or "unknown") for report in reports]
        if "fail" in statuses:
            status = "fail"
        elif "blocked" in statuses:
            status = "blocked"
        elif all(item == "pass" for item in statuses):
            status = "pass"
        else:
            status = "review_required"
        claim_report = next(
            (report for report in reports if report.get("status") == status),
            next((report for report in reports if report.get("status") != "pass"), topology),
        )
        raw = {
            "status": status,
            "claim_status": claim_report.get("claim_status"),
            "findings": [
                finding
                for report in reports
                for finding in (report.get("findings") if isinstance(report.get("findings"), list) else [])
            ],
            "artifacts": [
                artifact
                for report in reports
                for artifact in (report.get("artifacts") if isinstance(report.get("artifacts"), list) else [])
            ],
            "topology": topology,
            "connection_mode": connection,
            "overlap": overlap,
        }
        summary = "Standard network audit completed without running SUMO."
    return _result(
        raw,
        summary=summary,
        next_actions=["Review any findings before materializing or promoting a candidate."],
    )


def torii_network_compare(
    source_net_file: Annotated[
        str,
        Field(description="Path to the immutable source SUMO .net.xml baseline."),
    ],
    candidate_net_file: Annotated[
        str,
        Field(description="Path to the candidate SUMO .net.xml to compare against the source."),
    ],
    output_dir: Annotated[
        str,
        Field(description="Directory where comparison reports are created or replaced."),
    ],
    profile: NetworkCompareProfile = "standard",
    prefix: Annotated[
        str,
        Field(description="Filename prefix for generated comparison reports."),
    ] = "network_compare",
) -> ToriiToolResult:
    if profile != "standard":
        raise ValueError("network compare profile must be standard")
    raw = sumo_network_connection_mode_regression_audit(
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
        output_dir=output_dir,
        prefix=prefix,
    )
    return _result(
        raw,
        summary="Source and candidate networks were compared.",
        next_actions=["Review introduced findings before any promotion decision."],
    )


def torii_network_compare_mcp(
    source_net_file: Annotated[
        str,
        Field(description="Path to the immutable source SUMO .net.xml baseline."),
    ],
    candidate_net_file: Annotated[
        str,
        Field(description="Path to the candidate SUMO .net.xml to compare against the source."),
    ],
    output_dir: Annotated[
        str,
        Field(description="Directory where comparison reports are created or replaced."),
    ],
    prefix: Annotated[
        str,
        Field(description="Filename prefix for generated comparison reports."),
    ] = "network_compare",
) -> ToriiToolResult:
    """Expose the reduced MCP schema while accepting old standard-profile Python calls."""

    return torii_network_compare(
        source_net_file,
        candidate_net_file,
        output_dir,
        prefix=prefix,
    )


def torii_demand_audit(
    expected_counts_csv: Annotated[
        str,
        Field(description="Path to expected detector counts in CSV format."),
    ],
    detector_output_xml: Annotated[
        str,
        Field(description="Path to observed SUMO E1 detector output XML."),
    ],
    output_dir: Annotated[
        str,
        Field(description="Directory where demand-audit reports are created or replaced."),
    ],
    prefix: Annotated[str, Field(description="Filename prefix for generated audit reports.")] = (
        "demand_audit"
    ),
) -> ToriiToolResult:
    raw = sumo_detector_count_audit(
        expected_counts_csv=expected_counts_csv,
        detector_output_xml=detector_output_xml,
        output_dir=output_dir,
        prefix=prefix,
    )
    return _result(
        raw,
        summary="Detector count audit completed.",
        next_actions=["Review detector-fit metrics before using demand for promotion evidence."],
    )


def torii_review_create(
    output_dir: Annotated[
        str,
        Field(description="Directory where the review HTML and support files are created."),
    ],
    net_file: Annotated[
        str | None,
        Field(description="Optional SUMO .net.xml to render in the review page."),
    ] = None,
    title: Annotated[str, Field(description="Title shown on the generated review page.")] = (
        "SUMO Network Review"
    ),
    claim_status: Annotated[
        str,
        Field(description="Evidence claim label displayed on the review page."),
    ] = "diagnostic-demo",
    raw_net_file: Annotated[
        str | None,
        Field(description="Optional raw network path linked as source evidence."),
    ] = None,
    connected_core_file: Annotated[
        str | None,
        Field(description="Optional connected-core network path linked for comparison."),
    ] = None,
    tls_review_file: Annotated[
        str | None,
        Field(description="Optional TLS review JSON path included in the page."),
    ] = None,
    topology_audit_report_file: Annotated[
        str | None,
        Field(description="Optional topology-audit JSON path included in the page."),
    ] = None,
    junction_aggregation_report_file: Annotated[
        str | None,
        Field(description="Optional junction-aggregation JSON path included in the page."),
    ] = None,
    routeability_audit_report_file: Annotated[
        str | None,
        Field(description="Optional routeability-audit JSON path included in the page."),
    ] = None,
) -> ToriiToolResult:
    raw = sumo_network_review_html(
        output_dir=output_dir,
        net_file=net_file,
        title=title,
        claim_status=claim_status,
        raw_net_file=raw_net_file,
        connected_core_file=connected_core_file,
        tls_review_file=tls_review_file,
        topology_audit_report_file=topology_audit_report_file,
        junction_aggregation_report_file=junction_aggregation_report_file,
        routeability_audit_report_file=routeability_audit_report_file,
    )
    return _result(
        raw,
        summary="Network review page created without overwriting source files.",
        next_actions=["Open the review page before deciding whether to materialize a candidate."],
    )


def torii_netedit_open(
    source_net_file: Annotated[
        str,
        Field(description="Path to the immutable source SUMO network."),
    ],
    candidate_net_file: Annotated[
        str,
        Field(description="Path to the separate candidate SUMO network NetEdit may modify."),
    ],
    output_dir: Annotated[
        str,
        Field(description="Directory where session screenshots and reports are written."),
    ],
    expected_source_sha256: Annotated[
        Sha256,
        Field(description="Expected SHA-256 of the immutable source network."),
    ],
    gui_settings_file: Annotated[
        str | None,
        Field(description="Optional NetEdit GUI settings XML path."),
    ] = None,
    selection_file: Annotated[
        str | None,
        Field(description="Optional frozen NetEdit selection file used to bind edit scope."),
    ] = None,
    target_source_junction_ids: Annotated[
        list[str] | None,
        Field(description="Source junction IDs that may be replaced by a bounded edit."),
    ] = None,
    target_candidate_junction_ids: Annotated[
        list[str] | None,
        Field(description="Expected candidate junction IDs created by the bounded edit."),
    ] = None,
    window_size: Annotated[
        str,
        Field(description="NetEdit client size as width,height pixels."),
    ] = "1400,1000",
    window_pos: Annotated[
        str,
        Field(description="NetEdit window position as x,y pixels."),
    ] = "20,20",
) -> ToriiToolResult:
    raw = sumo_netedit_session(
        operation="open",
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
        output_dir=output_dir,
        expected_source_sha256=expected_source_sha256,
        gui_settings_file=gui_settings_file,
        selection_file=selection_file,
        target_source_junction_ids=target_source_junction_ids,
        target_candidate_junction_ids=target_candidate_junction_ids,
        window_size=window_size,
        window_pos=window_pos,
    )
    return _netedit_operation_result(
        raw,
        summary="Opened the hash-bound NetEdit review session.",
        next_actions=["Observe the session, then use exactly one act per review step."],
    )


def _netedit_operation_result(raw, *, summary, next_actions):
    passed = raw.get("status") == "pass" and raw.get("operation_status", "pass") == "pass"
    return _result(raw, summary=summary if passed else f"NetEdit {raw.get('operation', 'operation')} was {raw.get('status', 'not completed')}.",
                   next_actions=next_actions if passed else ["Review the payload reason before retrying."])


def torii_netedit_observe(
    session_id: Annotated[str, Field(min_length=1, description="Active NetEdit session ID.")],
    object_type: Annotated[
        NeteditObjectType | None,
        Field(description="Persisted object type to inspect; provide together with object_id."),
    ] = None,
    object_id: Annotated[
        str | None,
        Field(description="Persisted object ID to inspect; provide together with object_type."),
    ] = None,
    label: Annotated[str, Field(description="Short label used in screenshot artifact names.")] = (
        "observe"
    ),
) -> ToriiToolResult:
    raw = sumo_netedit_session(
        operation="observe",
        session_id=session_id,
        object_type=object_type,
        object_id=object_id,
        label=label,
    )
    return _netedit_operation_result(
        raw,
        summary="Observed the active NetEdit session state.",
        next_actions=["Use the returned screenshot SHA as the guard for the next act."],
    )


def torii_netedit_act(
    session_id: Annotated[str, Field(min_length=1, description="Active NetEdit session ID.")],
    action: Annotated[
        NeteditAction,
        Field(description="One allowed NetEdit action. click needs x/y; drag needs all coordinates."),
    ],
    expected_screenshot_sha256: Annotated[
        Sha256,
        Field(description="SHA-256 from the latest observe result."),
    ],
    x: Annotated[int | None, Field(description="Start/click x coordinate in client pixels.")] = None,
    y: Annotated[int | None, Field(description="Start/click y coordinate in client pixels.")] = None,
    to_x: Annotated[int | None, Field(description="Drag destination x coordinate in client pixels.")] = (
        None
    ),
    to_y: Annotated[int | None, Field(description="Drag destination y coordinate in client pixels.")] = (
        None
    ),
) -> ToriiToolResult:
    raw = sumo_netedit_session(
        operation="act",
        session_id=session_id,
        action=action,
        expected_screenshot_sha256=expected_screenshot_sha256,
        x=x,
        y=y,
        to_x=to_x,
        to_y=to_y,
    )
    return _netedit_operation_result(
        raw,
        summary="Executed one whitelisted NetEdit action.",
        next_actions=["Observe again after the action before another act or close."],
    )


def torii_netedit_close(
    session_id: Annotated[str, Field(min_length=1, description="Active NetEdit session ID.")],
    mode: Annotated[
        NeteditCloseMode,
        Field(description="finalize saves and audits; abort closes without saving."),
    ],
    expected_screenshot_sha256: Annotated[
        Sha256 | None,
        Field(
            description=(
                "Latest observe screenshot SHA-256. Required when mode='finalize'; "
                "omit when mode='abort'."
            )
        ),
    ] = None,
    reason: Annotated[
        str,
        Field(description="Abort reason; defaults to caller_aborted."),
    ] = "caller_aborted",
) -> ToriiToolResult:
    kwargs: dict[str, Any] = {"operation": mode, "session_id": session_id}
    if mode == "finalize":
        if not expected_screenshot_sha256:
            raise ValueError("expected_screenshot_sha256 is required when mode='finalize'")
        kwargs["expected_screenshot_sha256"] = expected_screenshot_sha256
    else:
        kwargs["reason"] = reason or "caller_aborted"
    raw = sumo_netedit_session(**kwargs)
    operation_passed = raw.get("status") == "pass" and raw.get("operation_status", "pass") == "pass"
    if operation_passed:
        summary = (
            "Finalized and closed the NetEdit review session."
            if mode == "finalize"
            else "Aborted and closed the NetEdit review session without saving."
        )
        next_actions = (
            ["Review the finalize audits; promotion remains blocked."]
            if mode == "finalize"
            else []
        )
    else:
        summary = (
            f"NetEdit {mode} was {raw.get('status', 'not completed')}; "
            "inspect payload before assuming the session state."
        )
        next_actions = [
            "Review the payload reason and observe again before retrying if the session remains active."
        ]
    return _result(
        raw,
        summary=summary,
        next_actions=next_actions,
    )


__all__ = [
    "ToriiArtifact",
    "ToriiToolResult",
    "torii_config_inspect",
    "torii_demand_audit",
    "torii_intersection_classify",
    "torii_netedit_act",
    "torii_netedit_close",
    "torii_netedit_observe",
    "torii_netedit_open",
    "torii_network_audit",
    "torii_network_compare",
    "torii_place_resolve",
    "torii_preflight",
    "torii_review_create",
    "torii_run_compare",
    "torii_signal_classify",
]
