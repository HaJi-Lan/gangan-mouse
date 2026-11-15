"""Real upstream supervisor integration, isolated config, no device access."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScrollsSupervisorTests(unittest.TestCase):
    def test_manual_off_is_not_overwritten_by_supervisor_status_save(self):
        code = r'''
import json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
root = Path.cwd()
sys.path.insert(0, str(root / 'bridge'))
upstream = next(path for path in (root / 'runtime/upstream', root / 'OAS', root)
                if (path / 'tasks/Exploration').is_dir())
os.chdir(upstream)
sys.path.insert(0, os.getcwd())
from scrolls_cycle import install_config_models
install_config_models()
import module.config.config as config_module
import script
from task_policy import install_runtime_policy
data = config_module.ConfigModel().model_dump(mode='json')
data['config_name'] = 'fixture'
data['exploration']['scrolls']['scrolls_enable'] = True
data['memory_scrolls']['scheduler']['enable'] = True
upstream = Path.cwd()
with tempfile.TemporaryDirectory(prefix='oas-scrolls-supervisor-') as tmp:
    os.chdir(tmp)
    Path('config').mkdir()
    path = Path('config/fixture.json')
    path.write_text(json.dumps(data), encoding='utf-8')
    phases = []
    def fake_run(self, phase):
        phases.append(phase)
        assert self.config.exploration.scrolls.scrolls_enable
        changed = json.loads(path.read_text('utf-8'))
        changed['exploration']['scrolls']['scrolls_enable'] = False
        path.write_text(json.dumps(changed), encoding='utf-8')
        return True
    try:
        with patch.object(script.Script, 'run', fake_run):
            DesktopScript = install_runtime_policy()
            runner = object.__new__(DesktopScript)
            runner.config_name = 'fixture'
            assert runner.run('MemoryScrolls')
            assert phases == ['Exploration']
            assert 'config' not in runner.__dict__
            # Exactly what upstream Script.loop() does after run() returns.
            runner.config.model.running_task = ''
            saved = json.loads(path.read_text('utf-8'))
            assert saved['exploration']['scrolls']['scrolls_enable'] is False
        # Moving the raid keeps both its standalone route and collection route.
        path.write_text(json.dumps(data), encoding='utf-8')
        phases.clear()
        def cycle_run(self, phase):
            phases.append(phase)
            cycle = self._desktop_scrolls_cycle
            if phases == ['Exploration']:
                cycle.requested = 'RealmRaid'
            elif phase == 'RealmRaid':
                cycle.depleted = True
            else:
                changed = json.loads(path.read_text('utf-8'))
                changed['exploration']['scrolls']['scrolls_enable'] = False
                path.write_text(json.dumps(changed), encoding='utf-8')
            return True
        with patch.object(script.Script, 'run', cycle_run):
            runner = object.__new__(DesktopScript)
            runner.config_name = 'fixture'
            events = []
            runner.state_queue = SimpleNamespace(put=events.append)
            with patch.object(script.Script, 'run', lambda self, phase: phases.append(phase) or True):
                assert runner.run('RealmRaid')
                assert phases == ['RealmRaid']
                assert events[0]['execution'] == {'task': 'RealmRaid', 'owner': 'RealmRaid'}
                assert events[-1]['execution'] == {'task': '', 'owner': ''}
            phases.clear()
            events.clear()
            assert runner.run('MemoryScrolls')
            assert phases == ['Exploration', 'RealmRaid', 'Exploration']
            actual = [event['execution'] for event in events
                      if event['execution']['task'] in ('Exploration', 'RealmRaid')]
            assert [event['task'] for event in actual] == phases
            assert all(event['owner'] == 'MemoryScrolls' for event in actual)
            assert events[-1]['execution'] == {'task': '', 'owner': ''}
        # The owner must be delayed after a deadline, including the
        # supervisor's final running_task save, so it cannot restart at once.
        from datetime import datetime
        data['exploration']['scrolls']['scrolls_limit_minutes'] = 1
        path.write_text(json.dumps(data), encoding='utf-8')
        phases.clear()
        def expire(self, phase):
            phases.append(phase)
            self._desktop_scrolls_cycle.started_at -= 61
            return True
        with patch.object(script.Script, 'run', expire):
            runner = object.__new__(DesktopScript)
            runner.config_name = 'fixture'
            assert runner.run('MemoryScrolls')
            assert phases == ['Exploration']
            runner.config.model.running_task = ''
            saved = json.loads(path.read_text('utf-8'))
            assert datetime.fromisoformat(saved['memory_scrolls']['scheduler']['next_run']) > datetime.now()
    finally:
        os.chdir(upstream)
print('Supervisor integration OK, no game access')
'''
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        self.assertIn('Supervisor integration OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
