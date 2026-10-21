"""Read fixed OCIT-C group timelines without assigning SUMO control states."""

from fractions import Fraction
from pathlib import Path
import math
import xml.etree.ElementTree as ET

from .candidate_contracts import file_sha256
from .ocit_c import _child, _children, _descendants, _normalize_group_label, _text


def _seconds(value, *, positive=False):
    try:
        result = Fraction(value)
        if not math.isfinite(float(result)):
            raise ValueError("Time is not finite.")
    except (ValueError, ZeroDivisionError, TypeError, OverflowError) as error:
        raise ValueError(f"Invalid OCIT time: {value!r}.") from error
    if result < 0 or positive and result == 0:
        raise ValueError(f"OCIT time must be {'positive' if positive else 'nonnegative'}: {value!r}.")
    return result


def _image(value):
    value = value.strip().upper()
    if len(value) != 2 or any(c not in "0123456789ABCDEF" for c in value):
        raise ValueError(f"Invalid OCIT signal image: {value!r}.")
    return value


def _image_classes(group):
    result = {}
    allowed = _child(group, "ZulaessigeSignalbilder")
    for name, kind in (("Frei", "release"), ("Gesperrt", "stop")):
        section = _child(allowed, name)
        if section is None:
            continue
        codes = [_text(_child(section, "Standard"))] + [
            _text(_child(item, "Signalbild")) for item in _children(section, "Zusaetzlich")]
        for code in filter(None, codes):
            result.setdefault(_image(code), set()).add(kind)
    return result


def _group_timeline(group, row, cycle):
    classes = _image_classes(group)
    if row is None:
        raise ValueError("The program omits this group. OCIT-C does not define its behavior.")
    # shortcut: named transitions stay unresolved until a source requires their group-specific definition.
    if next(_descendants(row, "Uebergang"), None) is not None:
        raise ValueError("The row references a named transition. Its timeline is not implemented.")
    constant = _text(_child(row, "DauerSignalbild"))
    commands = [(_seconds(_text(_child(command, "Schaltzeitpunkt"))),
                 _image(_text(_child(command, "Signalbild")))) for command in _children(row, "Schaltzeit")]
    if constant:
        if commands:
            raise ValueError("A program row contains both a constant image and switching commands.")
        code = _image(constant)
        return [{"begin_s": 0.0, "end_s": float(cycle), "signal_image": code,
                 "securing_states": sorted(classes.get(code, ())), "in_transition": False}]
    commands.sort()
    if not commands or any(time >= cycle for time, _ in commands):
        raise ValueError("Switching times must lie inside a positive program cycle.")
    if len({time for time, _ in commands}) != len(commands):
        raise ValueError("The group has more than one command at the same cycle time.")
    events = {}
    for index, (time, target) in enumerate(commands):
        previous = commands[index - 1][1]
        source_states, target_states = classes.get(previous, set()), classes.get(target, set())
        if len(source_states) != 1 or len(target_states) != 1:
            raise ValueError("A commanded image has unknown or ambiguous securing states.")
        transition = None
        if source_states != target_states:
            transition = _child(group, "AnwurfUebergang" if target_states == {"release"} else "AbwurfUebergang")
        elements = []
        if transition is not None:
            for item in _children(transition, "Uebergangselement"):
                duration = _seconds(_text(_child(item, "Zeitdauer")))
                if duration:
                    elements.append((_image(_text(_child(item, "Signalbild"))), duration))
        duration = sum((span for _, span in elements), Fraction(0))
        next_time = commands[index + 1][0] if index + 1 < len(commands) else commands[0][0] + cycle
        if time + duration > next_time:
            raise ValueError("A transition overlaps the next command. OCIT-C leaves this behavior unspecified.")
        for code, span in elements:
            events[time % cycle] = (1, code, True)
            time += span
        # A new command takes precedence over a zero-duration destination at the same boundary.
        entry = (0 if elements else 1, target, False)
        key = time % cycle
        if key not in events or events[key][0] <= entry[0]:
            events[key] = entry
    boundaries = sorted(set(events) | {Fraction(0), cycle})
    active = events[max(events)]
    result = []
    for begin, end in zip(boundaries, boundaries[1:]):
        active = events.get(begin, active)
        _, code, changing = active
        if result and (result[-1]["signal_image"], result[-1]["in_transition"]) == (code, changing):
            result[-1]["end_s"] = float(end)
        else:
            result.append({"begin_s": float(begin), "end_s": float(end), "signal_image": code,
                "securing_states": sorted(classes.get(code, ())), "in_transition": changing})
    return result


