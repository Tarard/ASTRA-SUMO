"""Freeze three completed variable-N responses; never start SUMO."""

import argparse
from collections import Counter
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
PARENT = REPO / "benchmarks/hamburg_ring1_535_late_departure_feedback_20261024_v1"
PARENT_SHA = "ce28186c2dd46ba46041fb3eea9c74991fd9301839cc4e1196621ac2aa4020c9"
PARENT_CASE = Path("offset/cases/minus30")
SOURCE = REPO / "outputs/hamburg-119-late-demand-thinning-20261024-v1"
SUPPORT = REPO / "outputs/hamburg-119-h13-count-support-20261024-v1"
SUPPORT_SHA = "73449128e1830654809255e1785e7df00c5d8b6c417b6a6cb85d34bd74d30fce"
RUNS = {"remove28": 8612, "remove30": 8610, "remove43": 8597}
SEALED = {
    "source-support": SUPPORT_SHA,
    "independent-result": "410e4e60aaef60b1937b3284ac4d149b77520946f86bdc104db71ac6d70cf3cd",
    "response-decomposition": "91acff9222a03965ea1dc67a6cf250d1fd93f188592e6c2ec36a7e2f650f73de",
    "independent-refinement": "21bc2221cc386fc9047f247aa6dd014c6e8d23de245257ccc86b9d876080d2bf",
    "refinement-response": "a81c3e6acd15bb2921e5f454ae1bbfdcc7d2af6474b8be9fd15b5dfca0ca8412",
}
spec = spec_from_file_location("thinning_archive_helpers", PARENT / "freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, verify_manifest = prior.save, prior.safe_relative, prior.verify_manifest
audit_measurement, PREPARED, physical, intervals = prior.audit_measurement, prior.PREPARED, prior.physical, prior.intervals


def require_terminal_cases():
    states = {}
    for label in RUNS:
        path = SOURCE / "cases" / label / "verdict.json"
        assert path.is_file(), "All three closed verdicts are required. Active native XML is not read."
        status = load(path)["status"]
        assert status in {"diagnostic_measurement_complete", "invalid"}
        states[label] = status
    return states


def subset_scope(source_bytes, candidate_bytes, old_intents, new_intents, expected_n, proposal, vector):
    roots = [ET.fromstring(value) for value in (source_bytes, candidate_bytes)]
    old, new = [{r.get("id"): r for r in root.findall("vehicle")} for root in roots]
    assert len(old) == 8640 and len(new) == expected_n and new.keys() < old.keys()
    removed = old.keys() - new.keys()
    assert len(removed) == 8640 - expected_n and set(proposal["removed_vehicle_ids"]) == removed
    assert old.keys() == old_intents.keys() and new.keys() == new_intents.keys()
    for identity, row in new.items():
        assert ET.tostring(row) == ET.tostring(old[identity]) and new_intents[identity] == old_intents[identity]
    assert [r.get("id") for r in roots[1].findall("vehicle")] == [r.get("id") for r in roots[0].findall("vehicle") if r.get("id") in new]
    assert roots[0].attrib == roots[1].attrib and [ET.tostring(r) for r in roots[0] if r.tag != "vehicle"] == [ET.tostring(r) for r in roots[1] if r.tag != "vehicle"]
    blocks = [{ET.fromstring(b).get("id"): b for b in re.findall(rb"<vehicle\b[\s\S]*?</vehicle>", value)} for value in (source_bytes, candidate_bytes)]
    assert all(blocks[0][identity] == blocks[1][identity] for identity in new)
    source_q = [sum(r["od_cell"] == i for r in old_intents.values()) for i in range(294)]
    candidate_q = [sum(r["od_cell"] == i for r in new_intents.values()) for i in range(294)]
    assert vector["source_integer_counts"] == source_q and vector["integer_counts"] == candidate_q
    assert sum(source_q) == 8640 and sum(candidate_q) == expected_n
    assert all(a >= b for a, b in zip(source_q, candidate_q, strict=True))
    assert sum(a - b for a, b in zip(source_q, candidate_q, strict=True)) == len(removed)
    assert source_q[:84] == candidate_q[:84] and all(old_intents[i]["od_cell"] >= 273 for i in removed)
    return dict(source_N=8640, actual_candidate_N=expected_n, removed_ids=sorted(removed),
        survivors_exact_raw_subset_and_order=True, survivor_routes_departures_stops_fields_metadata_unchanged=True,
        actual294_source_total=8640, actual294_candidate_total=expected_n, q_L1=len(removed),
        removed_fields=dict(Counter(old_intents[i]["field_id"] for i in removed)))


def materialize_case(case, label, out):
    prefix = Path("cases") / label
    for row in load(case / "source-provenance.json")["snapshots"]:
        name = Path(row["relative"])
        if name.is_relative_to(prefix / "prepared") or name.is_relative_to(prefix / "evaluation"):
            target = out / name.relative_to(prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(case, name))


def audit_case(case, label):
    prefix = Path("cases") / label
    verdict = jread(case, prefix / "verdict.json")
    if verdict["status"] == "invalid":
        return dict(raw_verdict="invalid", metrics=None, partial_outputs_preserved=True)
    assert verdict["status"] == "diagnostic_measurement_complete"
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(case, prefix / "prepared" / name) == read(PARENT, PARENT_CASE / "prepared" / name)
    def intents(value):
        return {r["vehicle_id"]: r for r in json.loads(value)["vehicles"]}
    scope = subset_scope(read(PARENT, PARENT_CASE / "prepared/demand.rou.xml"), read(case, prefix / "prepared/demand.rou.xml"),
        intents(read(PARENT, PARENT_CASE / "prepared/lane-intentions.json")), intents(read(case, prefix / "prepared/lane-intentions.json")),
        RUNS[label], jread(case, prefix / "proposal.json"), jread(case, prefix / "od-vector.json"))
    for file in ("scope-proof.json", "preflight.json"):
        record = jread(case, prefix / file)
        for name, expected in record.get("prepared_hashes", {}).items():
            assert hashlib.sha256(read(case, prefix / "prepared" / name)).hexdigest() == expected
    preflight = jread(case, prefix / "preflight.json")
    assert preflight["source_N"] == 8640 and preflight["candidate_N"] == RUNS[label]
    assert preflight["scope_sha256"] == hashlib.sha256(read(case, prefix / "scope-proof.json")).hexdigest()
    for name, expected in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, prefix / "measurement-implementation" / name)).hexdigest() == expected
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        materialize_case(case, label, out)
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]}
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == verdict["after"]["station40_MAE"]
    assert measured["completion"]["loaded"] == RUNS[label]
    assert measured["healthy"] and measured["safe"] and measured["event_count"] == RUNS[label]
    assert len(outside) == len(verdict["contributions_outside_prior_primary_intention"]) and len(repeated) == len(verdict["repeated_vehicle_fields"])
    source_rows = intervals(read(PARENT, PARENT_CASE / "evaluation/native/field-counts.xml"))
    new_rows = intervals(read(case, prefix / "evaluation/native/field-counts.xml"))
    keys = [k for k in source_rows if k[2] <= 11700]
    assert len(keys) == 169 and all(source_rows[k] == new_rows[k] for k in keys)
    return dict(raw_verdict=verdict["status"], measurement=measured, input_scope=scope,
        prefix169_all_native_E1_attributes_equal=True, all53556=verdict["all53556"],
        source_real_N=8640, candidate_real_N=RUNS[label], unchanged_true_targets=True)


