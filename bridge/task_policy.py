"""Compatibility executor for the native desktop's retained task catalog."""


def task_selection():
    import json
    import os
    from pathlib import Path
    from module.config.config_menu import ConfigMenu
    from module.config.utils import convert_to_underscore
    menu = ConfigMenu().gui_menu_list
    available = {task for tasks in menu.values() for task in tasks}
    # The Rust host supplies its compiled catalog to the executor and child tasks.
    # Direct Python/source use reads the same data, without a second policy table.
    source = os.environ.get('GANGAN_TASK_CATALOG')
    if source is None:
        source = (Path(__file__).resolve().parents[1] / 'assets/task-catalog.json').read_text('utf-8')
    catalog = json.loads(source)
    selected = {category: [task for task in tasks if task in available]
                for category, tasks in catalog.items()}
    allowed = {task for tasks in selected.values() for task in tasks}
    removed = {convert_to_underscore(task) for task in available - allowed}
    return selected, removed


def install_runtime_policy():
    """Apply policy to reloads, callbacks and direct Script.run calls alike."""
    import module.config.config as config_module
    from module.config.utils import convert_to_underscore
    from module.logger import logger
    selected, _ = task_selection()
    allowed = {convert_to_underscore(task) for tasks in selected.values() for task in tasks}
    upstream_config = config_module.Config
    cycle_context = {'cycle': None, 'recovery': False}

    class Config(upstream_config):
        def update_scheduler(self):
            super().update_scheduler()
            self.pending_task = [task for task in self.pending_task
                                 if convert_to_underscore(task.command) in allowed]
            self.waiting_task = [task for task in self.waiting_task
                                 if convert_to_underscore(task.command) in allowed]
            scrolls = self.model.exploration.scrolls
            hidden = set()
            if not scrolls.scrolls_enable:
                hidden.add('MemoryScrolls')
            elif self.model.memory_scrolls.scheduler.enable:
                hidden.update(('Exploration', 'RealmRaid'))
            self.pending_task = [task for task in self.pending_task if task.command not in hidden]
            self.waiting_task = [task for task in self.waiting_task if task.command not in hidden]
            if cycle_context['recovery']:
                self.pending_task.sort(key=lambda task: task.command != 'Restart')

        def task_call(self, task=None, force_call=True):
            if task and convert_to_underscore(task) not in allowed:
                return False
            if task and convert_to_underscore(task) == 'restart':
                cycle = cycle_context['cycle']
                if cycle is not None and cycle.active:
                    cycle.interrupted = True
                    cycle_context['recovery'] = True
            return super().task_call(task, force_call=force_call)

    config_module.Config = Config
    import script as script_module
    from script import Script as UpstreamScript
    original_load = script_module.load_module

    def load_task(name, path):
        from pathlib import Path
        module = original_load(name, path)
        cycle = cycle_context['cycle']
        if Path(path).parent.name == 'Exploration' and not (cycle and cycle.active):
            upstream = module.ScriptTask
            class StandaloneExploration(upstream):
                def activate_realm_raid(self, scrolls, con):
                    return
            module.ScriptTask = StandaloneExploration
        return module
    script_module.load_module = load_task

    class Script(UpstreamScript):
        def report_execution(self, task, owner=''):
            queue = getattr(self, 'state_queue', None)
            if queue is not None:
                execution = {'task': task, 'owner': owner}
                cycle = getattr(self, '_desktop_scrolls_cycle', None)
                if owner and cycle is not None and cycle.active and cycle.started_at is not None:
                    execution['scrolls_elapsed_seconds'] = max(0, cycle.clock() - cycle.started_at)
                queue.put({'execution': execution})

        def run(self, command):
            if convert_to_underscore(command) not in allowed:
                logger.info(f'已移除任务，跳过执行：{command}')
                return True
            self.report_execution(command, command)
            try:
                return self.run_enabled(command)
            finally:
                self.report_execution('')

        def run_enabled(self, command):
            key = convert_to_underscore(command)
            if key == 'memory_scrolls':
                from scrolls_cycle import ScrollsCycle, attach_cycle_loader
                def read_config():
                    return Config(self.config_name)
                if read_config().exploration.scrolls.scrolls_enable:
                    cycle = getattr(self, '_desktop_scrolls_cycle', None)
                    if cycle is None:
                        def finish_timeout(owner):
                            from datetime import datetime
                            config = read_config()
                            config.task_delay('MemoryScrolls' if owner == 'memory_scrolls' else 'Exploration',
                                              start_time=datetime.now(), success=True)
                        cycle = ScrollsCycle(read_config, logger.info, on_timeout=finish_timeout)
                        attach_cycle_loader(cycle)
                        self._desktop_scrolls_cycle = cycle
                        cycle_context['cycle'] = cycle
                    def run_leg(phase):
                        self.report_execution(phase, command)
                        self.__dict__.pop('config', None)
                        self.config.model.running_task = phase
                        try:
                            return super(Script, self).run(phase)
                        finally:
                            self.report_execution('', command)
                            # The upstream loop saves running_task after run().
                            # Reload first so that save cannot restore an old
                            # manual switch value edited while this leg ran.
                            self.__dict__.pop('config', None)
                    return cycle.run(key, run_leg)
                if key == 'memory_scrolls':
                    return True
            result = super().run(command)
            if key == 'restart' and result:
                cycle_context['recovery'] = False
            return result

    return Script
