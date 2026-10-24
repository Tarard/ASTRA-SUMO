import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


spec = importlib.util.spec_from_file_location(
    "native_junit_check", Path(__file__).parents[1] / ".github/scripts/check_windows_native_junit.py"
)
native_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_check)


@pytest.mark.parametrize("fault", [None, "missing", "skipped", "failure"])
def test_native_junit_policy_requires_execution_and_success(tmp_path, fault):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for prefix, minimum in native_check.REQUIRED.items():
        for index in range(minimum):
            ET.SubElement(suite, "testcase", name=f"{prefix}[{index}]")
    first = suite.find("testcase")
    if fault == "missing":
        suite.remove(first)
    elif fault is not None:
        ET.SubElement(first, fault)
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    if fault is not None:
        with pytest.raises(ValueError, match="critical native|Critical native"):
            native_check.check_native_execution(path)
    else:
        counts = native_check.check_native_execution(path)
        assert counts["test_windows_native_fatal_exit_is_bounded"] == 20
