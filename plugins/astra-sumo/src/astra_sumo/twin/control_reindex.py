"""Re-index the executed control file to a candidate net's linkIndex layout (fragility fixes, M4).

A typed connection edit materializes through netconvert, which renumbers the edited controller's link indices and
rewrites the net's embedded programs consistently. The executed control (``control.add.xml``, e.g. program
``readme_fixed_A``) is a separate file and would no longer line up with the links: a silent wrong-signal run. This
module derives the old -> new link map from the connections themselves (one ``from_lane -> to_lane`` connection per
link index on both nets) and rewrites every ``tlLogic`` state string of the control file to the candidate's layout:

- a retained link keeps its state column at its new index;
- a removed link's column is dropped (it must be declared removed);
- an added link copies the column of a declared sibling at the same controller (``copy_rule`` ``verbatim``), or the
  same column with a priority green ``G`` written as ``g`` in every phase where the candidate's request matrix makes
  the added link yield to a link that is green then (``minor_where_yielding``). A verbatim copy that would give a
  yielding link priority green is ambiguous and refused.

Every other case is refused rather than guessed: a link the declaration does not explain, a split or merged link, a
second connection on one index, a ``linkIndex2``, a controller missing from either net, unbound trailing states on a
changed controller, or a candidate embedded program whose retained columns disagree with the same map applied to the
parent's embedded program (the map is then not the one netconvert applied). Controllers whose links are unchanged are
copied byte for byte. The rewrite is textual: only the changed ``state`` attributes move, so the file stays
byte-identical elsewhere, and the result is re-parsed and compared with the computed states.

A fragility-fix goal's declared signal targets must be exactly this map (``crosscheck_declared``): every renumbered,
removed and added link the goal declares is the one netconvert's layout shows, and nothing else changes.

``sumo_load`` and ``load_check_report`` load the parent and the candidate net with their controls (and further
additional files) in SUMO for one step and compare the warnings; every additional file is first copied into the load
directory, and one whose output path is absolute or leaves its own directory is refused, so a load check never
writes beside an input or into another store. ``route_continuity`` checks every demand route's consecutive edges on
each net. A load check is no Calibrate run.

Model-relative consistency only: the re-indexed control executes the same signal plan on the candidate's links; it is
no evidence that the plan or the edit is right in the field.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = "astra.twin-control-reindex/v1"
GREEN = frozenset("Gg")
OPEN = frozenset("GgsOo")  # states under which a link may be passed (green, stop sign, off)
COPY_RULES = ("verbatim", "minor_where_yielding")
RULE = ("retained links keep their state column at the candidate index; removed links (declared) are dropped; added "
        "links (declared) copy a declared sibling's column at the same controller, verbatim or with G written as g "
        "where the candidate's request matrix makes the added link yield to a link green in that phase; anything else "
        "is refused")
_TL_BLOCK = re.compile(r"<tlLogic\b(?P<head>[^>]*)>(?P<body>.*?)</tlLogic>", re.S)
_ID = re.compile(r'\bid="([^"]*)"')
_PROGRAM = re.compile(r'\bprogramID="([^"]*)"')
_STATE = re.compile(r'(<phase\b[^>]*?\bstate=")([^"]*)(")')

Key = tuple[str, str, str, str]


class ReindexError(ValueError):
    """A case the re-index refuses instead of guessing."""


def lane_pair_key(pair) -> Key:
    """``(from edge, from lane, to edge, to lane)`` of a ``[from_lane_id, to_lane_id]`` pair."""
    (start, _, start_lane), (end, _, end_lane) = (str(pair[0]).rpartition("_"), str(pair[1]).rpartition("_"))
    return start, start_lane, end, end_lane


def _key(connection: ET.Element) -> Key:
    return (connection.get("from"), connection.get("fromLane"), connection.get("to"), connection.get("toLane"))


def link_layout(root: ET.Element) -> dict[str, dict[int, Key]]:
    """``{tl: {link index: connection key}}`` of one net; a second connection on one index or a ``linkIndex2`` is
    refused (the map would be ambiguous)."""
    layout: dict[str, dict[int, Key]] = defaultdict(dict)
    for connection in root.iter("connection"):
        tl = connection.get("tl")
        if not tl:
            continue
        if connection.get("linkIndex2") is not None:
            raise ReindexError(f"{tl}: linkIndex2 on {_key(connection)} is not supported")
        index = int(connection.get("linkIndex"))
        if index in layout[tl]:
            raise ReindexError(f"{tl}: link index {index} carries two connections")
        layout[tl][index] = _key(connection)
    return dict(layout)


def _programs(root: ET.Element) -> dict[tuple[str, str], list[str]]:
    return {(row.get("id"), row.get("programID")): [phase.get("state") for phase in row.findall("phase")]
            for row in root.iter("tlLogic")}


def link_map(parent: Mapping[int, Key], candidate: Mapping[int, Key], *, tl: str, removed: set[Key],
             added: Mapping[Key, Mapping]) -> dict:
    """The old -> new link map of one controller; ``removed`` and ``added`` are this controller's declarations."""
    old_by_key = {key: index for index, key in parent.items()}
    new_by_key = {key: index for index, key in candidate.items()}
    gone = set(old_by_key) - set(new_by_key)
    new = set(new_by_key) - set(old_by_key)
    if gone != set(removed):
        raise ReindexError(f"{tl}: removed links {sorted(gone)} but the declaration removes {sorted(removed)}")
    if new != set(added):
        raise ReindexError(f"{tl}: added links {sorted(new)} but the declaration adds {sorted(added)}")
    mapping = {old_by_key[key]: new_by_key[key] for key in old_by_key if key in new_by_key}
    additions = {}
    for key, row in added.items():
        sibling = lane_pair_key(row["sibling_pair"])
        if sibling not in old_by_key or sibling not in new_by_key:
            raise ReindexError(f"{tl}: the sibling {sibling} of added link {key} is not a retained link here")
        if row.get("copy_rule") not in COPY_RULES:
            raise ReindexError(f"{tl}: unknown copy rule {row.get('copy_rule')!r}")
        additions[new_by_key[key]] = {"key": list(key), "sibling": list(sibling), "sibling_old": old_by_key[sibling],
                                      "copy_rule": row["copy_rule"]}
    changed = bool(gone or new) or any(old != index for old, index in mapping.items())
    if changed:
        for name, layout in (("parent", parent), ("candidate", candidate)):
            if sorted(layout) != list(range(len(layout))):
                raise ReindexError(f"{tl}: the {name} link indices are not contiguous from 0")
    return {"tl": tl, "changed": changed, "map": dict(sorted(mapping.items())),
            "removed": sorted(old_by_key[key] for key in gone), "added": dict(sorted(additions.items())),
            "n_parent": len(parent), "n_candidate": len(candidate)}


