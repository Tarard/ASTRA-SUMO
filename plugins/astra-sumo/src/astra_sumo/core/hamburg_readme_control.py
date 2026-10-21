"""Install the declared fixed-control assumptions for the README Hamburg corridor."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .network_link_semantics import _capture
from .ocit_c import _child, _descendants, _normalize_group_label, _text, _vt_references
from .ocit_c_programs import read_ocit_fixed_program


def embedded_group_bridge(root):
    """Use embedded lane identities and vt references; standalone MAP group numbers are separate."""
    names = {}
    for group in _descendants(root, "Signalgruppe"):
        name = _normalize_group_label(_text(_child(group, "BezeichnungKurz")))
        if name:
            vt = _text(_child(group, "OCITOutstationNr"))
            if vt in names and names[vt] != name:
                raise ValueError("The source assigns one vt to different signal groups.")
            names[vt] = name
    bridge = defaultdict(set)
    for movement in _descendants(root, "TrafficStreamConfigData"):
        groups = _child(movement, "signalGroups")
        key = tuple(_text(_child(movement, tag)) for tag in ("intersectionPart", "refLaneId", "refConnectTo"))
        refs = set(_vt_references(_child(groups, "primary"))) | set(_vt_references(_child(groups, "secondary")))
        if refs - names.keys():
            raise ValueError("An embedded movement references an undefined vt.")
        bridge[key].update(names[vt] for vt in refs)
    return bridge


def group_state(group, time_s):
    if group["status"] != "measured":
        if set(group["allowed_image_securing_states"]) == {"00"}:
            return "O"
        raise ValueError(f"The source group timeline is unresolved: {group['group_id']}.")
    span = next(row for row in group["intervals"] if row["begin_s"] <= time_s < row["end_s"])
    image = span["signal_image"]
    if image in {"00", "0F", "0C"}:
        return {"00": "O", "0F": "u", "0C": "y"}[image]
    if span["securing_states"] == ["release"]:
        return "G"
    if span["securing_states"] == ["stop"]:
        return "r"
    raise ValueError("A bound signal image has ambiguous securing states.")


def intersect_group_states(states):
    states = set(states)
    if states <= {"G", "O"}:
        return "G" if "G" in states else "O"
    if "r" in states or {"u", "y"} <= states:
        return "r"
    return "u" if "u" in states else "y"


def _key(connection):
    return (
        connection.get("from"),
        int(connection.get("fromLane")),
        connection.get("to"),
        int(connection.get("toLane")),
    )


def build_hamburg_readme_control(*, request_file, output_dir, sumo_binary):
    request_file = Path(request_file).resolve(strict=True)
    request = json.loads(request_file.read_bytes())
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new control directory.")
    source_paths = {key: (request_file.parent / value).resolve(strict=True) for key, value in request["inputs"].items()}
    if any(p.is_relative_to(output) for p in source_paths.values()):
        raise ValueError("Keep control inputs separate from the output directory.")
    source_hashes = {key: file_sha256(value) for key, value in source_paths.items()}
    inputs = output / "inputs"
    inputs.mkdir(parents=True)
    for name, path in source_paths.items():
        content = path.read_bytes()
        if path.suffix == ".gz":
            content = gzip.decompress(content)
        (inputs / f"{name}.{'json' if name == 'candidate_manifest' else 'xml'}").write_bytes(content)
    network = inputs / "network.xml"
    root = ET.parse(network).getroot()
    audit = json.loads(
        gzip.decompress(source_paths["candidate_manifest"].read_bytes())
        if source_paths["candidate_manifest"].suffix == ".gz"
        else source_paths["candidate_manifest"].read_bytes()
    )["official_connection_audit"]
    native = _capture(network, str(sumo_binary), "m2_source")
    if not native["native_version"][1].endswith("1.27.1"):
        raise ValueError("Use SUMO 1.27.1 for control installation.")
    write_json_atomic(output / "native-network-semantics.json", native)
    programs, bridges, roots = {}, {}, {}
    refs = {"104": "5", "118": "5", "119": "1", "200": "5", "535": "1"}
    for node, reference in refs.items():
        source = inputs / f"ocit_{node}.xml"
        programs[node] = read_ocit_fixed_program(source, reference)
        roots[node] = ET.parse(source).getroot()
        bridges[node] = embedded_group_bridge(roots[node])
        if programs[node]["cycle_s"] != 90 or programs[node]["signal_times_offset_native"] not in {"", "0"}:
            raise ValueError("Reassess the declared 90-second midnight alignment for this source.")
        write_json_atomic(output / f"source-program-{node}.json", programs[node])
    groups119 = {g["group_id"]: g for g in programs["119"]["groups"]}
    boundaries119 = {0.0, 90.0} | {
        x for g in groups119.values() for row in g["intervals"] for x in (row["begin_s"], row["end_s"])
    }
    assert all(
        group_state(groups119["K1"], time) != "G" or group_state(groups119["K7"], time) in "GO"
        for time in boundaries119
        if time < 90
    ), "119 K1 release must lie inside K7 release."
    bound = defaultdict(set)
    evidence = defaultdict(list)
    for row in audit["required"]:
        key = tuple(row["sumo_connection"])
        lane_pair = tuple(row[k] for k in ("intersection_part", "ingress_lane_id", "egress_lane_id"))
        names = bridges[row["node_id"]].get(lane_pair, set())
        bound[key].update((row["node_id"], name) for name in names)
        evidence[key].append({"node_id": row["node_id"], "movement_id": row["movement_id"], "groups": sorted(names)})
    physical = {_key(c): c for c in root.findall("connection") if c.get("tl")}
    by_tls = defaultdict(list)
    for key, connection in physical.items():
        tls = connection.get("tl")
        origin = (
            "embedded_official_vt"
            if bound[key]
            else "declared_uncontrolled"
            if tls.startswith("LSA")
            else "declared_z15_alternative"
            if tls == "6390683126"
            else "retained_static_placeholder"
        )
        if tls.startswith("LSA") and not bound[key]:
            siblings = {
                tuple(sorted(value))
                for other, value in bound.items()
                if other[0:2] == key[0:2] and other[2] == key[2] and value
            }
            if len(siblings) == 1:
                bound[key].update(next(iter(siblings)))
                origin = "inherited_same_source_lane_target_road"
            elif len(siblings) > 1:
                raise ValueError("Supplementary movement has inconsistent sibling group expressions.")
        by_tls[tls].append(
            {
                "physical_key": list(key),
                "link_index": int(connection.get("linkIndex")),
                "via": connection.get("via", ""),
                "groups": sorted(bound[key]),
                "origin": origin,
                "official_movements": evidence[key],
            }
        )
    write_json_atomic(
        output / "link-bindings.json", {"rows": [{"tls": tls, **row} for tls, rows in by_tls.items() for row in rows]}
    )
    foes = {}
    for node in native["junctions"].values():
        foes.update(node["internal_foes"])
    outputs = {}
    for alternative in "ABC":
        additional = ET.Element("additional")
        for original in root.findall("tlLogic"):
            tls = original.get("id")
            if not tls.startswith("LSA") and tls != "6390683126":
                logic = deepcopy(original)
                logic.set("type", "static")
                logic.set("programID", f"m2_{alternative}")
                for phase in logic.findall("phase"):
                    phase.set("state", phase.get("state")[: max(row["link_index"] for row in by_tls[tls]) + 1])
                    for attr in ("minDur", "maxDur", "earliestEnd", "latestEnd", "next"):
                        phase.attrib.pop(attr, None)
                additional.append(logic)
                continue
            node = tls.split("_")[0].removeprefix("LSA") if tls.startswith("LSA") else "119"
            program = programs[node]
            definitions = {g["group_id"]: g for g in program["groups"]}
            rows = by_tls[tls]
            schedule = {r["link_index"]: set() for r in rows}
            for row in rows:
                schedule[row["link_index"]].update(g for n, g in row["groups"] if n == node)
            if tls == "6390683126":
                schedule = {index: {"K9" if alternative == "A" else "K8"} for index in schedule}
            boundaries = sorted(
                {0.0, 90.0}
                | {
                    x
                    for names in schedule.values()
                    for name in names
                    for span in definitions[name]["intervals"]
                    for x in (span["begin_s"], span["end_s"])
                }
            )
            logic = ET.SubElement(
                additional, "tlLogic", id=tls, type="static", programID=f"m2_{alternative}", offset="0"
            )
            ET.SubElement(logic, "param", key="source_program_reference", value=refs[node])
            for begin, end in zip(boundaries, boundaries[1:]):
                chars = ["O"] * len(original.find("phase").get("state"))
                for index, names in schedule.items():
                    chars[index] = (
                        intersect_group_states(group_state(definitions[name], begin) for name in names)
                        if names
                        else "O"
                    )
                if tls == "6390683126" and alternative == "C":
                    chars = ["r"] * len(chars)
                for row in rows:
                    index = row["link_index"]
                    if chars[index] == "G" and any(
                        chars[other["link_index"]] in "Gg"
                        and (other["via"] in foes.get(row["via"], []) or row["via"] in foes.get(other["via"], []))
                        for other in rows
                    ):
                        chars[index] = "g"
                state = "".join(chars)
                previous = logic.findall("phase")[-1:]
                if previous and previous[0].get("state") == state:
                    previous[0].set("duration", str(float(previous[0].get("duration")) + end - begin))
                else:
                    ET.SubElement(logic, "phase", duration=str(end - begin), state=state)
        path = output / f"control-{alternative}.add.xml"
        ET.ElementTree(additional).write(path, encoding="utf-8", xml_declaration=True)
        outputs[alternative] = {"path": str(path), "sha256": file_sha256(path)}
    report = {
        "schema": "astra.hamburg-readme-control/v1",
        "status": "measured",
        "native_verified": False,
        "formal_result": False,
        "exact_count_fit": False,
        "source_hashes": source_hashes,
        "outputs": outputs,
        "binding_origins": dict(Counter(row["origin"] for rows in by_tls.values() for row in rows)),
        "original_nonofficial_actuated_controllers": 59,
        "fixed_placeholders_after_z15_override": 58,
        "clock_assumption": "Simulation 0 is 2026-10-19 04:30 UTC / 06:30 CEST. Source cycle zero is assumed at local midnight; all selected cycles and offsets are 90 s and 0. 535 uses SP1 from simulation 0; earlier warm-up requires SP10.",
        "z15_alternatives": {"A": "declared K9", "B": "declared K8, dark release O", "C": "declared blocked"},
        "unbound_official_links": "Declared O; no official group correspondence established.",
        "omitted_dark_only_groups": "Declared O when only native image 00 is defined, even if the program omits the row.",
        "placeholder_phase_preservation": "Retain every controlled state and phase duration. Remove unused trailing state characters and actuated duration bounds in the new static programs.",
        "inputs_unchanged": all(file_sha256(path) == source_hashes[key] for key, path in source_paths.items()),
        "claim_boundary": "Fixed-control assumptions installed in additional files. OCIT schedule equivalence, clock alignment, dark-only handling, and supplementary bindings are declared; field timing and traffic validity are not established.",
    }
    write_json_atomic(output / "report.json", report)
    return report
