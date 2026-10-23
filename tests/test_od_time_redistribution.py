import numpy as np
import pytest

from astra_sumo.core.od_time_redistribution import propose_measured_unit_step, propose_od_time_redistribution


def test_one_od_moves_integer_departure_counts_without_adding_vehicles():
    result = propose_od_time_redistribution(response=np.eye(2), counts=[10, 10], measured=[10, 10],
        targets=[6, 14], od_ids=['a', 'a'], max_per_cell=4, max_moved=4)
    assert result['status'] == 'candidate'
    assert result['counts'] == [6, 14]
    assert result['moved_vehicles'] == 4 and result['predicted_total_absolute_error'] == 0
    assert not result['formal_result']


def test_no_move_across_od_groups_and_no_unobserved_counts():
    result = propose_od_time_redistribution(response=np.eye(2), counts=[10, 10], measured=[10, 10],
        targets=[6, 14], od_ids=['a', 'b'], max_per_cell=4, max_moved=4)
    assert result['status'] == 'no_effect'
    with pytest.raises(ValueError, match='sampled'):
        propose_od_time_redistribution(response=np.eye(2), counts=[0, 10], measured=[0, 10],
            targets=[6, 14], od_ids=['a', 'a'], max_per_cell=4, max_moved=4)
    with pytest.raises(ValueError, match='reconcile'):
        propose_od_time_redistribution(response=np.eye(2), counts=[10, 10], measured=[9, 10],
            targets=[6, 14], od_ids=['a', 'a'], max_per_cell=4, max_moved=4)


def test_move_budget_is_a_hard_limit_and_all_observations_remain_in_the_loss():
    result = propose_od_time_redistribution(response=np.eye(2), counts=[10, 10], measured=[10, 10],
        targets=[0, 20], od_ids=['a', 'a'], max_per_cell=8, max_moved=3)
    assert result['moved_vehicles'] == 3
    assert sum(result['counts']) == 20 and min(result['counts']) >= 0
    assert result['predicted_total_absolute_error'] == 14


def test_incomplete_solver_does_not_claim_no_effect(monkeypatch):
    from types import SimpleNamespace
    from astra_sumo.core import od_time_redistribution

    monkeypatch.setattr(od_time_redistribution, 'milp', lambda *args, **kwargs: SimpleNamespace(success=False))
    result = propose_od_time_redistribution(response=np.eye(2), counts=[10, 10], measured=[10, 10],
        targets=[6, 14], od_ids=['a', 'a'], max_per_cell=4, max_moved=4)
    assert result['status'] == 'review_required' and result['incomplete_od_searches'] == ['a']


def test_measured_unit_step_uses_all_fields_to_reverse_a_harmful_action():
    result = propose_measured_unit_step(prior=[10, 10], trial=[13, 7], od_ids=['a', 'a'],
        before=[10, 10, 10], after=[13, 7, 7], targets=[10, 20, 20])
    assert result['status'] == 'candidate' and result['counts'] == [9, 11]
    assert result['fraction_of_measured_change'] == -1 / 3
    assert result['predicted_total_absolute_error'] == 19
    assert result['moved_vehicles'] == 1 and not result['formal_result']


def test_measured_unit_step_cannot_cross_od_groups_or_create_negative_requests():
    with pytest.raises(ValueError, match='one OD'):
        propose_measured_unit_step(prior=[10, 10], trial=[13, 7], od_ids=['a', 'b'],
            before=[10, 10, 10], after=[13, 7, 7], targets=[10, 20, 20])
    result = propose_measured_unit_step(prior=[0, 10], trial=[3, 7], od_ids=['a', 'a'],
        before=[10, 10, 10], after=[13, 7, 7], targets=[10, 20, 20])
    assert result['status'] == 'no_effect' and result['counts'] == [0, 10]
