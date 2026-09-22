from __future__ import annotations

from torii_sumo.core.environment import collect_environment_report


def sumo_get_environment() -> dict[str, object]:
    return collect_environment_report().to_dict()


def sumo_preflight() -> dict[str, object]:
    report = collect_environment_report()
    return report.to_dict()
