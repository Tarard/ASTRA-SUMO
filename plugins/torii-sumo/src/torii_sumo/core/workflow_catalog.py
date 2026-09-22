"""Host-LLM scenario choices over existing, explicitly registered functions.

This module does not call a language model or classify prose with keywords.
The host model reads the catalog and supplies a reviewable selection.
"""

import importlib
import inspect
import json
from pathlib import Path
from typing import get_type_hints

from pydantic import TypeAdapter


# The only callable names accepted by this dispatcher are maintained here.
# A scenario is not necessarily a complete workflow: checks and guidance are explicit.
SCENARIOS = {
    'environment_preflight': dict(kind='check', reference_bundle='torii-simulate', description='Check Python, SUMO and the available execution environment.',
        entrypoint='torii_sumo.tools.environment_tools:sumo_preflight', reference='preflight-sumo-environment.md'),
    'hamburg_network': dict(kind='workflow', reference_bundle='torii-build', description='Rebuild Hamburg road topology from user-supplied construction drawings or official MAP data, including continuous corridor lanes declared as road_runs. Check local connections and complete lane paths. Preserve the chosen year and source authority; do not calibrate counts.',
        entrypoint='torii_sumo.core.hamburg_topology_workflow:build_hamburg_topology_workflow', reference='hamburg-five-intersection-aerial-workflow.md'),
    'osm_network': dict(kind='workflow', reference_bundle='torii-build', description='Build and clean an OSM network for a declared area and traffic layers. Use hamburg_network when the task is led by Hamburg construction drawings.',
        entrypoint='torii_sumo.tools.osm_tools:sumo_osm_cleanup_workflow', reference='osm-to-sumo-workflow.md'),
    'intersection_scene': dict(kind='workflow', reference_bundle='torii-build', description='Build a synthetic intersection from a scene description. Do not use it to reconstruct a supplied real-road drawing.',
        entrypoint='torii_sumo.tools.intersection_tools:sumo_intersection_scene_workflow', reference='osm-to-sumo-workflow.md'),
    'intersection_clean': dict(kind='workflow', reference_bundle='torii-build', description='Clean and compile one local OSM intersection patch.',
        entrypoint='torii_sumo.tools.intersection_tools:sumo_intersection_clean', reference='osm-to-sumo-workflow.md'),
    'network_audit': dict(kind='check', reference_bundle='torii-build', description='Inspect an existing network and its lane connections without rebuilding it.',
        entrypoint='torii_sumo.mcp_contract_tools:torii_network_audit', reference='osm-to-sumo-workflow.md'),
    'network_compare': dict(kind='check', reference_bundle='torii-build', description='Compare a source network and a candidate network, preserving their identities.',
        entrypoint='torii_sumo.mcp_contract_tools:torii_network_compare', reference='osm-to-sumo-workflow.md'),
    'signal_classification': dict(kind='check', reference_bundle='torii-build', description='Classify signal devices and groups from an OCIT-C supply file; this does not generate field timing.',
        entrypoint='torii_sumo.tools.signal_tools:sumo_signal_device_profile_classify', reference='composable-signal-device-classification.md'),
    'tls_review': dict(kind='check', reference_bundle='torii-build', description='Review traffic signals using an existing network, OSM, and supplied inventory or field evidence.',
        entrypoint='torii_sumo.tools.osm_tools:sumo_tls_multisource_review', reference='model-osm-detectors.md'),
    'intersection_classification': dict(kind='check', reference_bundle='torii-build', description='Classify physical intersection structure before node joining or signal binding.',
        entrypoint='torii_sumo.tools.intersection_tools:sumo_intersection_archetype_classify', reference='composable-intersection-classification.md'),
    'detector_audit': dict(kind='check', reference_bundle='torii-calibrate', description='Compare expected detector counts and observed detector outputs.',
        entrypoint='torii_sumo.tools.demand_tools:sumo_detector_count_audit', reference='detector-constrained-demand-reconstruction.md'),
    'experiment_pair_audit': dict(kind='check', reference_bundle='torii-report', description='Check whether baseline and variant SUMO configurations form a valid comparison pair.',
        entrypoint='torii_sumo.tools.evidence_tools:sumo_config_pair_preflight', reference='evaluate-and-report-results.md'),
    'run_comparison': dict(kind='check', reference_bundle='torii-report', description='Compare already completed baseline and variant outputs. Do not start new simulations.',
        entrypoint='torii_sumo.tools.evidence_tools:sumo_compare_outputs', reference='evaluate-and-report-results.md',
        required_any=[['baseline_summary', 'baseline_tripinfo'], ['variant_summary', 'variant_tripinfo']]),
    'network_review': dict(kind='check', reference_bundle='torii-build', description='Create review HTML for an existing network and available audit outputs.',
        entrypoint='torii_sumo.tools.osm_tools:sumo_network_review_html', reference='osm-to-sumo-workflow.md',
        required_any=[['net_file', 'raw_net_file', 'connected_core_file', 'tls_review_file', 'topology_audit_report_file', 'routeability_audit_report_file']]),
    'road_design_review': dict(kind='check', reference_bundle='torii-build', description='Review source-bound engineering-plan width observations against the implemented rule fragments. Check edition, year and applicability; do not overwrite the user drawing.',
        entrypoint='torii_sumo.road_network.design_review:build_road_design_review', reference='hamburg-five-intersection-aerial-workflow.md'),
    'rigid_vehicle_sweep': dict(kind='check', reference_bundle='torii-build', description='Check a declared rigid vehicle along a front-bumper path against supplied road and obstacle polygons. Missing boundaries do not prove clearance; articulated vehicles are unsupported.',
        entrypoint='torii_sumo.road_network.rigid_vehicle_sweep:rigid_vehicle_sweep', reference='hamburg-five-intersection-aerial-workflow.md'),
    'detector_calibration': dict(kind='stage', reference_bundle='torii-calibrate', description='Fit route demand from detector constraints using routeSampler. This stage is not a complete simulation replay.',
        entrypoint='torii_sumo.tools.digital_twin_tools:sumo_detector_route_sampler_calibrate', reference='detector-constrained-demand-reconstruction.md'),
    'hamburg_demand': dict(kind='stage', reference_bundle='torii-calibrate', description='Generate and check Hamburg demand on a fixed network from bound counts and route candidates. Do not rebuild roads; SUMO replay is separate.',
        entrypoint='torii_sumo.core.hamburg_aerial_demand:generate_hamburg_aerial_demand', reference='hamburg-count-calibration-workflow.md'),
    'hamburg_replay': dict(kind='workflow', reference_bundle='torii-calibrate', description='Run the named Hamburg corridor replay from a fixed network, detector and signal bindings, and count snapshots.',
        entrypoint='torii_sumo.tools.digital_twin_tools:sumo_hamburg_sandtorkai_named_replay', reference='hamburg-sandtorkai-digital-twin.md'),
    'experiment_planning': dict(kind='guidance', reference_bundle='torii-simulate', description='Clarify an experiment objective, inputs and comparison design before choosing an execution workflow.',
        entrypoint=None, reference='interactive-experiment-intake.md'),
    'project_triage': dict(kind='guidance', reference_bundle='torii-simulate', description='Inspect a project or a bad run, identify the actual deviation, and then select the smallest relevant check.',
        entrypoint=None, reference='route-project-workflow.md'),
    'controller_design': dict(kind='guidance', reference_bundle='torii-simulate', description='Choose or develop a controller family with explicit observations, actions and comparison conditions.',
        entrypoint=None, reference='sumolights-controller-patterns.md'),
    'code_development': dict(kind='guidance', reference_bundle='torii-simulate', description='Implement or repair code using a failing check, the smallest change, and fresh verification.',
        entrypoint=None, reference='develop-and-verify-code.md'),
    'sumo_knowledge': dict(kind='guidance', reference_bundle='torii-simulate', description='Explain SUMO semantics or investigate a source-supported question without claiming a simulation was run.',
        entrypoint=None, reference='learn-sumo-knowledge.md'),
    'release_review': dict(kind='guidance', reference_bundle='torii-report', description='Review release readiness and exposure. Selecting this guide does not authorize publication.',
        entrypoint=None, reference='release-project.md'),
}


