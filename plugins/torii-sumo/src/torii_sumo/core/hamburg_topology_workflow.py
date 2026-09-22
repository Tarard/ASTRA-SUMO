"""Rebuild a Hamburg road network from frozen source data, without count fitting."""

from __future__ import annotations

import gzip
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_aerial_corridor_candidate import build_hamburg_aerial_combined_candidate
from .hamburg_road_geometry import build_hamburg_road_geometry_candidate
from .hamburg_context_junctions import construction_coverage, rebuild_context_intersections
from .hamburg_aerial_movement import build_hamburg_aerial_corridor_plan
from .hamburg_aerial_road_edges import build_hamburg_road_edge_evidence
from .hamburg_corridor_candidate import bind_hamburg_corridor_tls_clusters
from .hamburg_topology_audit import audit_hamburg_topology_candidate
from .hamburg_topology_route_checks import run_hamburg_topology_route_checks
from .movement_routeability import run_candidate_movement_probes
from .osm_network import audit_tls, build_osm_network, parse_bbox
from .road_scope import resolve_highway_classes
from .network_source_policy import resolve_network_source_policy


REQUEST_SCHEMA = "torii.hamburg-topology-workflow-request/v1"
REPORT_SCHEMA = "torii.hamburg-topology-workflow/v1"
_BUILD_KEYS = {"bbox", "geo_boundary", "tls_join_distance_m", "highway_classes", "historical_date",
               "clip_source_ways_to_bbox", "netconvert_profile", "traffic_side"}
_CONSTRUCTION_DEFAULTS = {"seed": 104, "vehicle_count": 100, "simulation_end": 600,
                        "simulation_max_end": 2400, "timeout_seconds": 240.0, "junction_contours": "fused",
                        "junction_corner_radius_m": 8.0}
_CONSTRUCTION_KEYS = {*_CONSTRUCTION_DEFAULTS, "maximum_anchor_projection_error_m",
                      "maximum_lane_projection_error_m", "minimum_lane_match_margin_m",
                      "context_joins", "context_geometry_neighbors", "context_lane_change_policy", "sumo_binary", "netconvert_binary"}
_REQUEST_KEYS = {"schema", "source_osm", "lsa_identity", "osm_build", "intersections", "road_names",
                 "construction", "aerial_max_error_m", "tls_cluster_radius_m", "max_binding_distance_m",
                 "construction_plan", "scenario", "supporting_maps", "context_intersections"}


def _identity(path: Path | str) -> dict[str, str]:
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": file_sha256(path)}


