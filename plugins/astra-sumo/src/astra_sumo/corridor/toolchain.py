from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from .base import ContractModel, Sha256, StableToken
from .ids import stable_id


class ToolIdentity(ContractModel):
    name: str
    executable: str
    version: str
    build_features: tuple[str, ...] = ()
    executable_sha256: Sha256 | None = None
    required: bool = True


class ToolchainLock(ContractModel):
    toolchain_id: StableToken
    frozen_at: datetime
    platform: str
    python_version: str
    dependencies: dict[str, str]
    tools: tuple[ToolIdentity, ...]
    command_parameters: dict[str, tuple[str, ...]]
    random_seeds: tuple[int, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "python_version": self.python_version,
            "dependencies": self.dependencies,
            "tools": [tool.model_dump(mode="json") for tool in self.tools],
            "command_parameters": self.command_parameters,
            "random_seeds": self.random_seeds,
        }

    @classmethod
    def build(
        cls,
        *,
        frozen_at: datetime,
        platform: str,
        python_version: str,
        dependencies: dict[str, str],
        tools: tuple[ToolIdentity, ...],
        command_parameters: dict[str, tuple[str, ...]],
        random_seeds: tuple[int, ...],
    ) -> ToolchainLock:
        payload = {
            "platform": platform,
            "python_version": python_version,
            "dependencies": dependencies,
            "tools": [tool.model_dump(mode="json") for tool in tools],
            "command_parameters": command_parameters,
            "random_seeds": random_seeds,
        }
        return cls(
            toolchain_id=stable_id("toolchain", payload),
            frozen_at=frozen_at,
            platform=platform,
            python_version=python_version,
            dependencies=dependencies,
            tools=tools,
            command_parameters=command_parameters,
            random_seeds=random_seeds,
        )

    @model_validator(mode="after")
    def validate_lock(self) -> ToolchainLock:
        if self.frozen_at.tzinfo is None:
            raise ValueError("Toolchain locks require a timezone-aware frozen_at.")
        expected = stable_id("toolchain", self.identity_payload())
        if self.toolchain_id != expected:
            raise ValueError("toolchain_id does not match the locked toolchain payload.")
        if not self.tools:
            raise ValueError("Toolchain locks require at least one tool.")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique in a toolchain lock.")
        if not self.random_seeds:
            raise ValueError("Toolchain locks require explicit random seeds.")
        return self