def _reference_path(entry):
    return f"skills/{entry['reference_bundle']}/references/{entry['reference']}"


def _resolve(entry):
    module, name = entry['entrypoint'].split(':')
    function = getattr(importlib.import_module(module), name)
    if not callable(function):
        raise ValueError('The registered entry is not callable.')
    return function


def _parameters(function):
    signature = inspect.signature(function)
    hints = get_type_hints(function)
    return signature, hints


def _input_files(signature):
    # These registered entry points use these existing names for input files.
    # Output directories and coordinate-array paths (front_bumper_path) are excluded.
    return [name for name in signature.parameters if not name.startswith('output') and
            (name.endswith(('_file', '_xml', '_csv', '_manifest', '_config', '_snapshot', '_summary', '_tripinfo'))
             or name in {'source_osm_path', 'route_sampler_script'})]


def get_workflow_catalog(scenario_id=None):
    """Expose real signatures to the host model; never execute the functions."""
    if scenario_id is not None and scenario_id not in SCENARIOS:
        return dict(status='blocked', error='Unknown scenario_id.', scenarios=[])
    rows = []
    for identifier, entry in SCENARIOS.items():
        if scenario_id is not None and identifier != scenario_id:
            continue
        row = dict(scenario_id=identifier, **entry, available=True, required_arguments=[], optional_arguments={})
        row['reference'] = _reference_path(entry)
        if entry['entrypoint']:
            try:
                signature, hints = _parameters(_resolve(entry))
                for name, parameter in signature.parameters.items():
                    if parameter.default is inspect.Parameter.empty:
                        row['required_arguments'].append(name)
                    else:
                        value = parameter.default
                        row['optional_arguments'][name] = dict(type=str(hints.get(name, 'unspecified')),
                            default=value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else str(value))
                row['argument_types'] = {name: str(hints.get(name, 'unspecified')) for name in signature.parameters}
                row['input_file_arguments'] = _input_files(signature)
            except (ImportError, AttributeError, TypeError, ValueError, NameError) as error:
                row.update(available=False, unavailable_reason=str(error))
        row['executable'] = bool(entry['entrypoint']) and row['available']
        rows.append(row)
    return dict(schema='torii.workflow-catalog/v1', status='pass', selection_provider='host_llm', scenarios=rows,
        selection_fields=['user_request', 'scenario_id', 'reason', 'arguments'],
        instructions='Choose from user intent and supplied evidence. Preserve target year and primary source. '
                     'A check or stage is not a complete workflow. Reference bundles are optional supporting material; '
                     'consult them when useful. Do not invent missing inputs or claim guidance-only entries were executed.')


