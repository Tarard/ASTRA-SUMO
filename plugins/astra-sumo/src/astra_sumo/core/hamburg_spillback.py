"""Locate downstream queue spillback in completed native Hamburg records."""

from collections import defaultdict
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import sumolib

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import read_csv_rows, write_csv


def read_queue_series(path, lane_ids):
    """Keep absent lane reports unknown, including in otherwise complete samples."""
    times, records = [], []
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rb') as stream:
        for _, node in ET.iterparse(stream, events=('end',)):
            if node.tag != 'data':
                continue
            time = float(node.get('timestep'))
            if not np.isfinite(time) or times and time <= times[-1]:
                raise ValueError('Queue samples need finite increasing times.')
            values = {}
            for lane in node.findall('lanes/lane'):
                identity = lane.get('id')
                if identity not in lane_ids:
                    continue
                value = float(lane.get('queueing_length'))
                if identity in values or not np.isfinite(value) or value < 0:
                    raise ValueError('Queue lengths need unique lanes and finite nonnegative values.')
                values[identity] = value
            times.append(time)
            records.append(values)
            node.clear()
    series = {lane: np.array([row.get(lane, np.nan) for row in records]) for lane in lane_ids}
    return np.array(times), series


def static_green(program, link_indices, times):
    """Reconstruct declared static green; do not infer actuated downstream states."""
    if program.get('type') != 'static' or not link_indices:
        raise ValueError('Green reconstruction requires a declared static movement.')
    phases = program.findall('phase')
    durations = np.array([float(row.get('duration')) for row in phases])
    if np.any(~np.isfinite(durations)) or np.any(durations <= 0) or any(row.get('next') for row in phases):
        raise ValueError('Use a fixed positive-duration sequence without conditional jumps.')
    phase = np.searchsorted(np.cumsum(durations), (times + float(program.get('offset', '0'))) % durations.sum(), side='right')
    return np.array([any(phases[index].get('state')[link] in 'Gg' for link in link_indices) for index in phase])


def spillback_mask(upstream_queue, receiving_queues, receiving_lengths, green):
    """Require a reported upstream queue and full queues on every planned receiver."""
    if not receiving_queues or len(receiving_queues) != len(receiving_lengths):
        raise ValueError('Retain every planned passenger receiving lane.')
    full = np.logical_and.reduce([np.isfinite(queue) & (queue >= length)
        for queue, length in zip(receiving_queues, receiving_lengths)])
    return np.isfinite(upstream_queue) & (upstream_queue > 0) & full & green


def queue_episodes(times, mask):
    """Count only recorded one-second intervals, without bridging missing samples."""
    result = []
    for time in times[mask]:
        if result and abs(result[-1]['end_s'] - time) < 1e-6:
            result[-1]['end_s'] = float(time + 1)
        else:
            result.append({'begin_s': float(time), 'end_s': float(time + 1)})
    return [{**row, 'duration_s': row['end_s'] - row['begin_s']} for row in result]


