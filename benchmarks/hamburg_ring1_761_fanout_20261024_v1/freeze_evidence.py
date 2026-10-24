"""Freeze the closed761 construction, smoke and two full responses; no SUMO runs."""

import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / "outputs/hamburg-761-fanout-20261024-v1"
SOURCE_AUDIT = REPO / "outputs/hamburg-445-761-source-audit-20261024-v1"
SOURCE_AUDIT_SHA = "b2b91220d59cc22f9e345c4ff05e001f67e1ed09b765a0aa30e23868fac33040"
NET_SHA = "91f94db4dcef1201b4ccdaa03a2c1b3cfdac3072fdb7af947fa8b4f981a73697"
CANDIDATE_MANIFEST_SHA = "fcd1acf57a241cea5faa710765f5a3f12e39300b87a9c8c169b2d31edbb021ff"
RESPONSE_FILES = {
    "response-summary.json": "422bb4932563c046d2e76b3d9ce39f9f7b5064446669fc20e2518c65a5b12789",
    "response-count-matrix.csv": "42c64f8bdc4db7155045c117a8aa284c286aafcfd9ec2b208c80c854770ff23e",
}
INDEPENDENT_FILES = {
    "full/retained8597/independent.json": "4ffb392d2a3ffd6469993264fc12082ab04906f2c6ad1824084e0a8aa503ea62",
    "full/restored8640/independent.json": "9d271006a7ae520f3168d2ebe975886b30ed7a2304f940be076dbefc28e08e79",
}
PARENTS = {
    "retained8597": ("hamburg_ring1_119_late_demand_thinning_20261024_v1", "9141f590930dec43aa7384ba4fb1c46dac9c631a452d87b660c97407f1a8bdd8", "cases/remove43", 8597, 324),
    "restored8640": ("hamburg_ring1_535_late_departure_feedback_20261024_v1", "ce28186c2dd46ba46041fb3eea9c74991fd9301839cc4e1196621ac2aa4020c9", "offset/cases/minus30", 8640, 365),
}
spec = spec_from_file_location("fanout_archive_helpers", REPO / "benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, save, safe_relative = prior.read, prior.jread, prior.save, prior.safe_relative
verify_manifest, physical, audit_measurement, PREPARED, intervals = prior.verify_manifest, prior.physical, prior.audit_measurement, prior.PREPARED, prior.intervals


def require_terminal():
    for label in PARENTS:
        case = SOURCE / "full" / label
        assert (case / "verdict.json").is_file(), "Wait for both terminal verdicts; active XML is not read."
        assert load(case / "verdict.json")["status"] == "diagnostic_measurement_complete"
        assert (case / "independent.json").is_file(), "Wait for both independent audits."
    assert RESPONSE_FILES and all(sha(SOURCE / name) == digest for name, digest in RESPONSE_FILES.items()), "Wait for the final response file/hash handoff."
    assert all(sha(SOURCE / name) == digest for name, digest in INDEPENDENT_FILES.items())


def audit_case(case, label):
    packet, seal, parent_case, count, parent_tae = PARENTS[label]
    parent, prefix = REPO / "benchmarks" / packet, Path("full") / label
    assert sha(parent / "manifest.json") == seal
    before = jread(parent, Path(parent_case) / "verdict.json")["after"]
    verdict, preflight, independent = [jread(case, prefix / n) for n in ("verdict.json", "preflight.json", "independent.json")]
    assert independent["schema"] == "astra.hamburg.761-fixed-demand-independent/v1" and independent["audit_status"] == "pass"
    assert independent["raw_instant_equals_compact"] and independent["runtime_and_snapshots_verified"]
    assert independent["after"] == verdict["after"] and independent["source"] == before
    assert verdict["source_N"] == verdict["candidate_N"] == count
    assert verdict["source_parent"] == before and before["metrics130"]["TAE"] == parent_tae
    assert hashlib.sha256(read(case, prefix / "prepared/network.net.xml")).hexdigest() == NET_SHA
    for name in PREPARED:
        if name not in {"network.net.xml", "scenario.json"}:
            assert read(case, prefix / "prepared" / name) == read(parent, Path(parent_case) / "prepared" / name)
    for name, digest in preflight["prepared_hashes"].items():
        assert hashlib.sha256(read(case, prefix / "prepared" / name)).hexdigest() == digest
    for name, digest in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, prefix / "measurement-implementation" / name)).hexdigest() == digest
    assert preflight["candidate_N"] == preflight["source_N"] == count
    assert preflight["backend"] == "libsumo" and (preflight["end_s"], preflight["step_s"], preflight["seed"]) == (14400, .1, 104)
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        for row in load(case / "source-provenance.json")["snapshots"]:
            name = Path(row["relative"])
            if name.is_relative_to(prefix / "prepared") or name.is_relative_to(prefix / "evaluation"):
                target = out / name.relative_to(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read(case, name))
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]}
        assert len(signatures) == count
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == verdict["after"]["station40_MAE"]
    prefix_result = jread(case, prefix / "evaluation/prefix-comparison.json")
    assert prefix_result["status"] == "not_applicable" and "all_native_attributes_identical" not in prefix_result
    return dict(measurement=measured, parent_N=count, parent_main_TAE=parent_tae, parent_measurement=before,
        unchanged_full_demand_bytes=True, no_unchanged_prefix_claim=True, all53556=verdict["all53556"],
        contributions_outside=len(outside), repeated_vehicle_fields=len(repeated), raw_verdict=verdict["status"])


