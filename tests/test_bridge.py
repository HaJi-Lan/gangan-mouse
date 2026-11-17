"""Real upstream configuration/protocol tests with an isolated runtime fixture."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import psutil

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = next(path for path in (ROOT / 'runtime/upstream', ROOT / 'OAS', ROOT)
                if (path / 'tasks/Exploration').is_dir())


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='oas-bridge-')
        cls.fixture = Path(cls.temp.name)
        # Copy only source and configuration, never account files or image archives.
        for folder in ['module', 'tasks', 'assets', 'deploy']:
            for path in (UPSTREAM / folder).rglob('*'):
                if path.is_file() and path.suffix in ('.py', '.json', '.yaml', '.txt'):
                    target = cls.fixture / path.relative_to(UPSTREAM)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
        (cls.fixture / 'config').mkdir()
        shutil.copy2(UPSTREAM / 'deploy/template', cls.fixture / 'deploy/template')
        shutil.copy2(UPSTREAM / 'config/template.json', cls.fixture / 'config/template.json')
        shutil.copy2(UPSTREAM / 'config/template.json', cls.fixture / 'config/oas1.json')
        (cls.fixture / 'script.py').write_text('''
import time
from module.logger import logger
def load_module(name, path):
    raise AssertionError('Fixture must not load actual game tasks')
class Script:
    def __init__(self, config_name):
        self.config_name = config_name
    def loop(self):
        from module.config.config import Config
        config = Config(self.config_name)
        from module.config.config_menu import ConfigMenu
        from module.config.utils import convert_to_underscore
        removed = ['DailyTrifles', 'WeeklyTrifles', 'HeroTest', 'FindJade', 'FloatParade']
        guild = [task for task in ConfigMenu().gui_menu_list['Guild'] if task != 'RealmRaid']
        for key in [*(convert_to_underscore(task) for task in removed + guild), 'orochi']:
            object.__setattr__(getattr(config.model, key).scheduler, 'enable', True)
        config.update_scheduler()
        scheduled = {task.command for task in config.pending_task + config.waiting_task}
        assert not set(removed) & scheduled
        assert 'Orochi' in scheduled
        assert set(guild) <= scheduled
        for task in removed:
            assert config.task_call(task, force_call=True) is False
            assert self.run(task) is True
        object.__setattr__(config.model.exploration.scheduler, 'enable', True)
        object.__setattr__(config.model.realm_raid.scheduler, 'enable', True)
        object.__setattr__(config.model.memory_scrolls.scheduler, 'enable', True)
        object.__setattr__(config.model.exploration.scrolls, 'scrolls_enable', True)
        config.update_scheduler()
        scheduled = {task.command for task in config.pending_task + config.waiting_task}
        assert 'MemoryScrolls' in scheduled
        assert 'Exploration' not in scheduled and 'RealmRaid' not in scheduled
        object.__setattr__(config.model.memory_scrolls.scheduler, 'enable', False)
        config.update_scheduler()
        scheduled = {task.command for task in config.pending_task + config.waiting_task}
        assert 'Exploration' in scheduled and 'RealmRaid' in scheduled
        object.__setattr__(config.model.exploration.scrolls, 'scrolls_enable', False)
        object.__setattr__(config.model.memory_scrolls.scheduler, 'enable', True)
        config.update_scheduler()
        scheduled = {task.command for task in config.pending_task + config.waiting_task}
        assert 'MemoryScrolls' not in scheduled and 'RealmRaid' in scheduled
        logger.info('Fixture task started without any device access')
        self.state_queue.put({'state': 1})
        while True:
            time.sleep(0.05)
''', encoding='utf-8')
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        cls.process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'bridge/worker.py'), '--root', str(cls.fixture)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', env=env)
        cls.output = queue.Queue()
        cls.errors = []

        def stdout():
            for line in cls.process.stdout:
                cls.output.put(json.loads(line))

        def stderr():
            cls.errors.extend(cls.process.stderr)

        cls.readers = [threading.Thread(target=stdout, daemon=True), threading.Thread(target=stderr, daemon=True)]
        for reader in cls.readers:
            reader.start()
        first = cls.output.get(timeout=30)
        if first.get('event') != 'ready':
            cls.process.wait(timeout=15)
            raise AssertionError(first)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.process.poll() is None:
                cls.process.stdin.write('{"command":"shutdown"}\n')
                cls.process.stdin.flush()
                cls.process.wait(timeout=15)
        finally:
            if cls.process.poll() is None:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.process.stdin.close()
        for reader in cls.readers:
            reader.join(timeout=5)
        cls.process.stdout.close()
        cls.process.stderr.close()
        cls.temp.cleanup()

    def request(self, command, **fields):
        self.process.stdin.write(json.dumps(dict(command=command, **fields)) + '\n')
        self.process.stdin.flush()
        while True:
            result = self.output.get(timeout=30)
            if result.get('event') == 'response':
                return result

    def test_hello_lists_upstream_tasks_and_combines_translations(self):
        result = self.request('hello')
        self.assertTrue(result['ok'])
        self.assertIn('oas1', result['data']['configs'])
        self.assertIn('MetaDemon', result['data']['menu']['Activity Task'])
        self.assertNotIn('Daily Task', result['data']['menu'])
        self.assertNotIn('Weekly Task', result['data']['menu'])
        self.assertEqual(set(result['data']['menu']['Guild']),
                         {'KekkaiUtilize', 'KekkaiActivation', 'RyouToppa', 'Dokan',
                          'CollectiveMissions', 'Hunt', 'AbyssShadows', 'GuildBanquet',
                          'DemonRetreat', 'GuildActivityMonitor'})
        tasks = [task for category in result['data']['menu'].values() for task in category]
        for task in ('HeroTest', 'FindJade', 'FloatParade'):
            self.assertNotIn(task, tasks)
        self.assertIn('Exploration', tasks)
        self.assertIn('MemoryScrolls', tasks)
        self.assertIn('RealmRaid', result['data']['menu']['Liver Emperor Exclusive'])
        self.assertEqual(tasks.count('RealmRaid'), 1)
        self.assertNotEqual(result['data']['translations'].get('Script'), None)
        self.assertIn('desktop_enable_help', result['data']['translations'])
        self.assertEqual(result['data']['translations']['scrolls_threshold'], '突破卷数量大于等于 X 时才会挑战')
        self.assertIn('数量小于 X 时继续探索', result['data']['translations']['scrolls_threshold_help'])

    def test_all_menu_tasks_have_editable_schema(self):
        menu = self.request('hello')['data']['menu']
        for task in (task for tasks in menu.values() for task in tasks):
            with self.subTest(task=task):
                result = self.request('schema', config='oas1', task=task)
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['data']['groups'])
                for removed in ('switch_soul', 'switch_soul_config', 'cycle_exploration_soul', 'cycle_raid_soul'):
                    self.assertNotIn(removed, result['data']['groups'])
                for group, fields in result['data']['groups'].items():
                    self.assertFalse({'buff_gold_50_click', 'buff_gold_100_click',
                                      'buff_exp_50_click', 'buff_exp_100_click'}
                                     & {field['name'] for field in fields})
                    if group == 'scheduler':
                        self.assertEqual({field['name'] for field in fields},
                                         {'enable', 'next_run', 'priority', 'float_time'})
                    if group in ('general_battle_config', 'cycle_exploration_battle', 'cycle_raid_battle'):
                        self.assertEqual([field['name'] for field in fields],
                                         ['lock_team_enable', 'random_click_swipt_enable'])

    def test_total_scroll_duration_is_shared_and_exploration_limits_hidden(self):
        for task in ('Exploration', 'MemoryScrolls'):
            data = self.request('schema', config='oas1', task=task)['data']['groups']
            self.assertNotIn('limit_time', {f['name'] for f in data['exploration_config']})
            self.assertNotIn('minions_cnt', {f['name'] for f in data['exploration_config']})
            if task == 'MemoryScrolls':
                self.assertIn('scrolls_limit_minutes', {f['name'] for f in data['scrolls']})
            else:
                self.assertNotIn('scrolls', data)
        result = self.request('set', config='oas1', task='MemoryScrolls', group='scrolls',
                              argument='scrolls_limit_minutes', value=120)
        self.assertTrue(result['ok'], result)
        data = self.request('schema', config='oas1', task='MemoryScrolls')['data']['groups']
        self.assertEqual(next(f['value'] for f in data['scrolls'] if f['name']=='scrolls_limit_minutes'), 120)
        self.assertFalse(self.request('set', config='oas1', task='Exploration', group='scrolls',
                                     argument='scrolls_limit_minutes', value=0)['ok'])
        groups = self.request('schema', config='oas1', task='MemoryScrolls')['data']['groups']
        self.assertNotIn('invite_config', groups)
        self.assertIn('invite_config', self.request('schema', config='oas1', task='Exploration')['data']['groups'])

    def test_enabled_task_list_reads_saved_switches_including_stopped_tasks(self):
        before = self.request('schema', config='oas1', task='Orochi')['data']['task_switches']['Orochi']
        try:
            result = self.request('set', config='oas1', task='Orochi', group='scheduler', argument='enable', value=True)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['data']['task_switches']['Orochi'])
            switches = self.request('schema', config='oas1', task='MemoryScrolls')['data']['task_switches']
            self.assertTrue(switches['Orochi'])
            self.assertIn('RealmRaid', switches)
            self.assertNotIn('FloatParade', switches)
            result = self.request('set', config='oas1', task='Orochi', group='scheduler', argument='enable', value=False)
            self.assertFalse(result['data']['task_switches']['Orochi'])
        finally:
            self.request('set', config='oas1', task='Orochi', group='scheduler', argument='enable', value=before)

    def test_fresh_clone_has_chinese_notes_without_generated_upstream_json(self):
        generated = self.fixture / 'module/config/i18n/zh-CN.json'
        content = generated.read_bytes() if generated.exists() else None
        if generated.exists():
            generated.unlink()
        try:
            result = self.request('hello')
            self.assertEqual(result['data']['translations']['Exploration'], '探索')
            self.assertTrue(result['data']['translations']['serial_help'])
        finally:
            if content is not None:
                generated.write_bytes(content)

    def test_removed_tasks_cannot_be_loaded_or_reenabled(self):
        # Read upstream category definitions without importing the live runtime.
        import ast
        tree = ast.parse((UPSTREAM / 'module/config/config_menu.py').read_text('utf-8'))
        removed = ['HeroTest', 'FindJade', 'FloatParade']
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value in ('Daily Task', 'Weekly Task'):
                        removed.extend(ast.literal_eval(node.value))
        removed = [task for task in removed if task != 'RealmRaid']
        self.assertNotIn('Hunt', removed)
        for task in removed:
            with self.subTest(task=task):
                self.assertFalse(self.request('schema', config='oas1', task=task)['ok'])
                self.assertFalse(self.request('set', config='oas1', task=task, group='scheduler', argument='enable', value=True)['ok'])

    def test_guild_task_switches_can_be_saved_and_reloaded(self):
        name = self.request('create')['data']['selected']
        guild = self.request('hello')['data']['menu']['Guild']
        for task in guild:
            with self.subTest(task=task):
                result = self.request('set', config=name, task=task, group='scheduler',
                                      argument='enable', value=True)
                self.assertTrue(result['ok'], result)
                switches = self.request('schema', config=name, task=task)['data']['task_switches']
                self.assertTrue(switches[task])

    def test_unported_upstream_tasks_cannot_be_loaded_or_enabled(self):
        for task in ('FutureUpstreamTask', 'future_upstream_task'):
            self.assertFalse(self.request('schema', config='oas1', task=task)['ok'])
            self.assertFalse(self.request('set', config='oas1', task=task,
                                         group='scheduler', argument='enable', value=True)['ok'])

    def test_create_and_save_are_isolated_and_persisted(self):
        name = self.request('create')['data']['selected']
        result = self.request('set', config=name, task='Script', group='device', argument='serial', value='127.0.0.1:5557')
        self.assertTrue(result['ok'], result)
        saved = json.loads((self.fixture / f'config/{name}.json').read_text('utf-8'))
        self.assertEqual(saved['script']['device']['serial'], '127.0.0.1:5557')

    def test_collection_shares_exploration_and_raid_settings(self):
        name = self.request('create')['data']['selected']
        groups = self.request('schema', config=name, task='MemoryScrolls')['data']['groups']
        self.assertNotIn('memory_scrolls_config', groups)
        self.assertNotIn('memory_scrolls_finish', groups)
        self.assertIn('raid_config', groups)
        self.assertIn('exploration_config', groups)
        self.assertNotIn('scrolls_cd', [f['name'] for f in groups['scrolls']])
        self.assertIn('scrolls_transition_delay_enable', [f['name'] for f in groups['scrolls']])
        for group, argument, value in [('scrolls', 'scrolls_threshold', 15),
                                       ('scrolls', 'scrolls_transition_delay_enable', True),
                                       ('raid_config', 'number_attack', 9)]:
            result = self.request('set', config=name, task='MemoryScrolls', group=group, argument=argument, value=value)
            self.assertTrue(result['ok'], result)
        for task, group, argument, expected in [('MemoryScrolls', 'scrolls', 'scrolls_threshold', 15),
                                                ('RealmRaid', 'raid_config', 'number_attack', 9)]:
            fields = self.request('schema', config=name, task=task)['data']['groups'][group]
            self.assertEqual(next(f['value'] for f in fields if f['name'] == argument), expected)
        saved = json.loads((self.fixture / f'config/{name}.json').read_text('utf-8'))
        self.assertEqual(saved['realm_raid']['raid_config']['number_attack'], 9)
        result = self.request('set', config=name, task='RealmRaid', group='raid_config', argument='number_attack', value=7)
        self.assertTrue(result['ok'], result)
        fields = self.request('schema', config=name, task='MemoryScrolls')['data']['groups']['raid_config']
        self.assertEqual(next(f['value'] for f in fields if f['name'] == 'number_attack'), 7)
        self.assertTrue(saved['exploration']['scrolls']['scrolls_transition_delay_enable'])
        self.assertFalse(self.request('set', config=name, task='MemoryScrolls', group='scrolls', argument='scrolls_threshold', value=31)['ok'])
        self.assertFalse(self.request('set', config=name, task='Exploration', group='scrolls', argument='scrolls_cd', value='00 00:30:00')['ok'])

    def test_path_traversal_is_rejected(self):
        result = self.request('schema', config='../outside', task='Script')
        self.assertFalse(result['ok'])

    def test_failed_save_preserves_request_id(self):
        result = self.request('set', id='save-test', config='oas1', task='Exploration',
                              group='scrolls', argument='scrolls_threshold', value=31)
        self.assertFalse(result['ok'])
        self.assertEqual(result['id'], 'save-test')

    def test_delete_without_backup_stops_its_task(self):
        name = self.request('create')['data']['selected']
        source = self.fixture / f'config/{name}.json'
        started = self.request('start', config=name)
        self.assertTrue(started['ok'], started)
        pid = started['data']['pid']
        result = self.request('delete', config=name)
        self.assertTrue(result['ok'], result)
        self.assertFalse(source.exists())
        self.assertFalse(psutil.pid_exists(pid))
        self.assertNotIn(name, result['data']['configs'])
        self.assertNotIn('backup', result['data'])
        self.assertFalse((self.fixture / 'config/backup/deleted').exists())
        self.assertTrue((self.fixture / 'config/oas1.json').exists())

    def test_delete_rejects_missing_and_traversal(self):
        for name in ['does_not_exist', '../outside']:
            with self.subTest(name=name):
                self.assertFalse(self.request('delete', config=name)['ok'])
        self.assertTrue((self.fixture / 'config/template.json').exists())

    def test_y_delete_all_including_template_stays_empty_until_explicit_create(self):
        names = self.request('hello')['data']['configs']
        for name in names:
            self.assertTrue(self.request('delete', config=name)['ok'])
        self.assertEqual(self.request('hello')['data']['configs'], [])
        self.assertFalse(list((self.fixture / 'config').glob('*.json')))
        self.assertFalse(self.request('schema', config='oas1', task='Script')['ok'])
        self.assertEqual(self.request('hello')['data']['configs'], [])
        self.assertTrue(self.request('create')['ok'])
        self.assertTrue(self.request('schema', config='oas1', task='Script')['ok'])
        self.assertFalse((self.fixture / 'config/template.json').exists())

    def test_bad_request_does_not_crash_worker(self):
        self.assertFalse(self.request('unknown')['ok'])
        self.assertTrue(self.request('hello')['ok'])

    def test_stopping_idle_config_is_safe(self):
        self.assertTrue(self.request('stop', config='oas1')['ok'])

    def test_task_start_duplicate_rejection_and_stop_cleanup(self):
        result = self.request('start', config='oas1')
        self.assertTrue(result['ok'], result)
        pid = result['data']['pid']
        self.assertTrue(psutil.pid_exists(pid))
        deadline = time.monotonic() + 15
        logged = False
        seen = []
        while time.monotonic() < deadline:
            try:
                event = self.output.get(timeout=15)
            except queue.Empty:
                self.fail('Task log timeout. Events: ' + repr(seen) + '\nSTDERR:\n' + ''.join(self.errors[-80:]))
            seen.append(event)
            if event.get('event') == 'log' and 'Fixture task started' in event.get('text', ''):
                logged = True
                break
        self.assertTrue(logged, 'Task did not produce its expected log event')
        self.assertFalse(self.request('start', config='oas1')['ok'])
        self.assertTrue(self.request('stop', config='oas1')['ok'])
        self.assertFalse(psutil.pid_exists(pid))

    def test_z_shutdown_reaps_active_task(self):
        result = self.request('start', config='oas1')
        self.assertTrue(result['ok'], result)
        pid = result['data']['pid']
        self.assertTrue(self.request('shutdown')['ok'])
        self.process.wait(timeout=10)
        self.assertFalse(psutil.pid_exists(pid))


if __name__ == '__main__':
    unittest.main()
