"""Structural guardrails for the dependency direction documented in AGENTS.md.

The intended direction is::

    server.py -> tools/* -> domain packages -> external libraries

This test suite is intentionally small and ratcheted.  It forbids the
currently unused bad edges and freezes the two known tool-to-tool imports so
new ones cannot appear without an explicit architecture decision.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "torii-sumo" / "src" / "torii_sumo"

DOMAIN_PACKAGES = {"core", "corridor", "evidence", "intersection", "road_network"}

# Known tool-to-tool imports.  These are orchestration shortcuts that should
# eventually be replaced by shared workflow/services functions, not extended.
_ALLOWED_TOOL_TO_TOOL_IMPORTS = {
    ("torii_sumo.tools.digital_twin_tools", "torii_sumo.tools.osm_tools"),
    ("torii_sumo.tools.road_network_tools", "torii_sumo.tools.intersection_tools"),
    ("torii_sumo.tools.workflow_tools", "torii_sumo.tools.osm_tools"),
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["torii_sumo", *parts])


def _resolve_import(module: str, node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.ImportFrom):
        package_parts = module.split(".")[:-1]
        if node.level == 0:
            if node.module:
                yield node.module
                for alias in node.names:
                    if alias.name != "*":
                        yield f"{node.module}.{alias.name}"
            return
        if node.level > len(package_parts):
            return
        prefix = package_parts[: len(package_parts) - (node.level - 1)]
        target = ".".join([*prefix, node.module] if node.module else prefix)
        yield target
        for alias in node.names:
            if alias.name != "*":
                yield f"{target}.{alias.name}"
        return

    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name


def _source_imports() -> dict[str, set[str]]:
    imports: dict[str, set[str]] = {}
    paths = [path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts]
    source_modules = {_module_name(path) for path in paths}
    for path in paths:
        module = _module_name(path)
        targets: set[str] = set()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            targets.update(target for target in _resolve_import(module, node) if target in source_modules)
        imports[module] = targets
    return imports


def _torii_targets(targets: set[str]) -> set[str]:
    return {target for target in targets if target.startswith("torii_sumo")}


def test_resolve_absolute_from_import_includes_alias_candidates() -> None:
    node = ast.parse("from torii_sumo import tools, server").body[0]

    assert set(_resolve_import("torii_sumo.core.example", node)) == {
        "torii_sumo",
        "torii_sumo.server",
        "torii_sumo.tools",
    }


def test_resolve_parent_relative_from_import_includes_alias_candidates() -> None:
    node = ast.parse("from .. import tools, server").body[0]

    assert set(_resolve_import("torii_sumo.core.example", node)) == {
        "torii_sumo",
        "torii_sumo.server",
        "torii_sumo.tools",
    }


def test_resolve_sibling_relative_from_import_includes_alias_candidate() -> None:
    node = ast.parse("from . import osm_tools").body[0]

    assert set(_resolve_import("torii_sumo.tools.workflow_tools", node)) == {
        "torii_sumo.tools",
        "torii_sumo.tools.osm_tools",
    }


def test_domain_packages_do_not_import_tools_or_server() -> None:
    violations: list[tuple[str, str]] = []
    for module, targets in _source_imports().items():
        top = module.split(".")[1] if module.startswith("torii_sumo.") else ""
        if top not in DOMAIN_PACKAGES:
            continue
        for target in _torii_targets(targets):
            target_top = target.split(".")[1] if target.count(".") >= 1 else ""
            if target_top == "tools" or target == "torii_sumo.server":
                violations.append((module, target))

    assert violations == [], f"domain packages must not import tools or server: {violations}"


def test_tool_to_tool_imports_are_frozen() -> None:
    violations: list[tuple[str, str]] = []
    for module, targets in _source_imports().items():
        if not module.startswith("torii_sumo.tools."):
            continue
        for target in _torii_targets(targets):
            if target.startswith("torii_sumo.tools.") and target != module:
                violations.append((module, target))

    assert sorted(violations) == sorted(_ALLOWED_TOOL_TO_TOOL_IMPORTS), (
        f"new tool-to-tool imports require an explicit architecture decision; current={sorted(violations)}"
    )