def run_selected_workflow(selection, *, execute=False):
    """Validate one model choice and invoke only its registered existing function."""
    report = dict(schema='torii.selected-workflow/v1', status='blocked', executed=False,
                  execution_status='selection_invalid', selection_provider='host_llm',
                  validation_scope='Registered entry, argument signature/types, and supplied file presence. The selected workflow owns content and domain checks.')
    if type(execute) is not bool:
        return {**report, 'error': 'execute must be a boolean.'}
    if not isinstance(selection, dict) or set(selection) - {'user_request', 'scenario_id', 'reason', 'arguments'}:
        return {**report, 'error': 'Use user_request, scenario_id, reason, and arguments.'}
    if any(not isinstance(selection.get(key), str) or not selection[key].strip()
           for key in ('user_request', 'scenario_id', 'reason')):
        return {**report, 'error': 'The original request, scenario_id, and selection reason are required.'}
    identifier = selection['scenario_id']
    if identifier not in SCENARIOS:
        return {**report, 'error': 'Unknown scenario_id. No fallback workflow was executed.'}
    arguments = selection.get('arguments', {})
    if not isinstance(arguments, dict):
        return {**report, 'error': 'arguments must be an object.'}
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as error:
        return {**report, 'error': f'arguments must contain finite JSON values: {error}'}
    entry = SCENARIOS[identifier]
    report.update(selection=selection, scenario_id=identifier, kind=entry['kind'], reference_bundle=entry['reference_bundle'], entrypoint=entry['entrypoint'],
                  reference=_reference_path(entry))
    if entry['kind'] == 'guidance':
        if arguments:
            return {**report, 'error': 'Guidance entries do not accept callable arguments.'}
        return {**report, 'status': 'review_required', 'execution_status': 'guidance_only',
                'next_action': 'Use the associated reference bundle or reference if it is useful for the requested reasoning.'}
    try:
        function = _resolve(entry)
        signature, hints = _parameters(function)
        unknown = sorted(set(arguments) - set(signature.parameters))
        if unknown:
            return {**report, 'error': 'Unknown arguments: ' + ', '.join(unknown)}
        missing = [name for name, parameter in signature.parameters.items()
                   if parameter.default is inspect.Parameter.empty and (name not in arguments or arguments[name] is None or arguments[name] == '')]
        groups = [group for group in entry.get('required_any', []) if not any(arguments.get(key) for key in group)]
        if identifier == 'osm_network':
            required = 'reference_net_file' if arguments.get('profile') == 'reference_matched' else 'traffic_layers'
            if not arguments.get(required):
                groups.append([required])
        if missing or groups:
            return {**report, 'status': 'review_required', 'execution_status': 'needs_input',
                    'missing_arguments': missing, 'missing_input_groups': groups}
        signature.bind(**arguments)
        bound = {name: TypeAdapter(hints[name], config={'arbitrary_types_allowed': True}).validate_json(json.dumps(value), strict=True)
                 if name in hints else value for name, value in arguments.items()}
        missing_files = {name: str(bound[name]) for name in _input_files(signature) if bound.get(name) not in (None, '')
                         and not Path(bound[name]).expanduser().is_file()}
        if missing_files:
            return {**report, 'status': 'review_required', 'execution_status': 'needs_input', 'missing_files': missing_files}
        resolved_paths = {}
        for name, value in bound.items():
            if value not in (None, '') and (name in _input_files(signature) or name.endswith('_dir')):
                path = Path(value).expanduser().resolve()
                bound[name] = path if isinstance(value, Path) else str(path)
                resolved_paths[name] = str(path)
        report['resolved_paths'] = resolved_paths
    except (ImportError, AttributeError, TypeError, ValueError, NameError) as error:
        return {**report, 'error': str(error)}
    if not execute:
        return {**report, 'status': 'pass', 'execution_status': 'ready_not_executed',
                'next_action': 'Execute the selected registered workflow when it is within the user-authorized task.'}
    try:
        result = function(**bound)
        if hasattr(result, 'model_dump'):
            result = result.model_dump(mode='json')
        if not isinstance(result, dict) or ('status' in result and not isinstance(result['status'], str)):
            raise ValueError('The registered function did not return a structured result.')
        return {**report, 'status': result.get('status', 'review_required'), 'executed': True,
                'execution_status': 'executed', 'workflow_result': result}
    except Exception as error:
        return {**report, 'status': 'error', 'executed': True, 'execution_status': 'execution_failed',
                'error': f'{type(error).__name__}: {error}'}
