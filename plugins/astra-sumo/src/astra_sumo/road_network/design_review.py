"""Compose independent design-rule checks and preserved engineering-plan observations."""

from collections import Counter
import json
from pathlib import Path
import re

from ..core.artifact_io import write_json_atomic
from ..core.candidate_contracts import file_sha256
from .adapters.engineering_plan import read_engineering_plan_observations
from .design_rules import check_cycle_lane_width, check_left_turn_lane_width, check_two_lane_carriageway_width


REQUEST_SCHEMA = "torii.road-design-review-request/v1"


def build_road_design_review(*, request_file, output_dir):
    """Write a new review without changing a plan, standard, or SUMO network."""
    request_path = Path(request_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new output directory.")
    request_hash = file_sha256(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"Use request schema {REQUEST_SCHEMA}.")
    inputs = {request_path: request_hash}

    def register(path, digest):
        path = Path(path).resolve(strict=True)
        if path in inputs and inputs[path] != digest:
            raise ValueError(f"Input identity changed between reads: {path.name}")
        inputs[path] = digest

    def source(record):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("Each input requires a path and SHA-256.")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("Each input requires a valid SHA-256.")
        path = (request_path.parent / record["path"]).resolve(strict=True)
        if file_sha256(path) != digest.lower():
            raise ValueError(f"Input hash mismatch: {path.name}")
        register(path, digest.lower())
        return dict(path=str(path), sha256=digest.lower())

    standards = request.get("standard_files")
    if not isinstance(standards, dict) or not standards:
        raise ValueError("Provide the standard_files used by the checks.")
    standards = {key: source(record) for key, record in standards.items()}
    plans = {}
    plan_requests = request.get("plan_requests", [])
    if not isinstance(plan_requests, list) or any(not isinstance(record, dict) for record in plan_requests):
        raise ValueError("plan_requests must be a list of input records.")
    for record in plan_requests:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in plans:
            raise ValueError("Plan request IDs must be nonempty and unique.")
        identity = source(record)
        plan = read_engineering_plan_observations(identity["path"], request.get("target_date"))
        register(plan["source"]["path"], plan["source"]["sha256"])
        plans[identifier] = plan
    functions = {"cycle_lane_width": check_cycle_lane_width, "two_lane_carriageway_width": check_two_lane_carriageway_width,
                 "left_turn_lane_width": check_left_turn_lane_width}
    cases = request.get("checks")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Provide at least one design-rule check.")
    results, identifiers = [], set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].strip() or case["id"] in identifiers:
            raise ValueError("Check IDs must be nonempty and unique.")
        identifiers.add(case["id"])
        rule = case.get("rule")
        if not isinstance(rule, str) or rule not in functions or not isinstance(case.get("parameters"), dict):
            raise ValueError("Use a supported rule and an object of parameters.")
        parameters = dict(case["parameters"])
        record = dict(id=case["id"], rule=rule, evidence_kind="caller_supplied_design_case")
        if "observation" in case:
            reference = case["observation"]
            if not isinstance(reference, dict) or any(not isinstance(reference.get(k), str) or not reference[k].strip()
                                                       for k in ("plan_id", "observation_id")):
                raise ValueError("An observation reference requires text plan_id and observation_id.")
            if reference["plan_id"] not in plans:
                raise ValueError("The observation must name a supplied plan request.")
            plan = plans[reference["plan_id"]]
            observation = next((r for r in plan["observations"] if r["id"] == reference.get("observation_id")), None)
            if observation is None:
                raise ValueError("The requested plan observation is absent.")
            if observation["property"] != "width" and not observation["property"].endswith("_width"):
                raise ValueError("A width check must reference a width observation.")
            if {"width_m", "width_basis"} & parameters.keys():
                raise ValueError("Do not override a linked observation's width or measurement basis.")
            parameters.update(width_m=observation["value_m"], width_basis=observation["width_basis"])
            parameters.setdefault("uncertainty_m", None)
            record.update(evidence_kind="engineering_plan_dimension", observation=observation,
                          observation_source=plan["source"], observation_request=plan["request"])
        parameters.setdefault("width_m", None)
        parameters.setdefault("context", None)
        try:
            result = functions[rule](**parameters)
        except TypeError as error:
            raise ValueError(f"Invalid parameters for {rule}: {error}") from error
        for citation in result["sources"]:
            ref = citation["ref"]
            if standards.get(ref["dataset"], {}).get("sha256") != ref["source_sha256"]:
                raise ValueError(f"Supply the exact reviewed standard file: {ref['dataset']}")
        results.append({**record, "parameters": parameters, "result": result})
    if any(file_sha256(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("An input changed during design review.")
    report = dict(schema="torii.road-design-review/v1", status="pass", decision="review_required",
                  claim_status="diagnostic-demo", network_changed=False, automatic_promotion_gate="blocked",
                  input_files=[dict(path=str(path), sha256=digest) for path, digest in inputs.items()],
                  standard_files=standards, plan_observations=plans, checks=results,
                  summary=dict(rule_check_count=len(results), plan_observation_count=sum(len(p["observations"]) for p in plans.values()),
                               rule_status_counts=dict(Counter(r["result"]["status"] for r in results))),
                  claim_boundary="This review combines cited rule fragments and manual plan dimensions. Passed numerical checks do not certify an existing road or a complete design. Dates, measurement bases, and observation provenance remain separate. No network construction stage is enabled.")
    destination.mkdir(parents=True, exist_ok=False)
    report_file = destination / "design-review.json"
    write_json_atomic(report_file, report, ensure_ascii=False)
    return dict(status="pass", decision="review_required", report_file=str(report_file),
                report_sha256=file_sha256(report_file), **report["summary"])
