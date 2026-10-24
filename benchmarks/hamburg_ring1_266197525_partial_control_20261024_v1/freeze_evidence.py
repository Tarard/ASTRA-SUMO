"""Freeze the completed bounded525 family without starting SUMO."""

import argparse
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / "outputs/hamburg-266197525-partial-control-20261024-v1"
PARENT = REPO / "benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1"
PARENT_SHA = "3a5db2e98e7a36be5b5347b0d299cc074d157f23eefb6dd4620f1914054a0b94"
spec = spec_from_file_location("partial_control_archive_helpers", REPO / "benchmarks/hamburg_ring1_209_z5_spread_20261024_v1/freeze_evidence.py")
prior = module_from_spec(spec)
spec.loader.exec_module(prior)
raw, load, sha, raw_sha = prior.raw, prior.load, prior.sha, prior.raw_sha
read, jread, rows = prior.read, prior.jread, prior.rows
save, safe_relative, verify_manifest = prior.save, prior.safe_relative, prior.verify_manifest
audit_measurement, PREPARED, physical = prior.audit_measurement, prior.PREPARED, prior.physical
intervals = prior.prior.prior.intervals
LOCAL = {"full/evaluation/native/instant-e1.xml", "full/evaluation/native/queues.xml"}
SOURCES = {
    "control-audit": ("hamburg-266197525-control-audit-20261024-v1", "36e89ad4f7bee49371a6daa2604c564bc6c5b30e358666e12c14291c6d23b138"),
    "ownership-source": ("hamburg-266197525-ownership-source-20261024-v1", "1b87bffeecff4f8196273746bed48a429b935b86637efe1029b94f5ebbc57946"),
    "uncontrolled-semantics": ("hamburg-266197525-uncontrolled-semantics-20261024-v1", "89590a0abe764a50ffe85968dc61e9f555fbd4d4b09b31b9850bb50a845e9856"),
    "api-decoder": ("hamburg-libsumo-observation-decoder-20261024-v1", "6994be2c5d1a6f17e21dc0f8d8b8c1a7abbbf97c6dfa04ddb858722bc59e842e"),
}
STATIC_REVIEW_SHA = "3dff58d49fa333ebc275dc46e529d40859051818d921b9ee30c67234a2a3338f"
SEALED = {"independent-review": STATIC_REVIEW_SHA,
          "independent-full": "7219c044d411bda6ab09165cc4aa16bb495cb5ef79a50faf062f042ed0611aa3",
          "smoke": "002371d07cc5a9fa91833c70e37af5bef71ae51846baf84a5494f61221d3cb36",
          "smoke/episode-audit": "6214e0e00f692c8bf51000b8b8c6968f90b5ea2b75d40cc3c9bdf97d2bf640e4"}


def require_terminal(source):
    path = source / "full/verdict.json"
    assert path.is_file(), "A completed full verdict is required. Active native files are not read."
    verdict = load(path)
    assert verdict["status"] == "diagnostic_measurement_complete", "Retain incomplete or invalid results without a full-fit claim."
    assert (source / "reviewed-smoke-binding.json").is_file(), "Require the reviewed paired smoke binding."
    return verdict


def materialize_run(case, out):
    for row in load(case / "source-provenance.json")["snapshots"]:
        name = Path(row["relative"])
        if name.is_relative_to("full/prepared") or name.is_relative_to("full/evaluation"):
            target = out / name.relative_to("full")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(case, name))


def audit_construction(case):
    failed, passed = [jread(case, f"candidate-v{n}/manifest.json") for n in (1, 2)]
    assert failed["status"] == "blocked" and not failed["checks"]["requests_and_foes_same_by_movement"]
    assert passed["status"] == "review_required" and all(passed["checks"].values()) and passed["source_unchanged"]
    assert passed["candidate_sha256"] == hashlib.sha256(read(case, "candidate-v2/network.net.xml")).hexdigest() == "6c52382e7664eca33fceff6fd02ae700d93139a316a3f9f5139d0e139d7a18ce"
    assert read(case, "candidate-v2/control.add.xml") == read(PARENT, "full/prepared/control.add.xml")
    assert sorted(r["cont"] for r in failed["request_mapping"]) == ["0", "0", "0", "1", "1", "1"]
    assert all(r["cont"] == "0" for r in passed["request_mapping"])
    for label, (_, expected) in SOURCES.items():
        assert hashlib.sha256(read(case, Path("source-audits") / label / "manifest.json")).hexdigest() == expected
    assert hashlib.sha256(read(case, "independent-review/manifest.json")).hexdigest() == STATIC_REVIEW_SHA
    return dict(status="review_required", v1_retained_blocked_for_native_cont1=True,
                v2_explicit_contPos0_preserves_source_cont0=True, requests_not_hand_overlaid=True,
                outside_roads_programs_and_permissions_protected=True, field_control_or_SPAT_certified=False)


