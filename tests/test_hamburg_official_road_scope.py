from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.core.hamburg_official_road_scope import (
    materialize_hamburg_official_road_feature_scope,
)


def test_feature_scope_preserves_source_and_selects_exact_ids(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"id": "a", "type": "Feature", "properties": {}},
                    {"id": "b", "type": "Feature", "properties": {}},
                ],
            }
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()
    output = tmp_path / "scope.json"
    report = materialize_hamburg_official_road_feature_scope(
        source_file=source,
        feature_ids=("b",),
        output_file=output,
    )
    assert report["status"] == "pass"
    assert source.read_bytes() == before
    assert [item["id"] for item in json.loads(output.read_text(encoding="utf-8"))["features"]] == ["b"]
