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
        for scene in (Scene.ENTRANCE, Scene.WORLD, Scene.TEAM, Scene.UNKNOWN):
            with self.subTest(scene=scene):
                task = self.task([scene])
                self.assertFalse(task.fire(task.I_NORMAL_BATTLE_BUTTON))
                task.appear_then_click.assert_not_called()
                task.run_general_battle.assert_not_called()
                self.assertEqual(task.recover_exploration_page.called, scene == Scene.UNKNOWN)

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
        task.goto_page = Mock()
        task.recover_exploration_page()
        self.assertEqual(task.goto_page.call_args.kwargs,
                         {'skip_first_screenshot': False, 'timeout': 15})

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
