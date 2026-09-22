from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_links_plugin_install_doc() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/codex-plugin-install.md" in readme
    assert "docs/README.md" in readme
    assert "docs/architecture.md" in readme
    assert "## Quick Start" in readme
    assert "## Documentation" in readme
    assert "docs/repository-guide.md" not in readme
    assert "docs/superpowers" not in readme


def test_plugin_install_doc_explains_marketplace_and_new_thread() -> None:
    doc = (ROOT / "docs" / "codex-plugin-install.md").read_text(encoding="utf-8")

    assert "codex plugin marketplace add" in doc
    assert "codex plugin marketplace add Tarard/Torii-SUMO --ref main" in doc
    assert "codex plugin add torii-sumo@torii-sumo" in doc
    assert "Start a new Codex thread" in doc
    assert "plugins/torii-sumo" in doc
    assert "plugins/torii-sumo/.codex-plugin/plugin.json" in doc
    assert "skill is the reasoning layer" in doc
    assert "MCP server is the execution layer" in doc
    assert "torii_auto_workflow" in doc
    assert "workflow router" in doc
    assert "sumo_osm_cleanup_workflow" in doc
    assert "area confirmation" in doc
    assert "Netedit" in doc
    assert "sumo_osm_build_network" in doc
    assert "sumo_tls_audit" in doc
    assert "sumo_tls_multisource_review" in doc
    assert "Mapillary" in doc
    assert "KartaView" in doc
    assert "sumo_network_connected_core" in doc
    assert "sumo_network_routeability_probe" in doc
    assert "sumo_network_routeability_audit" in doc
    assert "sumo_network_overlapping_junction_audit" in doc
    assert "sumo_network_teacher_guided_junction_variant" in doc
    assert "tiled Overpass" in doc
    assert "retry" in doc
    assert "deduplicate" in doc
    assert "OSMnx" in doc
    assert "OSMNet" in doc
    assert "pyrosm" in doc
    assert "SUMO osmGet/osmBuild" in doc
    assert "osm-to-xodr" in doc
    assert "Use a region-aware reality baseline" in doc
    assert "Amap/Gaode" in doc
    assert "WGS84/GCJ-02/BD-09" in doc
    assert "For a historical target" in doc
    assert "full OSM cleanup" in doc
    assert "TLS inventory" in doc
    assert "controller generation" in doc
    assert "controller-log inspection" in doc


def test_osm_source_patterns_doc_tracks_external_projects_without_vendoring() -> None:
    doc = (ROOT / "docs" / "osm-source-patterns.md").read_text(encoding="utf-8")

    assert "OSMnx" in doc
    assert "OSMNet" in doc
    assert "pyrosm" in doc
    assert "SUMO osmGet/osmBuild" in doc
    assert "osm-to-xodr" in doc
    assert "Do not vendor external source code" in doc
    assert "Overpass subdivision" in doc
    assert "offline PBF" in doc
    assert "multi-stage netconvert" in doc
    assert "Observed OSM-to-SUMO Cleanup Profiles" in doc
    assert "sumo-berlin" in doc
    assert "MoSTScenario" in doc
    assert "actrys" in doc
    assert "not a substitute for TUM-style teacher replay" in doc


def test_public_docs_do_not_reference_internal_superpowers_paths() -> None:
    public_files = [
        ROOT / "README.md",
        ROOT / "docs" / "readme" / "README.zh-CN.md",
        ROOT / "docs" / "readme" / "README.de.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "index.html",
        ROOT / "docs" / "release" / "mailing-list-announcement.md",
        ROOT / "docs" / "release" / "linkedin-posts.md",
    ]

    for path in public_files:
        body = path.read_text(encoding="utf-8")
        assert "docs/superpowers" not in body
        assert "superpowers/plans" not in body
        assert "superpowers/specs" not in body

    manifest = (ROOT / "docs" / "release" / "public-repo-manifest.md").read_text(encoding="utf-8")
    assert "docs/architecture.md" in manifest
    assert "benchmarks/" in manifest
    assert "docs/superpowers" not in manifest
    assert "LICENSE-CODE" not in manifest
    assert "LICENSE-DOCS" not in manifest
    assert not (ROOT / "docs" / "superpowers").exists()
    assert not (ROOT / "ARCHITECTURE.md").exists()
    assert not (ROOT / "NOTICE.md").exists()
    assert not (ROOT / "README.zh-CN.md").exists()
    assert not (ROOT / "README.de.md").exists()
