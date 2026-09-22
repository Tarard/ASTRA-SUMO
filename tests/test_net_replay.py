from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.corridor.enums import GateStatus
from torii_sumo.corridor.net_replay import compare_netconvert_replay
from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.corridor.schema import build_net_replay_report_schema


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_normalized_net_hash_ignores_comments_and_xml_formatting(tmp_path: Path) -> None:
    primary = tmp_path / "primary.net.xml"
    replay = tmp_path / "replay.net.xml"
    primary.write_text(
        """<?xml version="1.0"?>
<!-- generated on first timestamp -->
<net version="1.20" lefthand="true"><edge id="road"/></net>
""",
        encoding="utf-8",
    )
    replay.write_text(
        """<?xml version="1.0"?>
<!-- generated on second timestamp -->
<net lefthand="true" version="1.20">
  <edge id="road" />
</net>
""",
        encoding="utf-8",
    )

    report = compare_netconvert_replay(primary, replay)

    assert report.status is GateStatus.PASS
    assert report.reproducible_semantics is True
    assert report.primary_net_sha256 != report.replay_net_sha256
    assert report.primary_normalized_sha256 == report.replay_normalized_sha256
    assert normalized_net_sha256(primary) == normalized_net_sha256(replay)


def test_net_replay_blocks_a_semantic_network_change(tmp_path: Path) -> None:
    primary = tmp_path / "primary.net.xml"
    replay = tmp_path / "replay.net.xml"
    primary.write_text('<net><edge id="road-a"/></net>', encoding="utf-8")
    replay.write_text('<net><edge id="road-b"/></net>', encoding="utf-8")

    report = compare_netconvert_replay(primary, replay)

    assert report.status is GateStatus.BLOCKED
    assert report.reproducible_semantics is False
    assert report.blockers == ("normalized_net_replay_mismatch",)


def test_net_replay_schema_is_current() -> None:
    expected = json.dumps(
        build_net_replay_report_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert (
        REPOSITORY_ROOT
        / "schemas"
        / "torii.corridor.net-replay-report.v1.schema.json"
    ).read_text(encoding="utf-8") == expected
