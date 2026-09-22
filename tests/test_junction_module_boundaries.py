"""The junction domains load without importing their compatibility entry points."""
from __future__ import annotations

import subprocess
import sys


def test_hamburg_junction_domains_load_independently_and_keep_curve_behavior():
    result = subprocess.run([sys.executable, "-c", '''
import importlib
import sys
for name in ("geometry", "source", "boundaries", "groups", "lanes", "movements"):
    importlib.import_module("torii_sumo.core.hamburg_junctions." + name)
assert "torii_sumo.core.hamburg_aerial_corridor_candidate" not in sys.modules
from torii_sumo.core.hamburg_junctions.geometry import fit_movement_shape_to_anchors
shape, error = fit_movement_shape_to_anchors([(0, 0), (5, 0), (10, 0)], start=(2, 0), end=(8, 0))
assert shape == [(2, 0), (5.0, 0.0), (8, 0)] and error == 0
from torii_sumo.core.hamburg_aerial_corridor_candidate import fit_movement_shape_to_anchors as original
assert original is fit_movement_shape_to_anchors
'''], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_teacher_rebuild_domains_load_without_the_legacy_entry_points():
    result = subprocess.run([sys.executable, "-c", '''
import importlib
import sys
import xml.etree.ElementTree as ET
for name in ("network", "geometry", "signatures", "parity", "scope", "edge_mapping",
             "tls", "pedestrians", "boundary_restore", "restoration", "connections",
             "lane_inputs", "scope_inputs", "cases", "planning", "replay_plans",
             "target_replay", "controller_replay", "variant", "queue", "shape_repair", "artifacts"):
    importlib.import_module("torii_sumo.core.junction_rebuild." + name)
for name in ("junction_rebuild_candidate", "junction_rebuild_tail", "junction_rebuild_helpers"):
    assert "torii_sumo.core." + name not in sys.modules
from torii_sumo.core.junction_rebuild.network import _connection_key
assert _connection_key(ET.Element("connection", {"from": "a", "to": "b", "fromLane": "1", "toLane": "2"})) == ("a", "b", "1", "2")
from torii_sumo.core.junction_rebuild.target_replay import write_teacher_target_internal_replay_net
from torii_sumo.core.junction_rebuild_candidate import write_teacher_target_internal_replay_net as original
assert original is write_teacher_target_internal_replay_net
'''], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
