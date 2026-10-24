"""Materialize the declared A-prime motor-control corrections for the first validity run."""

from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_control_inventory import _json
from .hamburg_readme_control import group_state


def fallback_decision(row, bindings):
    key = tuple(row["physical_key"])
    if key in {("186821027#0", 4, "186821036#1", 1), ("139083723#0", 2, "139056860#1", 1)}:
        return "declared_red", None
    if not row["pool_lane_feasible_route_ids"]:
        return "unused_dark_priority", None
    if row["same_approach_proximity_without_foe"]:
        return ("declared_red" if row["official_other_lane_same_road_pair"] else "pending_crossing"), None
    official = [r for r in bindings if r["tls"] == row["tls"] and r["origin"] == "embedded_official_vt" and r["groups"]]
    lane_groups = {tuple(g) for r in official if r["physical_key"][:2] == list(key[:2]) for g in r["groups"]}
    if len(lane_groups) == 1:
        return "inherited_lane_head", next(iter(lane_groups))
    if len(lane_groups) > 1:
        return "pending_multiple_lane_groups", None
    if not any(r["physical_key"][0] == key[0] for r in official):
        return "declared_unsignalized_yield", None
    return "pending_ungrouped_lane_on_signalized_approach", None


def materialize_hamburg_a_prime(*, control_dir, inventory_dir, output_dir):
    control_dir, inventory_dir = Path(control_dir).resolve(strict=True), Path(inventory_dir).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new A-prime directory.")
    root = ET.parse(control_dir / "control-A.add.xml").getroot()
    report = _json(inventory_dir / "report.json")
    if report["control_sha256"] != file_sha256(control_dir / "control-A.add.xml"):
        raise ValueError("The dark-link inventory belongs to another control file.")
    bindings = _json(control_dir / "link-bindings.json")["rows"]
    rows = _json(inventory_dir / "motor-dark-links.json")["rows"]
    semantics = _json(control_dir / "native-network-semantics.json")
    foes = {
        lane: set(values) for node in semantics["junctions"].values() for lane, values in node["internal_foes"].items()
    }
    decisions, by_key = [], {}
    for row in rows:
        if row["origin"] != "declared_uncontrolled":
            continue
        decision, group = fallback_decision(row, bindings)
        record = {
            "physical_key": row["physical_key"],
            "tls": row["tls"],
            "link_index": row["link_index"],
            "decision": decision,
            "group": group,
            "intended_vehicle_passages": row["intended_vehicle_passages"],
            "selected_lane_feasible_vehicles": row["selected_lane_feasible_vehicles"],
        }
        decisions.append(record)
        by_key[tuple(row["physical_key"])] = record
    output.mkdir(parents=True)
    (output / "inputs").mkdir()
    for name in ("network.xml", "ocit_104.xml", "ocit_118.xml", "ocit_119.xml", "ocit_200.xml", "ocit_535.xml"):
        (output / "inputs" / name).write_bytes((control_dir / "inputs" / name).read_bytes())
    definitions = {}
    for node in ("104", "118", "119", "200", "535"):
        program = _json(control_dir / f"source-program-{node}.json")
        write_json_atomic(output / f"source-program-{node}.json", program)
        definitions[node] = {g["group_id"]: g for g in program["groups"]}
    write_json_atomic(output / "native-network-semantics.json", semantics)
    for row in bindings:
        record = by_key.get(tuple(row["physical_key"]))
        if record and record["decision"] == "inherited_lane_head":
            row["groups"] = [list(record["group"])]
            row["origin"] = "inherited_lane_head"
    dark_translations, mutual_yields = [], []
    for logic in root.findall("tlLogic"):
        tls = logic.get("id")
        logic.set("programID", "m3_Aprime")
        if not tls.startswith("LSA"):
            continue
        members = [r for r in bindings if r["tls"] == tls]
        clock = 0.0
        for phase in logic.findall("phase"):
            chars = list(phase.get("state"))
            for row in members:
                record = by_key.get(tuple(row["physical_key"]))
                if not record:
                    continue
                index = row["link_index"]
                if record["decision"] == "declared_red":
                    chars[index] = "r"
                elif record["decision"] == "declared_unsignalized_yield":
                    chars[index] = "o"
                elif record["decision"] == "inherited_lane_head":
                    node, group = record["group"]
                    chars[index] = group_state(definitions[node][group], clock)
            before = list(chars)
            for row in members:
                index = row["link_index"]
                live_foes = [
                    other
                    for other in members
                    if other["via"] in foes.get(row["via"], set()) or row["via"] in foes.get(other["via"], set())
                ]
                if before[index] in "Gg" and any(before[other["link_index"]] in "Gg" for other in live_foes):
                    chars[index] = "g"
                elif (
                    before[index] == "O"
                    and row["groups"]
                    and any(before[other["link_index"]] in "GgO" for other in live_foes)
                ):
                    chars[index] = "o"
                    dark_translations.append(
                        {
                            "tls": tls,
                            "physical_key": row["physical_key"],
                            "groups": row["groups"],
                            "begin_s": clock,
                            "end_s": clock + float(phase.get("duration")),
                            "foes": [
                                other["physical_key"] for other in live_foes if before[other["link_index"]] in "GgO"
                            ],
                        }
                    )
            for i, row in enumerate(members):
                if chars[row["link_index"]] != "g":
                    continue
                for other in members[i + 1 :]:
                    if chars[other["link_index"]] == "g" and (
                        other["via"] in foes.get(row["via"], set()) or row["via"] in foes.get(other["via"], set())
                    ):
                        mutual_yields.append(
                            {
                                "tls": tls,
                                "first": row["physical_key"],
                                "second": other["physical_key"],
                                "begin_s": clock,
                                "end_s": clock + float(phase.get("duration")),
                                "involves_lane_head_inheritance": "inherited_lane_head"
                                in {row["origin"], other["origin"]},
                            }
                        )
            phase.set("state", "".join(chars))
            clock += float(phase.get("duration"))
    ET.ElementTree(root).write(output / "control-A.add.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(output / "link-bindings.json", {"rows": bindings})
    write_json_atomic(
        output / "decisions.json",
        {"rows": decisions, "bound_dark_translations": dark_translations, "mutual_green_yield_pairs": mutual_yields},
    )
    result = {
        "schema": "astra.hamburg-a-prime/v1",
        "status": "measured",
        "formal_result": False,
        "network_sha256": file_sha256(output / "inputs/network.xml"),
        "control_sha256": file_sha256(output / "control-A.add.xml"),
        "decision_counts": dict(Counter(r["decision"] for r in decisions)),
        "pending_decisions": [r for r in decisions if r["decision"].startswith("pending")],
        "mutual_yield_intervals_involving_inheritance": sum(r["involves_lane_head_inheritance"] for r in mutual_yields),
        "traffic_simulation_performed": False,
        "ready_for_traffic": not any(r["decision"].startswith("pending") for r in decisions)
        and not any(r["involves_lane_head_inheritance"] for r in mutual_yields),
        "claim_boundary": "Declared signal-state corrections only; lane paths, turn connections and road permissions remain unchanged. Pending source-lane groups or inherited mutual green yields require Claude assessment before the first full-window run.",
    }
    write_json_atomic(output / "report.json", result)
    return result
