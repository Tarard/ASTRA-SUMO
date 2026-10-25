"""Offline scope, raw-byte and native opt-in checks for the single saved run."""

from copy import deepcopy
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("spread_freeze", HERE / "freeze_evidence.py")
freeze = module_from_spec(spec)
spec.loader.exec_module(freeze)


def inputs():
    before = freeze.read(freeze.PARENT, "full/prepared/demand.rou.xml")
    after = freeze.read(HERE, "spread15/prepared/demand.rou.xml")
    old = {r["vehicle_id"]: r for r in json.loads(freeze.read(freeze.PARENT, "full/prepared/lane-intentions.json"))["vehicles"]}
    new = {r["vehicle_id"]: r for r in freeze.jread(HERE, "spread15/prepared/lane-intentions.json")["vehicles"]}
    return before, after, old, new, freeze.jread(HERE, "spread15/od-vector.json")


def test_complete_frozen_evidence():
    freeze.verify(HERE)


@pytest.mark.parametrize("kind", ["formula", "field", "inventory"])
def test_departure_scope_rejects_undeclared_changes(kind):
    before, after, old, new, vector = inputs()
    identity = freeze.jread(HERE, "spread15/departure-proposals.json")[0]["vehicle_id"]
    if kind == "formula":
        root = ET.fromstring(after)
        vehicle = next(v for v in root.findall("vehicle") if v.get("id") == identity)
        vehicle.set("depart", str(float(vehicle.get("depart")) + .001))
        after = ET.tostring(root)
        new[identity]["depart_s"] = float(vehicle.get("depart"))
    elif kind == "field":
        new[identity]["field_id"] = "undeclared"
    else:
        vector = deepcopy(vector)
        vector["values"][0] += 1
    with pytest.raises(AssertionError):
        freeze.departure_scope(before, after, old, new, vector)


def test_gzip_keeps_original_crlf(tmp_path):
    value = "字段\r\n".encode()
    (tmp_path / "original.json.gz").write_bytes(gzip.compress(value, mtime=0))
    assert freeze.raw(tmp_path / "original.json") == value


def test_byte_reference_rejects_wrong_hash(tmp_path):
    (tmp_path / "original.json.gz").write_bytes(gzip.compress(b"unchanged\r\n", mtime=0))
    (tmp_path / "byte-references.json").write_text(json.dumps({"aliases": {"alias": {
        "scope": "packet", "target": "original.json", "sha256": hashlib.sha256(b"other").hexdigest()}}}), encoding="utf-8")
    with pytest.raises(AssertionError):
        freeze.read(tmp_path, "alias")


@pytest.mark.parametrize("kind", ["existing", "frozen"])
def test_native_reproduction_requires_new_unfrozen_directory(tmp_path, kind):
    spec = spec_from_file_location("spread_reproduce", HERE / "reproduce.py")
    reproduce = module_from_spec(spec)
    spec.loader.exec_module(reproduce)
    with pytest.raises(AssertionError):
        reproduce.new_output(tmp_path if kind == "existing" else HERE / "new-native")
