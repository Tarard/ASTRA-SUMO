"""Run unchanged OSM through separate netconvert processes with one fixed command."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import xml.etree.ElementTree as ET


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def semantic_snapshot(path: Path) -> dict:
    """Ignore comments, whitespace, attribute order, and root child order only."""
    def element(node: ET.Element) -> tuple:
        attributes = dict(node.attrib)
        if node.tag == "roundabout":
            for key in ("nodes", "edges"):
                if key in attributes:
                    attributes[key] = " ".join(sorted(attributes[key].split()))
        return (node.tag, sorted(attributes.items()), (node.text or "").strip(),
                [element(child) for child in node])

    root = ET.parse(path).getroot()
    groups = {}
    for name, nodes in {
        "roundabouts": root.findall("roundabout"),
        "other_elements": [node for node in root if node.tag != "roundabout"],
    }.items():
        records = sorted(json.dumps(element(node), separators=(",", ":")) for node in nodes)
        groups[name] = sha256(json.dumps(records).encode()).hexdigest()
    groups["roundabout_records"] = sorted(
        [{key: sorted(node.get(key, "").split()) for key in ("edges", "nodes")}
         for node in root.findall("roundabout")],
        key=lambda record: (record["edges"], record["nodes"]),
    )
    return groups


def reproduce(binary: Path, source: Path, output: Path, runs: int) -> dict:
    binary, source, output = binary.resolve(), source.resolve(), output.resolve()
    if runs < 2:
        raise ValueError("Use at least two processes.")
    if not binary.is_file() or not source.is_file():
        raise FileNotFoundError("The binary and input must exist.")
    if source == binary:
        raise ValueError("The binary and input must be different files.")
    # A new directory prevents stale files and preserves all previous evidence.
    output.mkdir(parents=True, exist_ok=False)
    source_hash, binary_hash = digest(source), digest(binary)
    local_source = output / "input.osm.xml"
    shutil.copyfile(source, local_source)
    version = subprocess.run([str(binary), "--version"], capture_output=True,
                             text=True, check=True, timeout=60).stdout
    command = [str(binary), "--osm-files", str(local_source), "--output-file",
               str(output / "network.net.xml"), "--seed", "42",
               "--roundabouts.guess", "false", "--no-turnarounds"]
    records = []
    for index in range(runs):
        record = {"index": index}
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (output / f"run-{index:03d}.log").write_text(
            process.stdout + process.stderr, encoding="utf-8")
        record["returncode"] = process.returncode
        if process.returncode == 0:
            network = output / f"run-{index:03d}.net.xml"
            shutil.copyfile(output / "network.net.xml", network)
            record.update(semantic_snapshot(network))
            record["network_sha256"] = digest(network)
        records.append(record)
    unchanged = (digest(source) == source_hash and digest(local_source) == source_hash
                 and digest(binary) == binary_hash)
    success = all(record["returncode"] == 0 for record in records) and unchanged
    roundabout_groups = Counter(record["roundabouts"] for record in records if "roundabouts" in record)
    report = {
        "schema": "astra.netconvert-roundabout-reproduction/v1",
        "status": ("failed" if not success else
                   "reproduced" if len(roundabout_groups) > 1 else "not_observed"),
        "source_sha256": source_hash, "binary_sha256": binary_hash,
        "source_path": str(source), "binary_path": str(binary),
        "inputs_unchanged": unchanged, "version": version,
        "platform": platform.platform(), "command": command, "runs": records,
        "roundabout_variants": dict(sorted(roundabout_groups.items())),
        "other_element_variants": len({record["other_elements"] for record in records
                                       if "other_elements" in record}),
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Absolute native netconvert executable.")
    parser.add_argument("output", type=Path, help="New evidence directory.")
    parser.add_argument("--source", type=Path, default=Path(__file__).with_name("input.osm.xml"))
    parser.add_argument("--runs", type=int, default=64)
    args = parser.parse_args()
    report = reproduce(args.binary, args.source, args.output, args.runs)
    print(json.dumps({key: report[key] for key in (
        "status", "roundabout_variants", "other_element_variants", "inputs_unchanged")}, indent=2))
    raise SystemExit(2 if report["status"] == "failed" else 0)


if __name__ == "__main__":
    main()
