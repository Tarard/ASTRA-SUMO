"""Recompute the frozen passive TLS comparison offline; never start SUMO."""
import argparse
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    tools = runpy.run_path(str(case/'freeze_evidence.py'))
    tools['verify'](case)
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    out.mkdir(parents=True)
    result = tools['recompute'](case, out/'full-vector')
    movement = tools['recompute_movement'](case, out/'movement')
    tools['save'](out/'offline-reproduction.json', {'status': 'pass', 'native_runs': 0, 'comparison': result, 'movement': movement})


if __name__ == '__main__':
    main()
