"""Freeze the five completed late535 responses; never start SUMO."""

import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PARENT = REPO / "benchmarks/hamburg_ring1_266197525_partial_control_20261024_v1"
PARENT_SHA = "3f3c6c31f80169017b28a70d73fea5354d8bbe3e083628b8b93d308d8130dec6"
spec = spec_from_file_location("late535_archive_helpers", PARENT / "freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, verify_manifest = prior.save, prior.safe_relative, prior.verify_manifest
audit_measurement, PREPARED, physical, intervals = prior.audit_measurement, prior.PREPARED, prior.physical, prior.intervals
FAMILIES = {
    "compression": REPO / "outputs/hamburg-535z4-tail-compression-20261024-v1",
    "offset": REPO / "outputs/hamburg-535z4-tail-offset-20261024-v1",
    "extension": REPO / "outputs/hamburg-535z4-tail-offset-extension-20261024-v1",
}
RUNS = {"compress29": ("compression", "compress29"), "minus30": ("offset", "cases/minus30"),
        "plus30": ("offset", "cases/plus30"), "minus60": ("extension", "cases/minus60"),
        "minus90": ("extension", "cases/minus90")}
RESIDUAL = REPO / "outputs/hamburg-266197525-partial-control-20261024-v1/residual-diagnosis"
SEALED = {
    "parent-post-residual": "05d43f9eee5dcb78c4998de7dff47a5e7521b6c78fe9cd7124d4ab51bdb0ffeb",
    "compression/independent-result": "5e60347e983af522030af028fc6115695d6af20c0ae2472e8481c5399e1b79da",
    "compression/response-decomposition": "6a2c482c8acfaecd2b7ca9cf8c1271c1ec064b66f9817071ee5be77e071d8477",
    "compression/route-exit-localization": "c823297ad10ff02b743841c1b17d7b331c41cc24fdd03a4ff3fb700fbc4584be",
    "offset/independent-result": "33a78deb75a5e8d5b95b695b27a7b198cd56f676f9d62c0e462e3e46fd2ae145",
    "offset/response-decomposition": "82447c8839ba8d84d8cc5decfb225e3499cbf8161cbd6e569087edcd0aa40ee7",
    "extension/independent-result": "1b91ff31f18f76d5311e93e21f7ac50dde53a48daf5916ac27d6bfbdb2f09bf8",
    "extension/response-decomposition": "b6705a3f0158b341914820837c48386ed65124547143105e8966ea8527d67494",
}


def require_all_terminal():
    result = {}
    for label, (family, relative) in RUNS.items():
        path = FAMILIES[family] / relative / "verdict.json"
        assert path.is_file(), "All five terminal verdicts are required. Active native files are not read."
        verdict = load(path)
        assert verdict["status"] in {"diagnostic_measurement_complete", "invalid"}, "Preserve unknown states without interpreting partial XML."
        result[label] = verdict["status"]
    return result


def case_prefix(label):
    family, relative = RUNS[label]
    return Path(family) / relative


def materialize_case(case, label, out):
    prefix = case_prefix(label)
    for row in load(case / "source-provenance.json")["snapshots"]:
        name = Path(row["relative"])
        if name.is_relative_to(prefix / "prepared") or name.is_relative_to(prefix / "evaluation"):
            target = out / name.relative_to(prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(case, name))


def audit_case(case, label):
    prefix = case_prefix(label)
    verdict = jread(case, prefix / "verdict.json")
    if verdict["status"] == "invalid":
        return dict(raw_verdict="invalid", partial_outputs_preserved=True, complete_metrics=None,
                    unmeasured_values_are_unknown_not_zero=True)
    assert verdict["status"] == "diagnostic_measurement_complete"
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(case, prefix / "prepared" / name) == read(PARENT, Path("full/prepared") / name)
    before_bytes, after_bytes = read(PARENT, "full/prepared/demand.rou.xml"), read(case, prefix / "prepared/demand.rou.xml")
    before, after = ET.fromstring(before_bytes), ET.fromstring(after_bytes)
    old, new = [{v.get("id"): v for v in root.findall("vehicle")} for root in (before, after)]
    assert len(old) == len(new) == 8640 and old.keys() == new.keys()
    selected = {f"od_0256_v{i:05d}" for i in range(29)}
    changed = set()
    for identity, source in old.items():
        candidate = new[identity]
        assert {k: v for k, v in source.attrib.items() if k != "depart"} == {k: v for k, v in candidate.attrib.items() if k != "depart"}
        assert [ET.tostring(r) for r in source] == [ET.tostring(r) for r in candidate]
        if source.get("depart") != candidate.get("depart"):
            changed.add(identity)
        else:
            assert ET.tostring(source) == ET.tostring(candidate)
    assert changed == selected
    old_intents = {r["vehicle_id"]: r for r in jread(PARENT, "full/prepared/lane-intentions.json")["vehicles"]}
    new_intents = {r["vehicle_id"]: r for r in jread(case, prefix / "prepared/lane-intentions.json")["vehicles"]}
    assert old_intents.keys() == new_intents.keys() == old.keys()
    assert all(old_intents[i]["field_id"] == new_intents[i]["field_id"] for i in old)
    assert all(old_intents[i] == new_intents[i] for i in old.keys() - selected)
    pairs = sorted({(v.find("route").get("edges").split()[0], v.find("route").get("edges").split()[-1]) for v in old.values()})
    assert len(pairs) == 21
    inventories = [[0] * 294, [0] * 294]
    def ms(value):
        return math.floor(float(value) * 1000 + .5)
    crossing = []
    for identity in old:
        path = old[identity].find("route").get("edges").split()
        od = pairs.index((path[0], path[-1]))
        cells = [(ms(v.get("depart")) // 900000) * 21 + od for v in (old[identity], new[identity])]
        assert cells == [old_intents[identity]["od_cell"], new_intents[identity]["od_cell"]]
        for n, cell in enumerate(cells):
            inventories[n][cell] += 1
        if cells[0] != cells[1]:
            crossing.append([identity, cells[0], cells[1]])
    expected_crossing = [["od_0256_v00000", 256, 235]] if label in {"minus60", "minus90"} else []
    assert crossing == expected_crossing and sum(inventories[0]) == sum(inventories[1]) == 8640
    proposal = jread(case, prefix / "proposal.json")
    if label == "compress29":
        assert all(ms(new[i].get("depart")) == ms(proposal["anchor_s"] + proposal["beta"] * (float(old[i].get("depart")) - proposal["anchor_s"])) for i in selected)
    else:
        shift = {"minus30": -30, "plus30": 30, "minus60": -60, "minus90": -90}[label]
        assert all(ms(new[i].get("depart")) - ms(old[i].get("depart")) == shift * 1000 for i in selected)
    for name in ("scope-proof.json", "preflight.json"):
        record = jread(case, prefix / name)
        if "prepared_hashes" in record:
            for file, expected in record["prepared_hashes"].items():
                assert hashlib.sha256(read(case, prefix / "prepared" / file)).hexdigest() == expected
    preflight = jread(case, prefix / "preflight.json")
    assert preflight["scope_sha256"] == hashlib.sha256(read(case, prefix / "scope-proof.json")).hexdigest()
    for name, expected in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, prefix / "implementation" / name)).hexdigest() == expected
    shared = preflight["shared_measurement_driver"]
    assert shared["sha256"] == shared["source_sha256"] == hashlib.sha256(read(case, prefix / "executed-shared-driver.py")).hexdigest()
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        materialize_case(case, label, out)
        intentions = json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in intentions}
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    for local, saved in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][saved][key] for key, value in measured[local].items())
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == verdict["after"]["station40_MAE"]
    assert len(outside) == len(verdict["contributions_outside_prior_primary_intention"]) and len(repeated) == len(verdict["repeated_vehicle_fields"])
    assert measured["healthy"] and measured["safe"] and measured["event_count"] == 8640 and not outside and not repeated
    parent_counts = intervals(read(PARENT, "full/evaluation/native/field-counts.xml"))
    candidate_counts = intervals(read(case, prefix / "evaluation/native/field-counts.xml"))
    prefix_keys = [k for k in parent_counts if k[2] <= 10800]
    assert len(prefix_keys) == 156 and all(parent_counts[k] == candidate_counts[k] for k in prefix_keys)
    return dict(raw_verdict=verdict["status"], measurement=measured, all53556=verdict["all53556"],
                changed_departures=29, unchanged_routes_fields_and_observations=True,
                prefix156_all_native_E1_attributes_equal=True, actual_cell_crossings=crossing,
                q_L1=sum(abs(a - b) for a, b in zip(*inventories, strict=True)),
                q_may_change_when_actual_departures_cross_bins=True)


