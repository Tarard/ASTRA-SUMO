"""Record lane-length measures for explicit construction-stage network files."""

import argparse
import json

from astra_sumo.core.network_length_audit import audit_network_lengths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_file")
    parser.add_argument("net_files", nargs="+")
    args = parser.parse_args()
    report = audit_network_lengths(net_files=args.net_files, output_file=args.output_file)
    print(json.dumps({"status": report["status"], "report_file": report["report_file"],
                      "network_count": len(report["networks"])}, indent=2))


if __name__ == "__main__":
    main()
