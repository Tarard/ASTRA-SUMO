import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "astra-sumo" / "skills"

EXPECTED_REFERENCES = {
    "astra-build": {
        "composable-intersection-classification.md",
        "composable-signal-device-classification.md",
        "hamburg-five-intersection-aerial-workflow.md",
        "hamburg-count-calibration-workflow.md",
        "model-osm-detectors.md",
        "osm-source-patterns.md",
        "osm-to-sumo-workflow.md",
        "osm-way-fragmentation-and-road-axis-reconstruction.md",
        "road-arm-and-connection-classification.md",
    },
    "astra-calibrate": {
        "cached-detector-demand.md",
        "detector-constrained-demand-reconstruction.md",
        "hamburg-count-calibration-workflow.md",
        "hamburg-five-intersection-aerial-workflow.md",
        "hamburg-sandtorkai-digital-twin.md",
    },
    "astra-simulate": {
        "audit-sumo-controllers.md",
        "compare-corridor-perturbations.md",
        "debug-sumo-traci.md",
        "develop-and-verify-code.md",
        "experiment-problem-solving.md",
        "interactive-experiment-intake.md",
        "learn-sumo-knowledge.md",
        "mcp-tool-routing.md",
        "plan-experiment.md",
        "preflight-sumo-environment.md",
        "route-project-workflow.md",
        "sumolights-controller-patterns.md",
    },
    "astra-report": {
        "asd-ste100-skill.md",
        "capture-field-lesson.md",
        "evaluate-and-report-results.md",
        "humanizer-skill.md",
        "release-project.md",
        "traffic-control-reporting.md",
    },
}


def skill_dir(name: str) -> Path:
    return SKILLS / name


def read_skill(name: str) -> str:
    return (skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")


def test_plugin_bundles_four_topic_skills() -> None:
    actual = {path.name for path in SKILLS.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()}
    assert actual == set(EXPECTED_REFERENCES)
    assert not (SKILLS / "simulation-helper-skill-for-eclipse-sumo").exists()


def test_each_skill_has_matching_frontmatter_and_agent_metadata() -> None:
    for name in EXPECTED_REFERENCES:
        body = read_skill(name)
        frontmatter = yaml.safe_load(body.split("---", 2)[1])
        assert frontmatter["name"] == name
        assert frontmatter["description"]

        agent = yaml.safe_load((skill_dir(name) / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert agent["interface"]["display_name"]
        assert agent["interface"]["short_description"]
        assert f"${name}" in agent["interface"]["default_prompt"]
        assert agent["policy"]["allow_implicit_invocation"] is True


def test_topic_reference_sets_are_present() -> None:
    for name, expected in EXPECTED_REFERENCES.items():
        bundled = {path.name for path in (skill_dir(name) / "references").glob("*.md")}
        assert bundled == expected


def test_each_skill_routes_only_to_local_reference_files() -> None:
    for name in EXPECTED_REFERENCES:
        body = read_skill(name)
        routed = set(re.findall(r"`references/([^`]+\.md)`", body))
        missing = sorted(ref for ref in routed if not (skill_dir(name) / "references" / ref).is_file())
        assert missing == []


def test_reference_loading_is_optional_guidance() -> None:
    for name in EXPECTED_REFERENCES:
        body = read_skill(name)
        assert "Read only the references that help with the current task." in body


def test_reporting_reference_preserves_traffic_evidence_contract() -> None:
    body = (skill_dir("astra-report") / "references" / "traffic-control-reporting.md").read_text(encoding="utf-8")
    for term in (
        "network and demand",
        "Controller Information Contract",
        "TraCI` is an interface, not a sensor model",
        "minimum and maximum green",
        "unfinished-vehicle treatment",
        "algorithm benefit from information benefit",
        "simulator truth",
    ):
        assert term in body


def test_report_bundles_upstream_writing_skill_bodies_and_licenses() -> None:
    refs = skill_dir("astra-report") / "references"
    ste = (refs / "asd-ste100-skill.md").read_text(encoding="utf-8")
    humanizer = (refs / "humanizer-skill.md").read_text(encoding="utf-8")
    ste_license = (refs / "asd-ste100-LICENSE.txt").read_text(encoding="utf-8")
    humanizer_license = (refs / "humanizer-LICENSE.txt").read_text(encoding="utf-8")

    assert "name: asd-ste100" in ste
    assert "version: 0.4.0" in ste
    assert "name: humanizer" in humanizer
    assert 'version: "3.0.0"' in humanizer
    assert "Dustin Yuchen Teng" in ste_license
    assert "Siqi Chen" in humanizer_license
    assert "MIT License" in ste_license and "MIT License" in humanizer_license


def test_debugging_and_experiment_diagnosis_live_in_simulate() -> None:
    refs = skill_dir("astra-simulate") / "references"
    debug = (refs / "debug-sumo-traci.md").read_text(encoding="utf-8")
    diagnosis = (refs / "experiment-problem-solving.md").read_text(encoding="utf-8")
    assert "environment-fault" in debug
    assert "controller-logic-fault" in debug
    assert "blocking_uncertainty" in diagnosis
    assert "smallest_next_step" in diagnosis


def test_release_reference_matches_current_bundle_and_license() -> None:
    body = (skill_dir("astra-report") / "references" / "release-project.md").read_text(encoding="utf-8")
    assert "MIT License" in body
    for name in EXPECTED_REFERENCES:
        assert f"`{name}`" in body
    assert "PolyForm" not in body
    assert "main bundled skill name remains" not in body
