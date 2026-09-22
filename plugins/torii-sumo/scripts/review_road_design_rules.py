"""Run independent road-design rule checks without constructing a network."""
import argparse
import json

from torii_sumo.road_network.design_review import build_road_design_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    try:
        result = build_road_design_review(request_file=args.request_file, output_dir=args.output_dir)
    except (ValueError, OSError) as error:
        print(json.dumps(dict(status="blocked", error=str(error))))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
