"""Freeze two completed early-window quantity responses; never start SUMO."""

import argparse
from collections import Counter
import copy
import csv
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import io
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PARENT = REPO / "benchmarks/hamburg_ring1_119_late_demand_thinning_20261024_v1"
PARENT_SHA = "9141f590930dec43aa7384ba4fb1c46dac9c631a452d87b660c97407f1a8bdd8"
PARENT_CASE = Path("cases/remove43")
SOURCE = REPO / "outputs/hamburg-chronological-count-feedback-20261024-v1"
RUNS = {"window0": (8604, 10, 356), "window0_remove3": (8594, 0, 448)}
SEALED = {
    "cases/window0/independent.json": "9b10e8aec85b6557c08e006bfb97de1bfb88a6d6e24678d494e5e4f59718e1cb",
    "cases/window0_remove3/independent.json": "d685b77027ca860fc0c95a170b26c08e34d707da54ced9517e4eb4d94cebd243",
    "primary-window0-diagnosis.json": "ca1950904702c5f998d39d4ae492129d78541b2c577b5951d403212b07cfcbeb",
    "primary-window0-offset-correction.json": "e8949d016f3b40b6ffbfd22faccf2237e18d3ee5d9bd1a4ee4a54d39da7b5419",
}
spec = spec_from_file_location("early_quantity_helpers", PARENT / "freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, verify_manifest = prior.save, prior.safe_relative, prior.verify_manifest
audit_measurement, PREPARED, physical, intervals = prior.audit_measurement, prior.PREPARED, prior.physical, prior.intervals


def request_scope(old_bytes, new_bytes, old_intents, new_intents, proposal, vector, expected):
    roots = [ET.fromstring(v) for v in (old_bytes, new_bytes)]
    old, new = [{v.get("id"): v for v in root.findall("vehicle")} for root in roots]
    count, additions, _ = expected
    removed, added, common = old.keys() - new.keys(), new.keys() - old.keys(), old.keys() & new.keys()
    assert len(old) == 8597 and len(new) == count and len(added) == additions and len(removed) == 3
    assert removed == set(proposal["removed_vehicle_ids"]) == {"od_0104_v00015", "od_0104_v00049", "od_0088_v00008"}
    assert added == {r["vehicle_id"] for r in proposal["additions"]}
    assert old.keys() == old_intents.keys() and new.keys() == new_intents.keys()
    assert all(ET.tostring(old[i]) == ET.tostring(new[i]) and old_intents[i] == new_intents[i] for i in common)
    assert [v.get("id") for v in roots[0].findall("vehicle") if v.get("id") in common] == [v.get("id") for v in roots[1].findall("vehicle") if v.get("id") in common]
    assert roots[0].attrib == roots[1].attrib
    assert [ET.tostring(v) for v in roots[0] if v.tag != "vehicle"] == [ET.tostring(v) for v in roots[1] if v.tag != "vehicle"]
    for row in proposal["additions"]:
        identity, template = row["vehicle_id"], row["source_template_vehicle_id"]
        assert identity.startswith("hyp_w0_") and template in old
        a, b = copy.deepcopy(old[template]), copy.deepcopy(new[identity])
        for v in (a, b):
            v.attrib.pop("id")
            v.attrib.pop("depart")
        assert ET.tostring(a) == ET.tostring(b)
        assert float(new[identity].get("depart")) == row["requested_depart_s"]
        assert new_intents[identity]["hypothetical_new_request"] and new_intents[identity]["source_template_vehicle_id"] == template
        assert new_intents[identity]["field_id"] == old_intents[template]["field_id"] == row["field_id"]
        assert new_intents[identity]["od_cell"] == old_intents[template]["od_cell"] == row["od_cell"]
    if additions:
        assert Counter(new_intents[i]["field_id"] for i in added) == Counter({"hh_0119_Z_11_28853": 1, "hh_0119_Z_12_31546": 5, "hh_0119_Z_13_34018": 4})
    source_q = [sum(r["od_cell"] == i for r in old_intents.values()) for i in range(294)]
    new_q = [sum(r["od_cell"] == i for r in new_intents.values()) for i in range(294)]
    assert source_q == vector["source_integer_counts"] and new_q == vector["integer_counts"]
    assert sum(source_q) == 8597 and sum(new_q) == count and source_q[:84] == new_q[:84]
    assert sum(abs(a - b) for a, b in zip(source_q, new_q, strict=True)) == vector["l1_change"] == additions + 3
    return dict(source_N=8597, actual_N=count, added_IDs=sorted(added), removed_IDs=sorted(removed), common_survivors=len(common),
        survivors_XML_intent_routes_times_stops_and_relative_order_exact=True, new_IDs_are_explicit_hypothetical_template_clones=True,
        actual294_inventory_verified=True, q_L1=additions + 3)


def audit_case(case, label):
    prefix = Path("cases") / label
    verdict, independent, preflight = [jread(case, prefix / n) for n in ("verdict.json", "independent.json", "preflight.json")]
    assert verdict["status"] == "diagnostic_measurement_complete" and independent["audit_status"] == "pass"
    assert independent["raw_instant_equals_compact"] and independent["runtime_and_snapshots_verified"]
    assert independent["after"] == verdict["after"] and independent["source"] == verdict["source_parent"] == jread(PARENT, PARENT_CASE / "verdict.json")["after"]
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(case, prefix / "prepared" / name) == read(PARENT, PARENT_CASE / "prepared" / name)
    def intents(value):
        return {r["vehicle_id"]: r for r in json.loads(value)["vehicles"]}
    scope = request_scope(read(PARENT, PARENT_CASE / "prepared/demand.rou.xml"), read(case, prefix / "prepared/demand.rou.xml"),
        intents(read(PARENT, PARENT_CASE / "prepared/lane-intentions.json")), intents(read(case, prefix / "prepared/lane-intentions.json")),
        jread(case, prefix / "proposal.json"), jread(case, prefix / "od-vector.json"), RUNS[label])
    for name, digest in preflight["prepared_hashes"].items():
        assert hashlib.sha256(read(case, prefix / "prepared" / name)).hexdigest() == digest
    for name, digest in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, prefix / "measurement-implementation" / name)).hexdigest() == digest
    assert preflight["source_N"] == 8597 and preflight["candidate_N"] == RUNS[label][0]
    assert preflight["end_s"] == 14400 and preflight["step_s"] == .1 and preflight["seed"] == 104 and preflight["backend"] == "libsumo"
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        for row in load(case / "source-provenance.json")["snapshots"]:
            name = Path(row["relative"])
            if name.is_relative_to(prefix / "prepared") or name.is_relative_to(prefix / "evaluation"):
                target = out / name.relative_to(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read(case, name))
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]}
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == verdict["after"]["station40_MAE"]
    assert measured["healthy"] and measured["safe"] and measured["event_count"] == RUNS[label][0] and not outside and not repeated
    assert measured["main130"]["TAE"] == RUNS[label][2] > 324
    before = intervals(read(PARENT, PARENT_CASE / "evaluation/native/field-counts.xml"))
    after = intervals(read(case, prefix / "evaluation/native/field-counts.xml"))
    keys = [key for key in before if key[2] <= 3600]
    assert len(keys) == 52 and all(before[k] == after[k] for k in keys)
    assert verdict["all53556"]["TAE"] == 0 and verdict["prefix"]["all_native_attributes_identical"]
    main = list(csv.DictReader(io.StringIO(read(case, prefix / "prepared/expected-counts.csv").decode("utf-8-sig"))))
    first = [r for r in main if float(r["begin"]) == 3600]
    first_tae = sum(abs(int(after[r["detector_id"], float(r["begin"]), float(r["end"])]["nVehContrib"]) - int(r["expected_total"])) for r in first)
    assert len(first) == 13 and first_tae == {"window0": 9, "window0_remove3": 10}[label]
    return dict(measurement=measured, input_scope=scope, prefix52_all_native_attributes_equal=True, all53556=verdict["all53556"],
        first_main_window_TAE=first_tae, remaining117_main_TAE=measured["main130"]["TAE"] - first_tae,
        raw_verdict=verdict["status"], selected=False, reason="Main130 error is worse than source324; first-window improvement is not global success.")


