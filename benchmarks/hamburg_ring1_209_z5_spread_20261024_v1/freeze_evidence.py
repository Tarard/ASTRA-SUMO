"""Freeze one completed Z5 timing sensitivity; never start SUMO."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PARENT = REPO / "benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1"
PARENT_SHA = "3a5db2e98e7a36be5b5347b0d299cc074d157f23eefb6dd4620f1914054a0b94"
SOURCE = REPO / "outputs/hamburg-209-z5-spread-20261024-v1"
CONCENTRATION = REPO / "outputs/hamburg-z5-departure-concentration-audit-20261024-v1"
spec = spec_from_file_location("spread_archive_helpers", REPO / "benchmarks/hamburg_ring1_119_common_offset_20261024_v1/freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, verify_manifest = prior.save, prior.safe_relative, prior.verify_manifest
audit_measurement, PREPARED = prior.audit_measurement, prior.PREPARED
FIELD = "hh_0119_Z_5_30494"
LOCAL = {"spread15/evaluation/native/instant-e1.xml", "spread15/evaluation/native/queues.xml"}
SEALED = {"independent": "f36d62db2ee81122bb9a0b48fc7142480afecdc1d7644c66a47aaa088154b056",
          "independent-input": "cfb463ff61eb40fd854a3d5b5a5964b0534f46e82ea616ed40ef13efa8579c48",
          "source-concentration": "27f52a642a77675e71b5fdba191813894e83ffb6889e5c3867055aadaf848184"}


def physical(case, name):
    path = case / safe_relative(name)
    if path.is_file() or Path(str(path) + ".gz").is_file():
        return path
    row = load(case / "byte-references.json")["aliases"][str(name).replace("\\", "/")]
    return physical(case, row["target"]) if row["scope"] == "packet" else REPO / safe_relative(row["target"])


def native_ms(value):
    return int((Decimal(str(value)) * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def departure_scope(before, after, old_intents, new_intents, vector):
    roots = [ET.fromstring(value) for value in (before, after)]
    old, new = [{v.get("id"): v for v in root.findall("vehicle")} for root in roots]
    assert len(old) == len(new) == 8640 and old.keys() == new.keys() == old_intents.keys() == new_intents.keys()
    selected = {i for i, v in old.items() if old_intents[i]["field_id"] == FIELD and 4 <= native_ms(v.get("depart")) // 900000 < 14}
    assert len(selected) == 521
    pairs = sorted({(v.find("route").get("edges").split()[0], v.find("route").get("edges").split()[-1]) for v in old.values()})
    assert len(pairs) == 21
    inventory, groups, crossed = [[0] * 294, [0] * 294], defaultdict(list), []
    for i, first in old.items():
        last = new[i]
        assert {k: v for k, v in first.attrib.items() if k != "depart"} == {k: v for k, v in last.attrib.items() if k != "depart"}
        assert [ET.tostring(v) for v in first] == [ET.tostring(v) for v in last]
        edges = first.find("route").get("edges").split()
        od = pairs.index((edges[0], edges[-1]))
        cells = [(native_ms(v.get("depart")) // 900000) * 21 + od for v in (first, last)]
        for n, cell in enumerate(cells):
            assert 0 <= cell < 294
            inventory[n][cell] += 1
        assert [old_intents[i]["od_cell"], new_intents[i]["od_cell"]] == cells
        if i in selected:
            assert native_ms(first.get("depart")) != native_ms(last.get("depart"))
            assert new_intents[i] == {**old_intents[i], "depart_s": float(last.get("depart")), "od_cell": cells[1]}
            groups[cells[0] // 21].append(i)
        else:
            assert ET.tostring(first) == ET.tostring(last) and old_intents[i] == new_intents[i]
        if cells[0] != cells[1]:
            crossed.append(i)
    for h, ids in groups.items():
        values = [Decimal(old[i].get("depart")) for i in ids]
        center = (min(values) + max(values)) / 2
        for i, value in zip(ids, values, strict=True):
            ideal, actual = center + Decimal("1.5") * (value - center), Decimal(new[i].get("depart"))
            assert abs(actual - ideal) <= Decimal("0.000000001") and native_ms(actual) == native_ms(ideal)
            assert native_ms(actual) // 900000 in (h - 1, h)
    assert len(crossed) == 55 and sum(native_ms(new[i].get("depart")) < 3600000 for i in crossed) == 3
    assert inventory == [vector["source_values"], vector["values"]]
    assert sum(inventory[0]) == sum(inventory[1]) == 8640 and sum(inventory[1][:84]) == 1956
    assert [sum(inventory[0][h * 21 + i] for h in range(14)) for i in range(21)] == [sum(inventory[1][h * 21 + i] for h in range(14)) for i in range(21)]
    assert Counter(r["field_id"] for r in old_intents.values()) == Counter(r["field_id"] for r in new_intents.values())
    blocks = [{ET.fromstring(b).get("id"): b for b in re.findall(rb"<vehicle\b[\s\S]*?</vehicle>", value)} for value in (before, after)]
    assert all(blocks[0][i] == blocks[1][i] for i in old.keys() - selected)
    assert [[v.get("id") for v in root.findall("vehicle") if v.get("id") not in selected] for root in roots][0] == [v.get("id") for v in roots[1].findall("vehicle") if v.get("id") not in selected]
    assert [ET.tostring(v) for v in roots[0] if v.tag != "vehicle"] == [ET.tostring(v) for v in roots[1] if v.tag != "vehicle"]
    return dict(depart_only=521, unchanged_raw_vehicle_blocks=8119, crossed_native_bins=55, main_to_initial=3,
                source_initial_requests=1953, candidate_initial_requests=1956, actual294_recomputed=True,
                global21OD_and13field_totals_preserved=True, fixed_minmax_midpoint_not_sample_mean=True)


def audit_scope(case):
    def intents(value):
        return {r["vehicle_id"]: r for r in json.loads(value)["vehicles"]}
    checked = departure_scope(read(PARENT, "full/prepared/demand.rou.xml"), read(case, "spread15/prepared/demand.rou.xml"),
        intents(read(PARENT, "full/prepared/lane-intentions.json")), intents(read(case, "spread15/prepared/lane-intentions.json")), jread(case, "spread15/od-vector.json"))
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(case, Path("spread15/prepared") / name) == read(PARENT, Path("full/prepared") / name)
    history = "spread15/metadata-correction-history/"
    first, last = jread(case, history + "scenario.json"), jread(case, "spread15/prepared/scenario.json")
    delta = {k for k in first.keys() | last.keys() if first.get(k) != last.get(k)}
    assert delta == {"demand_prior"}
    old_scope, scope = jread(case, history + "scope-proof.json"), jread(case, "spread15/scope-proof.json")
    assert all(h == scope["prepared_hashes"][n] for n, h in old_scope["prepared_hashes"].items() if n != "scenario.json")
    for name, expected in scope["prepared_hashes"].items():
        assert hashlib.sha256(read(case, Path("spread15/prepared") / name)).hexdigest() == expected
    preflight = jread(case, "spread15/preflight.json")
    assert preflight["scope_sha256"] == hashlib.sha256(read(case, "spread15/scope-proof.json")).hexdigest()
    assert preflight["measurement_plan_sha256"] == hashlib.sha256(read(case, "measurement-plan.json")).hexdigest()
    assert preflight["source_full_health_gate"]["parent_main130_MAE"] == 421 / 130
    assert preflight["source_full_health_gate"]["sha256"] == hashlib.sha256(read(PARENT, "full/verdict.json")).hexdigest()
    for name, expected in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, Path("spread15/implementation") / name)).hexdigest() == expected
    assert hashlib.sha256(read(case, "spread15/executed-driver.py")).hexdigest() == hashlib.sha256(read(case, "run_spread.py")).hexdigest()
    for path, expected in jread(case, "measurement-plan.json")["source_hashes"].items():
        if Path(path).suffix == ".py":
            assert hashlib.sha256(read(case, Path("spread15/measurement-implementation") / Path(path).name)).hexdigest() == expected
    return checked


def materialize_run(case, out):
    for row in load(case / "source-provenance.json")["snapshots"]:
        name = Path(row["relative"])
        if name.is_relative_to("spread15/prepared") or name.is_relative_to("spread15/evaluation"):
            target = out / name.relative_to("spread15")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(case, name))


def audit_packet(case):
    checked = audit_scope(case)
    verdict, independent = jread(case, "spread15/verdict.json"), jread(case, "independent/summary.json")
    assert verdict["status"] == "diagnostic_measurement_complete" and independent["audit_status"] == "pass"
    assert verdict["after"] == independent["after"] and verdict["source_209_parent"] == independent["before_parent_209"]
    assert verdict["source_209_parent"] == jread(PARENT, "full/verdict.json")["after"]
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        materialize_run(case, out)
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]}
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    assert not outside and not repeated and measured["event_count"] == 8640 and measured["healthy"] and measured["safe"]
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["main130"]["TAE"] == 427 and measured["main130"]["SSE"] == 5529
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == 10.025
    fields = rows(case, "independent/field13.csv")
    all_rows = rows(case, "spread15/evaluation/all182-comparison.csv")
    for row in fields:
        errors = [int(r["diff_nVehContrib_minus_expected"]) for r in all_rows if r["detector_id"] == row["field"] and float(r["begin"]) >= 3600]
        assert len(errors) == 10 and sum(map(abs, errors)) == int(row["spread15_main_TAE"]) and sum(errors) == int(row["spread15_main_signed"])
    parent_counts = prior.prior.intervals(read(PARENT, "full/evaluation/native/field-counts.xml"))
    candidate_counts = prior.prior.intervals(read(case, "spread15/evaluation/native/field-counts.xml"))
    assert len(candidate_counts) == 208 and all(int(row["nVehContrib"]) == int(parent_counts[k]["nVehContrib"]) for k, row in candidate_counts.items() if k[2] <= 3600)
    assert verdict["all53556"]["rows"] == 56 and verdict["all53556"]["TAE"] == 0
    return dict(status="not_improved", raw_verdict=verdict["status"], formal_result=False, measurement=measured,
        parent_main130_TAE=421, parent_main130_MAE=421 / 130, lowest_measured_healthy_main_MAE=421 / 130,
        initial52_counts_row_equal=True, all53556=verdict["all53556"], input_scope=checked,
        new_native_calls_recorded=1, native_runs_by_freeze=0, calibration_complete=False,
        pair_only_interpretation="Z4/Z5 main counts1034 to1036 in this pair do not establish a capacity bound or a common cause.")


def verify(case):
    verify_manifest(case)
    verify_manifest(PARENT)
    assert sha(PARENT / "manifest.json") == PARENT_SHA == load(case / "source-references.json")["parent_manifest_sha256"]
    for row in load(case / "source-provenance.json")["snapshots"]:
        value = read(case, row["relative"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    for folder, expected in SEALED.items():
        assert hashlib.sha256(read(case, Path(folder) / "manifest.json")).hexdigest() == expected
        for row in jread(case, Path(folder) / "manifest.json")["files"]:
            value = read(case, Path(folder) / safe_relative(row["path"]))
            assert len(value) == row.get("bytes", row.get("size_bytes")) and hashlib.sha256(value).hexdigest() == row["sha256"]
    for row in load(case / "historical-source-bindings.json")["bindings"]:
        value = read(case, row["target"]) if row["scope"] == "packet" else raw(REPO / safe_relative(row["target"]))
        assert hashlib.sha256(value).hexdigest() == row["sha256"]
    local = load(case / "local-only-large-traces.json")["files"]
    assert {r["relative"] for r in local} == LOCAL
    compact = json.loads(gzip.decompress(read(case, "spread15/evaluation/contributing-passages.json.gz")))
    instant_hash = next(r["sha256"] for r in local if r["relative"].endswith("instant-e1.xml"))
    assert instant_hash in {r["sha256"] for r in compact["inputs"]}
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("Z5 spread: frozen521/55 scope, metadata history and native130/52/182/40/208 pass", flush=True)


def freeze():
    assert not (HERE / "manifest.json").exists()
    assert sha(PARENT / "manifest.json") == PARENT_SHA
    locations = {}
    for row in load(PARENT / "source-provenance.json")["snapshots"]:
        target = physical(PARENT, row["relative"])
        assert raw_sha(target) == row["raw_sha256"]
        locations[row["raw_sha256"]] = dict(scope="repository", target=target.relative_to(REPO).as_posix())
    selected = {p.relative_to(SOURCE): p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    selected.update({Path("source-concentration") / p.relative_to(CONCENTRATION): p for p in CONCENTRATION.rglob("*") if p.is_file() and "__pycache__" not in p.parts})
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected.items()):
        digest, relative = sha(source), name.as_posix()
        if relative in LOCAL:
            local.append(dict(path=str(source), relative=relative, bytes=source.stat().st_size, sha256=digest))
            continue
        if digest in locations:
            aliases[relative] = dict(**locations[digest], sha256=digest)
        else:
            target = HERE / (relative + ".gz")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
            locations[digest] = dict(scope="packet", target=relative)
        provenance.append(dict(source=str(source), relative=relative, raw_bytes=source.stat().st_size, raw_sha256=digest))
    save(HERE / "source-provenance.json", dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / "byte-references.json", dict(aliases=aliases, rule="Exact raw SHA aliases only."))
    bindings = []
    for name in ("spread15/scope-proof.json", "measurement-plan.json", "spread15/metadata-correction-history/scope-proof.json", "independent-input/source-hashes.json"):
        value = load(SOURCE / name)
        for path, digest in value.get("source_hashes", value).items():
            assert digest in locations, (path, digest)
            bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parent_packet=PARENT.relative_to(REPO).as_posix(), parent_manifest_sha256=PARENT_SHA))
    save(HERE / "local-only-large-traces.json", dict(files=local, rule="Only the two named large native traces stay local. Complete counts, summary, trips, routes and compact events are frozen."))
    save(HERE / "results.json", dict(audit=audit_packet(HERE)))
    assert all(sha(Path(r["source"])) == r["raw_sha256"] for r in provenance)
    save(HERE / "manifest.json", dict(status="not_improved", formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]))
    verify(HERE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