def _build_construction_plan_workflow(request_path, request, destination):
    """Run the same entry from interpreted user drawings instead of MAP authority."""
    if request.get('schema') != REQUEST_SCHEMA or set(request) - _REQUEST_KEYS:
        raise ValueError(f'Use the supported {REQUEST_SCHEMA} fields.')
    policy = resolve_network_source_policy(request)
    inputs = [{'role': 'request', **_identity(request_path)}]

    def source(record, role):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            raise ValueError(f'{role} requires path and sha256.')
        digest = record.get('sha256')
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-fA-F]{64}', digest) is None:
            raise ValueError(f'{role} requires a valid SHA-256.')
        path = Path(record['path']).expanduser()
        if not path.is_absolute():
            path = request_path.parent / path
        identity = _identity(path)
        if identity['sha256'] != digest.lower():
            raise ValueError(f'{role} source hash does not match.')
        if Path(identity['path']).is_relative_to(destination):
            raise ValueError('Output must be separate from all inputs.')
        inputs.append({'role': role, **identity})
        return {**record, **identity}

    plan = source(request['construction_plan'], 'construction_plan')
    with Path(plan['path']).open('rb') as handle:
        if handle.read(5) != b'%PDF-':
            raise ValueError('construction_plan.path must reference the supplied PDF drawing.')
    if plan.get('document_kind', 'design') not in {'design', 'as_built', 'survey', 'evaluation'}:
        raise ValueError('Use design, as_built, survey, or evaluation for document_kind.')
    topology = source(plan.get('topology'), 'interpreted_plan_topology')
    interpretation = json.loads(Path(topology['path']).read_text(encoding='utf-8'))
    if not isinstance(interpretation, dict) or interpretation.get('source_plan_sha256') != plan['sha256']:
        raise ValueError('topology.source_plan_sha256 must match the authoritative construction plan.')
    osm = source(request['source_osm'], 'source_osm') if request.get('source_osm') is not None else None
    maps = []
    for i, record in enumerate(request.get('supporting_maps', [])):
        if not isinstance(record.get('provider'), str) or not record['provider'].strip():
            raise ValueError('Each supporting map needs a provider name.')
        if record.get('path') is not None:
            resolved = source(record, f'supporting_map.{i}')
            availability = 'provided_artifact_for_review'
        else:
            url = record.get('url')
            if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
                raise ValueError('A supporting map needs a supplied file or HTTP(S) reference URL.')
            resolved, availability = dict(record), 'reference_link_only_not_fetched'
        maps.append({**resolved, **policy['supporting_maps'][i], 'availability': availability,
                     'automatic_geometry_extraction': False})
    options = request.get('construction', {})
    if not isinstance(options, dict) or set(options) - {'netconvert_binary', 'sumo_binary'}:
        raise ValueError('The construction-plan branch supports netconvert_binary and sumo_binary options only.')
    for name in ('netconvert_binary', 'sumo_binary'):
        if name in options and (not isinstance(options[name], str) or not options[name].strip()):
            raise ValueError(f'{name} must be a nonempty executable path.')
    if request_path.is_relative_to(destination):
        raise ValueError('Output must be separate from the request.')
    destination.mkdir(parents=True)
    report_file = destination / 'workflow.manifest.json'
    report = dict(schema=REPORT_SCHEMA, purpose='network_construction', status='running',
        started_utc=datetime.now(timezone.utc).isoformat(), inputs=inputs, source_policy=policy,
        comparison_target='provided_construction_plan', supporting_maps=maps,
        google_maps_status=('provided_artifact_for_review' if any(r['provider'] == 'google_maps' and r.get('path') for r in maps)
                            else 'reference_only_not_fetched' if any(r['provider'] == 'google_maps' for r in maps) else 'not_provided'),
        stages={'input_resolution': {'status': policy['status']}},
        skipped_stages={'MAP_KML_binding': 'not_applicable: construction plan defines topology',
                        'latest_map_conformity': 'not_applicable: user data define the target state'},
        supplied_legacy_fields_not_used=[key for key in ('intersections', 'lsa_identity', 'osm_build') if key in request],
        calibration={'status': 'not_run', 'required_for_network_construction': False},
        topology_complete=False, network_handoff=None, first_failed_stage=None, report_file=str(report_file))
    write_json_atomic(report_file, report, ensure_ascii=False)
    current_stage = 'network_construction'
    generated_inputs = []
    continuity = None
    try:
        if interpretation.get('road_runs'):
            from ..road_network.continuous_lanes import reconstruct_continuous_lanes
            current_stage = 'road_continuity'
            continuity = reconstruct_continuous_lanes(topology_file=topology['path'], source_osm=osm,
                target_year=policy['target_year'], output_dir=destination / 'road-continuity')
            report['stages']['road_continuity'] = {'status': continuity['status'], 'artifact': _identity(continuity['report_file'])}
            generated_inputs.extend([continuity['topology'], _identity(continuity['report_file'])])
            if continuity['status'] == 'blocked':
                raise ValueError('Continuous-lane reconstruction failed its input checks.')
            topology = continuity['topology']
        current_stage = 'network_construction'
        from ..road_network.engineering_topology import build_engineering_topology
        result = build_engineering_topology(topology_file=topology['path'], source_osm=osm,
            output_dir=destination / 'candidate', target_year=policy['target_year'], **options)
        result_file = destination / 'construction-result.json'
        write_json_atomic(result_file, result, ensure_ascii=False)
        report['stages']['network_construction'] = {'status': result['status'], 'artifact': _identity(result_file)}
        load = result.get('load_result') or {}
        report['stages']['sumo_load'] = {**load, 'status': load.get('status',
            'pass' if load.get('returncode') == 0 else 'blocked' if load else 'not_run')}
        report['construction_checks'] = result.get('construction_checks', {})
        report['declared_topology_reproduced'] = result.get('declared_topology_reproduced', False)
        report['declared_geometry_reproduced'] = result.get('declared_geometry_reproduced', False)
        report['lane_geometry_checks'] = result.get('lane_geometry_checks', [])
        report['topology_complete'] = result.get('topology_complete', False)
        report['unresolved'] = result.get('unknowns', [])
        report['status'] = ('blocked' if result['status'] == 'blocked' else 'review_required'
                            if result.get('decision') != 'pass' or policy['status'] != 'pass' else 'pass')
        junction_connections = [c for c in result.get('declared_topology', {}).get('connections', [])
                                if continuity is None or c.get('role') != 'continuous_road_boundary']
        if result['status'] == 'pass' and junction_connections:
            current_stage = 'movement_probes'
            movement_probes = run_candidate_movement_probes(candidate_manifest=result['manifest_file'],
                output_dir=destination / 'movement-probes', sumo_binary=options.get('sumo_binary', 'sumo'),
                junction_movements_only=continuity is not None)
            report['stages']['movement_probes'] = {'status': movement_probes['status'],
                                                   'artifact': _identity(movement_probes['report_file'])}
            if movement_probes['status'] != 'pass':
                report['status'] = 'review_required'
        elif result['status'] == 'pass':
            report['stages']['movement_probes'] = {'status': 'not_applicable', 'reason': 'No separate junction movements are declared.'}
        if result['status'] == 'pass' and continuity is not None:
            current_stage = 'corridor_lane_probes'
            from ..road_network.continuous_lane_probes import run_continuous_lane_probes
            lane_probes = run_continuous_lane_probes(network_file=result['artifacts']['network']['path'],
                continuity_file=continuity['report_file'], output_dir=destination / 'corridor-lane-probes',
                sumo_binary=options.get('sumo_binary', 'sumo'))
            report['stages']['corridor_lane_probes'] = {'status': lane_probes['status'], 'artifact': _identity(lane_probes['report_file'])}
            if lane_probes['status'] != 'pass':
                report['status'] = 'review_required'
        network = result.get('artifacts', {}).get('network')
        if network:
            network_path = Path(network['path']).resolve(strict=True)
            if file_sha256(network_path) != network['sha256']:
                raise ValueError('The candidate changed after construction.')
            report['network_handoff'] = dict(network=network, construction_decision=report['status'],
                topology_authority='construction_plan', target_year=policy['target_year'],
                construction_plan={k: plan[k] for k in ('path', 'sha256')})
        if _changed(inputs):
            raise ValueError('A supplied source changed during the workflow.')
        if _changed(generated_inputs):
            raise ValueError('A generated road-continuity input changed during the workflow.')
        report['inputs_unchanged'] = True
        report['generated_inputs'] = generated_inputs
    except Exception as error:
        report.update(status='blocked', first_failed_stage=current_stage, network_handoff=None,
                      inputs_unchanged=not _changed(inputs), error=f'{type(error).__name__}: {error}')
        report['stages'].setdefault(current_stage, {}).update(status='blocked', error=report['error'])
    report['completed_utc'] = datetime.now(timezone.utc).isoformat()
    report['claim_boundary'] = ('The supplied drawing and its reviewed interpretation define the target network. '
        'Google Maps and OSM cannot override its declared lanes or movements. Compilation does not certify '
        'drawing interpretation, field implementation, vehicle clearance, or historical signal timing.')
    write_json_atomic(report_file, report, ensure_ascii=False)
    handoff_file = destination / 'network-handoff.json'
    write_json_atomic(handoff_file, {'workflow': _identity(report_file), **(report['network_handoff'] or {}),
                                   'status': report['status']}, ensure_ascii=False)
    return {**report, 'handoff_file': str(handoff_file)}


