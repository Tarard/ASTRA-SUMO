# Public Repository Manifest

Use this manifest to create a clean GitHub repository named:

```text
academic-audit-for-eclipse-sumo
```

Do not publish the entire local worktree if it contains unrelated project code, private plans, local PDFs, generated outputs, or unpublished research notes.

## Include

```text
README.md
LICENSE-DOCS
skills/
  academic-audit-for-eclipse-sumo/
  debugging-audit-for-eclipse-sumo/
docs/
  common-sumo-signal-control-failures.md
  release/
    github-topics.txt
    mailing-list-announcement.md
    linkedin-posts.md
    conference-positioning.md
    public-repo-manifest.md
examples/
  01_fixed_time_audit/
  02_max_pressure_audit/
  03_data_informed_signal_control_audit/
```

## Exclude

```text
docs/superpowers/
src/
tests/
sumo/
outputs/
*.pdf
__pycache__/
*.pyc
```

## GitHub Topics

After creating the GitHub repository, add the topics listed in `docs/release/github-topics.txt` through the repository settings or GitHub CLI.
