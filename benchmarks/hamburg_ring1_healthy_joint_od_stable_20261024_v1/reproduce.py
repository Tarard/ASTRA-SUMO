"""Recompute the saved stable64 evidence offline; never start SUMO."""
import argparse
from pathlib import Path
import runpy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    case=Path(__file__).resolve().parent
    out=args.output_dir.resolve()
    tools=runpy.run_path(str(case/'freeze_evidence.py'))
    tools['verify'](case)
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    out.mkdir(parents=True)
    tools['save'](out/'offline-reproduction.json',{'status':'pass','native_runs':0,'audit':tools['audit_packet'](case)})


if __name__=='__main__':
    main()
