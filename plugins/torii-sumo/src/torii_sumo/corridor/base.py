from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


CORRIDOR_CONTRACT_VERSION = "torii.corridor.contracts/v1"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
STABLE_TOKEN_PATTERN = r"^[a-z][a-z0-9_]*_[0-9a-f]{24}$"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
StableToken = Annotated[str, Field(pattern=STABLE_TOKEN_PATTERN)]


def _contract_alias(field_name: str) -> str:
    return "schema" if field_name == "schema_id" else field_name


class ContractModel(BaseModel):
    """Immutable, strict base for corridor research artifacts."""

    schema_id: str = Field(default=CORRIDOR_CONTRACT_VERSION, alias="schema")

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=_contract_alias,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )
