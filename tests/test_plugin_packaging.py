import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "astra-sumo"
PACKAGE_LICENSE = "MIT"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_repo_marketplace_points_to_plugin_directory() -> None:
    marketplace = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")

    assert marketplace == {
        "name": "astra-sumo",
        "interface": {
            "displayName": "ASTRA",
        },
        "plugins": [
            {
                "name": "astra-sumo",
                "source": {
                    "source": "local",
                    "path": "./plugins/astra-sumo",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
            }
        ],
    }


def test_plugin_manifest_declares_skill_and_mcp_companion() -> None:
    manifest = load_json(PLUGIN / ".codex-plugin" / "plugin.json")

    assert manifest["name"] == "astra-sumo"
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert re.fullmatch(re.escape(version) + r"(?:\+codex\.\d{14})?", manifest["version"])
    assert manifest["license"] == PACKAGE_LICENSE
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["author"]["name"] == "ASTRA contributors"
    assert manifest["interface"]["displayName"] == "ASTRA"
    assert manifest["interface"]["category"] == "Developer Tools"
    assert "SUMO" in manifest["interface"]["defaultPrompt"]


def test_plugin_manifest_uses_app_logo_for_codex_icon() -> None:
    manifest = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
    logo_path = PLUGIN / "assets" / "app-logo.png"
    docs_logo_path = ROOT / "docs" / "assets" / "app-logo.png"

    assert manifest["interface"]["composerIcon"] == "./assets/app-logo.png"
    assert manifest["interface"]["logo"] == "./assets/app-logo.png"
    assert logo_path.is_file()
    assert logo_path.read_bytes() == docs_logo_path.read_bytes()


def test_mcp_config_uses_locked_plugin_launcher() -> None:
    mcp_config = load_json(PLUGIN / ".mcp.json")

    assert mcp_config == {
        "mcpServers": {
            "astra-sumo": {
                "command": "uv",
                "args": [
                    "run",
                    "--isolated",
                    "--frozen",
                    "--script",
                    "./scripts/run_astra_sumo.py",
                ],
            }
        }
    }


def test_plugin_contains_bundled_launcher() -> None:
    assert (PLUGIN / "scripts" / "run_astra_sumo.py").is_file()
    assert (PLUGIN / "scripts" / "run_astra_sumo.py.lock").is_file()
    assert (PLUGIN / "scripts" / "bootstrap_mcp.py").is_file()


def test_mcp_bootstrap_check_reports_usable_start_path() -> None:
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "bootstrap_mcp.py"), "--check"],
        check=False,
        cwd=PLUGIN,
    )

    assert result.returncode == 0


def test_plugin_contains_bundled_mcp_package() -> None:
    assert (PLUGIN / "src" / "astra_sumo" / "server.py").is_file()


def test_supported_platform_and_product_test_runners_are_windows_only() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "Operating System :: Microsoft :: Windows" in project["classifiers"]
    for name in ("full-suite.yml", "corridor-contracts.yml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        assert all(job["runs-on"].startswith("windows-") for job in workflow["jobs"].values())


def test_mcp_package_is_not_installed_at_repo_root() -> None:
    assert not (ROOT / "src" / "astra_sumo").exists()


def test_repository_license_scope_is_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    zenodo = load_json(ROOT / ".zenodo.json")
    manifest = load_json(PLUGIN / ".codex-plugin" / "plugin.json")

    assert project["license"] == PACKAGE_LICENSE
    assert project["license-files"] == ["LICENSE"]
    assert citation["license"] == PACKAGE_LICENSE
    assert zenodo["license"] == "mit"
    assert manifest["license"] == PACKAGE_LICENSE

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert not (ROOT / "LICENSE-CODE").exists()
    assert not (ROOT / "LICENSE-DOCS").exists()
