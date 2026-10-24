from __future__ import annotations

import ast
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re

import pytest

from astra_sumo.core import site_parameters


def test_site_data_restores_frozen_sets_tuples_and_mapping_keys(tmp_path, monkeypatch):
    folder = tmp_path / "data/sites"
    folder.mkdir(parents=True)
    document = {"schema": "astra.site-parameters/v1", "profile_id": "test_case",
        "field_acceptance": "legacy_snapshot_only_requires_source_match",
        "parameters": {"nodes": {"$frozenset": ["a", "b"]}, "pair": {"$tuple": ["a", 0]},
            "lookup": {"$mapping": [[{"$tuple": ["a", 0]}, "source-lane"]]}}}
    path = folder / "test_case.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(site_parameters, "files", lambda package: tmp_path)
    result = site_parameters.load_site_parameters("test_case")
    assert result["nodes"] == frozenset({"a", "b"})
    assert result["pair"] == ("a", 0)
    assert result["lookup"] == {("a", 0): "source-lane"}
    assert result["_source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["../outside", "x/y", "C:\\file", ""])
def test_site_profile_names_cannot_select_an_arbitrary_file(name):
    with pytest.raises(ValueError, match="simple package name"):
        site_parameters.load_site_parameters(name)


def test_legacy_road_identifiers_live_in_packaged_site_data():
    directory = files("astra_sumo").joinpath("data", "sites")
    profiles = list(directory.iterdir())
    assert len(profiles) == 9
    for resource in profiles:
        document = json.loads(resource.read_bytes())
        values = site_parameters.load_site_parameters(document["profile_id"])
        assert values["_source"]["field_acceptance"] == "legacy_snapshot_only_requires_source_match"
        source = Path(__file__).parents[1] / "plugins/astra-sumo/src/astra_sumo/core" / (document["profile_id"] + ".py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        ignored = {node.body[0].value for node in ast.walk(tree)
                   if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                   and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                ignored.update(ast.walk(node))
        identifiers = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and node not in ignored
                       and isinstance(node.value, str) and re.search(r"\d{6,}", node.value)]
        assert identifiers == [], (source, identifiers)
