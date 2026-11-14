"""Replay御灵 OCR failures through the real task, without an emulator."""
from datetime import datetime, time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from module.atom.ocr import RuleOcr
from module.exception import TaskEnd, GamePageUnknownError
from tasks.GoryouRealm.script_task import ScriptTask
from tasks.GoryouRealm.config import GoryouClass


class GoryouTicketsTests(unittest.TestCase):
    def task(self, samples, limit=1):
        task = object.__new__(ScriptTask)
        task.config = NS(goryou_realm=NS(
            goryou_config=NS(limit_time=time(1), limit_count=limit,
                            goryou_class=GoryouClass.Dark_Divine_Dragon),
            switch_soul_config=NS(enable=False),
            general_battle_config=NS(lock_team_enable=False)))
        task.start_time = datetime.now()
        task.current_count = 0
        task.device = NS(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        task.O_GR_TICKET = RuleOcr(name='GR_TICKET', mode='Digit', method='Default',
                                   roi=(938, 4, 100, 41), area=(938, 4, 100, 41), keyword='')
        task.O_GR_TICKET.model = Mock()
        task.O_GR_TICKET.model.ocr_single_line.side_effect = samples
        task.goto_page = Mock(return_value=True)
        task.check_lock = Mock()
        task.screenshot = Mock()
        task.ui_click = Mock()
        task.set_next_run = Mock()
        task.click = Mock(return_value=True)
        task.in_battle = False
        task.appear = Mock(side_effect=lambda *a, **kw: not task.in_battle)
        def fire(*a, **kw):
            task.in_battle = True
            return True
        task.appear_then_click = Mock(side_effect=fire)
        def battle(**kw):
            task.current_count += 1
            task.in_battle = False
            return True
        task.run_general_battle = Mock(side_effect=battle)
        self.guard = patch('tasks.GoryouRealm.script_task.sleep', create=True)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        return task

    def run_task(self, task):
        with self.assertRaises(TaskEnd):
            task.run()

    def test_empty_read_then_93_does_not_finish_without_battle(self):
        task = self.task([('', .99), ('93', .99), ('93', .99)])
        self.run_task(task)
        task.run_general_battle.assert_called_once()

    def test_repeated_empty_read_retries_without_daily_success(self):
        task = self.task([('', .99)] * 5)
        self.run_task(task)
        task.run_general_battle.assert_not_called()
        self.assertFalse(task.set_next_run.call_args.kwargs['success'])
        self.assertNotIn('finish', task.set_next_run.call_args.kwargs)

    def test_zero_is_confirmed_on_three_fresh_frames(self):
        task = self.task([('0', .99)] * 3)
        self.run_task(task)
        self.assertEqual(task.O_GR_TICKET.model.ocr_single_line.call_count, 3)
        self.assertTrue(task.set_next_run.call_args.kwargs['finish'])

    def test_low_confidence_and_spaced_digits_are_rejected(self):
        task = self.task([('0', .5), ('89 3', .99), ('00', .99), ('93', .99), ('93', .99)])
        self.run_task(task)
        task.run_general_battle.assert_called_once()

    def test_92_to_913_and_false_zero_cannot_end_the_task(self):
        task = self.task([('92', .99), ('92', .99), ('913', .99), ('0', .99),
                          ('91', .99), ('91', .99)], limit=2)
        self.run_task(task)
        self.assertEqual(task.run_general_battle.call_count, 2)

    def test_unreadable_after_battle_does_not_claim_no_tickets(self):
        task = self.task([('2', .99)] * 2 + [('0', .99)] * 5, limit=3)
        self.run_task(task)
        self.assertFalse(task.set_next_run.call_args.kwargs['success'])
        self.assertEqual(task.current_count, 1)

    def test_last_ticket_reaches_confirmed_zero(self):
        task = self.task([('1', .99)] * 2 + [('0', .99)] * 3, limit=2)
        self.run_task(task)
        self.assertEqual(task.current_count, 1)
        self.assertTrue(task.set_next_run.call_args.kwargs['finish'])

    def test_navigation_failure_is_retried_without_finishing(self):
        task = self.task([])
        task.goto_page.side_effect = GamePageUnknownError('cannot enter goryou')
        self.run_task(task)
        self.assertFalse(task.set_next_run.call_args.kwargs['success'])

    def test_single_false_zero_is_rechecked_before_battle(self):
        task = self.task([('0', .99), ('93', .99), ('93', .99)])
        self.run_task(task)
        task.run_general_battle.assert_called_once()

    def test_missing_challenge_button_has_bounded_retry(self):
        task = self.task([])
        task.appear.return_value = False
        task.appear.side_effect = None
        with patch('tasks.GoryouRealm.script_task.monotonic', side_effect=[0, 31]):
            self.run_task(task)
        self.assertFalse(task.set_next_run.call_args.kwargs['success'])

    def test_challenge_button_that_never_starts_battle_is_retried(self):
        task = self.task([('93', .99)] * 2)
        task.appear_then_click.side_effect = None
        with patch('tasks.GoryouRealm.script_task.monotonic', side_effect=[0, 0, 0, 0, 0, 0, 31]):
            self.run_task(task)
        task.run_general_battle.assert_not_called()
        self.assertFalse(task.set_next_run.call_args.kwargs['success'])


if __name__ == '__main__':
    unittest.main()
