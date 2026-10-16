from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest

from astra_sumo.core import public_reference_corpus as corpus
from astra_sumo.core.candidate_contracts import file_sha256


def _fixture(tmp_path, *, reference=None, source=None, relation="upstream_prepared_build_input"):
    cache = tmp_path / "cache"
    folder = cache / "toy"
    folder.mkdir(parents=True)
    net = folder / "reference.net.xml"
    if reference:
        shutil.copy2(reference, net)
    else:
        net.write_text('''<net><junction id="1" type="dead_end"/>
        <junction id="cluster_2_3" type="priority"/><junction id="4" type="dead_end"/>
        <edge id="a" from="1" to="cluster_2_3"><lane id="a_0" index="0"/></edge>
        <edge id="b" from="cluster_2_3" to="4"><lane id="b_0" index="0"/></edge>
        <connection from="a" to="b" fromLane="0" toLane="0"/></net>''', encoding="utf-8")
    license_path = folder / "LICENSE"
    license_path.write_text("Synthetic test fixture", encoding="utf-8")
    files = [("reference_net", net), ("license", license_path)]
    if source:
        osm = folder / "source.osm"
        shutil.copy2(source, osm)
        files.append(("source_osm", osm))
    document = {"schema": "astra.public-network-catalog/v1", "datasets": [{
        "id": "toy", "family_id": "toy_city", "repository": "https://github.com/test/fixture", "commit": "1" * 40,
        "license": "test fixture", "source_relation": relation, "osm_snapshot_date": None, "field_validity_date": None,
        "limitations": ["Synthetic model fixture. No field claim."], "files": [
            {"role": role, "path": path.name, "sha256": file_sha256(path), "bytes": path.stat().st_size}
            for role, path in files]}]}
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(document), encoding="utf-8")
    return catalog, cache, document


def _fake_load(**kwargs):
    folder = kwargs["output_dir"]
    folder.mkdir(parents=True)
    for name in ("report.json", "manifest.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    return {"status": "pass", "report_file": str(folder / "report.json"), "manifest_file": str(folder / "manifest.json")}


def test_membership_keeps_nested_negative_truncated_and_ambiguous_ids_distinct():
    assert corpus._members("cluster_1_cluster_2_-3") == ["-3", "1", "2"]
    assert corpus._members("cluster_1_2_#3more") is None
    assert corpus._members("cluster_1_1") is None
    assert corpus._members("new_manual_node") is None
    raw = ET.fromstring('<net><edge from="1" to="3"/></net>')
    reference = ET.fromstring('<net><junction id="1"/><junction id="cluster_1_2"/><junction id="3"/></net>')
    labels = list(corpus._pair_labels(raw, reference, {"id": "test", "family_id": "test"}, "binding"))
    assert labels[0]["expected_join"] is None
    assert labels[0]["status"] == "unknown_correspondence"


@pytest.mark.parametrize("path", ["../escape", "a/../escape", "C:/escape", "a\\escape", "/escape", "a/./b"])
def test_catalog_rejects_unsafe_or_aliased_artifact_paths(tmp_path, path):
    catalog, _, document = _fixture(tmp_path)
    document["datasets"][0]["files"][0]["path"] = path
    catalog.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="relative paths"):
        corpus._catalog(catalog)


def test_prepared_import_preserves_elevation_and_bound_types_without_upstream_output_paths(tmp_path):
    osm, types, config = (tmp_path / name for name in ("source.osm", "types.xml", "build.netcfg"))
    config.write_text('''<configuration><input><osm-files value="source.osm"/><type-files value="types.xml"/></input>
        <processing><osm.elevation value="true"/><junctions.join value="true"/><geometry.remove value="true"/></processing>
        <output><output-file value="../../outside.net.xml"/></output>
        <report><log value="../../outside.log"/></report></configuration>''', encoding="utf-8")
    files = [({"role": role}, path) for role, path in (("source_osm", osm), ("type_map", types), ("build_config", config))]
    arguments, recorded = corpus._prepared_import_arguments(files, osm)
    assert recorded == config
    assert arguments == ["--type-files", str(types), "--osm.elevation=true"]
    assert not any("outside" in value for value in arguments)
    full, _ = corpus._prepared_import_arguments(files, osm, unjoined=False)
    assert full == [*arguments, "--junctions.join=true", "--geometry.remove=true"]


