from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from pydantic import BaseModel


INPUT_OPTIONS = {"net-file", "route-files", "additional-files"}
OUTPUT_OPTIONS = {
    "summary-output",
    "tripinfo-output",
    "fcd-output",
    "emission-output",
    "queue-output",
}


class ConfigReference(BaseModel):
    role: str
    option: str
    value: str
    resolved_path: str
    exists: bool
    parent_exists: bool
    direction: str


class ConfigPreflightReport(BaseModel):
    role: str
    config_path: str
    config_exists: bool
    valid_xml: bool
    status: str
    references: list[ConfigReference]
    missing_inputs: list[str]
    missing_output_parents: list[str]
    declared_outputs: list[str]
    warnings: list[str]


class PairConfigPreflightReport(BaseModel):
    status: str
    baseline: ConfigPreflightReport
    variant: ConfigPreflightReport
    paired_warnings: list[str]
    claim_status: str

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def preflight_pair(baseline_config: Path, variant_config: Path) -> PairConfigPreflightReport:
    baseline = preflight_config(baseline_config, "baseline")
    variant = preflight_config(variant_config, "variant")
    paired_warnings = _paired_warnings(baseline, variant)
    statuses = {baseline.status, variant.status}
    if "fail" in statuses:
        status = "fail"
    elif paired_warnings or "warn" in statuses:
        status = "warn"
    else:
        status = "pass"
    return PairConfigPreflightReport(
        status=status,
        baseline=baseline,
        variant=variant,
        paired_warnings=paired_warnings,
        claim_status=(
            "construction-check"
            if status in {"pass", "warn"}
            else "construction-invalid"
        ),
    )


def preflight_config(config_path: Path, role: str) -> ConfigPreflightReport:
    if not config_path.exists():
        return ConfigPreflightReport(
            role=role,
            config_path=str(config_path),
            config_exists=False,
            valid_xml=False,
            status="fail",
            references=[],
            missing_inputs=[str(config_path)],
            missing_output_parents=[],
            declared_outputs=[],
            warnings=[f"config file does not exist: {config_path}"],
        )

    try:
        root = ET.parse(config_path).getroot()
    except ET.ParseError as exc:
        return ConfigPreflightReport(
            role=role,
            config_path=str(config_path),
            config_exists=True,
            valid_xml=False,
            status="fail",
            references=[],
            missing_inputs=[],
            missing_output_parents=[],
            declared_outputs=[],
            warnings=[f"invalid config XML: {exc}"],
        )
    except OSError as exc:
        return ConfigPreflightReport(
            role=role,
            config_path=str(config_path),
            config_exists=True,
            valid_xml=False,
            status="fail",
            references=[],
            missing_inputs=[],
            missing_output_parents=[],
            declared_outputs=[],
            warnings=[f"unreadable config XML due to OSError: {exc}"],
        )

    references = _extract_references(root, config_path.parent, role)
    missing_inputs = [
        ref.value for ref in references if ref.direction == "input" and not ref.exists
    ]
    missing_output_parents = [
        ref.value
        for ref in references
        if ref.direction == "output" and not ref.parent_exists
    ]
    warnings: list[str] = []
    if missing_output_parents:
        warnings.append("one or more output parent directories do not exist")
    status = "fail" if missing_inputs else "warn" if warnings else "pass"
    return ConfigPreflightReport(
        role=role,
        config_path=str(config_path),
        config_exists=True,
        valid_xml=True,
        status=status,
        references=references,
        missing_inputs=missing_inputs,
        missing_output_parents=missing_output_parents,
        declared_outputs=[ref.option for ref in references if ref.direction == "output"],
        warnings=warnings,
    )


def _extract_references(
    root: ET.Element, base_dir: Path, role: str
) -> list[ConfigReference]:
    refs: list[ConfigReference] = []
    for element in root.iter():
        option = _local_name(element.tag)
        value = element.attrib.get("value")
        if not value:
            continue
        direction = ""
        if option in INPUT_OPTIONS:
            direction = "input"
        elif option in OUTPUT_OPTIONS:
            direction = "output"
        if not direction:
            continue
        for token in _split_values(value):
            resolved = _resolve(base_dir, token)
            refs.append(
                ConfigReference(
                    role=role,
                    option=option,
                    value=token,
                    resolved_path=str(resolved),
                    exists=resolved.exists(),
                    parent_exists=resolved.parent.exists(),
                    direction=direction,
                )
            )
    return refs


def _paired_warnings(
    baseline: ConfigPreflightReport,
    variant: ConfigPreflightReport,
) -> list[str]:
    warnings: list[str] = []
    baseline_outputs = {
        ref.resolved_path for ref in baseline.references if ref.direction == "output"
    }
    variant_outputs = {
        ref.resolved_path for ref in variant.references if ref.direction == "output"
    }
    for shared in sorted(baseline_outputs & variant_outputs):
        warnings.append(f"shared output path would overwrite paired evidence: {shared}")
    return warnings


def _split_values(value: str) -> list[str]:
    return [token.strip() for token in value.split(",") if token.strip()]


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