def _request_rows(root: ET.Element, tl: str, candidate: Mapping[int, Key]) -> dict[int, set[int]]:
    """For each link index of ``tl`` on the candidate, the link indices of the same controller it must yield to
    (the junction's ``response`` bits, located through each connection's via lane in ``intLanes``)."""
    vias, junctions = {}, set()
    for connection in root.iter("connection"):
        if connection.get("tl") == tl and connection.get("via"):
            vias[int(connection.get("linkIndex"))] = connection.get("via")
    yields: dict[int, set[int]] = {}
    for junction in root.iter("junction"):
        if junction.get("type") == "internal":
            continue
        lanes = (junction.get("intLanes") or "").split()
        positions = {lane: position for position, lane in enumerate(lanes)}
        local = {index: positions[via] for index, via in vias.items() if via in positions}
        if not local:
            continue
        junctions.add(junction.get("id"))
        requests = {int(row.get("index")): row.get("response") for row in junction.findall("request")}
        by_request = {request: index for index, request in local.items()}
        for index, request in local.items():
            response = requests.get(request)
            if response is None:
                raise ReindexError(f"{tl}: link {index} has no request row at junction {junction.get('id')}")
            size = len(response)
            bits = {size - 1 - position for position, bit in enumerate(response) if bit == "1"}
            if bits - set(by_request):
                raise ReindexError(f"{tl}: link {index} yields to a link of another controller or an unlocated link")
            yields[index] = {by_request[bit] for bit in bits}
    missing = sorted(set(candidate) - set(yields))
    if missing:
        raise ReindexError(f"{tl}: links {missing} have no locatable request row (cont links are not supported)")
    return yields


