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
