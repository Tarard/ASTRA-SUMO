from __future__ import annotations

from pathlib import Path
from typing import Any

from torii_sumo.core.detector_demand import (
    active_detectors,
    aggregate_edge_counts,
    boundary_edges,
    build_boundary_routes,
    build_detector_anchored_routes,
    audit_expected_to_e1_strict,
    constraint_rows,
    merge_routes,
    read_csv_rows,
    read_detector_mapping,
    read_net,
    route_detector_incidence,
    route_rows,
    safe_id,
    source_sink_rows,
    summarize_comparison,
    write_csv,
    write_edge_data,
)


def sumo_detector_route_support(
    net_file: str,
    detector_mapping_csv: str,
    output_dir: str,
    prefix: str = "detector_demand",
    max_routes: int = 500,
    max_hops: int = 80,
    min_edges: int = 2,
) -> dict[str, Any]:
    try:
        net_path = _existing_file(net_file, "net_file")
        mapping_path = _existing_file(detector_mapping_csv, "detector_mapping_csv")
        out_dir = _output_dir(output_dir)
        clean_prefix = safe_id(prefix)

        edges, connections = read_net(net_path)
        sources, sinks = boundary_edges(edges, connections)
        detectors = active_detectors(read_detector_mapping(mapping_path))
        detector_routes = build_detector_anchored_routes(
            detectors,
            sources=sources,
            sinks=sinks,
            connections=connections,
            max_hops=max_hops,
        )
        boundary_routes = build_boundary_routes(
            sources=sources,
            sinks=sinks,
            connections=connections,
            max_routes=max_routes,
            max_hops=max_hops,
            min_edges=min_edges,
        )
        routes = merge_routes(detector_routes, boundary_routes, max_routes=max_routes)
        incidence_rows = route_detector_incidence(routes, detectors)
        covered_detector_ids = {str(row["detector_id"]) for row in incidence_rows if int(row["incidence"]) == 1}

        source_sink_file = out_dir / f"{clean_prefix}_source_sink_manifest.csv"
        route_candidate_file = out_dir / f"{clean_prefix}_route_candidate_manifest.csv"
        incidence_file = out_dir / f"{clean_prefix}_route_detector_incidence.csv"

        write_csv(
            source_sink_file,
            source_sink_rows(edges, sources, sinks),
            ["role", "edge_id", "from_node", "to_node", "length", "reason"],
        )
        write_csv(
            route_candidate_file,
            route_rows(routes, edges),
            ["route_id", "source_edge", "sink_edge", "edge_count", "route_length", "edges"],
        )
        write_csv(
            incidence_file,
            incidence_rows,
            [
                "route_id",
                "source_edge",
                "sink_edge",
                "detector_id",
                "detector_edge",
                "detector_direction",
                "incidence",
            ],
        )

        warnings: list[str] = []
        if not detectors:
            status = "fail"
            warnings.append("no active detectors in detector mapping")
        elif routes and len(covered_detector_ids) == len(detectors):
            status = "pass"
        else:
            status = "fail"
            warnings.append("one or more detectors lack route support")
        return {
            "status": status,
            "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
            "source_count": len(sources),
            "sink_count": len(sinks),
            "route_count": len(routes),
            "active_detector_count": len(detectors),
            "covered_detector_count": len(covered_detector_ids),
            "source_sink_manifest": str(source_sink_file),
            "route_candidate_manifest": str(route_candidate_file),
            "route_detector_incidence": str(incidence_file),
            "warnings": warnings,
        }
    except (OSError, ValueError) as exc:
        return _construction_invalid(str(exc))


def sumo_detector_count_constraints(
    expected_counts_csv: str,
    output_dir: str,
    prefix: str = "detector_demand",
    begin: float = 0.0,
    end: float = 3600.0,
) -> dict[str, Any]:
    try:
        expected_path = _existing_file(expected_counts_csv, "expected_counts_csv")
        out_dir = _output_dir(output_dir)
        clean_prefix = safe_id(prefix)
        edge_counts = aggregate_edge_counts(read_csv_rows(expected_path), begin=float(begin), end=float(end))

        constraints_file = out_dir / f"{clean_prefix}_time_bin_count_constraints.csv"
        edge_data_file = out_dir / f"{clean_prefix}_edge_counts.xml"
        write_csv(
            constraints_file,
            constraint_rows(edge_counts),
            ["begin", "end", "edge_id", "lane_ids", "detector_ids", "expected_total"],
        )
        write_edge_data(edge_data_file, edge_counts)

        status = "pass" if edge_counts else "fail"
        return {
            "status": status,
            "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
            "edge_count": len(edge_counts),
            "expected_total": sum(edge_count.entered for edge_count in edge_counts),
            "time_bin_count_constraints": str(constraints_file),
            "edge_data_file": str(edge_data_file),
            "warnings": [] if status == "pass" else ["no expected count rows matched the requested time window"],
        }
    except (OSError, ValueError) as exc:
        return _construction_invalid(str(exc))


def sumo_detector_count_audit(
    expected_counts_csv: str,
    detector_output_xml: str,
    output_dir: str,
    prefix: str = "detector_demand",
) -> dict[str, Any]:
    try:
        expected_path = _existing_file(expected_counts_csv, "expected_counts_csv")
        detector_path = _existing_file(detector_output_xml, "detector_output_xml")
        out_dir = _output_dir(output_dir)
        clean_prefix = safe_id(prefix)

        comparison_rows = audit_expected_to_e1_strict(read_csv_rows(expected_path), detector_path, count_attribute="nVehEntered")
        for row in comparison_rows:
            row["diff_entered_minus_expected"] = row.pop("diff_nVehEntered_minus_expected")
        comparison_file = out_dir / f"{clean_prefix}_detector_comparison.csv"
        if any(comparison_file.resolve() == source.resolve() or comparison_file.exists() and comparison_file.samefile(source)
               for source in (expected_path, detector_path)):
            raise ValueError("The comparison output must not replace a source input.")
        write_csv(
            comparison_file,
            comparison_rows,
            [
                "detector_id",
                "edge_id",
                "begin",
                "end",
                "expected_total",
                "measured_nVehEntered",
                "diff_entered_minus_expected",
                "measurement_attribute",
                "measurement_status",
            ],
        )
        matched = [row for row in comparison_rows if row["measurement_status"] == "matched"]
        missing = len(comparison_rows)-len(matched)
        summary = summarize_comparison(matched)
        status = "fail" if not comparison_rows else "review_required" if missing else "pass"
        return {
            "status": status,
            "claim_status": "diagnostic-demo" if comparison_rows else "construction-invalid",
            **summary,
            "measurement_attribute": "nVehEntered",
            "expected_rows": len(comparison_rows), "matched_rows": len(matched), "missing_measurement_rows": missing,
            "requested_expected_total": sum(row["expected_total"] for row in comparison_rows),
            "metric_scope": "matched_detector_intervals_only",
            "comparison_file": str(comparison_file),
            "warnings": [f"{missing} expected detector intervals have no measurement"] if missing else [] if comparison_rows else ["no comparable detector rows found"],
        }
    except (OSError, ValueError) as exc:
        return _construction_invalid(str(exc))


def _existing_file(value: str, field_name: str) -> Path:
    path = Path(value)
    if not value or not path.is_file():
        raise ValueError(f"{field_name} must point to an existing file: {value}")
    return path


def _output_dir(value: str) -> Path:
    if not value.strip():
        raise ValueError("output_dir is required")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _construction_invalid(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": error,
    }
