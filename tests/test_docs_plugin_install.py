import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_install_doc_matches_plugin_metadata() -> None:
    doc = (ROOT / "docs" / "codex-plugin-install.md").read_text(encoding="utf-8")
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / "plugins" / "astra-sumo" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert "codex plugin marketplace add Tarard/ASTRA-SUMO --ref main" in doc
    assert f"codex plugin add {plugin['name']}@{marketplace['name']}" in doc
    assert marketplace["plugins"][0]["name"] == plugin["name"]
    assert "plugins/astra-sumo/.codex-plugin/plugin.json" in doc


def test_retired_and_scratch_paths_are_not_tracked() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    tracked = subprocess.run(
        ["git", "ls-files", "--", "docs/superpowers", "ARCHITECTURE.md", "NOTICE.md", "README.zh-CN.md", "README.de.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert tracked == ""
