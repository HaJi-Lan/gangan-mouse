"""Exercise the actual upstream threshold method without game/device access."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = next(path for path in (ROOT / 'runtime/upstream', ROOT / 'OAS', ROOT)
                if (path / 'tasks/Exploration').is_dir())


class TaskEnd(Exception):
    pass


class ExplorationScrollsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((UPSTREAM / 'tasks/Exploration/base.py').read_text('utf-8'))
        owner = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'BaseExploration')
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'activate_realm_raid')
        module = ast.Module(body=[method], type_ignores=[])
        namespace = dict(datetime=datetime, TaskEnd=TaskEnd, page_main=object(), logger=Mock())
        exec(compile(module, 'upstream.activate_realm_raid', 'exec'), namespace)
        cls.activate = staticmethod(namespace['activate_realm_raid'])

    def run_threshold(self, count, threshold, should_challenge, entrance=False, enabled=True):
        device = SimpleNamespace(image=object())
        runner = Mock(device=device)
        runner.appear.side_effect = lambda target: entrance and (target is runner.I_E_EXPLORATION_CLICK or target is runner.I_EXP_CREATE_TEAM)
        runner.O_REALM_RAID_NUMBER.ocr.return_value = (count, 30-count, 30)
        runner.O_REALM_RAID_NUMBER1.ocr.return_value = (count, 30-count, 30)
        scrolls = SimpleNamespace(scrolls_enable=enabled, scrolls_threshold=threshold, scrolls_cd=timedelta(minutes=30))
        config = SimpleNamespace(buff_gold_50_click=False, buff_gold_100_click=False, buff_exp_50_click=False, buff_exp_100_click=False)
        if should_challenge:
            with self.assertRaises(TaskEnd):
                self.activate(runner, scrolls, config)
            calls = [call.kwargs['task'] for call in runner.set_next_run.call_args_list]
            self.assertIn('RealmRaid', calls)
            self.assertIn('Exploration', calls)
            runner.goto_page.assert_called_once()
        else:
            self.activate(runner, scrolls, config)
            runner.set_next_run.assert_not_called()
            runner.goto_page.assert_not_called()
        if enabled:
            selected = runner.O_REALM_RAID_NUMBER1 if entrance else runner.O_REALM_RAID_NUMBER
            selected.ocr.assert_called_once_with(device.image)

    def test_threshold_boundaries_in_both_exploration_screens(self):
        for entrance in (False, True):
            for count, challenge in ((24, False), (25, True), (26, True)):
                with self.subTest(entrance=entrance, count=count):
                    self.run_threshold(count, 25, challenge, entrance=entrance)

    def test_disabled_scroll_mode_does_not_trigger_challenge(self):
        self.run_threshold(30, 25, False, enabled=False)


if __name__ == '__main__':
    unittest.main()
