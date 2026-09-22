"""Build explicit plan topology; OSM may supply dated gaps, never lane counts or movements."""

from collections import defaultdict
from collections.abc import Mapping
import gzip
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from sumolib.net.lane import SUMO_VEHICLE_CLASSES

from ..core.artifact_io import write_json_atomic
from ..core.candidate_contracts import file_sha256
from ..core.command_runner import run_command
from ..core.connection_mode_audit import audit_network_connection_mode
from ..core.osm_access import _permission_set
from ..core.hamburg_aerial_approach import _parse, _project_polyline
from .observed_lane_candidate import _signal_signature
from .official_plainxml import _netconvert_projection, _write_xml


def _year(value, name):
    if value is not None and (type(value) is not int or not 1900 <= value <= 2200):
        raise ValueError(f"{name} must be an integer year or null.")
    return value


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite.") from error
    if not math.isfinite(result) or positive and result <= 0:
        raise ValueError(f"{name} must be finite{' and positive' if positive else ''}.")
    return result


def _id(value):
    if not isinstance(value, str) or not value or value.startswith(":") or any(c.isspace() for c in value):
        raise ValueError("Use nonempty external object IDs without whitespace or a leading colon.")
    return value


def _evidence(value):
    if not (isinstance(value, str) and value.strip() or isinstance(value, Mapping) and value):
        raise ValueError("Each declared node and edge requires nonempty evidence text or an object.")


def _shape(points):
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("A shape requires at least two metric points.")
    values = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each shape point requires two coordinates.")
        values.append(tuple(_number(v, "shape coordinate") for v in point))
    if len(set(values)) < 2:
        raise ValueError("A shape requires distinct points.")
    return " ".join(f"{x:.9f},{y:.9f}" for x, y in values)


def _identity(path):
    path = Path(path).expanduser().resolve(strict=True)
    return dict(path=str(path), sha256=file_sha256(path))


