from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_execution_workflow import (
    HamburgExecutionWorkflowError,
    materialize_hamburg_execution_plan,
    materialize_hamburg_execution_plan_from_config,
)


def _stage_argument(value: str) -> tuple[str, Path]:
    stage, separator, path = value.partition("=")
    if not separator or not stage.strip() or not path.strip():
        raise argparse.ArgumentTypeError("stage manifest must use STAGE=PATH, for example W0=C:\\run\\w0.json")
    return stage.strip().upper(), Path(path.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record and resume the Hamburg Sandtorkai workflow. "
            "A W5 capability summary is derived from existing stage manifests; no source network is overwritten."
        )
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        help="portable JSON configuration for the hash-bound evidence workflow",
    )
    parser.add_argument(
        "--stage-manifest",
        action="append",
        type=_stage_argument,
        metavar="STAGE=PATH",
        help="repeat for W0, W1, W2, W3a, W3b, and W4; omitted stages remain not_run",
    )
    parser.add_argument(
        "--stage-feedback",
        action="append",
        type=_stage_argument,
        metavar="STAGE=PATH",
        help=(
            "optional diagnostic manifest to merge into a stage's re-plan feedback; "
            "it never changes that stage's execution or promotion gate"
        ),
    )
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    if args.config is not None and (args.stage_manifest or args.stage_feedback):
        parser.error("--config cannot be combined with --stage-manifest or --stage-feedback")
    if args.config is None and args.output_dir is None:
        parser.error("--output-dir is required unless --config supplies it")
    stage_manifests = dict(args.stage_manifest or ())
    stage_feedback: dict[str, list[Path]] = {}
    for stage_id, path in args.stage_feedback or ():
        stage_feedback.setdefault(stage_id, []).append(path)
    try:
        if args.config is not None:
            report = materialize_hamburg_execution_plan_from_config(
                args.config,
                output_dir=args.output_dir,
                resume=False if args.no_resume else None,
            )
        else:
            report = materialize_hamburg_execution_plan(
                output_dir=args.output_dir,
                stage_manifests=stage_manifests,
                stage_feedback=stage_feedback,
                resume=not args.no_resume,
            )
    except (OSError, ValueError, HamburgExecutionWorkflowError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["promotion"]["decision"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