def audit_packet(case):
    assert hashlib.sha256(read(case, "candidate-v2/network.net.xml")).hexdigest() == NET_SHA
    assert hashlib.sha256(read(case, "candidate-v2/manifest.json")).hexdigest() == CANDIDATE_MANIFEST_SHA
    candidate = jread(case, "candidate-v2/manifest.json")
    assert candidate["source_unchanged"] and candidate["checks"] and all(candidate["checks"].values())
    independent = jread(case, "independent-candidate.json")
    assert independent["status"] == "pass" and all(independent["checks"].values())
    assert independent["declared_scope"]["candidate_logical_internal_lengths_m"] == [8.555760785474] * 3
    smoke = {label: jread(case, f"smoke/native-{label}/result.json") for label in ("source", "candidate")}
    assert all(r["status"] == "pass" and all(r["checks"].values()) for r in smoke.values())
    assert [r["requested_direct_movement_covered"] for r in smoke["source"]["coverage"]] == [True, False, False]
    assert all(r["requested_direct_movement_covered"] for r in smoke["candidate"]["coverage"])
    return dict(status="review_required", formal_result=False, native_runs_by_freeze=0,
        cases={label: audit_case(case, label) for label in PARENTS}, native_full_runs_recorded=2, native_smoke_runs_recorded=2,
        ordinary_roads_unchanged=True, internal_native_group_mean_m=8.555760785474,
        physical_old_curve_preserved_but_logical_internal_length_changed=True,
        field_geometry_control_true_OD_and_zero_fit_not_certified=True)


def verify(case):
    verify_manifest(case)
    for packet, seal, _, _, _ in PARENTS.values():
        assert sha(REPO / "benchmarks" / packet / "manifest.json") == seal
    for row in load(case / "source-provenance.json")["snapshots"]:
        value = read(case, row["relative"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    for row in load(case / "historical-source-bindings.json")["bindings"]:
        if row["scope"] == "local_trace":
            assert any(r["sha256"] == row["sha256"] and r["path"] == row["target"] for r in load(case / "local-only-large-traces.json")["files"])
        else:
            value = read(case, row["target"]) if row["scope"] == "packet" else raw(REPO / safe_relative(row["target"]))
            assert hashlib.sha256(value).hexdigest() == row["sha256"]
    assert hashlib.sha256(read(case, "source-audit/manifest.json")).hexdigest() == SOURCE_AUDIT_SHA
    for name, digest in {**RESPONSE_FILES, **INDEPENDENT_FILES}.items():
        assert hashlib.sha256(read(case, name)).hexdigest() == digest
    for row in jread(case, "source-audit/manifest.json")["files"]:
        value = read(case, Path("source-audit") / row["path"])
        assert len(value) == row["bytes"] and hashlib.sha256(value).hexdigest() == row["sha256"]
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("761: two distinct fixed-demand comparisons and construction/smoke evidence verified", flush=True)


def freeze():
    require_terminal()
    assert not (HERE / "manifest.json").exists() and (HERE / "README.md").is_file()
    assert sha(SOURCE_AUDIT / "manifest.json") == SOURCE_AUDIT_SHA
    locations = {}
    for packet, seal, _, _, _ in PARENTS.values():
        parent = REPO / "benchmarks" / packet
        assert sha(parent / "manifest.json") == seal
        for row in load(parent / "source-provenance.json")["snapshots"]:
            path = physical(parent, row["relative"])
            assert raw_sha(path) == row["raw_sha256"]
            locations[row["raw_sha256"]] = dict(scope="repository", target=path.relative_to(REPO).as_posix())
    selected = {p.relative_to(SOURCE): p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    for row in load(SOURCE_AUDIT / "manifest.json")["files"]:
        selected[Path("source-audit") / row["path"]] = SOURCE_AUDIT / row["path"]
    selected[Path("source-audit/manifest.json")] = SOURCE_AUDIT / "manifest.json"
    for name in ("plugins/astra-sumo/src/astra_sumo/core/hamburg_761_connection_support.py", "tests/test_hamburg_761_connection_support.py"):
        selected[Path("source-code") / Path(name).name] = REPO / name
    aliases, provenance, local = {}, [], []
    def store(name, source):
        digest, relative = sha(source), name.as_posix()
        if len(name.parts) >= 5 and name.parts[0] == "full" and name.name in {"instant-e1.xml", "queues.xml"}:
            local.append(dict(path=str(source), relative=relative, bytes=source.stat().st_size, sha256=digest))
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
                    if dependency.name in {"instant-e1.xml", "queues.xml"}:
                        local.append(dict(path=str(dependency), bytes=dependency.stat().st_size, sha256=digest, source_dependency=True))
                        locations[digest] = dict(scope="local_trace", target=str(dependency))
                    else:
                        store(Path("bound-dependencies") / digest / dependency.name, dependency)
                bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "source-provenance.json", dict(snapshots=provenance, source_files_unchanged=True))
    save(HERE / "byte-references.json", dict(aliases=aliases, rule="Exact raw SHA aliases only."))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parents=PARENTS, source_audit_sha256=SOURCE_AUDIT_SHA))
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
