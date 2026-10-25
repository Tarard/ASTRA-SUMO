from pathlib import Path
from datetime import date,datetime,timezone
import gzip,json
from astra_sumo.core.hamburg_official import SensorThingsClient,parse_hamburg_count_streams,fetch_hamburg_count_observations
from astra_sumo.core.digital_twin import WindowSelection,write_canonical_counts
from astra_sumo.core.digital_twin_timeline import build_simulation_window,aggregate_simulation_counts,select_comparison_count_rows
from astra_sumo.core.detector_demand import read_csv_rows
from astra_sumo.core.artifact_io import write_json_atomic
root=Path.cwd();source=root/'benchmarks/hamburg_ring1_count_window_20261020_v1';out=root/'outputs/ring1-initialization-source-counts-20261021-v1';out.mkdir()
meta=json.loads((source/'fields.raw.json').read_bytes());values=[v for pages in meta['pages_by_node'].values() for page in pages for v in page['value']]
streams=parse_hamburg_count_streams(values);assert len(streams)==13
started=datetime.now(timezone.utc).isoformat()
observations,raw=fetch_hamburg_count_observations(SensorThingsClient('https://iot.hamburg.de/v1.1/'),streams,local_date=date(2026,10,19))
raw['retrieval_begin_utc']=started;raw['retrieval_end_utc']=datetime.now(timezone.utc).isoformat()
(out/'monday-observations.json.gz').write_bytes(gzip.compress(json.dumps(raw,ensure_ascii=False).encode('utf-8'),mtime=0))
selection=WindowSelection(local_date=date(2026,10,19),timezone_name='Europe/Berlin',begin_utc=datetime(2026,10,19,5,30,tzinfo=timezone.utc),end_utc=datetime(2026,10,19,8,tzinfo=timezone.utc),duration_seconds=9000,source_bin_seconds=300,score=6687,complete=True,completeness_ratio=1,expected_cells=390,present_cells=390,missing_cells=(),scoring_method='unchanged user comparison window')
window=build_simulation_window(selection,warmup_seconds=3600,output_bin_seconds=900)
rows=aggregate_simulation_counts(streams,observations,window,require_complete=True)
write_canonical_counts(out/'all-counts-15min.csv',rows)
warm=[r for r in rows if r.end<=3600];formal=select_comparison_count_rows(rows,window)
write_canonical_counts(out/'initialization-counts-15min.csv',warm)
old=read_csv_rows(root/'benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1/inputs/expected-counts.csv')
lookup={(r['detector_id'],float(r['begin']),float(r['end'])):int(r['expected_total']) for r in old}
changes=[{'detector_id':r.detector_id,'begin':r.begin,'old':lookup[r.detector_id,r.begin,r.end],'new':r.count} for r in formal if lookup[r.detector_id,r.begin,r.end]!=r.count]
result={'status':'complete_source_data','publisher':'Freie und Hansestadt Hamburg','source_service':'https://iot.hamburg.de/v1.1/','initialization_begin_utc':window.simulation_begin_utc.isoformat(),'initialization_end_utc':selection.begin_utc.isoformat(),'initialization_field_bins':len(warm),'initialization_count_sum':sum(r.count for r in warm),'all_extended_field_bins':len(rows),'all_bins_complete':all(r.quality_status=='complete' for r in rows),'unchanged_comparison_begin_utc':selection.begin_utc.isoformat(),'unchanged_comparison_end_utc':selection.end_utc.isoformat(),'comparison_rows_checked':len(formal),'comparison_value_changes':changes,'original_target_files_modified':False,'role':'Source data for initialization only. Not new SUMO results and not additional independent station evidence.'}
write_json_atomic(out/'report.json',result);print(json.dumps(result))
