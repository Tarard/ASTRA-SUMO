from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path("plugins/torii-sumo/scripts/build_hamburg_official_intersection.py")


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "build_hamburg_official_intersection",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_forwards_hash_bound_official_inputs(monkeypatch, tmp_path: Path) -> None:
    script = _load_script()
    captured: dict[str, object] = {}

    def fake_materializer(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "candidate_id": "official-test"}

    monkeypatch.setattr(
        script,
        "materialize_hamburg_official_intersection_plainxml",
        fake_materializer,
    )
    output_dir = tmp_path / "candidate"
    result = script.main(
        [
            "--map-xml",
            "map.xml",
            "--map-kml",
            "map.kml",
            "--ocit-c",
            "ocit.xml",
            "--node-id",
            "2394",
            "--classification-file",
            "2394.classification.json",
            "--accepted-classification-id",
            "intersection-archetype-2394",
            "--expected-classification-sha256",
            "d" * 64,
            "--output-dir",
            str(output_dir),
            "--expected-map-xml-sha256",
            "a" * 64,
            "--expected-map-kml-sha256",
            "b" * 64,
            "--expected-ocit-c-sha256",
            "c" * 64,
            "--netconvert-binary",
            "netconvert-custom",
            "--timeout-seconds",
            "12.5",
        ]
    )

    assert result == 0
    assert captured == {
        "map_xml_file": Path("map.xml"),
        "map_kml_file": Path("map.kml"),
        "ocit_c_file": Path("ocit.xml"),
        "output_dir": output_dir,
        "classification_file": Path("2394.classification.json"),
        "accepted_classification_id": "intersection-archetype-2394",
        "expected_classification_sha256": "d" * 64,
        "expected_node_id": "2394",
        "expected_sha256": {
            "map_xml": "a" * 64,
            "map_kml": "b" * 64,
            "ocit_c": "c" * 64,
        },
        "prefix": None,
        "compile_net": True,
        "netconvert_binary": "netconvert-custom",
        "timeout_seconds": 12.5,
    }


def test_cli_can_write_plainxml_without_compiling(monkeypatch, tmp_path: Path) -> None:
    script = _load_script()
    captured: dict[str, object] = {}

    def fake_materializer(**kwargs):
        captured.update(kwargs)
        return {"status": "pass"}

    monkeypatch.setattr(
        script,
        "materialize_hamburg_official_intersection_plainxml",
        fake_materializer,
    )
    result = script.main(
        [
            "--map-xml",
            "map.xml",
            "--map-kml",
            "map.kml",
            "--ocit-c",
            "ocit.xml",
            "--node-id",
            "2349",
            "--classification-file",
            "2349.classification.json",
            "--accepted-classification-id",
            "intersection-archetype-2349",
            "--expected-classification-sha256",
            "d" * 64,
            "--output-dir",
            str(tmp_path / "candidate"),
            "--no-compile",
        ]
    )

    assert result == 0
    assert captured["compile_net"] is False
    assert captured["expected_sha256"] is None
