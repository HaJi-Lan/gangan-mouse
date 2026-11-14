"""Workflow transitions and task adapters, without controlling a game."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from scrolls_cycle import ScrollsCycle, ticket_count


class TaskEnd(Exception):
    pass


@contextmanager
def game_dependencies():
    exception = ModuleType('module.exception')
    exception.TaskEnd = TaskEnd
    page = ModuleType('tasks.GameUi.page')
    page.page_main = object()
    with patch.dict(sys.modules, {'module.exception': exception, 'tasks.GameUi.page': page}):
        yield


class ScrollsCycleTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(scrolls_enable=True, scrolls_threshold=25,
                                        scrolls_transition_delay_enable=False)
        self.config = SimpleNamespace(
            exploration=SimpleNamespace(scrolls=self.settings, scheduler=SimpleNamespace(enable=True)),
            memory_scrolls=SimpleNamespace(scheduler=SimpleNamespace(enable=True)))
        self.sleep = Mock()
        self.log = Mock()
        self.random = Mock(return_value=5)
        self.cycle = ScrollsCycle(lambda: self.config, self.log, self.sleep, self.random)
        self.cycle.owner = 'exploration'
        self.cycle.active = True

    def test_ocr_invalid_frames_are_not_empty_tickets(self):
        for result in [(0, 0, 0), (2, 29, 30), (-1, 31, 30), (True, 29, 30), None, ('25', 5, 30)]:
            with self.subTest(result=result):
                self.assertIsNone(ticket_count(result))
        self.assertEqual(ticket_count((0, 30, 30)), 0)
        self.assertEqual(ticket_count((25, 5, 30)), 25)

    def exploration_task(self, count, entrance=False):
        task = Mock()
        task.appear.side_effect = lambda target: entrance and (target is task.I_E_EXPLORATION_CLICK or target is task.I_EXP_CREATE_TEAM)
        task.O_REALM_RAID_NUMBER.ocr.return_value = (count, 30 - count, 30)
        task.O_REALM_RAID_NUMBER1.ocr.return_value = (count, 30 - count, 30)
        return task

    def test_exploration_threshold_equality_positive_ticket_and_both_ocr_positions(self):
        con = SimpleNamespace(buff_gold_50_click=False, buff_gold_100_click=False,
                              buff_exp_50_click=False, buff_exp_100_click=False)
        with game_dependencies():
            for entrance in (False, True):
                for threshold, count, switch in [(25, 24, False), (25, 25, True), (25, 26, True),
                                                 (0, 0, False), (0, 1, True)]:
                    with self.subTest(entrance=entrance, threshold=threshold, count=count):
                        self.cycle.requested = None
                        self.settings.scrolls_threshold = threshold
                        task = self.exploration_task(count, entrance)
                        if switch:
                            with self.assertRaises(TaskEnd):
                                self.cycle.exploration_check(task, self.settings, con)
                            self.assertEqual(self.cycle.requested, 'RealmRaid')
                            task.goto_page.assert_called_once()
                        else:
                            self.cycle.exploration_check(task, self.settings, con)
                            task.goto_page.assert_not_called()
                            self.assertIsNone(self.cycle.requested)
                        task.set_next_run.assert_not_called()

    def test_disabled_mode_stops_automatic_switching(self):
        self.settings.scrolls_enable = False
        with game_dependencies(), self.assertRaises(TaskEnd):
            self.cycle.exploration_check(Mock(), self.settings, Mock())
        self.assertIsNone(self.cycle.requested)

    def test_raid_confirms_empty_and_does_not_return_on_unreadable_frame(self):
        with game_dependencies():
            for frames, challenge, depleted in [([(1, 29, 30)], True, False),
                                                ([(0, 30, 30), (0, 30, 30)], False, True),
                                                ([(0, 0, 0), (0, 0, 0)], False, False),
                                                ([(0, 30, 30), (2, 28, 30)], True, False)]:
                with self.subTest(frames=frames):
                    self.cycle.depleted = False
                    task = Mock()
                    task.O_NUMBER.ocr.side_effect = frames
                    self.assertEqual(self.cycle.raid_check(task), challenge)
                    self.assertEqual(self.cycle.depleted, depleted)

    def cycle_legs(self, delay=False, owner='exploration'):
        self.settings.scrolls_transition_delay_enable = delay
        phases = []
        def leg(phase):
            phases.append(phase)
            if len(phases) == 1:
                self.cycle.requested = 'RealmRaid'
            elif len(phases) == 2:
                self.cycle.depleted = True
            else:
                self.settings.scrolls_enable = False
            return True
        self.assertTrue(self.cycle.run(owner, leg))
        self.assertEqual(phases, ['Exploration', 'RealmRaid', 'Exploration'])
        self.assertFalse(self.cycle.active)

    def test_combined_task_runs_both_directions_with_delay_off(self):
        self.cycle_legs(owner='memory_scrolls')
        self.sleep.assert_not_called()
        self.random.assert_not_called()

    def test_manual_delay_applies_on_both_transitions_only_and_includes_endpoints(self):
        self.random.side_effect = [5, 25]
        self.cycle_legs(delay=True)
        self.assertEqual(self.random.call_count, 2)
        self.random.assert_called_with(5, 25)
        self.assertEqual(self.sleep.call_count, 30)

    def test_switch_can_be_turned_off_during_wait(self):
        self.settings.scrolls_transition_delay_enable = True
        self.sleep.side_effect = lambda _: setattr(self.settings, 'scrolls_enable', False)
        phases = []
        def leg(phase):
            phases.append(phase)
            self.cycle.requested = 'RealmRaid'
            return True
        self.cycle.run('exploration', leg)
        self.assertEqual(phases, ['Exploration'])
        self.assertEqual(self.sleep.call_count, 1)

    def test_raid_limit_or_refresh_cooldown_does_not_look_like_ticket_depletion(self):
        phases = []
        def leg(phase):
            phases.append(phase)
            if len(phases) == 1:
                self.cycle.requested = 'RealmRaid'
            elif len(phases) == 3:
                self.settings.scrolls_enable = False
            return True
        self.cycle.run('exploration', leg)
        self.assertEqual(phases, ['Exploration', 'RealmRaid', 'RealmRaid'])
        self.assertEqual(self.sleep.call_count, 30)

    def test_failure_and_recovery_yield_to_supervisor_without_next_leg(self):
        self.assertFalse(self.cycle.run('exploration', lambda _: False))
        def recover(_):
            self.cycle.interrupted = True
            self.cycle.requested = 'RealmRaid'
            return True
        self.assertTrue(self.cycle.run('exploration', recover))
        self.sleep.assert_not_called()

    def test_raid_phase_resumes_after_failure_instead_of_exploring_with_tickets(self):
        phases = []
        def first(phase):
            phases.append(phase)
            self.cycle.requested = 'RealmRaid'
            return phase != 'RealmRaid'
        self.assertFalse(self.cycle.run('exploration', first))
        def resumed(phase):
            phases.append(phase)
            self.settings.scrolls_enable = False
            return True
        self.assertTrue(self.cycle.run('exploration', resumed))
        self.assertEqual(phases, ['Exploration', 'RealmRaid', 'RealmRaid'])
        self.assertIsNone(self.cycle.resume_owner)

    def test_adapter_ignores_independent_raid_minimum_and_child_scheduler(self):
        class UpstreamTask:
            def check_ticket(self, base):
                raise AssertionError('Independent raid minimum should not be used')
            def set_next_run(self, task, *args, **kwargs):
                raise AssertionError('Child task must not reschedule the workflow')
        module = SimpleNamespace(ScriptTask=UpstreamTask)
        self.cycle.adapt(module, 'RealmRaid')
        task = module.ScriptTask()
        task.wait_until_appear = Mock()
        task.screenshot = Mock()
        task.I_BACK_RED = object()
        task.device = SimpleNamespace(image=object())
        task.O_NUMBER = Mock()
        task.O_NUMBER.ocr.return_value = (1, 29, 30)
        with game_dependencies():
            self.assertTrue(task.check_ticket(20))
        task.set_next_run('RealmRaid', success=True)
        self.assertFalse(self.cycle.depleted)


if __name__ == '__main__':
    unittest.main()
