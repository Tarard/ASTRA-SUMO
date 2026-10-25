"""Freeze one complete chronological OD result and compact prefix records; no SUMO."""

import argparse
from collections import Counter
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / "outputs/hamburg-chronological-prefix-calibration-20261024-v1"
CASE = "h4_counter_z11_minus5_z5"
PARENT = REPO / "benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1"
PARENT_SHA = "b41a8463f4c8be5b569a31699f8bfaf712ae4f75ba2433541ec410c1ada7a2da"
OLD_BEST = REPO / "benchmarks/hamburg_ring1_119_late_demand_thinning_20261024_v1"
OLD_BEST_SHA = "9141f590930dec43aa7384ba4fb1c46dac9c631a452d87b660c97407f1a8bdd8"
AUDIT_SHA = "9e7e9be3e7f49687b6e3ccaa3641c637cadb1b32a3efbfa12ee78844b44b011d"
LINEAGE = {"h4_delay30", "h4_counter_z11_one"}
spec = spec_from_file_location("chronological_archive_helpers", PARENT / "freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, save, safe_relative = prior.read, prior.jread, prior.save, prior.safe_relative
verify_manifest, physical, audit_measurement, PREPARED, intervals = prior.verify_manifest, prior.physical, prior.audit_measurement, prior.PREPARED, prior.intervals


def audit_packet(packet):
    prefix = Path("cases") / CASE
    independent = jread(packet, prefix / "independent-full.json")
    assert hashlib.sha256(read(packet, prefix / "independent-full.json")).hexdigest() == AUDIT_SHA
    assert independent["audit_status"] == "pass" and independent["raw_instant_equals_compact"] and independent["runtime_and_snapshots_verified"]
    verdict, full, preflight = [jread(packet, prefix / n) for n in ("verdict.json", "full-result.json", "preflight.json")]
    assert verdict["status"] == "diagnostic_measurement_complete" and full["after"] == independent["after"] == verdict["after"]
    assert full["input_parent_prefix"]["full_measurement"] is False and full["comparison_reference"]["main130_TAE"] == 448
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(packet, prefix / "prepared" / name) == read(PARENT, Path("cases/window0_remove3/prepared") / name)
    for name, digest in preflight["prepared_hashes"].items():
        assert hashlib.sha256(read(packet, prefix / "prepared" / name)).hexdigest() == digest
    for name, digest in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(packet, prefix / "measurement-implementation" / name)).hexdigest() == digest
    for name, digest in preflight["executed_driver_snapshots"].items():
        assert hashlib.sha256(read(packet, prefix / name)).hexdigest() == digest
    extra = jread(packet, prefix / "additional-evaluation-dependencies.json")
    for name, digest in extra["snapshot_hashes"].items():
        assert hashlib.sha256(read(packet, prefix / "additional-measurement-implementation" / name)).hexdigest() == digest
    intentions = {r["vehicle_id"]: r for r in jread(packet, prefix / "prepared/lane-intentions.json")["vehicles"]}
    vector = jread(packet, prefix / "od-vector.json")
    pairs = [(r["origin_edge"], r["destination_edge"]) for r in vector["od_pairs"]]
    vehicles = ET.fromstring(read(packet, prefix / "prepared/demand.rou.xml")).findall("vehicle")
    q = [0] * 308
    for vehicle in vehicles:
        edges = vehicle.find("route").get("edges").split()
        cell = (math.floor(float(vehicle.get("depart")) * 1000 + .5) // 900000) * 22 + pairs.index((edges[0], edges[-1]))
        assert intentions[vehicle.get("id")]["od_cell"] == cell
        q[cell] += 1
    assert len(pairs) == 22 and len(vehicles) == len(intentions) == sum(q) == 8589 and q == vector["integer_counts"]
    assert pairs[21] == ("25866659#1", "1257937836#0") and vector["dimension"] == 308
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        for row in load(packet / "source-provenance.json")["snapshots"]:
            name = Path(row["relative"])
            if name.is_relative_to(prefix / "prepared") or name.is_relative_to(prefix / "evaluation"):
                target = out / name.relative_to(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read(packet, name))
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", {i: [r["field_id"]] for i, r in intentions.items()})
        main_rows = prior.rows(packet, prefix / "prepared/expected-counts.csv")
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["main130"]["TAE"] == 275 and measured["healthy"] and measured["safe"] and measured["event_count"] == 8589
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == 6.125 and not outside and not repeated
    old = intervals(read(PARENT, "cases/window0_remove3/evaluation/native/field-counts.xml"))
    current = intervals(read(packet, prefix / "evaluation/native/field-counts.xml"))
    assert all(old[k] == v for k, v in current.items() if k[2] <= 3600)
    supply = jread(packet, prefix / "field-supply-bound.json")
    field_stocks = Counter(r["field_id"] for r in intentions.values())
    for row in supply["rows"]:
        field = row["field"]
        assert row["requests"] == field_stocks[field]
        assert row["protected_initial"] == sum(int(r["nVehContrib"]) for key, r in current.items() if key[0] == field and key[2] <= 3600)
        assert row["main_target"] == sum(int(r["expected_total"]) for r in main_rows if r["detector_id"] == field)
        assert row["remaining_margin"] == row["requests"] - row["protected_initial"] - row["main_target"]
    negative = {r["field"]: r["remaining_margin"] for r in supply["rows"] if r["remaining_margin"] < 0}
    assert negative == {"hh_0119_Z_5_30494": -1, "hh_0535_Z_4_33055": -11}
    lineage = []
    stages = [(OLD_BEST, Path("cases/remove43/prepared")), (PARENT, Path("cases/window0_remove3/prepared")),
        (packet, Path("cases/h4_delay30/prepared")), (packet, Path("cases/h4_counter_z11_one/prepared")), (packet, prefix / "prepared")]
    for (old_packet, old_path), (new_packet, new_path) in zip(stages, stages[1:]):
        old_vehicles, new_vehicles = [{v.get("id"): v for v in ET.fromstring(read(p, path / "demand.rou.xml")).findall("vehicle")} for p, path in ((old_packet, old_path), (new_packet, new_path))]
        old_intents, new_intents = [{r["vehicle_id"]: r for r in jread(p, path / "lane-intentions.json")["vehicles"]} for p, path in ((old_packet, old_path), (new_packet, new_path))]
        common = old_vehicles.keys() & new_vehicles.keys()
        route_changes = [i for i in common if old_vehicles[i].find("route").get("edges") != new_vehicles[i].find("route").get("edges")]
        departure_changes = [i for i in common if old_vehicles[i].get("depart") != new_vehicles[i].get("depart")]
        assert all(old_intents[i]["field_id"] == new_intents[i]["field_id"] and [ET.tostring(r) for r in old_vehicles[i].findall("stop")] == [ET.tostring(r) for r in new_vehicles[i].findall("stop")] for i in common)
        lineage.append(dict(source=old_path.as_posix(), candidate=new_path.as_posix(), source_N=len(old_vehicles), candidate_N=len(new_vehicles),
            removed_IDs=sorted(old_vehicles.keys() - new_vehicles.keys()), added_IDs=sorted(new_vehicles.keys() - old_vehicles.keys()),
            departure_changes=len(departure_changes), route_changes=route_changes, common_fields_and_stops_exact=True))
    assert [(len(r["removed_IDs"]), len(r["added_IDs"]), r["departure_changes"], len(r["route_changes"])) for r in lineage] == [(3, 0, 0, 0), (0, 0, 269, 0), (0, 0, 0, 1), (5, 0, 0, 0)]
    prefixes = []
    for row in load(packet / "prefix-index.json")["cases"]:
        base = Path("cases") / row["case"]
        record = jread(packet, base / "record-result.json")
        coverage = jread(packet, base / "all182-coverage.json")
        assert len(coverage) == 182 and sum(r["measured_nVehContrib"] is None for r in coverage) == 117
        first = [r for r in coverage if float(r["begin"]) == 3600]
        assert len(first) == 13 and sum(abs(r["diff_nVehContrib_minus_expected"]) for r in first) == record["prefix13_metrics"]["TAE"]
        assert record["future117_unmeasured"] and record["full130_metrics"] is None and not record["full130_calibration_acceptance"]
        prefixes.append(dict(case=row["case"], first13=record["prefix13_metrics"], future117_unmeasured=True))
    return dict(status="review_required", formal_result=False, native_runs_by_freeze=0,
        cases={CASE: dict(measurement=measured, actual_OD_pairs=22, actual_OD_cells=308, first13_metrics=independent["first13_metrics"],
            first13_nonzero_residuals=independent["first13_nonzero_residuals"], conditional_inventory_deficits=negative)},
        previous_best_main_TAE=324, calibration_complete=False, prefix_trials=prefixes,
        chronological_input_lineage=lineage,
        input_parent_is_prefix_only=True, raw_complete_reference_TAE448_is_not_direct_input_parent=True)


def verify(packet):
    verify_manifest(packet)
    assert sha(PARENT / "manifest.json") == PARENT_SHA and sha(OLD_BEST / "manifest.json") == OLD_BEST_SHA
    for row in load(packet / "source-provenance.json")["snapshots"]:
        value = read(packet, row["relative"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    for row in load(packet / "historical-source-bindings.json")["bindings"]:
        if row["scope"] == "local_trace":
            assert any(r["sha256"] == row["sha256"] and r["path"] == row["target"] for r in load(packet / "local-only-large-traces.json")["files"])
        else:
            value = read(packet, row["target"]) if row["scope"] == "packet" else raw(REPO / safe_relative(row["target"]))
            assert hashlib.sha256(value).hexdigest() == row["sha256"]
    assert audit_packet(packet) == load(packet / "results.json")["audit"]
    print("Chronological OD: complete275 result,308 inventory and prefix/null boundaries verified", flush=True)


def freeze():
    assert not (HERE / "manifest.json").exists() and (HERE / "README.md").is_file()
    best = SOURCE / "cases" / CASE
    assert sha(best / "independent-full.json") == AUDIT_SHA and load(best / "verdict.json")["status"] == "diagnostic_measurement_complete"
    locations = {}
    for parent, seal in ((PARENT, PARENT_SHA), (OLD_BEST, OLD_BEST_SHA)):
        assert sha(parent / "manifest.json") == seal
        for row in load(parent / "source-provenance.json")["snapshots"]:
            path = physical(parent, row["relative"])
            locations[row["raw_sha256"]] = dict(scope="repository", target=path.relative_to(REPO).as_posix())
    selected = {p.relative_to(SOURCE): p for p in SOURCE.iterdir() if p.is_file()}
    index = []
    for case in (SOURCE / "cases").iterdir():
        if not case.is_dir():
            continue
        if case.name == CASE:
            selected.update({p.relative_to(SOURCE): p for p in case.rglob("*") if p.is_file() and "__pycache__" not in p.parts})
        elif (case / "record-result.json").is_file():
            for p in case.rglob("*"):
                relative = p.relative_to(case)
                if p.is_file() and "__pycache__" not in p.parts and (len(relative.parts) == 1 or relative.parts[0] == "measurement-implementation" or relative == Path("native/field-counts.xml") or case.name in LINEAGE and relative.parts[0] == "prepared"):
                    selected[p.relative_to(SOURCE)] = p
            record = load(case / "record-result.json")
            index.append(dict(case=case.name, record_sha256=sha(case / "record-result.json"), actual_first13=record["prefix13_metrics"],
                future117=None, native_output_hashes=record["native_output_hashes"], full_acceptance=False))
    aliases, provenance, local = {}, [], []
    def store(name, source):
        digest, relative = sha(source), name.as_posix()
        if name.parts[:2] == ("cases", CASE) and name.name in {"instant-e1.xml", "queues.xml"}:
            local.append(dict(path=str(source), bytes=source.stat().st_size, sha256=digest, relative=relative))
            locations[digest] = dict(scope="local_trace", target=str(source))
            return
        if digest in locations:
            aliases[relative] = dict(**locations[digest], sha256=digest)
        else:
            target = HERE / (relative + ".gz")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
            locations[digest] = dict(scope="packet", target=relative)
        provenance.append(dict(source=str(source), relative=relative, raw_bytes=source.stat().st_size, raw_sha256=digest))
    for name, source in sorted(selected.items()):
        store(name, source)
    bindings = []
    for source in selected.values():
        if source.suffix == ".json":
            record = load(source)
            hashes = record.get("source_hashes", {}) if isinstance(record, dict) else {}
            entries = hashes.items() if isinstance(hashes, dict) else [(r["path"], r["sha256"]) for r in hashes]
            for path, digest in entries:
                if digest not in locations:
                    dependency = Path(path)
                    if not dependency.is_absolute():
                        dependency = REPO / dependency
                    assert dependency.is_file() and sha(dependency) == digest, (path, digest)
                    relative = dependency.relative_to(SOURCE) if dependency.is_relative_to(SOURCE) else None
                    omitted_prefix_data = relative and len(relative.parts) > 2 and relative.parts[0] == "cases" and relative.parts[1] != CASE and relative.parts[2] in {"native", "prepared", "contributing-passages.json.gz"}
                    if omitted_prefix_data or dependency.name in {"instant-e1.xml", "queues.xml"}:
                        local.append(dict(path=str(dependency), bytes=dependency.stat().st_size, sha256=digest, source_dependency=True))
                        locations[digest] = dict(scope="local_trace", target=str(dependency))
                    else:
                        store(Path("bound-dependencies") / digest / dependency.name, dependency)
                bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "source-provenance.json", dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / "byte-references.json", dict(aliases=aliases, rule="Exact raw SHA aliases only."))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "local-only-large-traces.json", dict(files=local))
    save(HERE / "source-references.json", dict(input_reference_packet=PARENT.name, input_reference_manifest_sha256=PARENT_SHA,
        previous_best_packet=OLD_BEST.name, previous_best_manifest_sha256=OLD_BEST_SHA))
    save(HERE / "prefix-index.json", dict(cases=index, compact_only=True, unmeasured_future117_is_null=True))
    save(HERE / "results.json", dict(audit=audit_packet(HERE)))
    assert all(sha(Path(r["source"])) == r["raw_sha256"] for r in provenance)
    save(HERE / "manifest.json", dict(status="review_required", formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size,
        sha256=sha(p)) for p in sorted(HERE.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]))
    verify(HERE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
