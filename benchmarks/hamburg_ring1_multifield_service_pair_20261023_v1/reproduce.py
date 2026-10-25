"""Explicitly replay the saved service pair using the prior fixed-demand entry point."""
import argparse
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve()
    previous = here.parent.parent / 'hamburg_ring1_multifield_substitution_20261023_v1/reproduce.py'
    spec = spec_from_file_location('fixed_substitution_replay', previous)
    replay = module_from_spec(spec)
    spec.loader.exec_module(replay)
    # Its existing entry point resolves inputs relative to __file__; only bind this one case.
    replay.__file__ = str(here)
    sys.argv = [str(here), '--case', 'substitute-1pair', '--output-dir', str(args.output_dir)]
    replay.main()


if __name__ == '__main__':
    main()
