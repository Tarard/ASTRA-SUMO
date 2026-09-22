from __future__ import annotations

from pathlib import Path
from typing import Any

from torii_sumo.core.ocit_c_signal_devices import classify_ocit_c_signal_device_inventory


def sumo_signal_device_profile_classify(
    ocit_file: str,
    expected_node_id: str | None = None,
) -> dict[str, Any]:
    """Classify one immutable OCIT-C supply snapshot into physical signal profiles.

    This is a read-only identity pass.  It does not bind a profile to SUMO
    links, movements, phases, programs, or a controller strategy.
    """

    source = Path(ocit_file).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"ocit_file must be an existing local file: {source}")

    source_bytes = source.read_bytes()
    inventory = classify_ocit_c_signal_device_inventory(
        source_bytes,
        source_file=str(source),
        expected_node_id=expected_node_id,
    )
    return inventory.model_dump(mode="json", by_alias=True)
