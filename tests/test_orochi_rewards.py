from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import sys
import unittest
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.Orochi.script_task import ScriptTask
from tasks.Component.GeneralBattle.general_battle import GeneralBattle


class OrochiRewardTests(unittest.TestCase):
    def test_actual_reward_matches_both_channel_orders(self):
        image = cv2.imread(str(ROOT / 'tests/fixtures/orochi/pending-reward.png'))
        task = object.__new__(ScriptTask)
        task.interval_timer = {}
        for frame in (image, cv2.cvtColor(image, cv2.COLOR_BGR2RGB)):
            with self.subTest(channel='bgr' if frame is image else 'rgb'):
                task.device = NS(image=frame)
                self.assertTrue(task.appear(task.I_REWARD))
                self.assertTrue(task.appear(task.I_REWARD_GOLD))
                self.assertFalse(task.appear(task.I_BATTLE_INFO))

    def test_orochi_waits_thirty_seconds_for_loading(self):
        task = object.__new__(ScriptTask)
        with patch.object(GeneralBattle, 'battle_before', return_value=True) as before:
            self.assertTrue(task.battle_before(None, NS()))
        self.assertEqual(before.call_args.kwargs['timeout'], 30)

    def test_pending_reward_is_resumed_before_new_challenge(self):
        task = object.__new__(ScriptTask)
        task.config = NS(orochi=NS(general_battle_config=object()))
        task.is_in_battle = Mock(return_value=False)
        task.appear = Mock(side_effect=lambda rule: rule is task.I_REWARD_GOLD)
        task.run_general_battle = Mock()
        self.assertTrue(task.resume_pending_battle())
        task.run_general_battle.assert_called_once()

    def test_gray_reward_path_clicks_and_finishes_next_iteration(self):
        task = object.__new__(ScriptTask)
        task.device = Mock()
        task.screenshot = Mock()
        task.appear_then_click = Mock(return_value=False)
        readings = iter([True, True, False])
        task.appear = Mock(side_effect=lambda rule: next(readings) if rule is task.I_REWARD else False)
        task.click = Mock(return_value=True)
        self.assertTrue(task.battle_wait(False))
        task.click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
