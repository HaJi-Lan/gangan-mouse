"""Room identity and teammate checks must honor one/two-person invitations."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from tasks.Component.GeneralInvite.general_invite import GeneralInvite, RoomType
from tasks.Component.GeneralInvite.config_invite import InviteConfig, InviteNumber
from tasks.Orochi.script_task import ScriptTask as Orochi


class InviteStartTests(unittest.TestCase):
    def task(self, left_empty=True, right_empty=True, enabled=True, disabled=False):
        task = object.__new__(Orochi)
        task.screenshot = Mock()
        task.ensure_enter = Mock(return_value=True)
        task.ensure_room_type = Mock(return_value=True)
        task.is_in_room = Mock(return_value=True)
        task.invite_friends = Mock()
        task.click_fire = Mock()
        task.appear_then_click = Mock()
        for rule, score in ((task.I_FIRE, 0.97), (task.I_FIRE_FAIL, 0.99 if disabled else 0.5)):
            guard = patch.object(rule, 'last_score', score, create=True)
            guard.start()
            self.addCleanup(guard.stop)
        visible = {str(task.I_ADD_1): left_empty, str(task.I_ADD_2): right_empty,
                   str(task.I_FIRE): enabled, str(task.I_FIRE_FAIL): disabled}
        task.appear = Mock(side_effect=lambda rule, **kw: visible.get(str(rule), False))
        return task

    def test_orochi_room_type_does_not_depend_on_obscured_plus(self):
        task = self.task(left_empty=False)
        task.check_room_type = Mock(return_value=RoomType.NORMAL_2)
        self.assertEqual(task.room_type, RoomType.NORMAL_3)
        task.check_room_type.assert_not_called()

    def test_one_teammate_in_either_slot_can_start(self):
        for left, right in ((False, True), (True, False), (False, False)):
            with self.subTest(left=left, right=right):
                self.assertTrue(self.task(left, right).normal_three_ready(InviteNumber.ONE))

    def test_two_teammates_require_both_slots_filled(self):
        for left, right in ((True, True), (False, True), (True, False)):
            self.assertFalse(self.task(left, right).normal_three_ready(InviteNumber.TWO))
        self.assertTrue(self.task(False, False).normal_three_ready(InviteNumber.TWO))

    def test_empty_room_and_disabled_button_never_start(self):
        self.assertFalse(self.task().normal_three_ready(InviteNumber.ONE))
        self.assertFalse(self.task(False, True, enabled=False).normal_three_ready(InviteNumber.ONE))
        self.assertFalse(self.task(False, True, disabled=True).normal_three_ready(InviteNumber.ONE))

    def test_actual_joined_room_with_both_button_templates_matching_starts(self):
        task = object.__new__(Orochi)
        path = Path(__file__).parent / 'fixtures/invite/joined_room.png'
        image = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        task.appear = lambda rule, **kw: rule.match(image)
        self.assertTrue(task.appear(task.I_FIRE))
        self.assertTrue(task.appear(task.I_FIRE_FAIL))
        self.assertTrue(task.normal_three_ready(InviteNumber.ONE))
        self.assertFalse(task.normal_three_ready(InviteNumber.TWO))

    def test_dialog_disappearing_during_recent_tab_switch_returns_to_room(self):
        task = self.task()
        task.device = NS(image=object())
        task.invite_room_ready = Mock(side_effect=[False, True])
        visible = {str(task.I_INVITE_ENSURE): True}
        task.appear = Mock(side_effect=lambda rule, **kw: visible.get(str(rule), False))
        for rule, result in zip([task.O_F_LIST_1, task.O_F_LIST_2, task.O_F_LIST_3, task.O_F_LIST_4],
                                ['好友', '最近', '寮友', '跨区']):
            guard = patch.object(rule, 'ocr', return_value=result)
            guard.start()
            self.addCleanup(guard.stop)
        task.detect_select = Mock(return_value=False)
        from tasks.Component.GeneralInvite.config_invite import FindMode
        with patch('tasks.Component.GeneralInvite.general_invite.sleep'):
            self.assertTrue(task.invite_friend('friend', FindMode.AUTO_FIND))
        self.assertEqual(task.invite_room_ready.call_count, 2)

    def test_actual_joined_room_starts_without_another_invitation(self):
        task = self.task()
        image = cv2.cvtColor(cv2.imread(str(Path(__file__).parent / 'fixtures/invite/joined_room.png')),
                             cv2.COLOR_BGR2RGB)
        task.appear = lambda rule, **kw: rule.match(image)
        timer = Mock()
        timer.reached.side_effect = lambda: task.screenshot.call_count > 3
        # No setup invitation is needed when returning from a prior battle.
        with patch('tasks.Component.GeneralInvite.general_invite.Timer', return_value=timer):
            self.assertTrue(task.run_invite(InviteConfig(), is_first=False))
        task.click_fire.assert_called_once()
        task.invite_friends.assert_not_called()

    def run_invite(self, task, readings, first=True, number=InviteNumber.ONE):
        task.normal_three_ready = Mock(side_effect=readings)
        # Bound the loop even if a regression prevents starting the battle.
        timer = Mock()
        timer.reached.side_effect = lambda: task.normal_three_ready.call_count > len(readings) - 1
        with patch('tasks.Component.GeneralInvite.general_invite.Timer', return_value=timer):
            result = task.run_invite(InviteConfig(invite_number=number), is_first=first)
        return result

    def test_first_invite_replaces_cached_wrong_type_and_starts_after_join(self):
        task = self.task()
        task.__dict__['room_type'] = RoomType.NORMAL_2
        self.assertTrue(self.run_invite(task, [False, True, True]))
        self.assertEqual(task.room_type, RoomType.NORMAL_3)
        self.assertEqual(task.normal_three_ready.call_count, 3)
        task.click_fire.assert_called_once()

    def test_one_frame_flicker_does_not_start(self):
        task = self.task()
        self.assertFalse(self.run_invite(task, [True, False, True, False]))
        task.click_fire.assert_not_called()

    def test_followup_battle_also_starts_with_one_teammate(self):
        task = self.task()
        self.assertTrue(self.run_invite(task, [True, True], first=False))
        task.click_fire.assert_called_once()
        task.invite_friends.assert_not_called()

    def test_unknown_task_retains_two_player_detection(self):
        task = object.__new__(GeneralInvite)
        task.screenshot = Mock()
        task.device = NS(image=object())
        task.check_room_type = Mock(return_value=RoomType.NORMAL_2)
        self.assertEqual(task.room_type, RoomType.NORMAL_2)
        task.check_room_type.assert_called_once()

    def test_each_friend_tab_uses_its_own_selected_and_click_rules(self):
        for index in range(4):
            with self.subTest(index=index):
                task = self.task()
                task.invite_room_ready = Mock(return_value=False)
                task.appear = Mock(side_effect=[False, True])
                self.assertTrue(task.select_friend_tab(index))
                task.appear_then_click.assert_called_once_with(
                    getattr(task, f'I_FLAG_{index + 1}_OFF'), interval=1)
                self.assertEqual(task.appear.call_args.args[0], getattr(task, f'I_FLAG_{index + 1}_ON'))

    def test_friend_retry_waits_twice_without_deselecting_found_friend(self):
        for readings, calls in (([True], 1), ([False, True], 2), ([False, False], 2)):
            with self.subTest(readings=readings):
                task = self.task()
                task.detect_select = Mock(side_effect=readings)
                with patch('tasks.Component.GeneralInvite.general_invite.sleep') as sleep:
                    self.assertEqual(task.select_friend_with_retry('friend'), any(readings))
                self.assertEqual(task.detect_select.call_count, calls)
                self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
