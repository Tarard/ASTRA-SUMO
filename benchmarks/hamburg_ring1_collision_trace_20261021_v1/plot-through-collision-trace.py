"""Plot native lane geometry and collision positions in local metric coordinates."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / 'outputs/ring1-through-collision-trace-20261021-v1'
OUT = ROOT / 'outputs/ring1-through-collision-analysis-20261021-v1'
network = ET.parse(TRACE / 'network.net.xml').getroot()
events = ET.parse(TRACE / 'collisions.xml').getroot().findall('collision')
context = json.loads((OUT / 'trajectory-context.json').read_bytes())['events']
fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
for index, (event, ax, node) in enumerate(zip(events, axes, ('14398547', '205069138'))):
    center = tuple(map(float, event.get('colliderFront').split(',')))
    for edge in network.findall('edge'):
        for lane in edge.findall('lane'):
            points = [tuple(map(float, p.split(','))) for p in lane.get('shape').split()]
            if any(abs(x-center[0]) < 20 and abs(y-center[1]) < 20 for x,y in points):
                ax.plot(*zip(*points), color='.75', lw=1, zorder=0)
                if edge.get('id').startswith(':'+node+'_'):
                    midpoint = points[len(points)//2]
                    ax.text(*midpoint, lane.get('id').replace(':'+node+'_', ''), fontsize=8, color='.35')
    for role, color in (('collider', '#c55318'), ('victim', '#156aaf')):
        front = tuple(map(float, event.get(role+'Front').split(',')))
        back = tuple(map(float, event.get(role+'Back').split(',')))
        ax.plot(*zip(back, front), color=color, lw=4, label=role+' front/back', zorder=3)
        ax.scatter(*front, color=color, s=35, zorder=4)
        ax.scatter(*back, color=color, marker='x', s=35, zorder=4)
        rows = context[index]['vehicles'][role]['last_samples_through_event']
        rows = [r for r in rows if r['time'] < float(event.get('time'))]
        ax.plot([float(r['x']) for r in rows], [float(r['y']) for r in rows], ':', color=color)
    ax.set(xlim=(center[0]-12, center[0]+12), ylim=(center[1]-12, center[1]+12),
           title=f"Junction {node}, t={event.get('time')} s", xlabel='Native x (m)', ylabel='Native y (m)')
    ax.set_aspect('equal')
    ax.grid(alpha=.15)
    ax.legend(fontsize=8)
fig.suptitle('Native collision placement: lane centerlines and recorded front/back points\nSimulation geometry only; no aerial or field-geometry claim', fontsize=11)
fig.savefig(OUT / 'native-collision-positions.png', dpi=170)
print(OUT / 'native-collision-positions.png')