def audit_packet(case):
    cases = {label: audit_case(case, label) for label in RUNS}
    return dict(status="review_required", formal_result=False, native_runs_by_freeze=0, cases=cases,
        traffic_runs_recorded=2, working_best_unchanged=dict(source_N=8597, main_TAE=324, main_MAE=324 / 130),
        calibration_complete=False, first_window_improvement_is_not_global_acceptance=True)


def verify(case):
    verify_manifest(case)
    verify_manifest(PARENT)
    assert sha(PARENT / "manifest.json") == PARENT_SHA == load(case / "source-references.json")["parent_manifest_sha256"]
    for row in load(case / "source-provenance.json")["snapshots"]:
        value = read(case, row["relative"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    for row in load(case / "historical-source-bindings.json")["bindings"]:
        if row["scope"] == "local_trace":
            assert any(r["sha256"] == row["sha256"] and r["path"] == row["target"] for r in load(case / "local-only-large-traces.json")["files"])
            continue
        value = read(case, row["target"]) if row["scope"] == "packet" else raw(REPO / safe_relative(row["target"]))
        assert hashlib.sha256(value).hexdigest() == row["sha256"]
    for name, digest in SEALED.items():
        assert hashlib.sha256(read(case, name)).hexdigest() == digest
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("Early quantity: both complete responses and unchanged source324 verified; no global improvement", flush=True)


def freeze():
    assert not (HERE / "manifest.json").exists() and (HERE / "README.md").is_file()
    assert sha(PARENT / "manifest.json") == PARENT_SHA
    assert all(sha(SOURCE / name) == digest for name, digest in SEALED.items())
    selected = {p.relative_to(SOURCE): p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and
        (len(p.relative_to(SOURCE).parts) == 1 or p.relative_to(SOURCE).parts[:2] in [("cases", label) for label in RUNS])}
    assert all(Path("cases") / label / "independent.json" in selected for label in RUNS)
    locations = {}
    for row in load(PARENT / "source-provenance.json")["snapshots"]:
        path = physical(PARENT, row["relative"])
        assert raw_sha(path) == row["raw_sha256"]
        locations[row["raw_sha256"]] = dict(scope="repository", target=path.relative_to(REPO).as_posix())
    local_names = {f"cases/{label}/evaluation/native/{name}" for label in RUNS for name in ("instant-e1.xml", "queues.xml")}
    aliases, provenance, local = {}, [], []
    def store(name, source):
        digest, relative = sha(source), name.as_posix()
        if relative in local_names:
            local.append(dict(path=str(source), relative=relative, bytes=source.stat().st_size, sha256=digest))
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
    for row in local:
        locations[row["sha256"]] = dict(scope="local_trace", target=row["path"])
    bindings = []
    for name, source in selected.items():
        if source.suffix == ".json":
            record = load(source)
            hashes = record.get("source_hashes", {}) if isinstance(record, dict) else {}
            for path, digest in hashes.items() if isinstance(hashes, dict) else []:
                if digest not in locations:
                    dependency = Path(path)
                    assert dependency.is_file() and sha(dependency) == digest, (path, digest)
                    if dependency.name in {"instant-e1.xml", "queues.xml"}:
                        local.append(dict(path=str(dependency), bytes=dependency.stat().st_size, sha256=digest, source_dependency=True))
                        locations[digest] = dict(scope="local_trace", target=str(dependency))
                    else:
                        store(Path("bound-dependencies") / digest / dependency.name, dependency)
                bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "source-provenance.json", dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / "byte-references.json", dict(aliases=aliases, rule="Exact raw SHA aliases only."))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parent_packet=PARENT.relative_to(REPO).as_posix(), parent_manifest_sha256=PARENT_SHA,
        source_case=PARENT_CASE.as_posix(), source_N=8597))
    save(HERE / "local-only-large-traces.json", dict(files=local))
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
