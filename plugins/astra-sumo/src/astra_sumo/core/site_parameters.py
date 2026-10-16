"""Load versioned site parameters without executing configuration code."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files


def load_site_parameters(profile_id: str) -> dict:
    """Return the declared values and the exact bundled source identity."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", profile_id):
        raise ValueError("Site profile ID must be a simple package name.")
    resource = files("astra_sumo").joinpath("data", "sites", profile_id + ".json")
    raw = resource.read_bytes()
    document = json.loads(raw)
    if document.get("schema") != "astra.site-parameters/v1" or document.get("profile_id") != profile_id:
        raise ValueError("Site parameter schema or identity does not match.")

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"$mapping"}:
                return {decode(key): decode(item) for key, item in value["$mapping"]}
            for key, constructor in (("$tuple", tuple), ("$frozenset", frozenset), ("$set", set)):
                if set(value) == {key}:
                    return constructor(decode(item) for item in value[key])
            return {key: decode(item) for key, item in value.items()}
        return value

    parameters = decode(document["parameters"])
    if not isinstance(parameters, dict) or "_source" in parameters:
        raise ValueError("Site parameters must be an object without reserved source fields.")
    return {**parameters, "_source": {"profile_id": profile_id, "path": str(resource),
            "sha256": hashlib.sha256(raw).hexdigest(), "field_acceptance": document["field_acceptance"]}}