def analyze_hamburg_spillback(*, case_dir, output_dir):
    """Relate all thirteen field residuals to native receiving-lane queue records."""
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError('Use a new analysis directory separate from the completed case.')
    p, native = case / 'inputs', case / 'native'
    paths = [p / name for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'control.add.xml', 'lane-intentions.json')]
    paths += [native / name for name in ('queues.xml', 'run.json', 'collisions.xml', 'field-comparison.csv')]
    paths += [case / 'report/passage-reconciliation.json', native / 'instant-e1.xml', native / 'field-counts.xml']
    sources = {str(path): file_sha256(path) for path in paths}
    run = json.loads((native / 'run.json').read_bytes())
    if not run.get('observer_complete') or (run.get('stall_monitor') or {}).get('native_version') != [22, 'SUMO 1.27.1']:
        raise ValueError('Use completed records from the declared native SUMO 1.27.1 run.')
    declared = {row['path'].replace('\\', '/').rsplit('/', 1)[-1]: row['sha256'] for row in run['inputs']}
    if any(sources[str(path)] != declared[path.name] for path in paths[:5]):
        raise ValueError('Network, control and demand must match the native run inputs.')
    reconciliation = json.loads((case / 'report/passage-reconciliation.json').read_bytes())
    if not reconciliation.get('reconciled') or not reconciliation.get('inputs_unchanged'):
        raise ValueError('Use reconciled native periodic and instantaneous E1 records.')
    for row in reconciliation['inputs']:
        name = row['path'].replace('\\', '/').rsplit('/', 1)[-1]
        if sources[str(native / name)] != row['sha256']:
            raise ValueError('Native E1 files differ from the reconciled passage records.')
    passages = defaultdict(list)
    for row in reconciliation['passages']:
        passages[row['detector_id']].append(float(row['time_s']))
    network = ET.parse(p / 'network.net.xml').getroot()
    net = sumolib.net.readNet(str(p / 'network.net.xml'), withInternal=True)
    lanes = {lane.get('id'): lane for edge in network.findall('edge') for lane in edge.findall('lane')}
    lane_roads = {lane.get('id'): edge.get('id') for edge in network.findall('edge') for lane in edge.findall('lane')}
    loops = ET.parse(p / 'field-loops.add.xml').getroot().findall('inductionLoop')
    if len(loops) != 13:
        raise ValueError('Keep all thirteen field sections.')
    intentions = {row['vehicle_id']: row for row in json.loads((p / 'lane-intentions.json').read_bytes())['vehicles']}
    next_roads = defaultdict(set)
    field_roads = {row.get('id'): lane_roads[row.get('lane')] for row in loops}
    for vehicle in ET.parse(p / 'demand.rou.xml').getroot().findall('vehicle'):
        field = intentions[vehicle.get('id')]['field_id']
        route = vehicle.find('route').get('edges').split()
        index = route.index(field_roads[field])
        if index + 1 == len(route):
            raise ValueError('Retain the receiving road after every observed approach.')
        next_roads[field].add(route[index + 1])
    programs = {row.get('id'): row for row in ET.parse(p / 'control.add.xml').getroot().findall('tlLogic')}
    movements, wanted = {}, {row.get('lane') for row in loops}
    for loop in loops:
        field, lane = loop.get('id'), loop.get('lane')
        edge, index = lane.rsplit('_', 1)
        connections = [row for row in network.findall('connection') if row.get('from') == edge
            and row.get('fromLane') == index and row.get('to') in next_roads[field]
            and net.getLane(row.get('to') + '_' + row.get('toLane')).allows('passenger')]
        if not connections or len({row.get('tl') for row in connections}) != 1 or not connections[0].get('tl'):
            raise ValueError('Each field needs its planned controlled passenger connections.')
        receivers = sorted({row.get('to') + '_' + row.get('toLane') for row in connections})
        movements[field] = {'lane': lane, 'receivers': receivers, 'tls': connections[0].get('tl'),
            'links': sorted({int(row.get('linkIndex')) for row in connections})}
        wanted.update(receivers)
    times, queues = read_queue_series(native / 'queues.xml', wanted)
    if not np.array_equal(times, np.arange(run['end_s'])):
        raise ValueError('This analysis requires the complete one-second queue observation grid.')
    comparisons = read_csv_rows(native / 'field-comparison.csv')
    if len(comparisons) != 130 or any(row['measured_nVehContrib'] in ('', 'None') for row in comparisons):
        raise ValueError('Retain all 130 complete native count rows.')
    collisions = ET.parse(native / 'collisions.xml').getroot().findall('collision')
    first_collision = min((float(row.get('time')) for row in collisions), default=float(run['end_s']))
    main = (times >= 3600) & (times < 12600)
    summaries, bins, episodes, receiver_rows, series_rows = [], [], [], [], []
    for field, movement in movements.items():
        lane, receivers = movement['lane'], movement['receivers']
        lengths = [float(lanes[key].get('length')) for key in receivers]
        green = static_green(programs[movement['tls']], movement['links'], times)
        upstream = np.isfinite(queues[lane]) & (queues[lane] > 0)
        blocked = spillback_mask(queues[lane], [queues[key] for key in receivers], lengths, green)
        full = np.logical_and.reduce([np.isfinite(queues[key]) & (queues[key] >= length) for key, length in zip(receivers, lengths)])
        for key, length in zip(receivers, lengths):
            reported = np.isfinite(queues[key]) & main
            receiver_rows.append({'detector_id': field, 'receiving_lane': key, 'length_m': length,
                'queue_reported_samples': int(reported.sum()), 'full_queue_samples': int((reported & (queues[key] >= length)).sum()),
                'maximum_reported_queue_m': float(np.max(queues[key][reported])) if np.any(reported) else None})
        local = [row for row in comparisons if row['detector_id'] == field]
        local_episodes = [{**row, 'native_E1_contributions': sum(row['begin_s'] <= time < row['end_s'] for time in passages[field])}
                          for row in queue_episodes(times, main & blocked)]
        episodes.extend({'detector_id': field, **row} for row in local_episodes)
        summaries.append({'detector_id': field, **movement, 'expected_total': sum(int(row['expected_total']) for row in local),
            'native_total': sum(int(row['measured_nVehContrib']) for row in local),
            'green_samples': int((green & main).sum()), 'queued_green_samples': int((green & main & upstream).sum()),
            'full_receiving_queue_samples': int((full & main).sum()), 'spillback_green_samples': int((blocked & main).sum()),
            'spillback_green_before_first_collision_samples': int((blocked & main & (times < first_collision)).sum()),
            'spillback_episodes_without_E1_contribution': sum(row['native_E1_contributions'] == 0 for row in local_episodes),
            'longest_sampled_spillback_episode_s': max((row['duration_s'] for row in local_episodes), default=0)})
        for row in local:
            selected = (times >= float(row['begin'])) & (times < float(row['end']))
            bins.append({**row, 'green_samples': int((selected & green).sum()),
                'queued_green_samples': int((selected & green & upstream).sum()),
                'spillback_green_samples': int((selected & blocked).sum()),
                'spillback_green_before_first_collision_samples': int((selected & blocked & (times < first_collision)).sum())})
        selected = np.flatnonzero(main & (upstream | full))
        for index in selected:
            series_rows.append({'detector_id': field, 'time_s': float(times[index]),
                'declared_static_green': bool(green[index]), 'reported_upstream_queue_m': None if not np.isfinite(queues[lane][index]) else float(queues[lane][index]),
                'all_receiving_queues_reach_lane_start': bool(full[index]), 'spillback_green_evidence': bool(blocked[index]),
                **{key + '_queue_m': None if not np.isfinite(queues[key][index]) else float(queues[key][index]) for key in receivers}})
    out.mkdir(parents=True)
    for name, rows in [('field-summary.csv', summaries), ('all-130-bin-evidence.csv', bins), ('episodes.csv', episodes), ('receiving-lanes.csv', receiver_rows)]:
        if rows:
            write_csv(out / name, rows, list(rows[0]))
    columns = list(dict.fromkeys(key for row in series_rows for key in row))
    write_csv(out / 'queue-evidence.csv', series_rows, columns)
    report = {'status': 'measured', 'new_simulations': 0, 'formal_result': False, 'native_case_valid': run['healthy'],
        'native_completion': run['completion'], 'first_collision_s': first_collision, 'queue_sample_period_s': 1,
        'field_rows': len(summaries), 'count_target_rows': len(bins), 'fields': summaries,
        'evidence_rule': 'At a recorded one-second sample: positive reported upstream queue, declared static green on a planned movement, and reported queues reaching the upstream end of every planned passenger receiving lane. Missing lane records do not become zero queues or free-space evidence.',
        'sources_sha256': sources, 'sources_unchanged': all(file_sha256(Path(path)) == sha for path, sha in sources.items()),
        'claim_boundary': 'Receiving-queue spillback evidence in one realized model run. It does not measure the causal share of count residuals, prove excess unique demand, recover actuated signal states, establish field control truth or prove infeasibility. Queue and planned-green evidence are diagnostic simulator truth; calibration counts remain native E1 nVehContrib.'}
    write_json_atomic(out / 'report.json', report)
    return report


