import xml.etree.ElementTree as ET

import numpy as np

from astra_sumo.core.hamburg_spillback import queue_episodes, read_queue_series, spillback_mask, static_green


def test_spillback_requires_green_upstream_queue_and_every_planned_receiver():
    up = np.array([7., 7., 7., 7., np.nan, 0.])
    a = np.array([11., 11., 11., 11., 11., 11.])
    b = np.array([6., 3., np.nan, 6., 6., 6.])
    green = np.array([True, True, True, False, True, True])
    assert spillback_mask(up, [a, b], [10., 5.], green).tolist() == [True, False, False, False, False, False]


def test_queue_reader_keeps_absence_unknown_and_episode_gaps(tmp_path):
    p = tmp_path / 'queues.xml'
    p.write_text('<queue-export><data timestep="0"><lanes><lane id="a" queueing_length="8"/></lanes></data>'
                 '<data timestep="1"><lanes/></data><data timestep="3"><lanes><lane id="a" queueing_length="10"/></lanes></data></queue-export>')
    times, queues = read_queue_series(p, {'a'})
    assert times.tolist() == [0., 1., 3.] and np.isnan(queues['a'][1])
    assert queue_episodes(times, np.array([True, True, True])) == [
        {'begin_s': 0., 'end_s': 2., 'duration_s': 2.}, {'begin_s': 3., 'end_s': 4., 'duration_s': 1.}]


def test_static_green_uses_phase_boundaries_and_does_not_call_dark_green():
    program = ET.fromstring('<tlLogic type="static" offset="1"><phase duration="2" state="rO"/>'
                            '<phase duration="3" state="GO"/></tlLogic>')
    assert static_green(program, [0], np.array([0., 1., 3.9, 4.])).tolist() == [False, True, True, False]
    assert not static_green(program, [1], np.arange(5.)).any()
