"""Fetch initialization counts for the fixed README Hamburg comparison window."""

import argparse
from datetime import date, datetime, timezone
import gzip
import json

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows
from astra_sumo.core.digital_twin import WindowSelection, write_canonical_counts
from astra_sumo.core.digital_twin_timeline import (
    aggregate_simulation_counts, build_simulation_window, select_comparison_count_rows,
)
from astra_sumo.core.hamburg_official import (
    SensorThingsClient, fetch_hamburg_count_observations, parse_hamburg_count_streams,
)
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_count_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    source, out = args.source_count_dir.resolve(strict=True), args.output_dir.resolve()
    if out.exists():
        raise ValueError("Use a new source-data directory.")
    meta = json.loads((source / "fields.raw.json").read_bytes())
    values = [v for pages in meta["pages_by_node"].values() for page in pages for v in page["value"]]
    streams = parse_hamburg_count_streams(values)
    if len(streams) != 13 or {s.node_id for s in streams} != {"0119", "0535"}:
        raise ValueError("Use the original thirteen README count fields.")
    started = datetime.now(timezone.utc).isoformat()
    observations, raw = fetch_hamburg_count_observations(
        SensorThingsClient("https://iot.hamburg.de/v1.1/"), streams, local_date=date(2026, 10, 19))
    raw.update(retrieval_begin_utc=started, retrieval_end_utc=datetime.now(timezone.utc).isoformat())
    out.mkdir(parents=True)
    (out / "monday-observations.json.gz").write_bytes(
        gzip.compress(json.dumps(raw, ensure_ascii=False).encode("utf-8"), mtime=0))
    selection = WindowSelection(local_date=date(2026, 10, 19), timezone_name="Europe/Berlin",
        begin_utc=datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc),
        end_utc=datetime(2026, 10, 19, 8, tzinfo=timezone.utc), duration_seconds=9000,
        source_bin_seconds=300, score=6687, complete=True, completeness_ratio=1,
        expected_cells=390, present_cells=390, missing_cells=(), scoring_method="fixed user window")
    window = build_simulation_window(selection, warmup_seconds=3600, output_bin_seconds=900)
    rows = aggregate_simulation_counts(streams, observations, window, require_complete=True)
    warm = [r for r in rows if r.end <= 3600]
    formal = select_comparison_count_rows(rows, window)
    write_canonical_counts(out / "all-counts-15min.csv", rows)
    write_canonical_counts(out / "initialization-counts-15min.csv", warm)
    old = read_csv_rows(source / "field-counts-15min.csv")
    lookup = {(r["detector_id"], r["source_begin_utc"], r["source_end_utc"]): int(r["expected_total"]) for r in old}
    changes = []
    for r in formal:
        key = (r.detector_id, r.source_begin_utc.isoformat().replace("+00:00", "Z"),
               r.source_end_utc.isoformat().replace("+00:00", "Z"))
        if lookup[key] != r.count:
            changes.append({"detector_id": r.detector_id, "begin": r.begin, "old": lookup[key], "new": r.count})
    assert len(warm) == 52 and len(formal) == len(lookup) == 130
    report = {"status": "complete_source_data", "publisher": "Freie und Hansestadt Hamburg",
        "source_service": "https://iot.hamburg.de/v1.1/", "initialization_begin_utc": window.simulation_begin_utc.isoformat(),
        "initialization_end_utc": selection.begin_utc.isoformat(), "initialization_field_bins": len(warm),
        "initialization_count_sum": sum(r.count for r in warm), "all_extended_field_bins": len(rows),
        "all_bins_complete": all(r.quality_status == "complete" for r in rows),
        "unchanged_comparison_begin_utc": selection.begin_utc.isoformat(),
        "unchanged_comparison_end_utc": selection.end_utc.isoformat(), "comparison_rows_checked": len(formal),
        "comparison_value_changes": changes, "original_target_files_modified": False,
        "role": "Source data for initialization only. No new SUMO result or independent station evidence."}
    write_json_atomic(out / "report.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