def audit_smoke(case):
    binding = jread(case, "reviewed-smoke-binding.json")
    assert hashlib.sha256(read(case, "reviewed-smoke-binding.json")).hexdigest() == "178a280e16408da1d17c1ca198a26d3033c86b371787703249a0b8706d5edeac"
    assert binding["status"] == "pass" and len(binding["native_cases"]) == 6 and binding["vehicles_per_case"] == 16
    calls = []
    for folder, role in binding["native_cases"]:
        prefix = Path("smoke") / folder / ("native-" + role)
        trips = ET.fromstring(read(case, prefix / "tripinfo.xml")).findall("tripinfo")
        result = jread(case, prefix / "result.json")
        assert len(trips) == 16 and all(float(r.get("arrival")) >= 0 and not r.get("vaporized", "") for r in trips)
        assert not any(result["safety"].values())
        calls.append(dict(folder=folder, role=role, arrived=16, unsafe=0))
    final = jread(case, "smoke/family-final.json")
    assert final["status"] == "pass" and final["all96_arrived"] and final["unsafe_total"] == 0
    assert final["supplemental_after_full_launch"] and final["old_verdicts_preserved"]
    return dict(calls=calls, total_vehicle_runs=96, old_review_verdicts_preserved=True,
                root_raw_binding_precedes_full=True, detailed_episode_is_later_explanation=True,
                car_and_bicycle_mechanisms_use_different_timetables=True, universal_safety_claimed=False)


