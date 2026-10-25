"""Fail the Windows suite when a test was skipped because SUMO or one of its programs was missing."""

import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


NATIVE_PROGRAM = re.compile(r"(?i)\b(sumo|netconvert|netgenerate|duarouter|libsumo|traci|native)\b")
MISSING = re.compile(r"(?i)\b(required|not installed|unavailable|missing|not found)\b|could not import")
# pytest writes "<path>:<line>: <reason>" as the element text; the path (e.g. ...\ASTRA-SUMO\...) is not a reason.
LOCATION = re.compile(r"^.*?:\d+: ")


def check_native_execution(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        raise ValueError("The JUnit report contains no test cases.")
    native_skips = []
    for case in cases:
        for skipped in case.findall("skipped"):
            reason = skipped.get("message") or LOCATION.sub("", (skipped.text or "").strip(), count=1)
            if NATIVE_PROGRAM.search(reason) and MISSING.search(reason):
                native_skips.append(f"{case.get('classname', '')}::{case.get('name', '')}: {skipped.get('message', '')}")
    if native_skips:
        raise ValueError("Native cases were skipped because SUMO was unavailable:\n" + "\n".join(native_skips))
    return {"testcases": len(cases), "native_skips": 0}


if __name__ == "__main__":
    print(json.dumps(check_native_execution(Path(sys.argv[1])), sort_keys=True))
