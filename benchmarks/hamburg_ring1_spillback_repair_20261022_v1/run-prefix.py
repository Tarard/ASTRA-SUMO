"""Reproduce one declared spillback candidate through the diagnostic prefix."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch
import sumo
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.hamburg_spillback_repair import prepare_hamburg_merge_coordination,prepare_hamburg_pruned_phase_repair

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--variant',required=True,choices=['baseline','empty_stage_removed','merge_coordinated','merge_preclear'])
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve()==(root/'.venv/Scripts/python.exe').resolve()
    assert all(version(n)=='1.27.1' for n in ('eclipse-sumo','traci','sumolib'))
    case=root/'benchmarks/hamburg_ring1_munich_corridor_20261022_v1'
    p=args.output_dir.with_name(args.output_dir.name+'-inputs')
    if args.variant=='baseline':
        p.mkdir(parents=True,exist_ok=False)
        for name in ('network.net.xml','demand.rou.xml','field-loops.add.xml','control.add.xml','lane-intentions.json','initialization-counts.csv','expected-counts.csv','scenario.json'):
            (p/name).write_bytes(read_trace_source(case/'inputs',name))
    elif args.variant=='empty_stage_removed':
        prepare_hamburg_pruned_phase_repair(case_dir=case,output_dir=p,
            source_control=root/'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs/control.add.xml')
    else:
        prepare_hamburg_merge_coordination(case_dir=case,output_dir=p,preclear_receivers=args.variant=='merge_preclear')
    def observed_command(command,**kwargs):
        assert command[1:3]==['-m','astra_sumo.core.simulation_stall']
        return run_command([command[0],str(Path(__file__).with_name('observe-prefix.py')),command[-1]],**kwargs)
    with patch('astra_sumo.core.detector_demand_replay.run_command',observed_command):
        result=run_point_detector_demand(net_file=p/'network.net.xml',route_file=p/'demand.rou.xml',
            e1_additional_file=p/'field-loops.add.xml',expected_counts_csv=p/'initialization-counts.csv',
            output_dir=args.output_dir,end_s=3780,sumo_binary=str(Path(sumo.__file__).parent/'bin/sumo.exe'),
            timeout_seconds=1800,signal_program_file=p/'control.add.xml',lane_intentions_file=p/'lane-intentions.json',
            junction_body_hold=True,junction_body_hold_nodes=('14398547','205069138','18653534'))
    print(json.dumps({k:result.get(k) for k in ('native_status','completion','observer_complete')}))