@pytest.mark.parametrize("content,reason", [
    ('<input><osm-files value="other.osm"/></input>', "OSM input"),
    ('<input><osm-files value="source.osm"/><type-files value="unlisted.xml"/></input>', "type inputs"),
    ('<input><osm-files value="source.osm"/></input><processing><additional-files value="outside.xml"/></processing>', "Unsupported"),
    ('<input><osm-files value="source.osm"/></input><unknown value="true"/>', "Unsupported"),
])
def test_prepared_import_rejects_unbound_inputs_and_unsupported_settings(tmp_path, content, reason):
    config = tmp_path / "build.netcfg"
    config.write_text(f"<configuration>{content}</configuration>", encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        corpus._prepared_import_arguments([({"role": "build_config"}, config)], tmp_path / "source.osm")


def test_prepared_import_uses_option_semantics_across_configuration_sections(tmp_path):
    config = tmp_path / "build.netcfg"
    config.write_text('''<configuration><input><osm-files value="source.osm"/></input>
        <junctions><crossings.guess value="true"/></junctions><formats><precision value="8"/></formats>
        <report><ignore-errors value="false"/><log value="../../ignored.log"/></report></configuration>''', encoding="utf-8")
    arguments, _ = corpus._prepared_import_arguments([({"role": "build_config"}, config)], tmp_path / "source.osm")
    assert arguments == ["--crossings.guess=true", "--precision=8", "--ignore-errors=false"]
    config.write_text('''<configuration><input><osm-files value="source.osm"/></input>
        <output><unknown-content-option value="true"/></output></configuration>''', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported prepared import setting"):
        corpus._prepared_import_arguments([({"role": "build_config"}, config)], tmp_path / "source.osm")


@pytest.mark.parametrize("joined", [True, False, None])
def test_generator_control_attributes_exact_labels_without_manual_inference(tmp_path, monkeypatch, joined):
    source = ET.fromstring('''<net><location projParameter="!" netOffset="0,0"/>
        <junction id="1" x="0" y="0"/><junction id="2" x="1" y="0"/>
        <edge id="a" from="1" to="2"/></net>''')
    reference = ET.fromstring('''<net><location projParameter="!" netOffset="0,0"/>
        <junction id="cluster_1_2" x="0.5" y="0"/></net>''')
    osm, config = tmp_path / "source.osm", tmp_path / "build.netcfg"
    osm.write_text("<osm/>", encoding="utf-8")
    config.write_text('''<configuration><input><osm-files value="source.osm"/></input>
        <processing><junctions.join value="true"/></processing></configuration>''', encoding="utf-8")
    labels = list(corpus._pair_labels(source, reference, {"id": "toy", "family_id": "toy"}, "binding"))
    labels.append({**labels[0], "case_id": "unknown", "expected_join": None})
    label_file = tmp_path / "labels.jsonl"
    label_file.write_text("\n".join(json.dumps(row) for row in labels), encoding="utf-8")
    before = file_sha256(label_file)

    def run(command, **kwargs):
        assert "--junctions.join=true" in command
        assert "--offset.disable-normalization" not in command
        if joined is not None:
            ET.ElementTree(reference if joined else source).write(command[-1])
        return SimpleNamespace(to_dict=lambda: {"status": "fail" if joined is None else "pass", "returncode": 1 if joined is None else 0})

    monkeypatch.setattr(corpus, "run_command", run)
    monkeypatch.setattr(corpus, "run_sumo_load_audit", _fake_load)
    control = corpus._generator_control(files=[({"role": "build_config"}, config)], osm=osm, source=source,
        label_file=label_file, folder=tmp_path, netconvert_binary="netconvert", sumo_binary="sumo")
    assert control["human_cleaning_evaluation"] is False
    assert file_sha256(label_file) == before
    if joined is None:
        assert control["status"] == "failed"
        assert "label_provenance" not in control
    else:
        outcome = "recipe_reproduced" if joined else "unexplained_difference"
        assert control["counts"] == {outcome: 1, "unknown_reference": 1}
        assert control["baseline_difference_counts"] == {outcome: 1}
        rows = [json.loads(line) for line in Path(control["label_provenance"]["path"]).read_text().splitlines()]
        assert rows[0]["case_id"] == labels[0]["case_id"]
        assert rows[0]["unjoined_prediction"] is False


def test_corrupt_cache_is_preserved_and_wrong_download_never_commits(tmp_path, monkeypatch):
    catalog, cache, document = _fixture(tmp_path)
    dataset = document["datasets"][0]
    record = dataset["files"][0]
    net = cache / "toy" / record["path"]
    net.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="new cache"):
        corpus._cached_artifact(cache, dataset, record)
    assert net.read_text() == "corrupt"
    monkeypatch.setattr(corpus, "urlopen", lambda *_args, **_kwargs: BytesIO(b"wrong artifact"))
    new_cache = tmp_path / "fresh"
    with pytest.raises(ValueError, match="frozen identity"):
        corpus._cached_artifact(new_cache, dataset, record)
    assert not (new_cache / "toy" / record["path"]).exists()


def test_successful_download_is_hash_checked_and_reused_offline(tmp_path, monkeypatch):
    _, cache, document = _fixture(tmp_path)
    dataset = document["datasets"][0]
    record = dataset["files"][0]
    payload = (cache / "toy" / record["path"]).read_bytes()
    urls = []

    def fetch(url, **_kwargs):
        urls.append(url)
        return BytesIO(payload)

    monkeypatch.setattr(corpus, "urlopen", fetch)
    target = corpus._cached_artifact(tmp_path / "fresh", dataset, record)
    assert file_sha256(target) == record["sha256"]
    assert dataset["commit"] in urls[0]
    monkeypatch.setattr(corpus, "urlopen", lambda *_a, **_k: pytest.fail("Intact cache must work offline"))
    assert corpus._cached_artifact(tmp_path / "fresh", dataset, record) == target


def test_offline_corpus_retains_unpaired_reference_targets_and_inputs(tmp_path, monkeypatch):
    catalog, cache, _ = _fixture(tmp_path)
    monkeypatch.setattr(corpus, "run_sumo_load_audit", _fake_load)
    monkeypatch.setattr(corpus, "urlopen", lambda *_a, **_k: pytest.fail("Cached inputs must work offline"))
    before = file_sha256(cache / "toy/reference.net.xml")
    report = corpus.build_public_reference_corpus(catalog_file=catalog, cache_dir=cache, output_dir=tmp_path / "out")
    assert report["datasets"][0]["model_target_count"] == 3
    assert report["datasets"][0]["paired_labels"]["count"] == 0
    dataset = json.loads(Path(report["datasets"][0]["report"]["path"]).read_text())
    assert dataset["pairing_reason"] == "original_osm_not_published_in_this_snapshot"
    assert file_sha256(cache / "toy/reference.net.xml") == before
    assert report["formal_human_trial"] == "not_modified"
    assert report["field_precision"] is None
    manifest = json.loads(Path(report["manifest_file"]).read_text())
    for row in manifest["artifacts"]:
        assert file_sha256(Path(row["path"])) == row["sha256"]
    with pytest.raises(ValueError, match="new corpus"):
        corpus.build_public_reference_corpus(catalog_file=catalog, cache_dir=cache, output_dir=tmp_path / "out")


def _native_inputs(tmp_path):
    source = tmp_path / "original.osm"
    source.write_text('''<osm version="0.6"><bounds minlat="48.75" minlon="11.40" maxlat="48.76" maxlon="11.42"/>
    <node id="1" lat="48.755" lon="11.401"/><node id="2" lat="48.755" lon="11.410"/>
    <node id="3" lat="48.755" lon="11.41005"/><node id="4" lat="48.755" lon="11.419"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
    <way id="11"><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/></way>
    <way id="12"><nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/></way></osm>''', encoding="utf-8")
    raw, reference = tmp_path / "raw.net.xml", tmp_path / "reference.net.xml"
    executable = str(Path(__import__("sys").executable).with_name("netconvert.exe"))
    if not Path(executable).is_file():
        executable = shutil.which("netconvert")
    result = subprocess.run([executable, "--osm-files", str(source), "--geometry.remove", "false",
                             "--junctions.join", "false", "--offset.disable-normalization", "true", "--output-file", str(raw)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    joins = tmp_path / "join.nod.xml"
    joins.write_text('<nodes><join id="cluster_2_3" nodes="2 3" reset="false" type="priority"/></nodes>', encoding="utf-8")
    result = subprocess.run([executable, "-s", str(raw), "--node-files", str(joins), "--offset.disable-normalization", "true", "-o", str(reference)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return source, reference, executable


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
def test_native_prepared_import_uses_catalog_elevation_settings(tmp_path):
    source, reference, executable = _native_inputs(tmp_path)
    tree = ET.parse(source)
    for node in tree.getroot().findall("node"):
        ET.SubElement(node, "tag", k="ele", v="14.5")
    tree.write(source, encoding="utf-8")
    result = subprocess.run([executable, "--osm-files", str(source), "--osm.elevation", "true", "--geometry.remove", "false",
        "--junctions.join", "false", "--offset.disable-normalization", "true", "--output-file", str(reference)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    catalog, cache, document = _fixture(tmp_path, reference=reference, source=source)
    config = cache / "toy/build.netcfg"
    config.write_text('''<configuration><input><osm-files value="source.osm"/></input>
        <processing><osm.elevation value="true"/></processing></configuration>''', encoding="utf-8")
    document["datasets"][0]["files"].append({"role": "build_config", "path": config.name,
        "sha256": file_sha256(config), "bytes": config.stat().st_size})
    catalog.write_text(json.dumps(document), encoding="utf-8")
    report = corpus.build_public_reference_corpus(catalog_file=catalog, cache_dir=cache,
        output_dir=tmp_path / "out", netconvert_binary=executable)
    dataset = json.loads(Path(report["datasets"][0]["report"]["path"]).read_text())
    nodes = ET.parse(dataset["raw_net"]["path"]).getroot().findall("junction")
    assert nodes and all(float(node.get("z")) == 14.5 for node in nodes)
    assert dataset["import_configuration"]["applied_arguments"] == ["--osm.elevation=true"]
    assert dataset["paired_labels"]["count"] > 0
    assert dataset["paired_labels"]["counts"].get("unknown", 0) == 0
    assert dataset["paired_labels"]["label_usage"] == "generator_recipe_regression"
    assert dataset["paired_labels"]["human_cleaning_evaluation"] is False
    control = dataset["paired_labels"]["generator_control"]
    assert control["status"] == "measured"
    assert Path(control["network"]["path"]).is_file()
    assert sum(control["counts"].values()) == dataset["paired_labels"]["count"]
    assert control["human_cleaning_evaluation"] is False


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
def test_native_prepared_source_pairs_self_check_and_unjoined_baseline(tmp_path):
    source, reference, executable = _native_inputs(tmp_path)
    catalog, cache, _ = _fixture(tmp_path, reference=reference, source=source)
    report = corpus.build_public_reference_corpus(catalog_file=catalog, cache_dir=cache,
        output_dir=tmp_path / "out", netconvert_binary=executable)
    dataset_path = Path(report["datasets"][0]["report"]["path"])
    dataset = json.loads(dataset_path.read_text())
    assert dataset["reference_load"]["status"] == "pass"
    assert dataset["paired_labels"]["counts"]["joined"] == 1
    assert dataset["paired_labels"]["counts"]["separate"] == 2
    request_path = tmp_path / "request.json"
    for name, candidate in [("self", dataset["reference_net"]), ("baseline", dataset["raw_net"])]:
        request = {"schema": "astra.public-reference-request/v1", "dataset_sha256": file_sha256(dataset_path),
                   "comparison_source_sha256": dataset["comparison_source"]["sha256"], "candidate_net_file": candidate["path"],
                   "candidate_sha256": candidate["sha256"]}
        request_path.write_text(json.dumps(request), encoding="utf-8")
        checked = corpus.compare_public_reference(dataset_file=dataset_path, request_file=request_path,
                                                  output_file=tmp_path / f"{name}.json")
        assert checked["total_case_count"] == dataset["paired_labels"]["count"]
        assert checked["counts"]["junction_membership"] == ({"true_positive": 1, "true_negative": 2} if name == "self"
                                                            else {"false_negative": 1, "true_negative": 2})
        assert checked["field_precision"] is None
    request["comparison_source_sha256"] = "0" * 64
    request_path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="identities"):
        corpus.compare_public_reference(dataset_file=dataset_path, request_file=request_path, output_file=tmp_path / "bad.json")


def test_equal_ids_with_changed_coordinates_do_not_create_false_labels():
    source = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
        <junction id="1" x="0" y="0"/><junction id="2" x="1" y="0"/>
        <edge id="a" from="1" to="2"><lane id="a_0" index="0"/></edge></net>''')
    reference = ET.fromstring('''<net><location netOffset="-100,0" projParameter="!"/>
        <junction id="1" x="0" y="0"/><junction id="2" x="1" y="0"/></net>''')
    label = list(corpus._pair_labels(source, reference, {"id": "test", "family_id": "test"}, "binding"))[0]
    assert label["expected_join"] is None


def test_connection_change_labels_require_registered_approaches_and_lane_ids():
    source = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
        <junction id="1" x="0" y="0"/><junction id="2" x="1" y="0"/><junction id="3" x="2" y="0"/>
        <edge id="a" from="1" to="2"><lane id="a_0" index="0"/></edge>
        <edge id="b" from="2" to="3"><lane id="b_0" index="0"/></edge></net>''')
    reference = ET.fromstring(ET.tostring(source))
    ET.SubElement(reference, "connection", **{"from": "a", "fromLane": "0", "to": "b", "toLane": "0"})
    dataset = {"id": "test", "family_id": "test"}
    labels = list(corpus._connection_labels(source, reference, dataset, "binding"))
    assert labels[0]["change"] == "added" and labels[0]["expected_present"] is True
    reference.find("edge[@id='b']/lane").set("id", "renumbered_lane")
    assert list(corpus._connection_labels(source, reference, dataset, "binding"))[0]["expected_present"] is None


def test_published_revision_pair_supports_a_model_regression_without_human_trial(tmp_path, monkeypatch):
    catalog_path, cache, catalog = _fixture(tmp_path)
    dataset = catalog["datasets"][0]
    source = ET.fromstring('''<net><location netOffset="0,0" projParameter="!"/>
        <junction id="1" x="0" y="0"/><junction id="2" x="10" y="0"/><junction id="3" x="20" y="0"/>
        <edge id="a" from="1" to="2"><lane id="a_0" index="0"/></edge>
        <edge id="b" from="2" to="3"><lane id="b_0" index="0"/></edge></net>''')
    previous = cache / "toy" / ("2" * 12) / "reference.net.xml"
    previous.parent.mkdir()
    ET.ElementTree(source).write(previous, encoding="utf-8")
    dataset["files"].append({"role": "source_net", "path": "reference.net.xml", "commit": "2" * 40,
                             "sha256": file_sha256(previous), "bytes": previous.stat().st_size})
    ET.SubElement(source, "connection", **{"from": "a", "fromLane": "0", "to": "b", "toLane": "0"})
    current = cache / "toy/reference.net.xml"
    ET.ElementTree(source).write(current, encoding="utf-8")
    dataset["files"][0].update(sha256=file_sha256(current), bytes=current.stat().st_size)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(corpus, "run_sumo_load_audit", _fake_load)
    built = corpus.build_public_reference_corpus(catalog_file=catalog_path, cache_dir=cache, output_dir=tmp_path / "run")
    dataset_path = Path(built["datasets"][0]["report"]["path"])
    record = json.loads(dataset_path.read_text())
    assert record["comparison_source"]["kind"] == "published_model_revision"
    assert record["paired_labels"]["counts"]["connection_added"] == 1
    request = {"schema": "astra.public-reference-request/v1", "dataset_sha256": file_sha256(dataset_path),
               "comparison_source_sha256": file_sha256(previous), "candidate_net_file": str(current),
               "candidate_sha256": file_sha256(current)}
    request_path = tmp_path / "self-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    checked = corpus.compare_public_reference(dataset_file=dataset_path, request_file=request_path, output_file=tmp_path / "self.json")
    assert checked["status"] == "pass" and checked["scored_case_count"] == 3
    assert checked["connection_change_counts"]["added"] == {"true_positive": 1}
    assert checked["automatic_promotion_gate"] == "blocked" and checked["field_precision"] is None
    source.find("junction[@id='2']").set("x", "100")
    wrong = tmp_path / "wrong-frame.net.xml"
    ET.ElementTree(source).write(wrong, encoding="utf-8")
    request.update(candidate_net_file=str(wrong), candidate_sha256=file_sha256(wrong))
    wrong_request = tmp_path / "wrong-request.json"
    wrong_request.write_text(json.dumps(request), encoding="utf-8")
    rejected = corpus.compare_public_reference(dataset_file=dataset_path, request_file=wrong_request, output_file=tmp_path / "wrong.json")
    assert rejected["status"] == "review_required" and rejected["scored_case_count"] == 0
    source.find("junction[@id='2']").set("x", "10")
    ET.SubElement(source, "connection", **{"from": "b", "fromLane": "0", "to": "a", "toLane": "0"})
    extra = tmp_path / "extra-connection.net.xml"
    ET.ElementTree(source).write(extra, encoding="utf-8")
    request.update(candidate_net_file=str(extra), candidate_sha256=file_sha256(extra))
    extra_request = tmp_path / "extra-request.json"
    extra_request.write_text(json.dumps(request), encoding="utf-8")
    rejected = corpus.compare_public_reference(dataset_file=dataset_path, request_file=extra_request, output_file=tmp_path / "extra.json")
    assert rejected["status"] == "review_required" and rejected["scored_case_count"] == 3
    assert rejected["additional_candidate_connections"] == [["b", "0", "a", "0"]]


def test_cli_routes_reference_commands(monkeypatch, capsys):
    from astra_sumo.cli import main

    received = []
    monkeypatch.setattr(corpus, "build_public_reference_corpus", lambda **kwargs: received.append(kwargs) or {"status": "pass"})
    monkeypatch.setattr(corpus, "compare_public_reference", lambda **kwargs: received.append(kwargs) or {"status": "review_required"})
    assert main(["network", "reference-corpus", "catalog", "cache", "out", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"
    assert main(["network", "reference-check", "dataset", "request", "out.json", "--json"]) == 1
    capsys.readouterr()
    assert received[0]["catalog_file"] == "catalog"
    assert received[1]["dataset_file"] == "dataset"
