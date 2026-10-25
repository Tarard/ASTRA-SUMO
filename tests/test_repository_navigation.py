from __future__ import annotations

from pathlib import Path

import anyio
import pytest
from markdown_links import broken_links, link_targets, missing_named_paths, named_repository_paths

from astra_sumo.server import SUPPORTED_MCP_PROFILES, create_server


ROOT = Path(__file__).resolve().parents[1]

# Frozen research documents whose bytes are hashed by corridor/stage1_review_ready.py; their
# historical links are left as they were written.
FROZEN_DOCS = {
    Path("docs/held-out-corridor-blind-review-protocol-v2.md"),
    Path("docs/stage1-machine-review-ready-plan.md"),
    Path("docs/torii-corridor-human-modeling-implementation-status.md"),
}


def _markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for folder in ("docs", "examples", "schemas", "plugins/astra-sumo/skills"):
        files.extend(sorted((ROOT / folder).rglob("*.md")))
    return [path for path in files if path.relative_to(ROOT) not in FROZEN_DOCS]


def test_hash_bound_repository_text_uses_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    schema = ROOT / "schemas" / "product" / "torii.signal-device-profile.v1.schema.json"

    assert "* text=auto eol=lf" in attributes.splitlines()
    assert b"\r\n" not in schema.read_bytes()


def test_link_targets_read_markdown_structure() -> None:
    text = (
        "[inline](docs/a.md#part) ![image](assets/b.png) [angle](<docs/c d.md>) [web](https://example.org)\n"
        "[multiline](\nnext-line.md) [anchor](#here) `[code](not-a-link.md)` [ref][probe]\n"
        "\n"
        "```\n[fenced](not-a-link-either.md)\n```\n"
        "\n"
        "[probe]: missing.md\n"
        "[unused]: <other.md> \"title\"\n"
    )
    assert link_targets(text) == [
        (1, "assets/b.png"),
        (1, "docs/a.md"),
        (1, "docs/c d.md"),
        (1, "missing.md"),
        (1, "next-line.md"),
        (9, "missing.md"),
        (10, "other.md"),
    ]


def test_broken_links_reports_missing_and_escaping_targets(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "plugin" / "skills").mkdir(parents=True)
    (tmp_path / "docs" / "ok.md").write_text("[fine](../plugin/skills/s.md)\n", encoding="utf-8")
    (tmp_path / "docs" / "bad.md").write_text("[gone](\nmissing.md)\n", encoding="utf-8")
    (tmp_path / "plugin" / "skills" / "s.md").write_text("[out](../../docs/ok.md)\n", encoding="utf-8")
    files = sorted(tmp_path.rglob("*.md"))

    assert broken_links(files, tmp_path, {"skills": tmp_path / "plugin"}) == [
        "docs/bad.md:1 -> missing.md",
        "plugin/skills/s.md:1 -> ../../docs/ok.md (leaves plugin)",
    ]


def test_relative_markdown_links_resolve() -> None:
    assert broken_links(_markdown_files(), ROOT, {"skills": ROOT / "plugins" / "astra-sumo"}) == []


def test_named_repository_paths_skip_commands_placeholders_and_modules(tmp_path: Path) -> None:
    text = (
        "`README.md` `missing.csv` `scripts/run.ps1` `benchmarks/x.json` `docs/` `.gitattributes`\n"
        "`astra network verify-edit` `outputs/<run>/` `outputs/agent-notes/` `.venv` `.net.xml` `1.27.1`\n"
        "`astra_sumo.server` `measured` `$astra-build` `tests/test_*.py`\n"
        "\n"
        "```\n`fenced/example.md`\n```\n"
    )
    assert named_repository_paths(text) == [
        "README.md",
        "missing.csv",
        "scripts/run.ps1",
        "benchmarks/x.json",
        "docs/",
        ".gitattributes",
    ]

    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / ".gitattributes").write_text("x", encoding="utf-8")
    assert missing_named_paths(text, tmp_path) == ["benchmarks/x.json", "missing.csv", "scripts/run.ps1"]


def test_paths_named_in_agents_md_exist() -> None:
    assert missing_named_paths((ROOT / "AGENTS.md").read_text(encoding="utf-8"), ROOT) == []


@pytest.mark.parametrize("profile", SUPPORTED_MCP_PROFILES)
def test_mcp_catalog_covers_every_registered_tool(profile: str) -> None:
    catalog = (ROOT / "docs" / "mcp-tool-catalog.md").read_text(encoding="utf-8")
    names = {tool.name for tool in anyio.run(create_server(profile).list_tools)}

    assert names
    missing = sorted(name for name in names if f"`{name}`" not in catalog)
    assert missing == []
