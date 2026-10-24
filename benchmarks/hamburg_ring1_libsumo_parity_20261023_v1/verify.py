"""Check the complete native-library replay against the frozen socket replay."""
import gzip
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def lane_hold_correspondence(before, after):
    def per_vehicle(report):
        report = json.loads(json.dumps(report))
        sections = [report]
        if 'junction_body_hold' in report:
            sections.append(report['junction_body_hold'])
        for section in sections:
            grouped = {}
            for event in section['events']:
                grouped.setdefault(event['vehicle_id'], []).append(event)
            section['events'] = grouped
        return report
    # Different vehicles can be enumerated in a different order in the same step.
    # Keep every vehicle's event order, timestamps, modes and numerical values exact.
    return per_vehicle(before) == per_vehicle(after)


def traffic_digest(path, tag, *, omit=()):
    digest, count = hashlib.sha256(), 0
    with gzip.open(path, 'rb') as stream:
        for _, row in ET.iterparse(stream, events=('end',)):
            if row.tag == tag:
                if tag == 'step':
                    attrs = {k: v for k, v in row.attrib.items() if k not in omit}
                    raw = json.dumps(attrs, sort_keys=True).encode()
                else:
                    def content(node):
                        return (node.tag, sorted(node.attrib.items()), (node.text or '').strip(),
                                [content(child) for child in node])
                    raw = json.dumps(content(row)).encode()
                digest.update(raw)
                count += 1
                row.clear()
    return {'rows': count, 'sha256': digest.hexdigest()}


def verify(case):
    reference = case.parent / 'hamburg_ring1_119_straight_order_20261023_v1/full-native'
    native = case / 'native'
    checks = {}
    details = {}
    for name, tag in (('field-counts.xml.gz', 'interval'), ('tripinfo.xml.gz', 'tripinfo'),
                      ('vehicle-routes.xml.gz', 'vehicle'), ('collisions.xml.gz', 'collision'), ('summary.xml.gz', 'step')):
        # Wall-clock work per step changes with the backend and is not traffic.
        before = traffic_digest(reference / name, tag, omit=('duration',))
        after = traffic_digest(native / name, tag, omit=('duration',))
        checks[name] = before == after
        details[name] = {'reference': before, 'libsumo': after}
    checks['evaluation.json'] = json.loads((case / 'evaluation.json').read_bytes()) == json.loads((reference / 'evaluation.json').read_bytes())
    for name in ('field-comparison.csv', 'station-comparison.csv'):
        with (case / name).open(encoding='utf-8', newline='') as after_file, (reference / name).open(encoding='utf-8', newline='') as before_file:
            checks[name] = list(csv.DictReader(after_file)) == list(csv.DictReader(before_file))
    before = json.loads(gzip.decompress((reference / 'stall-monitor.json.gz').read_bytes()))
    after = json.loads(gzip.decompress((native / 'stall-monitor.json.gz').read_bytes()))
    checks['lane_section_hold_per_vehicle'] = lane_hold_correspondence(before['lane_section_hold'], after['lane_section_hold'])
    checks['library_completion'] = (after['backend'] == 'libsumo' and after['library_completed']
        and after['native_returncode'] is None and '--remote-port' not in after['command']
        and after['native_version'] == before['native_version'] == [22, 'SUMO 1.27.1'])
    return {'correspondence': all(checks.values()), 'checks': checks, 'traffic_digests': details,
        'raw_lane_hold_event_list_equal': before['lane_section_hold'] == after['lane_section_hold']}


if __name__ == '__main__':
    result = verify(Path(__file__).resolve().parent)
    print(json.dumps(result, indent=2))
    assert result['correspondence'], result
