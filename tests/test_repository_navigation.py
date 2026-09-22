from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hash_bound_repository_text_uses_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    benchmark = ROOT / "benchmarks" / "corridor_human_modeling_v1" / "benchmark.v1.json"

    assert "* text=auto eol=lf" in attributes.splitlines()
    assert b"\r\n" not in benchmark.read_bytes()


def _registered_tool_names() -> set[str]:
    legacy_tools_path = ROOT / "plugins" / "torii-sumo" / "src" / "torii_sumo" / "legacy_tools.py"
    tree = ast.parse(legacy_tools_path.read_text(encoding="utf-8"))
    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Call):
            continue
        registration = node.func
        if not isinstance(registration.func, ast.Attribute) or registration.func.attr != "tool":
            continue
        if node.args and isinstance(node.args[0], ast.Name):
            names.add(node.args[0].id)

    return names


def test_readme_exposes_stable_navigation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    expected_links = {
        "docs/README.md",
        "docs/codex-plugin-install.md",
        "docs/architecture.md",
        "examples/01_signal_control_audit/task.md",
        "LICENSE",
    }
    for link in expected_links:
        assert link in readme

    assert "docs/repository-guide.md" not in readme

    section_order = (
        "## What Torii Does",
        "## Quick Start",
        "## Hamburg Digital Twin",
        "## Documentation",
        "## License",
    )
    positions = [readme.index(heading) for heading in section_order]
    assert positions == sorted(positions)


def test_documentation_index_links_current_guides() -> None:
    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    for link in (
        "mcp-tool-catalog.md",
        "research/README.md",
        "development-history/README.md",
        "architecture.md",
        "codex-plugin-install.md",
    ):
        assert link in docs_index

    assert "repository-guide.md" not in docs_index
    assert "superpowers" not in docs_index


def test_documentation_indexes_separate_research_and_history() -> None:
    research_index = (ROOT / "docs" / "research" / "README.md").read_text(encoding="utf-8")
    for link in (
        "../research-paper-blueprint.md",
        "../stage1-machine-review-ready-plan.md",
        "../teacher-free-topology-discrimination-v4.md",
        "../held-out-corridor-blind-review-protocol-v2.md",
    ):
        assert link in research_index

    history_index = (ROOT / "docs" / "development-history" / "README.md").read_text(encoding="utf-8")
    for link in (
        "../architecture-audit-2026-07-13.md",
        "../research-plan-handoff-2026-07-14.md",
        "../hamburg-digital-twin-development-log.md",
        "../repository-guide.md",
    ):
        assert link in history_index


def test_mcp_catalog_covers_every_registered_tool() -> None:
    catalog = (ROOT / "docs" / "mcp-tool-catalog.md").read_text(encoding="utf-8")
    registered = _registered_tool_names()

    assert len(registered) == 73
    missing = sorted(name for name in registered if f"`{name}`" not in catalog)
    assert missing == []


def test_repository_guide_is_archived() -> None:
    guide = (ROOT / "docs" / "repository-guide.md").read_text(encoding="utf-8")

    assert "Repository Guide (Archived)" in guide
    assert "AGENTS.md" in guide
    assert "docs/architecture.md" in guide
    assert "no longer the current repository contract" in guide


def test_agent_instructions_preserve_repository_and_evidence_boundaries() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for required in (
        "docs/README.md",
        "docs/architecture.md",
        "docs/mcp-tool-catalog.md",
        "src/torii_sumo/tools/",
        "src/torii_sumo/core/",
        "source artifacts immutable",
        "review_required",
        "tests/test_repository_navigation.py",
        "docs/research/README.md",
        "docs/development-history/README.md",
    ):
        assert required in instructions

    assert "docs/repository-guide.md" not in instructions