def plot_hamburg_spillback(output_dir):
    """Show the longest zero-contribution example before the first collision per affected field."""
    out = Path(output_dir)
    if (out / 'spillback-examples.png').exists():
        raise ValueError('Keep the existing figure and use a new analysis directory.')
    report = json.loads((out / 'report.json').read_bytes())
    if not (out / 'episodes.csv').is_file():
        return None
    episodes = read_csv_rows(out / 'episodes.csv')
    data = read_csv_rows(out / 'queue-evidence.csv')
    receivers = read_csv_rows(out / 'receiving-lanes.csv')
    selected = []
    for field in report['fields']:
        candidates = [row for row in episodes if row['detector_id'] == field['detector_id']
            and float(row['end_s']) <= report['first_collision_s'] and int(row['native_E1_contributions']) == 0]
        if candidates:
            selected.append(max(candidates, key=lambda row: float(row['duration_s'])))
    if not selected:
        return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(len(selected), 1, figsize=(12, 3 * len(selected)), squeeze=False, constrained_layout=True)
    for ax, episode in zip(axes.flat, selected):
        field, begin, end = episode['detector_id'], float(episode['begin_s']), float(episode['end_s'])
        rows = [row for row in data if row['detector_id'] == field and begin <= float(row['time_s']) < end]
        times = [float(row['time_s']) for row in rows]
        assert len(times) == int(end - begin) and all(row['declared_static_green'] == 'True' for row in rows)
        ax.axvspan(begin, end, color='#e1f3df')
        ax.plot(times, [float(row['reported_upstream_queue_m']) for row in rows], color='#1565c0', label='Upstream queue')
        ax.set_ylabel('Upstream queue (m)', color='#1565c0')
        receiving_axis = ax.twinx()
        for receiver in receivers:
            if receiver['detector_id'] != field:
                continue
            lane = receiver['receiving_lane']
            receiving_axis.plot(times, [float(row[lane + '_queue_m']) for row in rows], color='#c62828')
            receiving_axis.axhline(float(receiver['length_m']), color='#c62828', linestyle='--')
        receiving_axis.set_ylabel('Receiving queue / lane length (m)', color='#c62828')
        ax.set_title(f"{field} | t={begin:g}–{end:g} s | native E1 contributions: 0")
        ax.set_xlabel('Simulation time (s); one-second samples')
        ax.grid(alpha=.2)
    figure.suptitle('Spillback examples before the first collision\nGreen background: declared upstream green; red dash: receiving-lane length')
    figure.savefig(out / 'spillback-examples.png', dpi=140)
    plt.close(figure)
    write_json_atomic(out / 'plot-selection.json', {'examples': selected, 'selection': 'Exploratory longest sampled zero-E1 episode before the first collision for each field with such evidence.'})
    return str(out / 'spillback-examples.png')