def audit_packet(case):
    cases = {label: audit_case(case, label) for label in RUNS}
    assert cases["minus30"]["measurement"]["main130"]["TAE"] == 365
    assert min((r["measurement"]["main130"]["TAE"], name) for name, r in cases.items()) == (365, "minus30")
    return dict(status="review_required", formal_result=False, native_runs_by_freeze=0,
                parent_packet=PARENT.name, cases=cases, lowest_measured_healthy_main_MAE=365 / 130,
                best_measured_case="minus30", calibration_complete=False, traffic_runs_recorded=5,
                response_decomposition_is_not_causal_isolation=True,
                residual_diagnosis_is_post_parent_explanation=True, smoothness_or_capacity_not_inferred=True)


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
    allowed = {(case_prefix(label) / "evaluation/native" / name).as_posix() for label in RUNS for name in ("instant-e1.xml", "queues.xml")}
    assert {r["relative"] for r in load(case / "local-only-large-traces.json")["files"]} <= allowed
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("Five late535 cases: complete measurements, preserved failures and fixed parent bytes pass", flush=True)


def freeze():
    require_all_terminal()
    for root in FAMILIES.values():
        assert (root / "independent-result/manifest.json").is_file(), "Require each completed family independent audit before freezing."
    assert not (HERE / "manifest.json").exists(), "Preserve the frozen packet."
    assert (HERE / "README.md").is_file(), "Write actual results and boundaries before freezing."
    assert sha(PARENT / "manifest.json") == PARENT_SHA
    locations = {}
    for row in load(PARENT / "source-provenance.json")["snapshots"]:
        target = physical(PARENT, row["relative"])
        assert raw_sha(target) == row["raw_sha256"]
        locations[row["raw_sha256"]] = dict(scope="repository", target=target.relative_to(REPO).as_posix())
    selected = {Path(family) / p.relative_to(root): p for family, root in FAMILIES.items() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    selected.update({Path("parent-post-residual") / p.relative_to(RESIDUAL): p for p in RESIDUAL.rglob("*") if p.is_file() and "__pycache__" not in p.parts})
    allowed = {(case_prefix(label) / "evaluation/native" / name).as_posix() for label in RUNS for name in ("instant-e1.xml", "queues.xml")}
    aliases, provenance, local = {}, [], []
    for name, source in sorted(selected.items()):
        digest, relative = sha(source), name.as_posix()
        if relative in allowed:
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
        if name.name not in {"scope-proof.json", "measurement-plan.json"}:
            continue
        for path, digest in load(source).get("source_hashes", {}).items():
            assert digest in locations, (path, digest)
            bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parent_packet=PARENT.relative_to(REPO).as_posix(), parent_manifest_sha256=PARENT_SHA))
    save(HERE / "local-only-large-traces.json", dict(files=local, rule="Only named large native traces remain local."))
    save(HERE / "results.json", dict(audit=audit_packet(HERE)))
    assert all(sha(Path(r["source"])) == r["raw_sha256"] for r in provenance)
    save(HERE / "manifest.json", dict(status="review_required", formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]))
    verify(HERE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
