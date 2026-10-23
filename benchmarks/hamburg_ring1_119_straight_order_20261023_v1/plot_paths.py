"""Plot native curve order, not an inferred field trajectory."""

import argparse
import gzip
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
out = parser.parse_args().output_dir.resolve()
out.mkdir(parents=True, exist_ok=False)
manifest = json.loads((root / "construction.json").read_bytes())
old = ET.fromstring(gzip.decompress((root.parent / 'hamburg_ring1_104_split_fanout_20261023_v1/candidate.net.xml.gz').read_bytes()))
left = old.find("edge/lane[@id=':LSA119_part0_0_0']")
left_points = [tuple(map(float, p.split(','))) for p in left.get('shape').split()]
row = next(r for r in manifest['vertex_order_changes'] if r['connection_key'][1] == 3)
with plt.rc_context({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False}):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), layout='constrained', sharex=True, sharey=True)
    for ax, key, title, color in zip(axes, ('before', 'ordered'),
            ('Before: forward and backward segments', 'Candidate: forward vertex order'), ('#D55E00', '#0072B2')):
        points = row[key]
        ax.plot(*zip(*left_points), color='#555555', linestyle='--', linewidth=1.5, label='Left-turn internal curve')
        ax.plot(*zip(*points), marker='.', color=color, linewidth=1.5, label='Straight internal curve')
        for a, b in zip(points, points[1:]):
            if abs(b[0] - a[0]) > 4:
                ax.annotate('', xy=(a[0]+.7*(b[0]-a[0]), a[1]+.7*(b[1]-a[1])),
                    xytext=(a[0]+.3*(b[0]-a[0]), a[1]+.3*(b[1]-a[1])),
                    arrowprops={'arrowstyle': '->', 'color': color, 'lw': 1.5})
        ax.set(xlim=(902, 931), ylim=(661, 675), xlabel='SUMO local x (m)', title=title)
        ax.set_aspect('equal')
        ax.grid(alpha=.2)
    axes[0].set_ylabel('SUMO local y (m)')
    axes[1].legend(loc='upper right', fontsize=8)
    fig.suptitle('LSA119 curve-order diagnostic | fixed endpoints and source-derived vertices\nModel geometry, not an official MAP reference line or a measured vehicle trajectory')
    fig.savefig(out / 'curve-order.png', dpi=180)
    fig.savefig(out / 'curve-order.svg')
    plt.close(fig)
