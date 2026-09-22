"""Isolated native SUMO continuity probes; vehicle envelopes are outside this check."""

import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..core.artifact_io import write_json_atomic
from ..core.candidate_contracts import file_sha256
from ..core.command_runner import run_command
from ..core.osm_access import _permission_set
from ..core.source_movement_support import _candidate_modes, _index


_MODES = ("passenger", "bus", "bicycle")


def run_continuous_lane_probes(*, network_file, continuity_file, output_dir, sumo_binary="sumo"):
    """Observe every external lane in each declared path, with separate structural via checks."""
    network, continuity_path = Path(network_file).resolve(strict=True), Path(continuity_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new output directory.")
    identities, generated_artifacts = {}, {}
    def pin(path, expected=None, *, generated=False):
        path = Path(path).expanduser()
        path = (path if path.is_absolute() else continuity_path.parent / path).resolve(strict=True)
        digest = file_sha256(path)
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or digest != expected.lower()):
            raise ValueError("Input SHA-256 does not match.")
        collection = generated_artifacts if generated else identities
        if str(path) in collection and collection[str(path)] != digest:
            raise ValueError("A shared input changed during validation.")
        collection[str(path)] = digest
        return dict(path=str(path), sha256=digest)
    network_identity, continuity_identity = pin(network), pin(continuity_path)
    continuity = json.loads(continuity_path.read_text(encoding="utf-8"))
    if continuity.get("schema") != "torii.continuous-lanes/v1" or not isinstance(continuity.get("runs"), list):
        raise ValueError("Use a continuous-lanes/v1 report.")
    for name in ("topology", "source_topology"):
        record = continuity.get(name)
        if record:
            pin(record["path"], record["sha256"])
    for run in continuity["runs"]:
        source = run.get("source_chain", {}).get("source")
        if source:
            pin(source["path"], source["sha256"])
    producer = None
    manifest = network.parent / "manifest.json"
    if manifest.is_file():
        producer = pin(manifest)
        generated = json.loads(manifest.read_text(encoding="utf-8"))
        identity = generated.get("artifacts", {}).get("network", {})
        manifest_network = Path(identity.get("path", ""))
        manifest_network = (manifest_network if manifest_network.is_absolute() else manifest.parent / manifest_network).resolve()
        if manifest_network != network or identity.get("sha256") != network_identity["sha256"]:
            raise ValueError("Producer manifest does not bind this network.")
        if generated.get("topology_sha256") != continuity.get("topology", {}).get("sha256"):
            raise ValueError("Producer manifest and continuity describe different topology inputs.")
    index = _index(ET.parse(network).getroot())
    pairs = {}
    for pair, first, last, connection in index["movements"]:
        pairs.setdefault(pair, []).append((first, last, connection))
    def lane_id(pair):
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or not isinstance(pair[0], str)
                or type(pair[1]) is not int or pair[1] < 0):
            raise ValueError("Each edge_lanes item requires an edge ID and an integer lane index.")
        return index["keys"].get(tuple(pair))
    def structure(edge_lanes):
        ids = [lane_id(pair) for pair in edge_lanes]
        if any(key is None for key in ids):
            return dict(status="review_required", reason="declared_lane_missing", transitions=[]), set()
        modes = set(_MODES)
        for key in ids:
            modes &= _permission_set(index["lanes"][key][1].attrib)
        transitions = []
        for left, right in zip(edge_lanes, edge_lanes[1:]):
            pair = (left[0], left[1], right[0], right[1])
            choices = pairs.get(pair, [])
            if len(choices) != 1:
                return dict(status="review_required", reason="connection_missing_or_ambiguous", transitions=transitions), set()
            first, last, connection = choices[0]
            _, chain = _candidate_modes(index, first, last, connection)
            if not chain:
                return dict(status="review_required", reason="internal_chain_unresolved", transitions=transitions), set()
            for key in chain:
                modes &= _permission_set(index["lanes"][key][1].attrib)
            for a, b in zip(chain, chain[1:]):
                links = [c for nxt, c in index["outgoing"][a] if nxt == b]
                for link in links:
                    modes &= _permission_set(link.attrib)
            transitions.append(dict(connection=list(pair), internal_lane_ids=chain[1:-1], status="pass"))
        return dict(status="pass", transitions=transitions, basis="actual_network_unique_via_chain"), modes
    tasks = []
    for run in continuity["runs"]:
        if not isinstance(run.get("lane_paths"), list):
            raise ValueError("Every run requires lane_paths.")
        for row in run["lane_paths"]:
            if not isinstance(row.get("edge_lanes"), list) or not row["edge_lanes"]:
                raise ValueError("Each lane path requires at least one edge lane.")
            structural, modes = structure(row["edge_lanes"])
            tasks.append((run, row, structural, modes))
    if not tasks:
        raise ValueError("No lane paths were declared.")
    destination.mkdir(parents=True)
    counter = 0
    def simulate(edge_lanes, mode, *, native_lane_change=False):
        nonlocal counter
        directory = destination / f"probe-{counter:03d}"
        counter += 1
        directory.mkdir()
        routes = ET.Element("routes")
        attributes = dict(id="native", vClass=mode, maxSpeed="1", speedFactor="1", sigma="0",
                          laneChangeModel="LC2013", lcSpeedGain="0", lcKeepRight="0")
        if not native_lane_change:
            attributes.update(lcStrategic="-1", lcCooperative="-1")
        ET.SubElement(routes, "vType", attributes)
        vehicle = ET.SubElement(routes, "vehicle", id="probe", type="native", depart="0", departPos="0", departSpeed="0",
                                departLane=str(edge_lanes[0][1]), arrivalLane=str(edge_lanes[-1][1]))
        ET.SubElement(vehicle, "route", edges=" ".join(pair[0] for pair in edge_lanes))
        route_file = directory / "route.rou.xml"
        ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
        command = [str(sumo_binary), "--net-file", str(network), "--route-files", str(route_file), "--begin", "0", "--end", "600",
                   "--step-length", "0.1", "--seed", "104", "--precision", "6", "--time-to-teleport", "-1",
                   "--collision.check-junctions", "true", "--no-step-log", "true"]
        for name, option in (("fcd.xml", "--fcd-output"), ("tripinfo.xml", "--tripinfo-output"),
                             ("summary.xml", "--summary-output"), ("lanechanges.xml", "--lanechange-output")):
            command += [option, str(directory / name)]
        outcome = run_command(command, cwd=directory, timeout_seconds=60)
        (directory / "sumo.log").write_text(outcome.stdout + outcome.stderr, encoding="utf-8")
        visits, external, changes, summary, trip, error = [], [], [], {}, None, None
        try:
            for step in ET.parse(directory / "fcd.xml").getroot():
                for car in step:
                    if car.get("id") != "probe":
                        continue
                    lane = car.get("lane", "")
                    if not visits or visits[-1]["lane"] != lane:
                        visits.append(dict(lane=lane, first_time=float(step.get("time")), last_time=float(step.get("time"))))
                    else:
                        visits[-1]["last_time"] = float(step.get("time"))
                    if not lane.startswith(":") and (not external or external[-1] != lane):
                        external.append(lane)
            states = ET.parse(directory / "summary.xml").getroot()
            summary = dict(states[-1].attrib) if len(states) else {}
            trips = ET.parse(directory / "tripinfo.xml").getroot().findall("tripinfo")
            trip = dict(trips[0].attrib) if len(trips) == 1 else None
            changes = [dict(c.attrib) for c in ET.parse(directory / "lanechanges.xml").getroot().findall("change") if c.get("id") == "probe"]
        except (OSError, ET.ParseError, ValueError) as exc:
            error = str(exc)
        completed = (outcome.returncode == 0 and trip is not None and trip.get("id") == "probe" and trip.get("vaporized") == ""
                     and summary.get("arrived") == "1" and summary.get("running") == "0"
                     and summary.get("collisions") == "0" and summary.get("teleports") == "0"
                     and trip.get("departLane") == lane_id(edge_lanes[0]) and trip.get("arrivalLane") == lane_id(edge_lanes[-1]))
        return dict(executed=True, vehicle_class=mode, requested_edge_lanes=edge_lanes, summary=summary, tripinfo=trip,
                    completed_without_collision_or_teleport=completed, external_lane_sequence=external,
                    fcd_lane_visits=visits, observed_lane_changes=changes, parse_error=error, command=outcome.to_dict(),
                    artifacts={f.name: pin(f, generated=True) for f in directory.iterdir() if f.is_file()})
    path_probes, entry_probes = [], []
    for run, path, structural, modes in tasks:
        record = dict(run_id=run["id"], lane_key=path["lane_key"], full_run=path.get("full_run", False),
                      structural_connections=structural, expected_external_lanes=[lane_id(p) for p in path["edge_lanes"]],
                      status="review_required", executed=False)
        if modes and structural["status"] == "pass":
            record.update(simulate(path["edge_lanes"], next(mode for mode in _MODES if mode in modes)))
            passed = (record["completed_without_collision_or_teleport"] and record["external_lane_sequence"] == record["expected_external_lanes"]
                      and not record["observed_lane_changes"])
            record.update(status="pass" if passed else "review_required",
                          reason="external_lane_sequence_and_arrival_observed" if passed else "external_lane_sequence_or_completion_not_proved")
        else:
            record["reason"] = "no_common_permitted_probe_class_or_structural_chain"
        path_probes.append(record)
        introduced = [r for r in run.get("introduced_lanes", []) if r["lane_key"] == path["lane_key"]]
        if not introduced:
            continue
        segments = run.get("segments", [])
        first = next((i for i, segment in enumerate(segments) if segment["edge_id"] == path["edge_lanes"][0][0]), None)
        entry = dict(run_id=run["id"], lane_key=path["lane_key"], status="needs_entry_evidence", executed=False,
                     reason="no_unique_compatible_previous_carrier", direct_carrier_to_new_lane_connection=False)
        candidates = []
        if first is not None and first > 0:
            previous, current = segments[first - 1], segments[first]
            for index_old, key in enumerate(previous["lane_keys"]):
                if key not in current["lane_keys"]:
                    continue
                index_new = current["lane_keys"].index(key)
                carrier = [[previous["edge_id"], index_old], [current["edge_id"], index_new]]
                carrier_structure, carrier_modes = structure(carrier)
                if carrier_structure["status"] == "pass" and carrier_modes & modes:
                    candidates.append((key, carrier, carrier_modes & modes))
        entry["compatible_carrier_count"] = len(candidates)
        if len(candidates) == 1:
            key, carrier, permitted = candidates[0]
            direct = (carrier[0][0], carrier[0][1], path["edge_lanes"][0][0], path["edge_lanes"][0][1]) in pairs
            entry.update(carrier_lane_key=key, direct_carrier_to_new_lane_connection=direct)
            if direct:
                entry["reason"] = "direct_entry_connection_cannot_prove_native_lane_change"
            else:
                prefixes = {}
                for earlier in run["lane_paths"]:
                    route = earlier["edge_lanes"]
                    if route[0][0] != segments[0]["edge_id"] or carrier[0] not in route:
                        continue
                    prefix = route[:route.index(carrier[0]) + 1]
                    checked, prefix_modes = structure(prefix)
                    if checked["status"] == "pass" and permitted & prefix_modes:
                        prefixes[tuple(map(tuple, prefix))] = permitted & prefix_modes
                entry["compatible_road_entry_prefix_count"] = len(prefixes)
                if len(prefixes) != 1:
                    entry["reason"] = "no_unique_compatible_road_entry_prefix"
                else:
                    prefix, prefix_modes = next(iter(prefixes.items()))
                    prefix = [list(pair) for pair in prefix]
                    entry["road_entry_prefix"] = prefix
                    entry.update(simulate([*prefix, *path["edge_lanes"]], next(mode for mode in _MODES if mode in prefix_modes), native_lane_change=True))
                    carrier_ids = {f"{s['edge_id']}_{s['lane_keys'].index(key)}" for s in segments if key in s["lane_keys"]}
                    target_ids = set(record["expected_external_lanes"])
                    observed = [change for change in entry["observed_lane_changes"] if change.get("from") in carrier_ids and change.get("to") in target_ids]
                    verified = (entry["completed_without_collision_or_teleport"] and entry["external_lane_sequence"][:len(prefix)] == [lane_id(p) for p in prefix]
                                and any(change["to"] in entry["external_lane_sequence"] for change in observed))
                    entry.update(status="pass" if verified else "needs_entry_evidence", native_entry_lane_changes=observed,
                                 reason="road_entry_prefix_native_lane_change_and_arrival_observed" if verified else "native_entry_not_observed")
        entry_probes.append(entry)
    unchanged = all(Path(path).is_file() and file_sha256(Path(path)) == digest for path, digest in identities.items())
    outputs_unchanged = all(Path(path).is_file() and file_sha256(Path(path)) == digest for path, digest in generated_artifacts.items())
    passed = sum(row["status"] == "pass" for row in path_probes)
    needed = sum(row["status"] != "pass" for row in entry_probes)
    status = "blocked" if not unchanged or not outputs_unchanged else "pass" if producer is not None and passed == len(path_probes) and not needed and continuity.get("status") == "pass" else "review_required"
    report = dict(schema="torii.continuous-lane-probes/v1", status=status, network=network_identity, continuity=continuity_identity,
                  producer_manifest=producer, inputs_unchanged=unchanged, generated_artifacts_unchanged=outputs_unchanged,
                  path_probes=path_probes, entry_probes=entry_probes,
                  lane_paths_total=len(path_probes), lane_paths_passed=passed, needs_entry_evidence=needed,
                  settings=dict(one_vehicle_per_run=True, step_length_s=0.1, seed=104, maximum_speed_m_s=1, horizon_s=600),
                  claim_boundary="Native default passenger, bus, or bicycle models check topology only. They are not manufacturer swept-body models. "
                                 "External lane sequences are observed in FCD; internal via chains are checked structurally and are not claimed as sampled when too short. "
                                 "Direct insertion on partial paths does not prove their entry. Native lane-change entry requires a unique permitted prefix from the declared road start.")
    report_file, manifest_file = destination / "summary.json", destination / "manifest.json"
    report.update(report_file=str(report_file), manifest_file=str(manifest_file))
    write_json_atomic(report_file, report, ensure_ascii=False)
    write_json_atomic(manifest_file, dict(schema="torii.continuous-lane-probes-manifest/v1", status=status,
        inputs=[dict(path=p, sha256=d) for p, d in identities.items()],
        generated_artifacts=[dict(path=p, sha256=d) for p, d in generated_artifacts.items()],
        report=dict(path=str(report_file), sha256=file_sha256(report_file))), ensure_ascii=False)
    return report
