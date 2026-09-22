from __future__ import annotations

from pathlib import Path

from pydantic import model_validator

from torii_sumo.core.candidate_contracts import file_sha256

from .base import ContractModel, Sha256
from .enums import GateStatus
from .netxml import normalized_net_sha256


class NetReplayReport(ContractModel):
    schema_id: str = "torii.corridor.net-replay-report/v1"
    status: GateStatus
    primary_net_path: str
    primary_net_sha256: Sha256
    primary_normalized_sha256: Sha256
    replay_net_path: str
    replay_net_sha256: Sha256
    replay_normalized_sha256: Sha256
    reproducible_semantics: bool
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> NetReplayReport:
        hashes_match = (
            self.primary_normalized_sha256 == self.replay_normalized_sha256
        )
        if self.reproducible_semantics != hashes_match:
            raise ValueError("Replay conclusion contradicts normalized network hashes.")
        if self.status is GateStatus.PASS and (
            not self.reproducible_semantics or self.blockers
        ):
            raise ValueError("Passing replay reports cannot hide blockers.")
        if self.status is not GateStatus.PASS and not self.blockers:
            raise ValueError("Non-passing replay reports require an explicit blocker.")
        return self


def compare_netconvert_replay(
    primary_net: Path,
    replay_net: Path,
) -> NetReplayReport:
    primary = primary_net.resolve(strict=True)
    replay = replay_net.resolve(strict=True)
    primary_normalized = normalized_net_sha256(primary)
    replay_normalized = normalized_net_sha256(replay)
    reproducible = primary_normalized == replay_normalized
    blockers = () if reproducible else ("normalized_net_replay_mismatch",)
    return NetReplayReport(
        status=GateStatus.PASS if reproducible else GateStatus.BLOCKED,
        primary_net_path=str(primary),
        primary_net_sha256=file_sha256(primary),
        primary_normalized_sha256=primary_normalized,
        replay_net_path=str(replay),
        replay_net_sha256=file_sha256(replay),
        replay_normalized_sha256=replay_normalized,
        reproducible_semantics=reproducible,
        blockers=blockers,
    )