def audit_full(case):
    verdict = jread(case, "full/verdict.json")
    assert verdict["status"] == "diagnostic_measurement_complete"
    assert verdict["source_209_parent"] == jread(PARENT, "full/verdict.json")["after"]
    for name in ("control.add.xml", "demand.rou.xml", "lane-intentions.json", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert read(case, Path("full/prepared") / name) == read(PARENT, Path("full/prepared") / name)
    assert read(case, "full/prepared/network.net.xml") == read(case, "candidate-v2/network.net.xml")
    proof = jread(case, "full/scope-proof.json")
    for name, expected in proof["prepared_hashes"].items():
        assert hashlib.sha256(read(case, Path("full/prepared") / name)).hexdigest() == expected
    preflight = jread(case, "full/preflight.json")
    assert preflight["scope_sha256"] == hashlib.sha256(read(case, "full/scope-proof.json")).hexdigest()
    for name, expected in preflight["implementation_sha256"].items():
        assert hashlib.sha256(read(case, Path("full/implementation") / name)).hexdigest() == expected
    assert hashlib.sha256(read(case, "full/executed-driver.py")).hexdigest() == preflight["implementation_sha256"]["run_full.py"]
    shared = preflight["shared_measurement_driver"]
    assert shared["sha256"] == shared["source_sha256"] == hashlib.sha256(read(case, "full/executed-shared-driver.py")).hexdigest()
    binding = jread(case, "reviewed-smoke-binding.json")
    assert binding["status"] == "pass" and binding["candidate_network_sha256"] == proof["prepared_hashes"]["network.net.xml"]
    assert preflight["smoke"]["sha256"] == hashlib.sha256(read(case, "reviewed-smoke-binding.json")).hexdigest()
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        materialize_run(case, out)
        signatures = {r["vehicle_id"]: [r["field_id"]] for r in json.loads((out / "prepared/lane-intentions.json").read_bytes())["vehicles"]}
        measured, _, outside, repeated = audit_measurement(out / "prepared", out / "evaluation", signatures)
    for a, b in (("main130", "metrics130"), ("initial52", "metrics52"), ("all182", "metrics182")):
        assert all(value == verdict["after"][b][key] for key, value in measured[a].items())
    assert measured["completion"] == verdict["after"]["completion"] and measured["station40_MAE"] == verdict["after"]["station40_MAE"]
    assert len(outside) == len(verdict["contributions_outside_prior_primary_intention"])
    assert len(repeated) == len(verdict["repeated_vehicle_fields"])
    independent = jread(case, "independent-full/summary.json")
    assert independent["schema"] == "astra.hamburg.525-partial-control-full-independent/v1" and independent["audit_status"] == "pass"
    assert independent["after"] == verdict["after"] and independent["references"]["parent209"] == verdict["source_209_parent"]
    assert measured["healthy"] and measured["safe"] and measured["event_count"] == 8640 and not outside and not repeated
    assert measured["main130"]["TAE"] == 395 and measured["main130"]["max_abs_error"] == 26
    return dict(raw_verdict=verdict["status"], measurement=measured, all53556=verdict["all53556"], comparisons=verdict["comparison"])


def audit_packet(case):
    return dict(status="review_required", formal_result=False, construction=audit_construction(case), smoke=audit_smoke(case), full=audit_full(case),
                native_runs_by_freeze=0, traffic_runs_recorded=7, scoped_conversions_recorded=2,
                lowest_measured_healthy_main_MAE=395 / 130, calibration_complete=False, maximum_error_regresses_21_to26=True,
                new_public_surface=False, actual_actuated_history_not_assumed_unchanged=True)


def verify(case):
    verify_manifest(case)
    verify_manifest(PARENT)
    assert sha(PARENT / "manifest.json") == PARENT_SHA == load(case / "source-references.json")["parent_manifest_sha256"]
    for row in load(case / "source-provenance.json")["snapshots"]:
        value = read(case, row["relative"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    sealed = {**SEALED, **{str(Path("source-audits") / label): expected for label, (_, expected) in SOURCES.items()}}
    for folder, expected in sealed.items():
        assert hashlib.sha256(read(case, Path(folder) / "manifest.json")).hexdigest() == expected
        for row in jread(case, Path(folder) / "manifest.json")["files"]:
            value = read(case, Path(folder) / safe_relative(row["path"]))
            assert len(value) == row.get("bytes", row.get("size_bytes")) and hashlib.sha256(value).hexdigest() == row["sha256"]
    for row in load(case / "historical-source-bindings.json")["bindings"]:
        value = read(case, row["target"]) if row["scope"] == "packet" else raw(REPO / safe_relative(row["target"]))
        assert hashlib.sha256(value).hexdigest() == row["sha256"]
    assert {r["relative"] for r in load(case / "local-only-large-traces.json")["files"]} == LOCAL
    assert audit_packet(case) == load(case / "results.json")["audit"]
    print("525 partial control: source scope, retained failures and complete native comparisons pass", flush=True)


def freeze():
    require_terminal(SOURCE)
    assert not (HERE / "manifest.json").exists(), "Preserve the existing frozen packet."
    assert (HERE / "README.md").is_file(), "Write the measured result and claim boundaries before freezing."
    assert sha(PARENT / "manifest.json") == PARENT_SHA
    locations = {}
    for row in load(PARENT / "source-provenance.json")["snapshots"]:
        target = physical(PARENT, row["relative"])
        assert raw_sha(target) == row["raw_sha256"]
        locations[row["raw_sha256"]] = dict(scope="repository", target=target.relative_to(REPO).as_posix())
    selected = {p.relative_to(SOURCE): p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    for name in (Path("README.md"), Path("manifest.json")):
        if name in selected:
            selected[Path("study-source") / name] = selected.pop(name)
    for label, (folder, expected) in SOURCES.items():
        source = REPO / "outputs" / folder
        assert sha(source / "manifest.json") == expected
        selected.update({Path("source-audits") / label / p.relative_to(source): p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts})
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
    for name in ("full/scope-proof.json", "candidate-v1/manifest.json", "candidate-v2/manifest.json", "reviewed-smoke-binding.json"):
        source = load(SOURCE / name)
        for path, digest in source.get("source_hashes", source.get("reviewed_evidence_hashes", {})).items():
            assert digest in locations, (path, digest)
            bindings.append(dict(declared_path=path, sha256=digest, **locations[digest]))
    save(HERE / "historical-source-bindings.json", dict(bindings=bindings, historical_hashes_not_rewritten=True))
    save(HERE / "source-references.json", dict(parent_packet=PARENT.relative_to(REPO).as_posix(), parent_manifest_sha256=PARENT_SHA))
    save(HERE / "local-only-large-traces.json", dict(files=local, rule="Only the two full-run large traces remain local."))
    save(HERE / "results.json", dict(audit=audit_packet(HERE)))
    assert all(sha(Path(r["source"])) == r["raw_sha256"] for r in provenance)
    save(HERE / "manifest.json", dict(status="review_required", formal_result=False, files=[dict(path=p.relative_to(HERE).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(HERE.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]))
    verify(HERE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else verify(HERE)
