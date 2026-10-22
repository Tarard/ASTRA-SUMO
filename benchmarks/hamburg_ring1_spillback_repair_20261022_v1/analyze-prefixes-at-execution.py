"""Compare the three completed prefixes without treating them as full calibration."""
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import numpy as np
from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.detector_demand import read_csv_rows,write_csv,audit_expected_to_e1_strict
from astra_sumo.core.hamburg_spillback import read_queue_series,static_green,spillback_mask

root=Path.cwd()
out=root/'outputs/ring1-spillback-repair-prefix-comparison-20261022-v2'
out.mkdir(exist_ok=False)
cases={'baseline':'ring1-spillback-prefix-20261022-v2','empty_stage_removed':'ring1-spillback-phase-repair-prefix-20261022-v1',
       'merge_coordinated':'ring1-native-merge-coordination-prefix-20261022-v1','merge_preclear':'ring1-native-merge-preclear-prefix-20261022-v1'}
movements=json.loads((root/'outputs/ring1-munich-spillback-analysis-20261022-v2/report.json').read_bytes())['fields']
wanted={r['lane'] for r in movements}|{lane for r in movements for lane in r['receivers']}
summary=[]; fields=[]; rows52=[]
base_rows=read_csv_rows(root/'outputs'/cases['baseline']/'field-comparison.csv')
base_counts={ (r['detector_id'],r['begin'],r['end']):int(r['measured_nVehContrib']) for r in base_rows }
for variant,name in cases.items():
    case=root/'outputs'/name
    run=json.loads((case/'run.json').read_bytes())
    assert run['observer_complete'] and run['stall_monitor']['simulation_time_s']==3780
    rows=read_csv_rows(case/'field-comparison.csv')
    assert len(rows)==52 and all(r['measured_nVehContrib'] not in ('','None') for r in rows)
    for row in rows:
        key=row['detector_id'],row['begin'],row['end']
        rows52.append({'variant':variant,**row,'difference_from_baseline':int(row['measured_nVehContrib'])-base_counts[key]})
    error=sum(abs(int(r['measured_nVehContrib'])-int(r['expected_total'])) for r in rows)
    summary.append({'variant':variant,'initialization_E1_contributions':sum(int(r['measured_nVehContrib']) for r in rows),
        'initialization_exact_rows':sum(int(r['measured_nVehContrib'])==int(r['expected_total']) for r in rows),
        'initialization_diagnostic_MAE':error/52,**run['completion']})
    unknown=audit_expected_to_e1_strict(read_csv_rows(root/'outputs/ring1-munich-corridor-calibration-20261022-v1/inputs/expected-counts.csv'),case/'field-counts.xml')
    assert len(unknown)==130 and all(r['measured_nVehContrib'] is None for r in unknown)
    write_csv(out/(variant+'-all-130-main-rows-unknown.csv'),unknown,list(unknown[0]))
    times,queues=read_queue_series(case/'queues.xml',wanted)
    net=ET.parse(case/'network.net.xml').getroot()
    lanes={l.get('id'):l for e in net.findall('edge') for l in e.findall('lane')}
    programs={r.get('id'):r for r in ET.parse(case/'signal-programs.add.xml').getroot()}
    intervals=ET.parse(case/'field-counts.xml').getroot().findall('interval')
    for movement in movements:
        field=movement['detector_id'];green=static_green(programs[movement['tls']],movement['links'],times)
        mask=spillback_mask(queues[movement['lane']],[queues[k] for k in movement['receivers']],
            [float(lanes[k].get('length')) for k in movement['receivers']],green)
        fields.append({'variant':variant,'detector_id':field,
            'initialization_E1_contributions':sum(int(r.get('nVehContrib')) for r in intervals if r.get('id')==field and float(r.get('end'))<=3600),
            'first_180s_main_E1_contributions':sum(int(r.get('nVehContrib')) for r in intervals if r.get('id')==field and float(r.get('begin'))==3600),
            'initialization_spillback_green_samples':int((mask&(times<3600)).sum()),
            'first_180s_main_spillback_green_samples':int((mask&(times>=3600)).sum())})
write_csv(out/'prefix-comparison.csv',summary,list(summary[0]))
write_csv(out/'all-52-field-comparisons.csv',fields,list(fields[0]))
write_csv(out/'all-208-initialization-bin-comparisons.csv',rows52,list(rows52[0]))
write_json_atomic(out/'report.json',{'new_simulations':0,'formal_result':False,'prefix_end_s':3780,
    'variants':summary,'all_130_main_rows_unknown_in_every_prefix':True,
    'decision':'Reject empty-stage-only treatment for worse backlog. Measure the locally coordinated merge through the complete unchanged window.',
    'claim_boundary':'Prefixes establish neither main-window count fit nor full-window safety. All other-field losses are retained.'})
print(json.dumps(summary),flush=True)
