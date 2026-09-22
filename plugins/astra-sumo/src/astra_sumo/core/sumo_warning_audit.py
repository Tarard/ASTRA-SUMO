from __future__ import annotations

import re
from collections import Counter
from typing import Any


_WARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "unused_states",
        re.compile(
            r"^Warning: Unused states in tlLogic '(?P<tl_id>[^']+)', program '(?P<program>[^']+)' "
            r"in phase (?P<phase>\d+) after tl-index (?P<after_tl_index>\d+)"
        ),
    ),
    (
        "missing_yellow_phase",
        re.compile(
            r"^Warning: Missing yellow phase in tlLogic '(?P<tl_id>[^']+)', program '(?P<program>[^']+)' "
            r"for tl-index (?P<tl_index>[\d,]+) when switching to phase (?P<phase>\d+)\."
        ),
    ),
    (
        "missing_green_phase",
        re.compile(
            r"^Warning: Missing green phase in tlLogic '(?P<tl_id>[^']+)', program '(?P<program>[^']+)' "
            r"for tl-index (?P<tl_index>[\d,]+)\."
        ),
    ),
    (
        "linkindex_no_detector",
        re.compile(
            r"^Warning: At actuated tlLogic '(?P<tl_id>[^']+)', linkIndex (?P<link_index>[\d,]+) "
            r"has no controlling detector\."
        ),
    ),
    (
        "actuated_phase_no_detector",
        re.compile(
            r"^Warning: At actuated tlLogic '(?P<tl_id>[^']+)', actuated phase (?P<phase>\d+) "
            r"has no controlling detector\."
        ),
    ),
)


def parse_sumo_tls_warnings(stderr: str) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for line in stderr.splitlines():
        for category, pattern in _WARNING_PATTERNS:
            match = pattern.match(line.strip())
            if not match:
                continue
            fields = {key: value for key, value in match.groupdict().items() if value is not None}
            fields["category"] = category
            fields["text"] = line.strip()
            fields["signature"] = _warning_signature(fields)
            warnings.append(fields)
            break
    return warnings


def compare_mapped_tls_warnings(
    teacher_stderr: str,
    candidate_stderr: str,
    tls_id_map: dict[str, str],
) -> dict[str, Any]:
    teacher_by_candidate: dict[str, Counter[str]] = {
        candidate_id: Counter() for candidate_id in tls_id_map.values()
    }
    candidate_by_id: dict[str, Counter[str]] = {
        candidate_id: Counter() for candidate_id in tls_id_map.values()
    }

    for warning in parse_sumo_tls_warnings(teacher_stderr):
        candidate_id = tls_id_map.get(warning["tl_id"])
        if candidate_id:
            teacher_by_candidate[candidate_id][warning["signature"]] += 1

    mapped_candidate_ids = set(tls_id_map.values())
    for warning in parse_sumo_tls_warnings(candidate_stderr):
        candidate_id = warning["tl_id"]
        if candidate_id in mapped_candidate_ids:
            candidate_by_id[candidate_id][warning["signature"]] += 1

    by_candidate_tls: dict[str, dict[str, Any]] = {}
    inherited_total = candidate_only_total = teacher_only_total = 0
    for candidate_id in sorted(mapped_candidate_ids):
        teacher_signatures = teacher_by_candidate[candidate_id]
        candidate_signatures = candidate_by_id[candidate_id]
        inherited = teacher_signatures & candidate_signatures
        candidate_only = candidate_signatures - teacher_signatures
        teacher_only = teacher_signatures - candidate_signatures
        inherited_total += inherited.total()
        candidate_only_total += candidate_only.total()
        teacher_only_total += teacher_only.total()
        by_candidate_tls[candidate_id] = {
            "inherited_warning_count": inherited.total(),
            "candidate_only_warning_count": candidate_only.total(),
            "teacher_only_warning_count": teacher_only.total(),
            "inherited_signatures": dict(sorted(inherited.items())),
            "candidate_only_signatures": dict(sorted(candidate_only.items())),
            "teacher_only_signatures": dict(sorted(teacher_only.items())),
        }

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "mapped_tls_count": len(mapped_candidate_ids),
        "inherited_warning_count": inherited_total,
        "candidate_only_warning_count": candidate_only_total,
        "teacher_only_warning_count": teacher_only_total,
        "by_candidate_tls": by_candidate_tls,
    }


def _warning_signature(warning: dict[str, str]) -> str:
    ignored = {"tl_id", "text", "signature"}
    parts = [warning["category"]]
    parts.extend(f"{key}={warning[key]}" for key in warning if key not in ignored and key != "category")
    return "|".join(parts)