def reindex_states(states: list[str], plan: Mapping, *, yields: Mapping[int, set[int]] | None = None) -> tuple[
        list[str], list[dict]]:
    """``states`` re-indexed by one controller's ``plan`` (``link_map``), with the per-phase record of every added
    link's copied character; a state string of another length than the parent's links is refused."""
    if not plan["changed"]:
        return list(states), []
    inverse = {new: old for old, new in plan["map"].items()}
    result, records = [], []
    for phase, state in enumerate(states):
        if len(state) != plan["n_parent"]:
            raise ReindexError(f"{plan['tl']}: phase {phase} has {len(state)} states for {plan['n_parent']} links")
        row = [""] * plan["n_candidate"]
        for new, old in inverse.items():
            row[new] = state[old]
        for new, added in plan["added"].items():
            row[new] = state[added["sibling_old"]]
        for new, added in plan["added"].items():
            copied = row[new]
            foes = sorted(index for index in (yields or {}).get(new, set()) if row[index] in OPEN)
            demote = copied == "G" and bool(foes)
            if demote and added["copy_rule"] == "verbatim":
                raise ReindexError(f"{plan['tl']}: phase {phase}: a verbatim copy gives added link {new} priority green "
                                   f"while it must yield to green links {foes}; declare copy_rule "
                                   "minor_where_yielding or another sibling")
            if demote:
                row[new] = "g"
            records.append({"phase": phase, "link": new, "sibling_state": copied, "written": row[new],
                            "yields_to_open": foes})
        result.append("".join(row))
    return result, records


def _declarations(declaration: Mapping) -> tuple[dict[str, set[Key]], dict[str, dict[Key, Mapping]]]:
    removed: dict[str, set[Key]] = defaultdict(set)
    added: dict[str, dict[Key, Mapping]] = defaultdict(dict)
    for row in declaration.get("removed_links") or []:
        removed[row["tls_id"]].add(lane_pair_key(row["pair"]))
    for row in declaration.get("added_links") or []:
        added[row["tls_id"]][lane_pair_key(row["pair"])] = row
    return removed, added


def plan_reindex(parent_root: ET.Element, candidate_root: ET.Element, declaration: Mapping) -> dict[str, dict]:
    """Every controller's link map and, for a changed one, the yield rows of its added links; the parent's embedded
    programs re-indexed by that map must equal the candidate's on every retained column."""
    parent, candidate = link_layout(parent_root), link_layout(candidate_root)
    if set(parent) != set(candidate):
        raise ReindexError(f"controllers differ: {sorted(set(parent) ^ set(candidate))}")
    removed, added = _declarations(declaration)
    unknown = sorted((set(removed) | set(added)) - set(parent))
    if unknown:
        raise ReindexError(f"declared controllers are not in the nets: {unknown}")
    plans = {}
    before, after = _programs(parent_root), _programs(candidate_root)
    for tl in sorted(parent):
        plan = link_map(parent[tl], candidate[tl], tl=tl, removed=removed.get(tl, set()), added=added.get(tl, {}))
        if plan["changed"]:
            plan["yields"] = ({index: sorted(found) for index, found in
                               _request_rows(candidate_root, tl, candidate[tl]).items()} if plan["added"] else {})
            plan["embedded_crosscheck"] = _embedded_crosscheck(tl, plan, before, after)
        plans[tl] = plan
    return plans


def _embedded_crosscheck(tl: str, plan: Mapping, before: Mapping, after: Mapping) -> list[dict]:
    """Every embedded program of ``tl``: the parent's states moved by the map must equal the candidate's on every
    retained column (else the map is not the one netconvert applied); netconvert's own state for each added link is
    reported beside the sibling's."""
    checks = []
    for (program_tl, program), states in sorted(before.items()):
        if program_tl != tl:
            continue
        expected = after.get((tl, program))
        if expected is None or len(expected) != len(states):
            raise ReindexError(f"{tl}: embedded program {program} is missing or has other phases on the candidate")
        for old_state, new_state in zip(states, expected):
            if len(old_state) != plan["n_parent"] or len(new_state) != plan["n_candidate"] or any(
                    new_state[new] != old_state[old] for old, new in plan["map"].items()):
                raise ReindexError(f"{tl}: the candidate's embedded program {program} does not follow the derived "
                                   "link map; it is not the map netconvert applied")
        checks.append({"program": program, "retained_equal": True,
                       "added": {str(new): {"netconvert": "".join(row[new] for row in expected),
                                            "sibling": "".join(row[added["sibling_old"]] for row in states)}
                                 for new, added in plan["added"].items()}})
    return checks


