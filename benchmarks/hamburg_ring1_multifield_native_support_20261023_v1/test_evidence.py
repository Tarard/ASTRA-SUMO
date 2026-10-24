"""A second different field is allowed; repeated or missing fields are rejected."""

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location("support_validation", Path(__file__).with_name("validate.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("variant", ["valid", "repeat", "missing"])
def test_distinct_complete_fields_and_identity(variant):
    rows = '<instantOut id="a" vehID="v" time="10" state="leave" occupancy="1"/>'
    if variant != "missing":
        rows += '<instantOut id="b" vehID="v" time="20" state="leave" occupancy="1"/>'
    if variant == "repeat":
        rows += '<instantOut id="a" vehID="v" time="30" state="leave" occupancy="1"/>'
    args = (ET.fromstring('<instantE1>' + rows + '</instantE1>'), {"a": "f1", "b": "f2"},
            {("f1", 0., 900.): 1, ("f2", 0., 900.): 1}, {"v": ["f1", "f2"]})
    if variant == "valid":
        assert len(audit.complete_events(*args)) == 2
    else:
        with pytest.raises(AssertionError):
            audit.complete_events(*args)
