from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import Field, model_validator

from torii_sumo.core.candidate_contracts import file_sha256

from .base import ContractModel, Sha256, StableToken
from .ids import stable_id
from .toolchain import ToolchainLock


_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")


class CodeProducerIdentity(ContractModel):
    repository_url: str
    revision: str
    tree_revision: str
    branch: str
    working_tree_clean: Literal[True] = True

    @model_validator(mode="after")
    def validate_revision(self) -> CodeProducerIdentity:
        for label, value in (
            ("revision", self.revision),
            ("tree_revision", self.tree_revision),
        ):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise ValueError(f"Invalid Git {label}: {value!r}")
        if not self.repository_url.strip() or not self.branch.strip():
            raise ValueError("Code producer identity requires repository and branch.")
        return self


class RuntimeExecutableIdentity(ContractModel):
    name: str
    path: str
    version: str
    sha256: Sha256


class RuntimeSupportFileIdentity(ContractModel):
    name: str
    path: str
    sha256: Sha256


class HeldOutMachineRunIdentity(ContractModel):
    schema_id: str = "torii.corridor.held-out-machine-run-identity/v1"
    run_identity_id: StableToken
    producer: CodeProducerIdentity
    entrypoint: str
    toolchain_id: StableToken
    toolchain_lock_sha256: Sha256
    platform: str
    python_version: str
    runtime_dependencies: dict[str, str]
    runtime_tools: tuple[RuntimeExecutableIdentity, ...]
    support_files: tuple[RuntimeSupportFileIdentity, ...]
    selected_corridor_keys: tuple[str, ...]
    timeout_seconds: float = Field(gt=0)
    blinding_seed_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return {
            "producer": self.producer.model_dump(mode="json", by_alias=True),
            "entrypoint": self.entrypoint,
            "toolchain_id": self.toolchain_id,
            "toolchain_lock_sha256": self.toolchain_lock_sha256,
            "platform": self.platform,
            "python_version": self.python_version,
            "runtime_dependencies": self.runtime_dependencies,
            "runtime_tools": [
                item.model_dump(mode="json", by_alias=True)
                for item in self.runtime_tools
            ],
            "support_files": [
                item.model_dump(mode="json", by_alias=True)
                for item in self.support_files
            ],
            "selected_corridor_keys": self.selected_corridor_keys,
            "timeout_seconds": self.timeout_seconds,
            "blinding_seed_sha256": self.blinding_seed_sha256,
        }

    @model_validator(mode="after")
    def validate_identity(self) -> HeldOutMachineRunIdentity:
        if self.run_identity_id != stable_id("toolchain", self.identity_payload()):
            raise ValueError("run_identity_id does not match the runtime payload.")
        if len({item.name for item in self.runtime_tools}) != len(
            self.runtime_tools
        ):
            raise ValueError("Runtime tool names must be unique.")
        if len({item.name for item in self.support_files}) != len(
            self.support_files
        ):
            raise ValueError("Runtime support file names must be unique.")
        if tuple(sorted(set(self.selected_corridor_keys))) != (
            self.selected_corridor_keys
        ):
            raise ValueError("Selected corridor keys must be sorted and unique.")
        return self

    @classmethod
    def build(
        cls,
        *,
        producer: CodeProducerIdentity,
        entrypoint: str,
        toolchain_id: str,
        toolchain_lock_sha256: str,
        platform_name: str,
        python_version: str,
        runtime_dependencies: dict[str, str],
        runtime_tools: tuple[RuntimeExecutableIdentity, ...],
        support_files: tuple[RuntimeSupportFileIdentity, ...],
        selected_corridor_keys: tuple[str, ...],
        timeout_seconds: float,
        blinding_seed_sha256: str,
    ) -> HeldOutMachineRunIdentity:
        payload = {
            "producer": producer.model_dump(mode="json", by_alias=True),
            "entrypoint": entrypoint,
            "toolchain_id": toolchain_id,
            "toolchain_lock_sha256": toolchain_lock_sha256,
            "platform": platform_name,
            "python_version": python_version,
            "runtime_dependencies": runtime_dependencies,
            "runtime_tools": [
                item.model_dump(mode="json", by_alias=True)
                for item in runtime_tools
            ],
            "support_files": [
                item.model_dump(mode="json", by_alias=True)
                for item in support_files
            ],
            "selected_corridor_keys": selected_corridor_keys,
            "timeout_seconds": timeout_seconds,
            "blinding_seed_sha256": blinding_seed_sha256,
        }
        return cls(
            run_identity_id=stable_id("toolchain", payload),
            producer=producer,
            entrypoint=entrypoint,
            toolchain_id=toolchain_id,
            toolchain_lock_sha256=toolchain_lock_sha256,
            platform=platform_name,
            python_version=python_version,
            runtime_dependencies=runtime_dependencies,
            runtime_tools=runtime_tools,
            support_files=support_files,
            selected_corridor_keys=selected_corridor_keys,
            timeout_seconds=timeout_seconds,
            blinding_seed_sha256=blinding_seed_sha256,
        )