def crosscheck_declared(plans: Mapping[str, Mapping], entries: Sequence[Mapping]) -> dict:
    """A fragility-fix goal's declared signal entries (``twin/declared_targets.py``) against the derived link maps:
    per controller, the declared renumbered ``old -> new`` pairs, removed and added indices must equal the map's own;
    a controller whose links are unchanged takes no declared signal entry. Refused otherwise."""
    declared: dict[str, dict] = defaultdict(lambda: {"renumbered": {}, "removed": set(), "added": set()})
    for entry in entries:
        if entry.get("kind") != "signal_group":
            continue
        old, new = entry.get("parent_expected") or {}, entry.get("expected") or {}
        row = declared[str((new or old).get("tls_id"))]
        if entry.get("reason") == "link_renumbered":
            row["renumbered"][int(old["link_index"])] = int(new["link_index"])
        elif entry.get("reason") == "link_removed":
            row["removed"].add(int(old["link_index"]))
        elif entry.get("reason") == "link_added":
            row["added"].add(int(new["link_index"]))
        else:
            raise ReindexError(f"{entry.get('fact')}: declared reason {entry.get('reason')} has no link-map meaning")
    checked = {}
    for tl in sorted(set(declared) | {tl for tl, plan in plans.items() if plan["changed"]}):
        plan = plans.get(tl)
        row = declared.get(tl, {"renumbered": {}, "removed": set(), "added": set()})
        if plan is None:
            raise ReindexError(f"{tl}: declared signal targets for a controller the nets do not have")
        found = {"renumbered": {old: new for old, new in plan["map"].items() if old != new},
                 "removed": set(plan["removed"]), "added": set(plan["added"])}
        if found != row:
            raise ReindexError(f"{tl}: the goal's declared signal targets are not the link map netconvert applied "
                               f"(declared {_jsonable(row)}, map {_jsonable(found)})")
        checked[tl] = {"renumbered": len(found["renumbered"]), "removed": sorted(found["removed"]),
                       "added": sorted(found["added"]), "equal": True}
    return {"rule": "the declared renumbered, removed and added links equal the derived link map per controller",
            "controllers": checked}


def _jsonable(row: Mapping) -> dict:
    return {key: (dict(sorted(value.items())) if isinstance(value, Mapping) else sorted(value))
            for key, value in row.items()}


def reindex_control(control: bytes, parent_root: ET.Element, candidate_root: ET.Element,
                    declaration: Mapping, *, entries: Sequence[Mapping] | None = None) -> tuple[bytes, dict]:
    """``(re-indexed control bytes, report)``: every tlLogic of ``control`` rewritten to the candidate's layout;
    ``entries`` (a fragility-fix goal's declared targets) are cross-checked against the link maps first."""
    plans = plan_reindex(parent_root, candidate_root, declaration)
    crosscheck = None if entries is None else crosscheck_declared(plans, entries)
    text = control.decode("utf-8")
    parsed = ET.fromstring(control)
    programs = [(row.get("id"), row.get("programID"), [phase.get("state") for phase in row.findall("phase")])
                for row in parsed.iter("tlLogic")]
    missing = sorted({tl for tl, _, _ in programs} - set(plans))
    if missing:
        raise ReindexError(f"control programs for controllers the nets do not have: {missing}")
    rows, computed = [], {}
    for tl, program, states in programs:
        plan = plans[tl]
        yields = {int(k): set(v) for k, v in plan.get("yields", {}).items()}
        new_states, records = reindex_states(states, plan, yields=yields)
        computed[(tl, program)] = new_states
        rows.append({"tl": tl, "program": program, "changed": plan["changed"], "phases": len(states),
                     "added_link_states": records})
    output, seen = [], 0
    position = 0
    for match in _TL_BLOCK.finditer(text):
        tl, program = _ID.search(match["head"])[1], (_PROGRAM.search(match["head"]) or [None, None])[1]
        output.append(text[position:match.start()])
        block = match.group(0)
        if plans[tl]["changed"]:
            states = iter(computed[(tl, program)])
            block = _STATE.sub(lambda found: f"{found[1]}{next(states)}{found[3]}", block)
        output.append(block)
        position = match.end()
        seen += 1
    output.append(text[position:])
    if seen != len(programs):
        raise ReindexError(f"found {seen} tlLogic blocks in the text but {len(programs)} parsed programs")
    data = "".join(output).encode("utf-8")
    check = ET.fromstring(data)
    rewritten = [(row.get("id"), row.get("programID"), [phase.get("state") for phase in row.findall("phase")])
                 for row in check.iter("tlLogic")]
    if rewritten != [(tl, program, computed[(tl, program)]) for tl, program, _ in programs]:
        raise ReindexError("the rewritten file does not hold the computed states")
    if _masked(parsed) != _masked(check):
        raise ReindexError("the rewrite changed something besides state attributes")
    changed = {tl: plan for tl, plan in plans.items() if plan["changed"]}
    report = {"schema": SCHEMA, "rule": RULE, "declaration": {key: declaration.get(key) or [] for key in
                                                             ("added_links", "removed_links")},
              "input_sha256": hashlib.sha256(control).hexdigest(), "output_sha256": hashlib.sha256(data).hexdigest(),
              "controllers": len(plans), "changed_controllers": sorted(changed),
              "plans": {tl: _plan_view(plan) for tl, plan in changed.items()},
              "programs": [row for row in rows if row["changed"]],
              "unchanged_programs": sum(not row["changed"] for row in rows),
              **({} if crosscheck is None else {"declared_crosscheck": crosscheck}),
              "claim": "Model-relative consistency: the same signal plan on the candidate's links; no field claim."}
    return data, report