def _changed(records: list[dict[str, str]]) -> list[dict[str, str]]:
    changed = []
    for record in records:
        try:
            matches = file_sha256(Path(record["path"])) == record["sha256"]
        except OSError:
            matches = False
        if not matches:
            changed.append(record)
    return changed


def _read_request(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    unknown = set(value) - _REQUEST_KEYS
    if unknown:
        raise ValueError(f"unsupported network-construction fields: {', '.join(sorted(unknown))}")
    inputs = [{"role": "request", **_identity(path)}]

    def source(record, role):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError(f"{role} requires a path and SHA-256")
        expected = str(record.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"{role} requires a valid SHA-256")
        file = Path(record["path"]).expanduser()
        if not file.is_absolute():
            file = path.parent / file
        try:
            identity = _identity(file)
        except OSError as error:
            raise ValueError(f"{role} source file is missing: {file}") from error
        if identity["sha256"] != expected:
            raise ValueError(f"{role} SHA-256 does not match")
        inputs.append({"role": role, **identity})
        return {**record, **identity}

    for role in ("source_osm", "lsa_identity"):
        value[role] = source(value.get(role), role)
    raw = Path(value["source_osm"]["path"])
    try:
        with (gzip.open(raw, "rb") if raw.suffix == ".gz" else raw.open("rb")) as handle:
            if next(ET.iterparse(handle, events=("start",)))[1].tag != "osm":
                raise ValueError("source_osm must contain raw OSM, not a previously built network")
    except (OSError, ET.ParseError, StopIteration) as error:
        raise ValueError("source_osm must be a readable OSM XML file") from error
    build = value.get("osm_build")
    if not isinstance(build, dict) or set(build) - _BUILD_KEYS:
        raise ValueError("osm_build contains unsupported options")
    parse_bbox(build.get("bbox", ""))
    resolve_highway_classes(build.get("highway_classes"), default_to_recommended=True)
    intersections = value.get("intersections")
    if not isinstance(intersections, list) or len(intersections) < 2:
        raise ValueError("a corridor requires at least two intersections with raw MAP, KML, and aerial inputs")
    node_ids = []
    contexts = value.setdefault("context_intersections", [])
    if not isinstance(contexts, list):
        raise ValueError("context_intersections must be a list")
    context_ids, context_members = set(), set()
    for row in contexts:
        if not isinstance(row, dict):
            raise ValueError("each context intersection must be an object")
        identifier = str(row.get("node_id", ""))
        if not identifier.isascii() or not identifier.isdigit():
            raise ValueError("context intersections require unique numeric node ids")
        identifier = str(int(identifier))
        if identifier in context_ids:
            raise ValueError("context intersections require unique numeric node ids")
        row["node_id"] = identifier
        context_ids.add(identifier)
        members = row.get("source_node_ids")
        if (not isinstance(members, list) or len(members) < 2
                or any(not isinstance(node, str) or not node or node.startswith(":") or any(c.isspace() for c in node) for node in members)
                or len(set(members)) != len(members) or context_members.intersection(members)):
            raise ValueError("context source nodes must be explicit, unique, and disjoint")
        context_members.update(members)
        if row.get("signal_policy") != "rebuild_diagnostic" or not str(row.get("review_basis", "")).strip():
            raise ValueError("context intersections require review_basis and signal_policy=rebuild_diagnostic")
        row["aerial_image"] = source(row.get("aerial_image"), f"context.{identifier}.aerial_image")
        bounds = row.get("bbox_epsg25832")
        if (not isinstance(bounds, list) or len(bounds) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise ValueError("context intersections require valid EPSG:25832 aerial bounds")
        year = row.get("aerial_year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
            raise ValueError("context intersections require an aerial_year")
    for row in intersections:
        if not isinstance(row, dict):
            raise ValueError("each intersection must be an object")
        node_id = str(row.get("node_id", ""))
        if not node_id.isascii() or not node_id.isdigit():
            raise ValueError("each intersection requires a numeric node_id")
        row["node_id"] = node_id
        if str(int(node_id)) in context_ids:
            raise ValueError("MAP and context intersections must not overlap")
        node_ids.append(node_id)
        for role in ("map_xml", "map_kml", "aerial_image"):
            row[role] = source(row.get(role), f"{node_id}.{role}")
        if "road_reference" in row:
            references = row["road_reference"]
            if not isinstance(references, dict) or set(references) != {"topology", "cross_sections"}:
                raise ValueError("road_reference requires topology and cross_sections identities")
            row["road_reference"] = {role: source(record, f"{node_id}.road_reference.{role}")
                                     for role, record in references.items()}
        if "road_prior" in row:
            raise ValueError("road_prior must be generated from raw road_reference inputs")
        bbox = row.get("bbox_epsg25832")
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox)
                or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
            raise ValueError(f"intersection {node_id} requires a valid EPSG:25832 aerial bbox")
        year = row.get("aerial_year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
            raise ValueError(f"intersection {node_id} requires an aerial_year")
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("intersection node ids must be distinct and ordered")
    roads = value.get("road_names")
    if not isinstance(roads, list) or not roads or any(not isinstance(name, str) or not name.strip() for name in roads):
        raise ValueError("road_names must declare the corridor roads for both-direction checks")
    options = value.get("construction", {})
    if not isinstance(options, dict) or set(options) - _CONSTRUCTION_KEYS:
        raise ValueError("construction contains unsupported options")
    options = {**_CONSTRUCTION_DEFAULTS, **options}
    if options.get("context_lane_change_policy", "fixed_paths") not in ("fixed_paths", "permitted_interior"):
        raise ValueError("context_lane_change_policy must be fixed_paths or permitted_interior")
    if options["junction_contours"] not in ("preserve", "guarded", "fused"):
        raise ValueError("junction_contours must be preserve, guarded, or fused")
    for name in ("seed", "vehicle_count", "simulation_end", "simulation_max_end"):
        number = options[name]
        if isinstance(number, bool) or not isinstance(number, int) or number < (0 if name == "seed" else 1):
            raise ValueError(f"{name} must be a valid integer")
    if options["simulation_max_end"] < options["simulation_end"]:
        raise ValueError("simulation_max_end must not be shorter than simulation_end")
    for name in ("timeout_seconds", "maximum_anchor_projection_error_m", "maximum_lane_projection_error_m", "minimum_lane_match_margin_m", "junction_corner_radius_m"):
        number = options.get(name, 1)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be finite and positive")
    for name, default in (("aerial_max_error_m", 3.0), ("tls_cluster_radius_m", 60.0), ("max_binding_distance_m", 35.0)):
        number = value.setdefault(name, default)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be finite and positive")
    value["construction"] = options
    return value, inputs


def build_hamburg_topology_workflow(*, request_file: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Run fresh construction and independent checks; never fit traffic counts."""
    request_path = Path(request_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    supplied = json.loads(request_path.read_text(encoding="utf-8"))
    if isinstance(supplied, dict) and supplied.get("construction_plan") is not None:
        return _build_construction_plan_workflow(request_path, supplied, destination)
    request, inputs = _read_request(request_path)
    source_policy = resolve_network_source_policy(request)
    if any(Path(item["path"]).is_relative_to(destination) for item in inputs):
        raise ValueError("output_dir must be separate from every frozen input")
    destination.mkdir(parents=True)
    report_file = destination / "workflow.manifest.json"
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "purpose": "network_construction", "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(), "inputs": inputs,
        "ordered_node_ids": [row["node_id"] for row in request["intersections"]],
        "road_names": request["road_names"], "parameters": request["construction"],
        "stages": {}, "generated_inputs": [], "topology_complete": False, "network_handoff": None,
        "calibration": {"status": "not_run", "required_for_network_construction": False,
                        "next_workflow": "separate signal/count binding and demand calibration on a frozen accepted network"},
        "first_failed_stage": None, "report_file": str(report_file),
        "source_policy": source_policy,
    }
    current_stage = None
    candidate = None

    def save():
        write_json_atomic(report_file, report, ensure_ascii=False)

    def pin(role, path, expected_sha256=None):
        identity = _identity(path)
        if expected_sha256 is not None and identity["sha256"] != str(expected_sha256).lower():
            raise ValueError(f"{role} does not match its producing stage")
        report["generated_inputs"].append({"role": role, **identity})
        return identity

    def stage(name, operation, artifact_file=None, *, require_pass=False):
        nonlocal current_stage
        current_stage = name
        report["stages"][name] = {"status": "running"}
        save()
        if _changed(inputs + report["generated_inputs"]):
            raise ValueError("a frozen source or generated input changed before this stage")
        started = time.monotonic()
        result = operation()
        if _changed(inputs + report["generated_inputs"]):
            raise ValueError("a frozen source or generated input changed during this stage")
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise ValueError(f"{name} did not return a structured status")
        if artifact_file is None:
            artifact_file = destination / f"{name}.json"
            write_json_atomic(artifact_file, result, ensure_ascii=False)
        report["stages"][name] = {"status": result["status"], "artifact": _identity(artifact_file),
                                  "elapsed_seconds": round(time.monotonic() - started, 3)}
        save()
        if require_pass and result["status"] != "pass":
            raise ValueError(result.get("error") or f"{name} did not pass; inspect its stage artifact")
        return result

    save()
    options = request["construction"]
    timeout = float(options["timeout_seconds"])
    sumo_binary = options.get("sumo_binary", "sumo")
    netconvert_binary = options.get("netconvert_binary", "netconvert")
    try:
        build_options = dict(request["osm_build"])
        build_options["allowed_highways"] = resolve_highway_classes(build_options.pop("highway_classes", None), default_to_recommended=True)
        source = stage("source_network", lambda: build_osm_network(
            **build_options, source_osm_path=Path(request["source_osm"]["path"]),
            output_dir=destination / "source", prefix="source", netconvert_binary=netconvert_binary,
            timeout_seconds=timeout), require_pass=True)
        net_file = Path(source["net_file"])
        source_identity = pin("source_network", net_file)
        pin("filtered_osm", source["filtered_osm_file"])
        road_geometry = stage("road_geometry", lambda: build_hamburg_road_geometry_candidate(
            source_net=net_file, output_dir=destination / "road-geometry",
            netconvert_binary=netconvert_binary, timeout_seconds=timeout))
        if road_geometry["status"] == "blocked":
            raise ValueError("reviewed road geometry failed its preservation or connection checks")
        if road_geometry.get("candidate_network"):
            repaired = road_geometry["candidate_network"]
            net_file = Path(repaired["path"])
            source_identity = pin("road_geometry_network", net_file, repaired["sha256"])
        context_rebuild = None
        if request["context_intersections"]:
            context_rebuild = stage("context_intersections", lambda: rebuild_context_intersections(
                source_net=net_file, request=request, output_dir=destination / "context-intersections",
                netconvert_binary=netconvert_binary, sumo_binary=sumo_binary,
                timeout_seconds=timeout, seed=options["seed"]), require_pass=True)
            rebuilt = context_rebuild["candidate_network"]
            net_file = Path(rebuilt["path"])
            source_identity = pin("context_network", net_file, rebuilt["sha256"])
            pin("context_reconstruction", destination / "context_intersections.json")
        coverage = stage("construction_coverage", lambda: construction_coverage(request))
        tls = stage("physical_areas", lambda: audit_tls(
            net_file=net_file, output_dir=destination / "physical-areas", prefix="source",
            osm_file=Path(source["filtered_osm_file"]), cluster_radius_m=request["tls_cluster_radius_m"]), require_pass=True)
        cluster_identity = pin("tls_clusters", tls["clusters_file"])
        selection = destination / "selection.json"
        write_json_atomic(selection, {"schema": "torii.hamburg-five-corridor-selection/v1",
            "selection_mode": "topology_only", "ordered_node_ids": report["ordered_node_ids"]})
        selection_identity = pin("selection", selection)
        binding_file = destination / "cluster-binding.json"
        stage("intersection_binding", lambda: bind_hamburg_corridor_tls_clusters(
            selection_file=selection, expected_selection_sha256=selection_identity["sha256"],
            lsa_identity_file=Path(request["lsa_identity"]["path"]), expected_lsa_identity_sha256=request["lsa_identity"]["sha256"],
            tls_clusters_file=Path(tls["clusters_file"]), expected_tls_clusters_sha256=cluster_identity["sha256"],
            net_file=net_file, expected_net_sha256=source_identity["sha256"], output_file=binding_file,
            max_distance_m=request["max_binding_distance_m"]), binding_file, require_pass=True)
        binding_identity = pin("intersection_binding", binding_file)
        movement_rows = []
        for row in request["intersections"]:
            movement_row = dict(row)
            if "road_reference" in row and (source_policy['target_year'] is None or row['aerial_year'] == source_policy['target_year']):
                node_id = row["node_id"]
                edges = stage(f"road_edges.{node_id}", lambda row=row: build_hamburg_road_edge_evidence(
                    aerial_image=row["aerial_image"]["path"], bbox=row["bbox_epsg25832"], aerial_year=row["aerial_year"],
                    references=row["road_reference"], output_dir=destination / "road-edges" / f"lsa-{row['node_id']}"),
                    require_pass=True)
                prior = pin(f"road_prior.{node_id}", edges["prior"]["path"], edges["prior"]["sha256"])
                pin(f"road_edges.{node_id}", edges["report_file"])
                movement_row["road_prior"] = {**prior, "bbox_epsg25832": row["bbox_epsg25832"],
                                              "image_sha256": row["aerial_image"]["sha256"]}
            movement_rows.append(movement_row)
        movement_request = destination / "movement-request.json"
        write_json_atomic(movement_request, {"schema": "torii.hamburg-aerial-movement-request/v1",
            "intersections": movement_rows, "max_error_m": request["aerial_max_error_m"],
            "target_year": source_policy['target_year']})
        movement_summary = destination / "movements" / "corridor-summary.json"
        movement_plan = stage("movement_geometry", lambda: build_hamburg_aerial_corridor_plan(
            request_file=movement_request, output_dir=destination / "movements"), movement_summary, require_pass=True)
        movement_identity = pin("movement_summary", movement_summary)
        for row in movement_plan["intersections"]:
            pin(f"movement_plan.{row['node_id']}", row["plan_file"], row["plan_sha256"])
        combine_request = destination / "combine-request.json"
        write_json_atomic(combine_request, {"schema": "torii.hamburg-aerial-corridor-candidate-request/v1",
            **options, "source_net": source_identity, "cluster_binding": binding_identity,
            "movement_summary": movement_identity})
        candidate_manifest = destination / "candidate" / "manifest.json"
        candidate = stage("network_construction", lambda: build_hamburg_aerial_combined_candidate(
            request_file=combine_request, output_dir=destination / "candidate"), candidate_manifest)
        report["topology_complete"] = bool(candidate.get("topology_complete"))
        report["counts"] = candidate.get("counts", {})
        network = candidate["artifacts"]["network"]
        pin("candidate_network", network["path"], network["sha256"])
        pin("candidate_manifest", candidate_manifest)
        quality = stage("construction_audit", lambda: audit_hamburg_topology_candidate(
            candidate_manifest, destination / "construction-audit", netconvert_binary=netconvert_binary, timeout_seconds=timeout))
        movements = stage("movement_probes", lambda: run_candidate_movement_probes(
            candidate_manifest=candidate_manifest, output_dir=destination / "movement-probes",
            context_manifest=destination / "context_intersections.json" if context_rebuild is not None else None,
            sumo_binary=sumo_binary, seed=options["seed"], end_time_s=options["simulation_end"], timeout_seconds=timeout))
        routes = stage("corridor_routes", lambda: run_hamburg_topology_route_checks(
            candidate_manifest, destination / "corridor-routes", road_names=request["road_names"],
            ordered_node_ids=report["ordered_node_ids"], sumo_binary=sumo_binary, seed=options["seed"],
            end_time_s=options["simulation_max_end"], timeout_seconds=timeout))
        decisions = [candidate["status"], quality["status"], movements["status"], routes["status"], source_policy['status']]
        decisions.append(coverage["status"])
        if context_rebuild is not None:
            decisions.append(context_rebuild["field_geometry_review"])
        if road_geometry["status"] != "not_applicable":
            decisions.append(road_geometry["status"])
        road_edge_review = any("road_reference" in row for row in request["intersections"])
        if road_edge_review:
            decisions.append("review_required")
        report["status"] = ("blocked" if any(value in {"blocked", "fail", "error"} for value in decisions)
            else "pass" if all(value == "pass" for value in decisions) and report["topology_complete"]
            else "review_required")
        report["checks"] = {"official_connectivity": "pass" if report["topology_complete"] else "review_required",
            "road_geometry": road_geometry["status"],
            "construction_coverage": coverage["status"],
            "movement_probes": movements["status"], "corridor_routes": routes["status"],
            "construction_audit": quality["status"]}
        if road_edge_review:
            report["checks"]["road_edge_interpretation"] = "review_required"
    except Exception as error:  # Keep partial construction and the first actionable failure.
        report["status"] = "blocked"
        report["first_failed_stage"] = current_stage
        report["stages"].setdefault(current_stage, {}).update(status="blocked", error=str(error))
    changed_inputs = _changed(inputs)
    report["inputs_unchanged"] = not changed_inputs
    report["changed_inputs"] = changed_inputs
    changed_generated = _changed(report["generated_inputs"])
    report["generated_inputs_unchanged"] = not changed_generated
    report["changed_generated_inputs"] = changed_generated
    if changed_inputs or changed_generated:
        report["status"] = "blocked"
    if candidate is not None:
        network = candidate["artifacts"]["network"]
        unchanged = Path(network["path"]).is_file() and file_sha256(Path(network["path"])) == network["sha256"]
        report["candidate_unchanged"] = unchanged
        if not unchanged:
            report["status"] = "blocked"
        report["network_handoff"] = {"network": network, "construction_decision": report["status"],
                                     "construction_manifest": str(report_file)}
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report["next_action"] = ("network_construction_complete" if report["status"] == "pass"
        else "inspect_the_recorded_construction_findings_and_rebuild_in_a_new_directory")
    report["first_incomplete_stage"] = next((name for name, row in report["stages"].items()
                                              if row["status"] not in {"pass", "not_applicable"}), None)
    save()
    if report["network_handoff"] is not None:
        handoff_file = destination / "network-handoff.json"
        write_json_atomic(handoff_file, {"schema": "torii.hamburg-network-handoff/v1",
            **report["network_handoff"], "workflow": _identity(report_file),
            "calibration_status": "not_run"})
        report = {**report, "handoff_file": str(handoff_file)}
    return report
