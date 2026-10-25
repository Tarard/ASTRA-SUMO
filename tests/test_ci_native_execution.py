import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


spec = importlib.util.spec_from_file_location(
    "native_junit_check", Path(__file__).parents[1] / ".github/scripts/check_windows_native_junit.py"
)
native_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_check)


CHECKOUT = r"D:\a\ASTRA-SUMO\ASTRA-SUMO\tests\test_skip.py"


def _write_report(tmp_path, skips, *, with_message=True):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    ET.SubElement(suite, "testcase", classname="tests.test_ok", name="test_passes")
    for index, message in enumerate(skips):
        case = ET.SubElement(suite, "testcase", classname="tests.test_skip", name=f"test_skipped_{index}")
        skipped = ET.SubElement(case, "skipped", type="pytest.skip", **({"message": message} if with_message else {}))
        skipped.text = f"{CHECKOUT}:90: {message}"
    path = tmp_path / "junit.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "message",
    [
        "SUMO binaries required",
        "netconvert is not installed",
        "The project netconvert binary is unavailable.",
        "could not import 'libsumo': No module named 'libsumo'",
        "Native SUMO is required.",
    ],
)
@pytest.mark.parametrize("with_message", [True, False])
def test_native_skip_fails_the_report(tmp_path, message, with_message):
    with pytest.raises(ValueError, match="skipped because SUMO was unavailable"):
        native_check.check_native_execution(_write_report(tmp_path, [message], with_message=with_message))


UNRELATED_SKIPS = [
    "current Hamburg 2394 MAP artifact is not present",
    "historical generated Hamburg artifact fixture is not committed",
    "untracked official Hamburg 2394 MAP KML is unavailable",
    "untracked official Hamburg 2394 MAP KML/XML files are unavailable",
    "untracked official Hamburg screen export KML/XML files are unavailable",
]


@pytest.mark.parametrize("with_message", [True, False])
def test_unrelated_skips_pass_even_under_an_astra_sumo_checkout_path(tmp_path, with_message):
    path = _write_report(tmp_path, UNRELATED_SKIPS, with_message=with_message)
    assert native_check.check_native_execution(path) == {"testcases": 6, "native_skips": 0}


def test_empty_report_fails(tmp_path):
    path = tmp_path / "junit.xml"
    ET.ElementTree(ET.Element("testsuites")).write(path, encoding="utf-8")
    with pytest.raises(ValueError, match="no test cases"):
        native_check.check_native_execution(path)