def _plan_view(plan: Mapping) -> dict:
    return {"map": {str(old): new for old, new in plan["map"].items()},
            "renumbered": sum(old != new for old, new in plan["map"].items()), "removed": plan["removed"],
            "added": {str(new): row for new, row in plan["added"].items()},
            "yields": {str(index): rows for index, rows in plan.get("yields", {}).items()},
            "n_parent": plan["n_parent"], "n_candidate": plan["n_candidate"],
            "embedded_crosscheck": plan.get("embedded_crosscheck", [])}


def _masked(root: ET.Element) -> list:
    """Every element's tag, attributes (``state`` masked) and text, in document order."""
    return [(element.tag, sorted((key, "*" if key == "state" else value) for key, value in element.attrib.items()),
             (element.text or "").strip()) for element in root.iter()]



# ---- load check (a SUMO load of the candidate net with its executed control; never a Calibrate run) ----------------

# Attributes through which an additional file names a file SUMO writes (detectors, edge/lane data, probes, calibrators).
OUTPUT_ATTRIBUTES = ("file", "output")


def output_paths(data: bytes) -> list[str]:
    """Every output path an additional file names (``OUTPUT_ATTRIBUTES`` on any element)."""
    return sorted({element.get(name) for element in ET.fromstring(data).iter() for name in OUTPUT_ATTRIBUTES
                   if element.get(name)})


def contained_outputs(data: bytes, name: str) -> list[str]:
    """The output paths of one additional file, refused when one is absolute or leaves the file's own directory
    (SUMO resolves a relative output path against the additional file's directory)."""
    paths = output_paths(data)
    for value in paths:
        windows, posix = PureWindowsPath(value), PurePosixPath(value.replace(chr(92), "/"))
        if windows.is_absolute() or windows.drive or posix.is_absolute() or ".." in posix.parts:
            raise ReindexError(f"{name}: output path {value!r} is absolute or leaves its directory; a load check never "
                               "writes outside its own load directory")
    return paths


def stage_load_files(cwd: Path, files: Sequence[Path]) -> list[Path]:
    """Copy every additional file into ``cwd`` (refusing outputs that would leave it) and return the copies, so the
    relative outputs a load writes land in the load directory, never beside an input."""
    staged: list[Path] = []
    for path in files:
        data = Path(path).read_bytes()
        contained_outputs(data, Path(path).name)
        target = Path(cwd) / Path(path).name
        if target in staged:
            raise ReindexError(f"two additional files share the name {target.name}")
        if target.exists() and target.read_bytes() != data:
            raise ReindexError(f"{target} exists with other content")
        target.write_bytes(data)
        staged.append(target)
    return staged