def read_ocit_fixed_program(path: str | Path, program_reference: str) -> dict:
    """Expand standard group transitions in one source-relative repeating cycle.

    The result retains native image codes. It neither selects a dated program
    nor assigns road permissions, SUMO green priorities, or an absolute clock.
    """
    path = Path(path).resolve(strict=True)
    digest = file_sha256(path)
    root = ET.parse(path).getroot()
    reference = str(program_reference).strip()
    programs = [p for p in _descendants(root, "Signalprogramm")
                if _text(_child(p, "BezeichnungKurz")) == reference]
    if not reference or len(programs) != 1:
        raise ValueError("Select one unique OCIT program by its source reference name.")
    program = programs[0]
    cycle = _seconds(_text(_child(_child(program, "SPKopfzeile"), "TU")), positive=True)
    definitions = {}
    for group in _descendants(root, "Signalgruppe"):
        name = _normalize_group_label(_text(_child(group, "BezeichnungKurz")))
        if not name:
            continue
        if name in definitions:
            raise ValueError(f"Duplicate OCIT signal-group definition: {name}.")
        definitions[name] = group
    rows = {}
    for row in _children(program, "SPZeile"):
        name = _normalize_group_label(_text(_child(row, "Signalgruppe")))
        rows.setdefault(name, []).append(row)
    measured, unresolved, records = [], [], []
    for name in sorted(definitions.keys() | rows.keys()):
        group = definitions.get(name)
        record = {"group_id": name, "traffic_class_native": _text(_child(group, "Verkehrsart")),
                  "ocit_outstation_number": _text(_child(group, "OCITOutstationNr"))}
        source_rows = rows.get(name, [])
        record["source_rows_xml"] = [ET.tostring(row, encoding="unicode") for row in source_rows]
        try:
            record["allowed_image_securing_states"] = {code: sorted(states) for code, states in _image_classes(group).items()}
            if group is None or len(source_rows) > 1:
                raise ValueError("The group definition is missing or the program has duplicate group rows.")
            timeline = _group_timeline(group, source_rows[0] if source_rows else None, cycle)
            record.update(status="measured", intervals=timeline)
            measured.append(name)
        except ValueError as error:
            record.update(status="unresolved", intervals=[], reason=str(error))
            unresolved.append(name)
        records.append(record)
    offset = _text(_child(_child(program, "SPKopfzeile"), "SignalzeitenVersatz"))
    return {"schema": "astra.ocit-fixed-program-timeline/v1", "status": "measured",
        "source": {"path": str(path), "sha256": digest}, "source_unchanged": file_sha256(path) == digest,
        "node_id": _text(_child(next(_descendants(root, "Kopfdaten"), None), "Kurzbezeichnung")),
        "program_reference": reference, "program_number": _text(_child(program, "OCITOutstationNr")),
        "program_name": _text(_child(program, "BezeichnungKurz")),
        "cycle_s": float(cycle), "signal_times_offset_native": offset,
        "time_basis": "raw_SPZeile_cycle_times_before_clock_alignment_or_SignalzeitenVersatz",
        "groups": records, "measured_groups": measured, "unresolved_groups": unresolved,
        "assumptions": ["Repeat the listed cycle and hold each destination image until its next listed transition."],
        "claim_boundary": "Source program timeline only. No actual program selection, field timing, detector correspondence, or SUMO signal-state mapping."}