def capture_held_out_machine_run_identity(
    *,
    repository_root: Path,
    entrypoint: str,
    toolchain_lock_file: Path,
    runtime_tool_paths: Mapping[str, Path],
    support_file_paths: Mapping[str, Path],
    selected_corridor_keys: Sequence[str],
    timeout_seconds: float,
    blinding_seed: str,
) -> HeldOutMachineRunIdentity:
    root = repository_root.resolve(strict=True)
    lock_path = toolchain_lock_file.resolve(strict=True)
    lock = ToolchainLock.model_validate_json(lock_path.read_text(encoding="utf-8"))
    producer = capture_code_producer_identity(root)
    expected_tool_versions = {tool.name: tool.version for tool in lock.tools}
    runtime_tools: list[RuntimeExecutableIdentity] = []
    for name, raw_path in sorted(runtime_tool_paths.items()):
        path = raw_path.resolve(strict=True)
        version = _runtime_version(path, name=name)
        expected = expected_tool_versions.get(name)
        if expected is not None and version != expected:
            raise ValueError(
                f"Runtime {name} version mismatch: expected={expected}, observed={version}."
            )
        runtime_tools.append(
            RuntimeExecutableIdentity(
                name=name,
                path=str(path),
                version=version,
                sha256=file_sha256(path),
            )
        )
    expected_runtime_tools = set(expected_tool_versions)
    observed_runtime_tools = set(runtime_tool_paths)
    if not expected_runtime_tools <= observed_runtime_tools:
        raise ValueError(
            "Locked runtime tools are missing: "
            + ", ".join(sorted(expected_runtime_tools - observed_runtime_tools))
        )

    python_path = Path(sys.executable).resolve(strict=True)
    python_version = platform.python_version()
    if not python_version.startswith(f"{lock.python_version}."):
        raise ValueError(
            "Runtime Python version mismatch: "
            f"expected={lock.python_version}.x, observed={python_version}."
        )
    runtime_tools.append(
        RuntimeExecutableIdentity(
            name="python",
            path=str(python_path),
            version=python_version,
            sha256=file_sha256(python_path),
        )
    )
    runtime_dependencies = _runtime_dependency_versions(lock.dependencies)
    support_files = tuple(
        RuntimeSupportFileIdentity(
            name=name,
            path=str(path.resolve(strict=True)),
            sha256=file_sha256(path.resolve(strict=True)),
        )
        for name, path in sorted(support_file_paths.items())
    )
    return HeldOutMachineRunIdentity.build(
        producer=producer,
        entrypoint=entrypoint,
        toolchain_id=lock.toolchain_id,
        toolchain_lock_sha256=file_sha256(lock_path),
        platform_name=platform.platform(),
        python_version=python_version,
        runtime_dependencies=runtime_dependencies,
        runtime_tools=tuple(sorted(runtime_tools, key=lambda item: item.name)),
        support_files=support_files,
        selected_corridor_keys=tuple(
            sorted({key.strip() for key in selected_corridor_keys if key.strip()})
        ),
        timeout_seconds=timeout_seconds,
        blinding_seed_sha256=hashlib.sha256(blinding_seed.encode("utf-8")).hexdigest(),
    )


def capture_code_producer_identity(repository_root: Path) -> CodeProducerIdentity:
    root = repository_root.resolve(strict=True)
    top_level = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top_level != root:
        raise ValueError(
            f"Repository root mismatch: requested={root}, git={top_level}."
        )
    dirty = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ValueError(
            "Formal evidence requires a clean Git worktree; commit or remove changes first."
        )
    return CodeProducerIdentity(
        repository_url=_git(root, "remote", "get-url", "origin"),
        revision=_git(root, "rev-parse", "HEAD"),
        tree_revision=_git(root, "rev-parse", "HEAD^{tree}"),
        branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        working_tree_clean=True,
    )


def _runtime_dependency_versions(expected: Mapping[str, str]) -> dict[str, str]:
    observed: dict[str, str] = {}
    for name, expected_version in sorted(expected.items()):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"Locked Python dependency is missing: {name}") from exc
        if version != expected_version:
            raise ValueError(
                "Runtime Python dependency mismatch: "
                f"{name} expected={expected_version}, observed={version}."
            )
        observed[name] = version
    return observed


def _runtime_version(path: Path, *, name: str) -> str:
    completed = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    match = _VERSION_RE.search(output)
    if completed.returncode or match is None:
        raise ValueError(f"Unable to identify runtime tool {name}: {path}")
    return match.group(1)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise ValueError(
            f"Git identity command failed ({' '.join(arguments)}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()