def sumo_load(net: Path, additional: Sequence[Path], *, sumo_binary: str, cwd: Path, timeout_s: float = 600.0) -> dict:
    """Load ``net`` with ``additional`` files in SUMO for one step (``--begin 0 --end 1``, no demand) from ``cwd``;
    ``pass`` only with return code 0 and no ``Error`` line. Warnings are kept for the comparison with the parent. The
    additional files are loaded as copies inside ``cwd`` (``stage_load_files``), so their outputs stay there."""
    from ..core.command_runner import run_command

    Path(cwd).mkdir(parents=True, exist_ok=True)
    staged = stage_load_files(Path(cwd), additional)
    command = [sumo_binary, "-n", str(net), "--no-step-log", "true", "--duration-log.disable", "true",
               "--begin", "0", "--end", "1"]
    if staged:
        command += ["--additional-files", ",".join(path.name for path in staged)]
    started = time.perf_counter()
    result = run_command(command, cwd=Path(cwd), timeout_seconds=timeout_s)
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    errors = sorted({line.strip() for line in text.splitlines() if line.strip().startswith("Error")})
    warnings = sorted({line.strip() for line in text.splitlines() if line.strip().startswith("Warning")})
    status = "pass" if result.status == "pass" and result.returncode == 0 and not errors else "fail"
    return {"status": status, "returncode": result.returncode, "errors": errors, "warnings": warnings,
            "wall_s": round(time.perf_counter() - started, 3),
            "files": {"net": file_record(net), "additional": [file_record(path) for path in additional]},
            "outputs_in_load_directory": sorted({value for path in staged for value in output_paths(path.read_bytes())})}


def file_record(path: Path) -> dict:
    return {"name": Path(path).name, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def load_comparison(parent: Mapping, candidate: Mapping) -> dict:
    """Warnings the candidate's load adds or drops against the parent's."""
    before, after = set(parent["warnings"]), set(candidate["warnings"])
    return {"parent": parent["status"], "candidate": candidate["status"], "new_warnings": sorted(after - before),
            "gone_warnings": sorted(before - after), "candidate_errors": list(candidate["errors"])}


def route_continuity(net: bytes, demand: bytes) -> dict:
    """Every demand route's consecutive edges joined by at least one connection of ``net`` (edge level)."""
    import io

    root = ET.fromstring(net)
    joined = {(row.get("from"), row.get("to")) for row in root.iter("connection") if not row.get("from").startswith(":")}
    broken, routes = [], 0
    for _, element in ET.iterparse(io.BytesIO(demand), events=("end",)):
        if element.tag != "vehicle":
            continue
        route = element.find("route")
        edges = route.get("edges", "").split() if route is not None else []
        routes += 1
        gaps = [f"{a}>{b}" for a, b in zip(edges, edges[1:]) if (a, b) not in joined]
        if gaps:
            broken.append({"vehicle": element.get("id"), "gaps": gaps})
        element.clear()
    return {"routes": routes, "broken": len(broken), "examples": broken[:10]}


def write_reindexed(parent_net: Path, candidate_net: Path, control: Path, declaration: Mapping, out: Path, *,
                    entries: Sequence[Mapping] | None = None) -> dict:
    """Write ``control`` re-indexed to ``candidate_net`` into the new file ``out``; returns the report with every
    file's SHA-256 (``entries``: a fragility-fix goal's declared targets, cross-checked first)."""
    data, report = reindex_control(Path(control).read_bytes(), ET.parse(parent_net).getroot(),
                                   ET.parse(candidate_net).getroot(), declaration, entries=entries)
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists; write the re-indexed control to a new path")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    report["files"] = {"parent_net": file_record(parent_net), "candidate_net": file_record(candidate_net),
                       "control": file_record(control), "output": file_record(out)}
    return report


def load_check_report(report: Mapping, *, parent_net: Path, candidate_net: Path, control: Path, out: Path,
                      additional: Sequence[Path], sumo_binary: str, demand: Path | None = None) -> dict:
    """``report`` with the SUMO load of the parent (its control) and of the candidate (the re-indexed ``out``), both
    with ``additional`` files, their warning comparison and, with ``demand``, each net's route continuity."""
    work = Path(out).with_name(Path(out).name + ".load")
    loads = {name: sumo_load(net, [program, *additional], sumo_binary=sumo_binary, cwd=work / name)
             for name, net, program in (("parent", Path(parent_net), Path(control)),
                                        ("candidate", Path(candidate_net), Path(out)))}
    result = {**report, "load_check": {**loads, "comparison": load_comparison(loads["parent"], loads["candidate"])}}
    if demand is not None:
        data = Path(demand).read_bytes()
        result["route_continuity"] = {name: route_continuity(Path(net).read_bytes(), data)
                                      for name, net in (("parent", parent_net), ("candidate", candidate_net))}
        result["route_continuity"]["demand"] = file_record(demand)
    return result