def audit_packet(case):
    cases = {label: audit_case(case, label) for label in RUNS}
    assert cases["remove43"]["measurement"]["main130"]["TAE"] == 324
    assert set(cases["remove28"]["input_scope"]["removed_ids"]) < set(cases["remove30"]["input_scope"]["removed_ids"]) < set(cases["remove43"]["input_scope"]["removed_ids"])
    return dict(status="review_required", formal_result=False, native_runs_by_freeze=0,
        source_real_N=8640, source_packet=PARENT.name, cases=cases, best_in_this_group="remove43",
        lowest_measured_healthy_main_MAE_in_this_group=324 / 130, calibration_complete=False, traffic_runs_recorded=3,
        variable_N_is_an_input_prior_not_runtime_vehicle_removal=True,
        historical_deleted_contributions_are_not_a_causal_response_prediction=True,
        real_OD_or_zero_residual_not_identified=True)


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
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("Variable-N thinning: source8640, actual populations, survivors and complete measurements pass", flush=True)


def freeze():
    require_terminal_cases()
    assert not (HERE / "manifest.json").exists() and (HERE / "README.md").is_file()
    assert sha(PARENT / "manifest.json") == PARENT_SHA and sha(SUPPORT / "manifest.json") == SUPPORT_SHA
    locations = {}
    for row in load(PARENT / "source-provenance.json")["snapshots"]:
        path = physical(PARENT, row["relative"])
        assert raw_sha(path) == row["raw_sha256"]
        locations[row["raw_sha256"]] = dict(scope="repository", target=path.relative_to(REPO).as_posix())
    selected = {}
    allowed_dirs = set(SEALED) - {"source-support"}
    top_files = {"prepare_thinning.py", "prepare_refinement.py", "run_thinning.py", "run_refinement.py"}
    for p in SOURCE.rglob("*"):
        name = p.relative_to(SOURCE)
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if name.parts[0] in allowed_dirs or len(name.parts) == 1 and name.name in top_files or len(name.parts) >= 2 and name.parts[0] == "cases" and name.parts[1] in RUNS:
            selected[name] = p
    for row in load(SUPPORT / "manifest.json")["files"]:
        name = safe_relative(row["path"])
        selected[Path("source-support") / name] = SUPPORT / name
    selected[Path("source-support/manifest.json")] = SUPPORT / "manifest.json"
    local_names = {f"cases/{label}/evaluation/native/{name}" for label in RUNS for name in ("instant-e1.xml", "queues.xml")}
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected.items()):
        digest, relative = sha(source), name.as_posix()
        if relative in local_names:
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
    for name, source in selected.items():
        if name.name in {"scope-proof.json", "measurement-plan.json"}:
            for path, digest in load(source).get("source_hashes", {}).items():
                assert digest in locations, (path, digest)
                bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parent_packet=PARENT.relative_to(REPO).as_posix(), parent_manifest_sha256=PARENT_SHA, source_case=PARENT_CASE.as_posix(), source_N=8640))
    save(HERE / "local-only-large-traces.json", dict(files=local))
    save(HERE / "results.json", dict(audit=audit_packet(HERE)))
    assert all(sha(Path(r["source"])) == r["raw_sha256"] for r in provenance)
    save(HERE / "manifest.json", dict(status="review_required", formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]))
    verify(HERE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
