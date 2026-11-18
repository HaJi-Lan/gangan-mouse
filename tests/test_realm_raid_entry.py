"""Raid entry must be confirmed before battle actions or defeat/refresh handling."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import sys
import unittest
import cv2
import numpy as np
from collections import deque

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.RealmRaid.script_task import ScriptTask
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from module.device.handle import Handle
from module.exception import RequestHumanTakeover


class RaidEntryTests(unittest.TestCase):
    def task(self, battle_at=None, prepare=False, locked=True):
        task = object.__new__(ScriptTask)
        self.now = 0
        task.screenshot = Mock(side_effect=lambda: setattr(self, 'now', self.now + 1))
        task.is_in_real_battle = Mock(side_effect=lambda _: battle_at is not None and self.now >= battle_at)
        task.appear = Mock(return_value=False)
        task.is_in_prepare = Mock(return_value=prepare)
        task.appear_then_click = Mock(return_value=False)
        task.switch_preset_team = Mock()
        task.check_and_open_buff = Mock()
        task.device = NS(save_screenshot=Mock())
        task.current_count = 1
        task.battle_wait = Mock(return_value=True)
        task.config = NS(realm_raid=NS(general_battle_config=NS(lock_team_enable=locked,
            preset_enable=False, preset_group=1, preset_team=1)))
        return task

    def clock(self):
        return patch('tasks.RealmRaid.script_task.time.monotonic', side_effect=lambda: self.now)

    def test_loading_longer_than_five_seconds_is_not_a_defeat(self):
        task = self.task(battle_at=12)
        with self.clock(), patch('tasks.RealmRaid.script_task.time.sleep'):
            self.assertTrue(task.battle_before(None, task.config.realm_raid.general_battle_config))
        self.assertEqual(self.now, 12)
        task.device.save_screenshot.assert_not_called()

    def test_locked_team_ready_fallback_only_clicks_matched_button_after_grace(self):
        task = self.task(prepare=True)
        clicks = []
        def click(rule, **kwargs):
            if rule is task.I_PREPARE_HIGHLIGHT:
                clicks.append(self.now)
                task.is_in_real_battle.return_value = True
                task.is_in_real_battle.side_effect = None
                return True
            return False
        task.appear_then_click.side_effect = click
        with self.clock(), patch('tasks.RealmRaid.script_task.time.sleep'):
            self.assertTrue(task.battle_before(None, task.config.realm_raid.general_battle_config))
        self.assertEqual(clicks, [5])
        task.switch_preset_team.assert_not_called()
        task.check_and_open_buff.assert_not_called()

    def test_unlocked_team_keeps_first_battle_setup_and_ready_click(self):
        task = self.task(battle_at=4, prepare=True, locked=False)
        with self.clock(), patch('tasks.RealmRaid.script_task.time.sleep'):
            self.assertTrue(task.battle_before(None, task.config.realm_raid.general_battle_config))
        task.switch_preset_team.assert_called_once_with(False, 1, 1)
        task.check_and_open_buff.assert_called_once_with(None)
        self.assertTrue(any(c.args[0] is task.I_PREPARE_HIGHLIGHT for c in task.appear_then_click.call_args_list))

    def test_fast_win_loss_and_rewards_enter_result_handler(self):
        for name in ('I_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD', 'I_DE_WIN'):
            task = self.task()
            task.appear.side_effect = lambda rule: rule is getattr(task, name)
            with self.clock():
                self.assertTrue(task.battle_before(None, task.config.realm_raid.general_battle_config))
            task.appear_then_click.assert_not_called()

    def test_unknown_entry_stops_raid_without_refresh_or_random_actions(self):
        task = self.task()
        task.current_count = 0
        task.config.realm_raid.switch_soul_config = NS(enable=False, enable_switch_by_name=False)
        task.config.realm_raid.raid_config = NS(number_base=0, number_attack=30)
        task.goto_page = Mock()
        task.ensure_lock = Mock()
        task.is_frog = Mock(return_value=False)
        task.check_ticket = Mock(return_value=True)
        task.find_one = Mock(return_value=(5, 9))
        task.check_medal_is_frog = Mock(return_value=False)
        task.fire = Mock()
        task.check_refresh = Mock()
        with self.clock(), patch('tasks.RealmRaid.script_task.time.sleep'):
            with self.assertRaisesRegex(RequestHumanTakeover, '入场超时'):
                task.run_2()
        self.assertEqual(self.now, 33)  # Pending-result check and two raid-list frames.
        task.device.save_screenshot.assert_called_once()
        task.battle_wait.assert_not_called()
        task.check_refresh.assert_not_called()
        self.assertFalse(any(c.args[0] is task.I_PREPARE_HIGHLIGHT for c in task.appear_then_click.call_args_list))

    def test_other_tasks_keep_original_preparation_method(self):
        from tasks.Exploration.base import BaseExploration
        self.assertIs(BaseExploration.battle_before, GeneralBattle.battle_before)

    def test_missing_emulator_handle_has_actionable_error(self):
        handle = object.__new__(Handle)
        handle.is_desktop_window = False
        with self.assertRaisesRegex(RequestHumanTakeover, '窗口截图未绑定模拟器句柄'):
            _ = handle.screenshot_handle_num

    def test_desktop_and_bound_emulator_handle_paths_still_work(self):
        for desktop in (False, True):
            handle = object.__new__(Handle)
            handle.is_desktop_window = desktop
            handle.root_handle_num = 123
            handle.root_node = NS(num=123, children=[])
            handle.root_handle_title = 'test window'
            self.assertEqual(handle.screenshot_handle_num, 123)


class RaidBattleRecognitionTests(unittest.TestCase):
    def task(self, frame):
        task = object.__new__(ScriptTask)
        task.device = NS(image=frame, save_screenshot=Mock())
        task.interval_timer = {}
        task.screenshot = Mock()
        return task

    def battle_frame(self):
        frame = cv2.imread(str(ROOT / 'tests/fixtures/realm-raid/in-battle-skin11.png'))
        self.assertIsNotNone(frame)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def test_real_timeout_frame_is_in_battle_and_advances_to_battle_wait(self):
        task = self.task(self.battle_frame())
        self.assertFalse(GeneralBattle.is_in_real_battle(task, False))
        self.assertTrue(task.is_in_real_battle(False))
        self.assertGreater(task.raid_battle_markers[0].last_score, 0.97)
        task.current_count = 0
        task.battle_wait = Mock(return_value=True)
        task.green_mark = Mock()
        task.appear_then_click = Mock()
        self.assertTrue(task.run_general_battle(NS(green_enable=False, green_mark=None,
                                                  random_click_swipt_enable=False)))
        task.battle_wait.assert_called_once_with(random_click_swipt_enable=False)
        task.appear_then_click.assert_not_called()
        task.device.save_screenshot.assert_not_called()

    def test_fallback_requires_both_markers(self):
        frame = self.battle_frame()
        for x1,x2 in ((0,80),(275,365)):
            modified = frame.copy()
            modified[:80,x1:x2] = 0
            self.assertFalse(self.task(modified).is_in_real_battle(False))

    def test_preparation_and_settlement_cannot_enable_fallback(self):
        task = self.task(self.battle_frame())
        with patch.object(task, 'is_in_prepare', return_value=True):
            self.assertFalse(task.is_in_real_battle(False))
        for key in ('I_WIN','I_FALSE','I_REWARD','I_REWARD_GOLD','I_DE_WIN'):
            actual = task.appear
            with patch.object(task, 'appear', side_effect=lambda rule: True if rule is getattr(task,key) else actual(rule)):
                self.assertFalse(task.is_in_real_battle(False))
        reward = cv2.imread(str(ROOT / 'tests/fixtures/realm-raid/retreat-won.png'))
        self.assertFalse(self.task(cv2.cvtColor(reward,cv2.COLOR_BGR2RGB)).is_in_real_battle(False))
        self.assertFalse(self.task(np.zeros((720,1280,3),np.uint8)).is_in_real_battle(False))

    def test_rules_and_other_task_recognition_remain_unchanged(self):
        from tasks.Component.CostumeBattle.assets import CostumeBattleAssets
        from tasks.Orochi.script_task import ScriptTask as Orochi
        original = CostumeBattleAssets.I_BATTLE_INFO_11
        roi = list(original.roi_back)
        task = self.task(self.battle_frame())
        self.assertTrue(task.is_in_real_battle(False))
        self.assertIsNot(task.raid_battle_markers[0], original)
        self.assertEqual(list(original.roi_back),roi)
        self.assertEqual(original.threshold,0.8)
        self.assertIs(Orochi.is_in_real_battle,GeneralBattle.is_in_real_battle)


class RaidRetreatTests(unittest.TestCase):
    def task(self, scenes):
        task = object.__new__(ScriptTask)
        self.now = 0
        self.scene = None
        sequence = iter(scenes)
        def screenshot():
            self.now += 1
            self.scene = next(sequence, self.scene)
        task.screenshot = Mock(side_effect=screenshot)
        mapping = {'exit': task.I_EXIT, 'confirm': task.I_UI_CONFIRM, 'loss': task.I_FALSE,
                   'win': task.I_WIN, 'reward': task.I_REWARD_GOLD, 'list': task.I_RR_PERSON}
        task.appear = Mock(side_effect=lambda rule: rule is mapping.get(self.scene))
        task.appear_then_click = Mock(side_effect=lambda rule, **kwargs: task.appear(rule))
        task.click = Mock()
        task.device = NS(save_screenshot=Mock(), click_record_clear=Mock())
        task.current_count = 0
        return task

    def run_retreat(self, task):
        with patch('tasks.RealmRaid.script_task.time.monotonic', side_effect=lambda: self.now), \
                patch('tasks.RealmRaid.script_task.time.sleep'):
            return task.run_general_battle_back(NS(lock_team_enable=True), exit_four=True)

    def test_normal_retreat_waits_for_failure_and_return_to_list(self):
        task = self.task(['loading', 'exit', 'confirm', 'loss', 'loading', 'list'])
        self.assertTrue(self.run_retreat(task))
        for rule in (task.I_EXIT, task.I_UI_CONFIRM, task.I_FALSE):
            self.assertTrue(any(c.args[0] is rule for c in task.appear_then_click.call_args_list))
        task.click.assert_not_called()
        task.device.save_screenshot.assert_not_called()

    def test_fast_win_collects_reward_instead_of_waiting_for_exit(self):
        task = self.task(['loading', 'win', 'reward', 'list'])
        self.assertFalse(self.run_retreat(task))
        self.assertEqual([c.args[0] for c in task.click.call_args_list], [task.C_WIN_1, task.C_REWARD_2])
        self.assertFalse(any(c.args[0] is task.I_UI_CONFIRM for c in task.appear_then_click.call_args_list))

    def test_observed_battle_skin_can_exit_and_confirm_retreat(self):
        frame=cv2.imread(str(ROOT / 'tests/fixtures/realm-raid/in-battle-skin11.png'))
        frame=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        task=self.task(['actual_battle','confirm','loss','list'])
        actual=task.appear.side_effect
        task.appear.side_effect=lambda rule: rule.match(frame) if self.scene=='actual_battle' else actual(rule)
        self.assertTrue(self.run_retreat(task))
        back=task.raid_battle_markers[1]
        self.assertTrue(any(c.args[0] is back for c in task.appear_then_click.call_args_list))
        self.assertLess(back.roi_front[0],50)

    def test_actual_stuck_reward_frame_is_collected_and_returns_to_list(self):
        frame = cv2.imread(str(ROOT / 'tests/fixtures/realm-raid/retreat-won.png'))
        self.assertIsNotNone(frame)
        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # Device screenshot contract.
        task = self.task(['actual_reward', 'list'])
        original = task.appear.side_effect
        task.appear.side_effect = lambda rule: rule.match(image) if self.scene == 'actual_reward' else original(rule)
        self.assertFalse(self.run_retreat(task))
        task.click.assert_called_once_with(task.C_REWARD_2, interval=2)
        self.assertEqual(task.current_count, 0)

    def test_stale_list_or_unrelated_confirm_never_counts_as_retreat(self):
        for scenes in (['list'], ['confirm'], ['loading'], ['exit', 'confirm']):
            task = self.task(scenes)
            with self.assertRaisesRegex(RequestHumanTakeover, '退四超时'):
                self.run_retreat(task)
            self.assertEqual(self.now, 45)
            task.device.save_screenshot.assert_called_once()
            if scenes == ['confirm']:
                self.assertFalse(any(c.args[0] is task.I_UI_CONFIRM for c in task.appear_then_click.call_args_list))

    def test_four_confirmed_retreats_and_no_retry_after_unexpected_win(self):
        config = NS(raid_config=NS(number_base=0), general_battle_config=NS())
        for outcomes, expected_count in (([True] * 4, 4), ([True, False], 2)):
            task = self.task([])
            task.fire = Mock()
            task.check_ticket = Mock(return_value=True)
            task.run_general_battle_back = Mock(side_effect=outcomes)
            self.assertEqual(task.retreat_four(1, config), expected_count == 4)
            self.assertEqual(task.fire.call_count, expected_count)
            task.fire.assert_called_with(1)

    def test_ticket_depletion_stops_retreats_before_challenging(self):
        task = self.task([])
        task.fire = Mock()
        task.check_ticket = Mock(return_value=False)
        task.run_general_battle_back = Mock()
        self.assertFalse(task.retreat_four(1, NS(raid_config=NS(number_base=0))))
        task.fire.assert_not_called()
        task.run_general_battle_back.assert_not_called()

    def test_confirmed_exit_never_reopens_dialog_and_confirmation_is_bounded(self):
        task=self.task(['exit'] + ['confirm']*7 + ['exit']*4 + ['loss','list'])
        self.assertTrue(self.run_retreat(task))
        calls=[c.args[0] for c in task.appear_then_click.call_args_list]
        self.assertEqual(calls.count(task.I_EXIT),1)
        self.assertEqual(calls.count(task.I_UI_CONFIRM),2)
        task.device.click_record_clear.assert_called_once()

    def test_four_retreats_preserve_click_guard_and_clear_history_only_on_progress(self):
        from module.device.device import Device
        scenes=(['exit']+['confirm']*7+['loss','list'])*4
        task=self.task(scenes)
        record=deque(maxlen=15)
        task.device.click_record=record
        task.device.click_record_clear=Mock(side_effect=record.clear)
        def click(rule, **kwargs):
            if task.appear(rule):
                Device.click_record_add(task.device,rule.name)
                Device.click_record_check(task.device)
                return True
            return False
        task.appear_then_click.side_effect=click
        task.fire=Mock()
        task.check_ticket=Mock(return_value=True)
        config=NS(raid_config=NS(number_base=0),general_battle_config=NS(lock_team_enable=True))
        with patch('tasks.RealmRaid.script_task.time.monotonic',side_effect=lambda:self.now), \
                patch('tasks.RealmRaid.script_task.time.sleep'):
            self.assertTrue(task.retreat_four(1,config))
        self.assertEqual(task.device.click_record_clear.call_count,4)
        self.assertEqual(task.fire.call_count,4)

    def test_failed_result_is_cleared_before_navigation_using_actual_frame(self):
        image=cv2.cvtColor(cv2.imread(str(ROOT/'tests/fixtures/realm-raid/failed-result.png')),cv2.COLOR_BGR2RGB)
        task=self.task(['actual_failure','list'])
        original=task.appear.side_effect
        task.appear.side_effect=lambda rule:rule.match(image) if self.scene=='actual_failure' else original(rule)
        task.config=NS(realm_raid=NS(switch_soul_config=NS(enable=False,enable_switch_by_name=False)))
        class NavigationReached(Exception): pass
        def navigate(*args):
            self.assertEqual(self.scene,'list')
            task.device.click_record_clear.assert_called_once()
            raise NavigationReached
        task.goto_page=Mock(side_effect=navigate)
        with patch('tasks.RealmRaid.script_task.time.monotonic',side_effect=lambda:self.now), \
                patch('tasks.RealmRaid.script_task.time.sleep'):
            with self.assertRaises(NavigationReached):task.run_2()
        self.assertTrue(any(c.args[0] is task.I_FALSE for c in task.appear_then_click.call_args_list))

    def test_actual_confirm_dialog_is_not_reopened(self):
        image=cv2.cvtColor(cv2.imread(str(ROOT/'tests/fixtures/realm-raid/retreat-confirm.png')),cv2.COLOR_BGR2RGB)
        task=self.task(['exit','actual_confirm','actual_confirm','loss','list'])
        original=task.appear.side_effect
        task.appear.side_effect=lambda rule:rule.match(image) if self.scene=='actual_confirm' else original(rule)
        self.assertTrue(self.run_retreat(task))
        exit_clicks=[c for c in task.appear_then_click.call_args_list if c.args[0] is task.I_EXIT]
        self.assertEqual(len(exit_clicks),1)
        task.device.click_record_clear.assert_called_once()

    def test_stuck_result_keeps_click_guard_history_and_stops(self):
        task=self.task(['loss'])
        with self.assertRaisesRegex(RequestHumanTakeover,'结算页未能关闭'):
            self.run_retreat(task)
        clicks=[c for c in task.appear_then_click.call_args_list if c.args[0] is task.I_FALSE]
        self.assertEqual(len(clicks),3)
        task.device.click_record_clear.assert_not_called()

    def test_other_tasks_and_non_exit_four_calls_keep_original_logic(self):
        from tasks.Orochi.script_task import ScriptTask as Orochi
        self.assertIs(Orochi.run_general_battle_back, GeneralBattle.run_general_battle_back)
        task = self.task([])
        config = NS()
        with patch.object(GeneralBattle, 'run_general_battle_back', return_value=True) as original:
            self.assertTrue(task.run_general_battle_back(config))
        original.assert_called_once_with(config, exit_four=False)


if __name__ == '__main__':
    unittest.main()
