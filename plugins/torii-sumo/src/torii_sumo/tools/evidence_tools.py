from __future__ import annotations

from pathlib import Path
from typing import Any

from torii_sumo.evidence.bundle import write_evidence_bundle
from torii_sumo.evidence.config_preflight import preflight_pair
from torii_sumo.evidence.output_inspection import inspect_output_pair


def sumo_config_pair_preflight(baseline_config: str, variant_config: str) -> dict[str, object]:
    return preflight_pair(Path(baseline_config), Path(variant_config)).to_dict()


def sumo_compare_outputs(
    *,
    baseline_summary: str | None = None,
    baseline_tripinfo: str | None = None,
    variant_summary: str | None = None,
    variant_tripinfo: str | None = None,
) -> dict[str, object]:
    return inspect_output_pair(
        baseline_summary=Path(baseline_summary) if baseline_summary else None,
        baseline_tripinfo=Path(baseline_tripinfo) if baseline_tripinfo else None,
        variant_summary=Path(variant_summary) if variant_summary else None,
        variant_tripinfo=Path(variant_tripinfo) if variant_tripinfo else None,
    ).to_dict()


def sumo_collect_evidence(output_dir: str, label: str, payload: dict[str, Any]) -> dict[str, object]:
    if not output_dir.strip():
        return _construction_invalid("output_dir is required")

    try:
        return write_evidence_bundle(
            Path(output_dir),
            label=label,
            payload=payload,
        ).to_dict()
    except (OSError, TypeError, ValueError) as exc:
        return _construction_invalid(str(exc))


def _construction_invalid(error: str) -> dict[str, object]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "error": error,
    }
