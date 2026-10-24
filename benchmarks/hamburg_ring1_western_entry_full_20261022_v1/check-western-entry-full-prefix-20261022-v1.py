"""Compare closed E1 prefix records during a live full run without advancing it."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_collision_trace import read_trace_source

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / 'benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1'
N = ROOT / 'outputs/ring1-western-entry-full-native-20261022-v1'
OUT = ROOT / 'outputs/ring1-western-entry-full-live-notes-20261022-v1/prefix-count-correspondence.json'
assert not OUT.exists()
expected = {tuple(r.get(k) for k in ('id', 'begin', 'end')): r.attrib
    for r in ET.fromstring(read_trace_source(PREFIX / 'native', 'field-counts.xml'))}
observed, parse_error = {}, None
try:
    for _, row in ET.iterparse(N / 'field-counts.xml', events=('end',)):
        if row.tag == 'interval' and float(row.get('end')) <= 3600:
            observed[tuple(row.get(k) for k in ('id', 'begin', 'end'))] = dict(row.attrib)
        row.clear()
except ET.ParseError as error:
    parse_error = str(error)
assert len(expected) == 52 and len(observed) == 52
differences = [key for key in expected if expected[key] != observed.get(key)]
report = {'formal_result': False, 'compared_intervals': 52,
    'all_initialization_intervals_match': expected == observed, 'different_intervals': differences,
    'live_file_parse_error_after_closed_prefix': parse_error, 'native_time_advanced_by_check': False,
    'complete_window_result_established': False,
    'claim_boundary': 'Only completed prefix intervals. An open later XML record is not a completed native result. No missing main-window count is assigned zero.'}
write_json_atomic(OUT, report)
print(json.dumps(report), flush=True)
