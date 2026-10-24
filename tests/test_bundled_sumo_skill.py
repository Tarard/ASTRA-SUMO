from pathlib import Path

import yaml
from markdown_links import routed_references

from astra_sumo.core.workflow_catalog import get_workflow_catalog


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "astra-sumo" / "skills"
SKILL_NAMES = {"astra-build", "astra-calibrate", "astra-simulate", "astra-report"}


def skill_dir(name: str) -> Path:
    return SKILLS / name


def read_skill(name: str) -> str:
    return (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")


def test_plugin_bundles_four_topic_skills() -> None:
    actual = {path.name for path in SKILLS.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()}
    assert actual == SKILL_NAMES


def test_each_skill_has_matching_frontmatter_and_agent_metadata() -> None:
    for name in SKILL_NAMES:
        body = read_skill(name)
        frontmatter = yaml.safe_load(body.split("---", 2)[1])
        assert frontmatter["name"] == name
        assert frontmatter["description"]

        agent = yaml.safe_load((skill_dir(name) / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert agent["interface"]["display_name"]
        assert agent["interface"]["short_description"]
        assert f"${name}" in agent["interface"]["default_prompt"]
        assert agent["policy"]["allow_implicit_invocation"] is True


def test_each_skill_routes_only_to_local_reference_files() -> None:
    for name in SKILL_NAMES:
        routed = routed_references(read_skill(name))
        missing = sorted(ref for ref in routed if not (skill_dir(name) / "references" / ref).is_file())
        assert missing == []


def unrouted_references(references: Path, skill_body: str, cataloged: set[str]) -> list[str]:
    """Reference documents, at any depth, that neither the SKILL.md nor the workflow catalog routes to."""
    routed = routed_references(skill_body)
    orphans = []
    for path in sorted(references.rglob("*.md")):
        relative = path.relative_to(references).as_posix()
        if relative not in routed and relative not in cataloged:
            orphans.append(relative)
    return orphans


def test_unrouted_references_are_found_at_any_depth(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    for name in ("routed.md", "cataloged.md", "orphan.md", "nested/routed.md", "nested/orphan.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    body = "Use `references/routed.md` and [the nested guide](references/nested/routed.md)."

    assert unrouted_references(tmp_path, body, {"cataloged.md"}) == ["nested/orphan.md", "orphan.md"]


def test_routes_are_read_from_code_spans_and_links() -> None:
    body = (
        "| Task | `references/a.md` |\n"
        "See [b](references/b.md), [c](./references/sub/c.md) and `references/./sub/../e.md`.\n"
        "Not routes: [web](https://example.org/references/d.md) `https://example.org/references/ghost.md`\n"
        "`../../astra-calibrate/references/ghost.md` [other](../astra-calibrate/references/ghost.md)\n"
        "`references/../escape.md` `see references/ghost.md`\n"
        "\n```\n`references/fenced.md`\n```\n"
    )
    assert routed_references(body) == {"a.md", "b.md", "sub/c.md", "e.md"}


def test_every_reference_is_routed_or_cataloged() -> None:
    catalog = [row["reference"] for row in get_workflow_catalog()["scenarios"]]
    orphans = []
    for name in SKILL_NAMES:
        prefix = f"skills/{name}/references/"
        cataloged = {ref.removeprefix(prefix) for ref in catalog if ref.startswith(prefix)}
        unrouted = unrouted_references(skill_dir(name) / "references", read_skill(name), cataloged)
        orphans += [f"{name}/references/{ref}" for ref in unrouted]
    assert orphans == []


def test_report_bundles_upstream_writing_skill_bodies_and_licenses() -> None:
    refs = skill_dir("astra-report") / "references"
    ste = (refs / "asd-ste100-skill.md").read_text(encoding="utf-8")
    humanizer = (refs / "humanizer-skill.md").read_text(encoding="utf-8")
    ste_license = (refs / "asd-ste100-LICENSE.txt").read_text(encoding="utf-8")
    humanizer_license = (refs / "humanizer-LICENSE.txt").read_text(encoding="utf-8")

    assert "name: asd-ste100" in ste
    assert "name: humanizer" in humanizer
    assert "Dustin Yuchen Teng" in ste_license
    assert "Siqi Chen" in humanizer_license
    assert "MIT License" in ste_license and "MIT License" in humanizer_license
