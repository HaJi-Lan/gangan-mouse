"""A disappearing icon is not evidence that battle started."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import sys
import unittest
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.Exploration.base import BaseExploration, Scene
from tasks.Component.GeneralBattle.general_battle import GeneralBattle


class ExplorationBattleTests(unittest.TestCase):
    def task(self, scenes):
        task = object.__new__(BaseExploration)
        task.screenshot = Mock()
        task.get_current_scene = Mock(side_effect=scenes)
        task.appear = Mock(return_value=True)
        task.appear_then_click = Mock(return_value=True)
        task.run_general_battle = Mock(return_value=True)
        task.recover_exploration_page = Mock()
        task.minions_cnt = 0
        task.__dict__['_config'] = NS(general_battle_config=object())
        return task

    def test_disappeared_icon_cannot_start_battle_wait(self):
        task = self.task([Scene.MAIN])
        task.appear.return_value = False
        self.assertFalse(task.fire(task.I_NORMAL_BATTLE_BUTTON))
        task.run_general_battle.assert_not_called()
        task.appear_then_click.assert_not_called()
        self.assertEqual(task.minions_cnt, 0)

    def test_changed_page_never_reuses_old_click_coordinates(self):
        for scene in (Scene.ENTRANCE, Scene.WORLD, Scene.TEAM):
            with self.subTest(scene=scene):
                task = self.task([scene])
                self.assertFalse(task.fire(task.I_NORMAL_BATTLE_BUTTON))
                task.appear_then_click.assert_not_called()
                task.run_general_battle.assert_not_called()
                task.recover_exploration_page.assert_not_called()

    def test_loading_frames_after_click_never_trigger_return_navigation(self):
        task = self.task([Scene.MAIN, Scene.UNKNOWN, Scene.UNKNOWN, Scene.BATTLE_FIGHTING])
        self.assertTrue(task.fire(task.I_NORMAL_BATTLE_BUTTON))
        task.recover_exploration_page.assert_not_called()
        task.appear_then_click.assert_called_once()
        task.run_general_battle.assert_called_once()

    def test_world_wait_yields_to_chapter_battle_or_reward(self):
        for scene in (Scene.MAIN, Scene.ENTRANCE, Scene.BATTLE_PREPARE, Scene.BATTLE_FIGHTING, Scene.REWARD):
            with self.subTest(scene=scene):
                task = self.task([scene, RuntimeError('wait failed to yield')])
                self.assertFalse(task.wait_world_stable())
                task.appear_then_click.assert_not_called()

    def test_world_wait_is_bounded_when_no_arrow_is_available(self):
        task = self.task([Scene.UNKNOWN])
        with patch('tasks.Exploration.base.Timer') as timer:
            timer.return_value.start.return_value.reached.side_effect = [False, True]
            self.assertFalse(task.wait_world_stable())
        task.appear_then_click.assert_not_called()

    def test_world_wait_requires_two_stable_world_frames(self):
        task = self.task([Scene.WORLD, Scene.WORLD])
        self.assertTrue(task.wait_world_stable())
        self.assertEqual(task.get_current_scene.call_count, 2)

    def test_single_world_frame_after_click_cannot_cancel_battle_entry(self):
        task = self.task([Scene.MAIN, Scene.WORLD, Scene.UNKNOWN, Scene.BATTLE_FIGHTING])
        task.appear.side_effect = lambda target, **kw: target.name == task.I_NORMAL_BATTLE_BUTTON.name
        self.assertTrue(task.fire(task.I_NORMAL_BATTLE_BUTTON))
        task.recover_exploration_page.assert_not_called()

    def test_actual_stuck_reward_frame_is_detected_and_collectable(self):
        image = cv2.imread(str(ROOT / 'tests/fixtures/exploration/reward-stuck.png'))
        for frame in (image, cv2.cvtColor(image, cv2.COLOR_BGR2RGB)):
            task = object.__new__(BaseExploration)
            task.device = NS(image=frame)
            task.appear = lambda rule, **kw: rule.match(frame)
            task.is_in_room = Mock(return_value=False)
            task.click = Mock()
            self.assertEqual(task.get_current_scene(), Scene.REWARD)
            task.collect_exploration_reward()
            task.click.assert_called_once()
            target = task.click.call_args.args[0]
            self.assertIn(target.name, (task.I_REWARD.name, task.I_REWARD_GOLD.name))

    def test_reward_at_battle_entry_is_collected_without_navigation(self):
        task = self.task([Scene.REWARD])
        task.collect_exploration_reward = Mock()
        self.assertFalse(task.fire(task.I_NORMAL_BATTLE_BUTTON))
        task.collect_exploration_reward.assert_called_once()
        task.recover_exploration_page.assert_not_called()

    def test_verified_battle_starts_only_after_fresh_local_icon_click(self):
        task = self.task([Scene.MAIN, Scene.BATTLE_PREPARE])
        original = list(task.I_NORMAL_BATTLE_BUTTON.roi_back)
        self.assertTrue(task.fire(task.I_NORMAL_BATTLE_BUTTON))
        local = task.appear_then_click.call_args.args[0]
        self.assertLess(local.roi_back[2], 150)
        self.assertEqual(list(task.I_NORMAL_BATTLE_BUTTON.roi_back), original)
        task.run_general_battle.assert_called_once()
        self.assertEqual(task.minions_cnt, 1)

    def test_preparation_timeout_never_runs_random_battle_actions(self):
        task = object.__new__(GeneralBattle)
        task.current_count = 0
        task.battle_before = Mock(return_value=False)
        task.battle_wait = Mock()
        task.is_in_battle = Mock()
        self.assertFalse(task.run_general_battle(NS()))
        task.battle_wait.assert_not_called()
        task.is_in_battle.assert_not_called()

    def test_unknown_page_recovery_uses_navigation(self):
        task = object.__new__(BaseExploration)
        task.is_in_prepare = Mock(return_value=False)
        task.is_in_battle = Mock(return_value=False)
        task.get_current_page = Mock(return_value=NS(key='page_mall'))
        task.goto_page = Mock()
        task.recover_exploration_page()
        self.assertEqual(task.goto_page.call_args.kwargs,
                         {'skip_first_screenshot': False, 'timeout': 15})

    def test_loading_or_battle_pages_cannot_trigger_return_navigation(self):
        for key in (None, 'page_battle', 'page_battle_prepare', 'page_battle_result', 'page_reward'):
            task = object.__new__(BaseExploration)
            task.is_in_prepare = Mock(return_value=False)
            task.is_in_battle = Mock(return_value=False)
            task.get_current_page = Mock(return_value=NS(key=key) if key else None)
            task.goto_page = Mock()
            self.assertFalse(task.recover_exploration_page())
            task.goto_page.assert_not_called()

    def test_fast_battle_settlement_can_bypass_loading_frame(self):
        task = object.__new__(GeneralBattle)
        task.screenshot = Mock()
        task.is_in_real_battle = Mock(return_value=False)
        task.appear = Mock(side_effect=lambda rule: rule is task.I_REWARD)
        self.assertTrue(task.battle_before(None, NS()))

    def test_unknown_solo_scene_recovers_after_five_seconds(self):
        from tasks.Exploration.solo import SoloExploration
        task = object.__new__(SoloExploration)
        task.screenshot = Mock(side_effect=[None, RuntimeError('test stopped')])
        task.get_current_scene = Mock(return_value=Scene.UNKNOWN)
        task.recover_exploration_page = Mock()
        with patch('tasks.Exploration.solo.Timer') as timer:
            timer.return_value.start.return_value.reached.return_value = True
            with self.assertRaisesRegex(RuntimeError, 'test stopped'):
                task.run_solo()
        task.recover_exploration_page.assert_called_once()
        self.assertFalse(task.explore_init)


if __name__ == '__main__':
    unittest.main()