def build_engineering_topology(*, topology_file, source_osm, output_dir, target_year,
                               netconvert_binary="netconvert", sumo_binary="sumo"):
    """Compile plan-declared lanes and movements with independent diagnostic signals."""
    target_year = _year(target_year, "target_year")
    topology_identity = _identity(topology_file)
    topology_path = Path(topology_identity["path"])
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("Choose a new output directory.")
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    if not isinstance(topology, dict) or topology.get("schema") != "torii.engineering-topology/v1":
        raise ValueError("Use topology schema torii.engineering-topology/v1.")
    plan_hash = topology.get("source_plan_sha256")
    if not isinstance(plan_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", plan_hash):
        raise ValueError("source_plan_sha256 requires a valid SHA-256.")
    crs = CRS.from_user_input(topology.get("crs", ""))
    if not crs.is_projected or len(crs.axis_info) != 2 or any(a.unit_conversion_factor != 1 for a in crs.axis_info):
        raise ValueError("Topology CRS must be projected and use metres.")
    projection = _netconvert_projection(dict(crs=crs.to_string()))
    global_validity = _year(topology.get("osm_geometry_valid_for_target_year"), "osm_geometry_valid_for_target_year")
    sources = {"topology": topology_identity}
    osm_nodes, osm_ways, osm_year = {}, {}, None
    if source_osm is not None:
        if not isinstance(source_osm, Mapping) or not isinstance(source_osm.get("path"), str):
            raise ValueError("source_osm requires a path and SHA-256 or must be null.")
        osm_path = Path(source_osm["path"]).expanduser()
        osm_path = osm_path if osm_path.is_absolute() else topology_path.parent / osm_path
        identity = _identity(osm_path)
        if identity["sha256"] != str(source_osm.get("sha256", "")).lower():
            raise ValueError("OSM SHA-256 does not match.")
        osm_year = _year(source_osm.get("data_year"), "source_osm.data_year")
        sources["osm"] = {**dict(source_osm), **identity}
        raw = Path(identity["path"]).read_bytes()
        osm = ET.fromstring(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)
        if osm.tag != "osm":
            raise ValueError("source_osm must contain raw OSM XML.")
        osm_nodes = {n.get("id"): n for n in osm.findall("node")}
        osm_ways = {w.get("id"): {t.get("k"): t.get("v") for t in w.findall("tag")} for w in osm.findall("way")}
    unknowns = []
    for field in ("assumptions", "unresolved"):
        values = topology.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f"topology.{field} must be a list.")
        unknowns.extend(dict(source_field=f"topology.{field}", value=value) for value in values)
    coverage = topology.get("coverage", {})
    if not isinstance(coverage, dict):
        raise ValueError("topology.coverage must be an object.")
    if coverage.get("partial") is True:
        unknowns.append(dict(source_field="topology.coverage", reason="declared_scope_is_partial", value=coverage))
    if target_year is None:
        unknowns.append(dict(reason="target_year_unknown"))
    node_rows, edge_rows, connections = (topology.get(k) for k in ("nodes", "edges", "connections"))
    if (not isinstance(node_rows, list) or not node_rows or not isinstance(edge_rows, list) or not edge_rows
            or not isinstance(connections, list)):
        raise ValueError("Declare nodes, edges, and an explicit connections list.")
    if any(not isinstance(row, dict) for row in node_rows + edge_rows + connections):
        raise ValueError("Nodes, edges, and connections must be objects.")
    nodes = {_id(n.get("id")): n for n in node_rows}
    edges = {_id(e.get("id")): e for e in edge_rows}
    if len(nodes) != len(node_rows) or len(edges) != len(edge_rows):
        raise ValueError("Node and edge IDs must be unique.")
    fills, positions, missing = [], {}, []
    node_xml, edge_xml, con_xml, tls_xml = [ET.Element(k) for k in ("nodes", "edges", "connections", "tlLogics")]
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    for key, row in nodes.items():
        _evidence(row.get("evidence"))
        if ("x" in row) != ("y" in row):
            raise ValueError("Provide both node coordinates or use osm_node_id.")
        if "x" in row:
            xy = [_number(row[axis], f"node.{axis}") for axis in ("x", "y")]
        else:
            valid_for = _year(row.get("osm_geometry_valid_for_target_year", global_validity), "node OSM geometry validity")
            osm_id = str(row.get("osm_node_id", ""))
            if target_year is None or not (osm_year == target_year or valid_for == target_year):
                missing.append(dict(object_id=key, reason="osm_geometry_not_valid_for_target_year", osm_data_year=osm_year, target_year=target_year))
                continue
            if osm_id not in osm_nodes:
                missing.append(dict(object_id=key, reason="missing_plan_coordinates_and_osm_node", osm_node_id=osm_id))
                continue
            point = osm_nodes[osm_id]
            lon, lat = float(point.get("lon")), float(point.get("lat"))
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError("OSM longitude and latitude are invalid.")
            xy = list(transform.transform(lon, lat, errcheck=True))
            fills.append(dict(object_id=key, field="position", osm_node_id=osm_id, value=xy,
                              data_year=osm_year, valid_for_target_year=valid_for, source_sha256=sources["osm"]["sha256"]))
        positions[key] = xy
        kind = row.get("type", "priority")
        if not isinstance(kind, str) or not kind:
            raise ValueError("Node type must be text.")
        attrs = dict(id=key, x=str(xy[0]), y=str(xy[1]), type=kind)
        if "shape" in row:
            attrs["shape"] = _shape(row["shape"])
        if 'keepClear' in row:
            if type(row['keepClear']) is not bool:
                raise ValueError('Node keepClear must be a boolean.')
            attrs['keepClear'] = str(row['keepClear']).lower()
        ET.SubElement(node_xml, "node", attrs)
        if "type" not in row:
            unknowns.append(dict(object_id=key, reason="node_control_unspecified", model_type="priority"))
    unknowns.extend(missing)
    report = dict(schema="torii.engineering-topology-build/v1", status="blocked", decision="review_required",
                  target_year=target_year, source_plan_sha256=plan_hash.lower(), topology_sha256=topology_identity["sha256"],
                  sources=sources, geometry_authority=dict(primary="engineering_plan", lane_counts="engineering_plan",
                  connections="engineering_plan", osm="dated_supplement_only"),
                  plan_pdf_validation="The caller validates the PDF against source_plan_sha256; this module validates the topology and OSM files.",
                  artifacts={}, auxiliary_fills=fills, unknowns=unknowns, construction_checks={}, load_result=None,
                  declared_topology_reproduced=False, topology_complete=False, field_readiness="review_required",
                  actual_signal_timing_verified=False, automatic_promotion_gate="blocked",
                  declared_topology=topology, projection=dict(crs=crs.to_string(), proj_parameter=projection),
                  claim_boundary="Construction and SUMO load checks do not certify current or historical field conditions. "
                                 "Plan lane counts and explicit connections remain authoritative. OSM never supplies either. "
                                 "Signal phases and any missing speed values are diagnostic assumptions.")
    def save():
        def unchanged(identities):
            try:
                return all(file_sha256(Path(v["path"])) == v["sha256"] for v in identities)
            except OSError:
                return False
        report["inputs_unchanged"] = unchanged(sources.values())
        report["generated_artifacts_unchanged"] = unchanged(report["artifacts"].values())
        if not report["inputs_unchanged"] or not report["generated_artifacts_unchanged"]:
            report["status"] = "blocked"
            report["declared_topology_reproduced"] = False
        destination.mkdir(parents=True, exist_ok=True)
        report["manifest_file"] = str(destination / "manifest.json")
        write_json_atomic(Path(report["manifest_file"]), report, ensure_ascii=False)
        return report
    if missing:
        report["construction_checks"]["node_geometry"] = "blocked"
        return save()
    lane_expectations = {}
    for key, row in edges.items():
        _evidence(row.get("evidence"))
        first, last = _id(row.get("from")), _id(row.get("to"))
        if first not in nodes or last not in nodes or first == last:
            raise ValueError("Edge endpoints must name two different declared nodes.")
        lanes = row.get("lanes")
        if not isinstance(lanes, list) or not lanes or any(not isinstance(lane, dict) for lane in lanes):
            raise ValueError("Each edge must explicitly declare its lanes.")
        tags = osm_ways.get(str(row.get("osm_way_id", "")), {})
        attrs = dict(id=key, **{"from": first, "to": last}, numLanes=str(len(lanes)), spreadType=row.get("spreadType", "center"))
        if "shape" in row:
            attrs["shape"] = _shape(row["shape"])
        name = row.get("name", tags.get("name"))
        if name is not None:
            if not isinstance(name, str):
                raise ValueError("Road name must be text.")
            attrs["name"] = name
            if "name" not in row:
                fills.append(dict(object_id=key, field="name", value=name, osm_way_id=str(row["osm_way_id"]), data_year=osm_year, source_sha256=sources["osm"]["sha256"]))
        speed = row.get("speed_m_s")
        if speed is None and target_year is not None and osm_year == target_year and re.fullmatch(r"\d+(?:\.\d+)?(?:\s*km/h)?", tags.get("maxspeed", "")):
            speed = float(tags["maxspeed"].split()[0].replace("km/h", "")) / 3.6
            fills.append(dict(object_id=key, field="speed_m_s", value=speed, osm_way_id=str(row["osm_way_id"]), data_year=osm_year, source_sha256=sources["osm"]["sha256"]))
        if speed is None:
            speed = 5.56
            unknowns.append(dict(object_id=key, reason="speed_unknown", diagnostic_speed_m_s=speed))
        speed = _number(speed, "edge speed_m_s", positive=True)
        attrs["speed"] = str(speed)
        edge = ET.SubElement(edge_xml, "edge", attrs)
        for index, lane in enumerate(lanes):
            width = _number(lane.get("width_m"), "lane width_m", positive=True)
            allow = lane.get("allow")
            if isinstance(allow, list) and all(isinstance(v, str) and v and not any(c.isspace() for c in v) for v in allow):
                allow = " ".join(allow)
            if not isinstance(allow, str) or not allow.strip() or set(allow.split()) - (SUMO_VEHICLE_CLASSES | {"all"}):
                raise ValueError("Each lane requires explicit supported SUMO vehicle classes in allow.")
            lane_attrs = dict(index=str(index), width=str(width), allow=allow,
                              speed=str(_number(lane.get("speed_m_s", speed), "lane speed_m_s", positive=True)))
            if "shape" in lane:
                lane_attrs["shape"] = _shape(lane["shape"])
            else:
                unknowns.append(dict(object_id=f"{key}_{index}", reason="lane_shape_derived_from_nodes_or_edge"))
            ET.SubElement(edge, "lane", lane_attrs)
            lane_expectations[(key, index)] = lane_attrs
    outgoing = defaultdict(list)
    for key, row in edges.items():
        outgoing[row["from"]].append(key)
    groups, expected_connections = defaultdict(list), {}
    for row in connections:
        first, last = _id(row.get("from")), _id(row.get("to"))
        a, b = row.get("fromLane"), row.get("toLane")
        if type(a) is not int or type(b) is not int or (first, a) not in lane_expectations or (last, b) not in lane_expectations:
            raise ValueError("Connections require explicit valid integer lane indices.")
        if edges[first]["to"] != edges[last]["from"]:
            raise ValueError("Connected edges must share their junction.")
        key = (first, a, last, b)
        if key in expected_connections:
            raise ValueError("Connection pairs must be unique.")
        attrs = dict(**{"from": first, "to": last}, fromLane=str(a), toLane=str(b))
        if "shape" in row:
            attrs["shape"] = _shape(row["shape"])
        ET.SubElement(con_xml, "connection", attrs)
        expected_connections[key] = attrs
        if nodes[edges[first]["to"]].get("type") == "traffic_light":
            groups[edges[first]["to"]].append(attrs)
    # Pair-level deletions also suppress later explicit lanes, so delete only undeclared edge pairs.
    declared_pairs = {(key[0], key[2]) for key in expected_connections}
    for key, row in edges.items():
        for target in outgoing[row["to"]]:
            if (key, target) not in declared_pairs:
                ET.SubElement(con_xml, "delete", {"from": key, "to": target})
    for node_id, row in nodes.items():
        if row.get("type") != "traffic_light":
            continue
        movements = groups[node_id]
        if not movements:
            raise ValueError("A traffic-light node requires explicit movements for diagnostic phases.")
        logic = ET.SubElement(tls_xml, "tlLogic", id=node_id, type="static", programID="torii-diagnostic", offset="0")
        for index, attrs in enumerate(movements):
            for duration, signal in (("5", "G"), ("2", "y"), ("1", "r")):
                ET.SubElement(logic, "phase", duration=duration, state="r" * index + signal + "r" * (len(movements) - index - 1))
            ET.SubElement(tls_xml, "connection", {k: attrs[k] for k in ("from", "to", "fromLane", "toLane")}, tl=node_id, linkIndex=str(index))
        unknowns.append(dict(object_id=node_id, reason="actual_signal_timing_unknown", diagnostic="one_declared_movement_at_a_time"))
    geographic = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon_lat = [geographic.transform(*xy, errcheck=True) for xy in positions.values()]
    def bounds(points):
        return ",".join(str(v) for v in (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)))
    location_attrs = dict(netOffset="0,0", convBoundary=bounds(list(positions.values())), origBoundary=bounds(lon_lat), projParameter=projection)
    for xml in (node_xml, edge_xml, con_xml):
        xml.insert(0, ET.Element("location", location_attrs))
    destination.mkdir(parents=True)
    paths = {key: destination / f"plan.{suffix}.xml" for key, suffix in (("nodes", "nod"), ("edges", "edg"), ("connections", "con"), ("signals", "tll"))}
    for key, xml in (("nodes", node_xml), ("edges", edge_xml), ("connections", con_xml), ("signals", tls_xml)):
        _write_xml(paths[key], xml)
        report["artifacts"][key] = _identity(paths[key])
    network = destination / "engineering.net.xml"
    command = [str(netconvert_binary), "--node-files", str(paths["nodes"]), "--edge-files", str(paths["edges"]),
               "--connection-files", str(paths["connections"]), "--tllogic-files", str(paths["signals"]),
               "--no-turnarounds", "true", "--junctions.internal-link-detail", "25",
               "--precision", "6", "--output-file", str(network)]
    compiled = run_command(command, cwd=destination, timeout_seconds=60)
    report["netconvert_result"] = compiled.to_dict()
    (destination / "netconvert.log").write_text(compiled.stdout + compiled.stderr, encoding="utf-8")
    if compiled.returncode != 0 or not network.is_file():
        report["construction_checks"]["netconvert"] = "blocked"
        return save()
    report["artifacts"]["network"] = _identity(network)
    actual = ET.parse(network).getroot()
    actual_edges = {e.get("id"): e for e in actual.findall("edge") if not e.get("id", "").startswith(":")}
    actual_connections = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c
                          for c in actual.findall("connection") if not c.get("from", "").startswith(":")}
    lane_checks = []
    for (edge_id, index), attrs in lane_expectations.items():
        lane = actual_edges[edge_id].find(f"lane[@index='{index}']") if edge_id in actual_edges else None
        matches = lane is not None and _permission_set(lane.attrib) == _permission_set(attrs) and all(
            math.isclose(float(lane.get(k, "nan")), float(attrs[k]), rel_tol=0, abs_tol=1e-6) for k in ("width", "speed"))
        lane_checks.append(dict(edge_id=edge_id, lane_index=index, expected=attrs, evidence=edges[edge_id]["lanes"][index].get("evidence"),
                                actual=dict(lane.attrib) if lane is not None else None, matches=matches))
    audit = audit_network_connection_mode(actual, endpoint_tolerance_m=0.1)
    report["lane_checks"] = lane_checks
    report["connection_audit"] = audit
    report["actual_connections"] = [dict(c.attrib) for c in actual_connections.values()]
    report["missing_connections"] = sorted(set(expected_connections) - actual_connections.keys())
    report["extra_connections"] = sorted(actual_connections.keys() - set(expected_connections))
    location = actual.find("location")
    frame_matches = location is not None and CRS.from_user_input(location.get("projParameter")).equals(CRS.from_user_input(projection), ignore_axis_order=True)
    offset = list(map(float, location.get("netOffset").split(","))) if location is not None else [0, 0]
    geometry_checks = []
    for row in lane_checks:
        if 'shape' not in row['expected'] or row['actual'] is None:
            continue
        requested = _parse(row['expected']['shape'])
        compiled_shape = [(p[0]-offset[0], p[1]-offset[1]) for p in _parse(row['actual']['shape'])]
        discrepancy = max(max(_project_polyline(p, compiled_shape)['distance_m'] for p in requested),
                          max(_project_polyline(p, requested)['distance_m'] for p in compiled_shape))
        start_error, end_error = math.dist(requested[0], compiled_shape[0]), math.dist(requested[-1], compiled_shape[-1])
        matches = max(discrepancy, start_error, end_error) <= 0.1
        geometry_checks.append(dict(edge_id=row['edge_id'], lane_index=row['lane_index'], matches=matches,
            maximum_directed_vertex_distance_m=discrepancy, start_displacement_m=start_error,
            end_displacement_m=end_error, comparison_tolerance_m=0.1))
        if not matches:
            unknowns.append(dict(object_id=f"{row['edge_id']}_{row['lane_index']}",
                                 reason='compiled_lane_geometry_differs_from_plan',
                                 note='Native junction trimming can move lane endpoints. This is not original drawing geometry.'))
    report['lane_geometry_checks'] = geometry_checks
    report['declared_geometry_reproduced'] = bool(geometry_checks) and all(row['matches'] for row in geometry_checks)
    actual_nodes = {n.get("id"): n for n in actual.findall("junction")}
    node_matches = all((node := actual_nodes.get(key)) is not None and all(
        abs(float(node.get(axis)) - offset[i] - xy[i]) <= 1e-5 for i, axis in enumerate(("x", "y"))) for key, xy in positions.items())
    counts_match = set(actual_edges) == set(edges) and all(len(actual_edges[key].findall("lane")) == len(row["lanes"]) for key, row in edges.items())
    connections_match = set(expected_connections) == set(actual_connections)
    bindings_match = all((c := actual_connections.get((a["from"], int(a["fromLane"]), a["to"], int(a["toLane"])))) is not None
                        and c.get("tl") == node_id and c.get("linkIndex") == str(i) for node_id, rows in groups.items() for i, a in enumerate(rows))
    expected_signals = {(t.get("id"), t.get("programID", "0")): _signal_signature(t) for t in tls_xml.findall("tlLogic")}
    actual_signals = {(t.get("id"), t.get("programID", "0")): _signal_signature(t) for t in actual.findall("tlLogic")}
    loaded = run_command([str(sumo_binary), "--net-file", str(network), "--begin", "0", "--end", "1", "--no-step-log", "true"], cwd=destination, timeout_seconds=60)
    report["load_result"] = loaded.to_dict()
    (destination / "sumo-load.log").write_text(loaded.stdout + loaded.stderr, encoding="utf-8")
    checks = dict(netconvert=True, lane_counts=counts_match, lane_attributes=all(r["matches"] for r in lane_checks),
                  explicit_connections=connections_match, node_positions=node_matches, projection=frame_matches,
                  signal_bindings=bindings_match, diagnostic_signal_programs=expected_signals == actual_signals,
                  connection_structure=audit["structural_failure_count"] == 0, sumo_load=loaded.returncode == 0)
    report["construction_checks"] = {k: "pass" if v else "blocked" for k, v in checks.items()}
    report["declared_topology_reproduced"] = counts_match and connections_match and checks["lane_attributes"]
    report["status"] = "pass" if all(checks.values()) else "blocked"
    return save()
