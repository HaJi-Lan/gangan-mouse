"""Task selection for this desktop edition, separate from upstream source."""
REMOVED_CATEGORIES = frozenset({'Daily Task', 'Weekly Task'})


def task_selection():
    from module.config.config_menu import ConfigMenu
    from module.config.utils import convert_to_underscore
    menu = ConfigMenu().gui_menu_list
    removed = {convert_to_underscore(task)
               for category, tasks in menu.items() if category in REMOVED_CATEGORIES
               for task in tasks}
    return {category: tasks for category, tasks in menu.items()
            if category not in REMOVED_CATEGORIES}, removed


def install_runtime_policy():
    """Apply policy to reloads, callbacks and direct Script.run calls alike."""
    import module.config.config as config_module
    from module.config.utils import convert_to_underscore
    from module.logger import logger
    _, removed = task_selection()
    upstream_config = config_module.Config
    cycle_context = {'cycle': None, 'recovery': False}

    class Config(upstream_config):
        def update_scheduler(self):
            super().update_scheduler()
            self.pending_task = [task for task in self.pending_task
                                 if convert_to_underscore(task.command) not in removed]
            self.waiting_task = [task for task in self.waiting_task
                                 if convert_to_underscore(task.command) not in removed]
            scrolls = self.model.exploration.scrolls
            hidden = set()
            if not scrolls.scrolls_enable:
                hidden.add('MemoryScrolls')
            elif self.model.memory_scrolls.scheduler.enable:
                hidden.update(('Exploration', 'RealmRaid'))
            elif self.model.exploration.scheduler.enable:
                hidden.add('RealmRaid')
            self.pending_task = [task for task in self.pending_task if task.command not in hidden]
            self.waiting_task = [task for task in self.waiting_task if task.command not in hidden]
            if cycle_context['recovery']:
                self.pending_task.sort(key=lambda task: task.command != 'Restart')

        def task_call(self, task=None, force_call=True):
            if task and convert_to_underscore(task) in removed:
                return False
            if task and convert_to_underscore(task) == 'restart':
                cycle = cycle_context['cycle']
                if cycle is not None and cycle.active:
                    cycle.interrupted = True
                    cycle_context['recovery'] = True
            return super().task_call(task, force_call=force_call)

    config_module.Config = Config
    from script import Script as UpstreamScript

    class Script(UpstreamScript):
        def run(self, command):
            if convert_to_underscore(command) in removed:
                logger.info(f'已移除任务，跳过执行：{command}')
                return True
            key = convert_to_underscore(command)
            if key in ('memory_scrolls', 'exploration'):
                from scrolls_cycle import ScrollsCycle, attach_cycle_loader
                def read_config():
                    return Config(self.config_name)
                if read_config().exploration.scrolls.scrolls_enable:
                    cycle = getattr(self, '_desktop_scrolls_cycle', None)
                    if cycle is None:
                        cycle = ScrollsCycle(read_config, logger.info)
                        attach_cycle_loader(cycle)
                        self._desktop_scrolls_cycle = cycle
                        cycle_context['cycle'] = cycle
                    def run_leg(phase):
                        self.__dict__.pop('config', None)
                        self.config.model.running_task = phase
                        try:
                            return super(Script, self).run(phase)
                        finally:
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
