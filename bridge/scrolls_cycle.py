"""Exploration / personal raid workflow; upstream files remain updateable."""
import random
import re
import time
from pathlib import Path


# The collection page edits the same settings as the individual task pages.
COLLECTION_GROUPS = {
    'scrolls': ('Exploration', 'scrolls'),
    'exploration_config': ('Exploration', 'exploration_config'),
    'invite_config': ('Exploration', 'invite_config'),
    'cycle_exploration_battle': ('Exploration', 'general_battle_config'),
    'cycle_exploration_soul': ('Exploration', 'switch_soul_config'),
    'raid_config': ('RealmRaid', 'raid_config'),
    'cycle_raid_battle': ('RealmRaid', 'general_battle_config'),
    'cycle_raid_soul': ('RealmRaid', 'switch_soul_config'),
}


def install_config_models():
    import module.config.config as config_module
    if getattr(config_module.ConfigModel, '_desktop_scrolls_model', False):
        return
    from pydantic import create_model, Field
    from tasks.Exploration.config import Exploration, Scrolls
    scrolls = create_model('DesktopScrolls', __base__=Scrolls,
        scrolls_threshold=(int, Field(default=25, ge=0, le=30, description='scrolls_threshold_help')),
        scrolls_transition_delay_enable=(bool, Field(default=False, description='scrolls_transition_delay_enable_help')))
    exploration = create_model('DesktopExploration', __base__=Exploration,
        scrolls=(scrolls, Field(default_factory=scrolls)))
    model = create_model('DesktopConfigModel', __base__=config_module.ConfigModel,
        exploration=(exploration, Field(default_factory=exploration)))
    model._desktop_scrolls_model = True
    # Keep the upstream module's ConfigModel unchanged: its type() method uses
    # the original annotations to derive scheduler task names.
    config_module.ConfigModel = model


def task_schema(config, task):
    from module.config.utils import convert_to_underscore
    if convert_to_underscore(task) == 'memory_scrolls':
        groups = {'scheduler': config.model.script_task('MemoryScrolls')['scheduler']}
        for name, (source_task, source_group) in COLLECTION_GROUPS.items():
            groups[name] = config.model.script_task(source_task)[source_group]
    else:
        groups = config.model.script_task(task)
    if 'scrolls' in groups:
        groups['scrolls'] = [field for field in groups['scrolls'] if field['name'] != 'scrolls_cd']
    hidden_buff_fields = {'buff_gold_50_click', 'buff_gold_100_click',
                          'buff_exp_50_click', 'buff_exp_100_click'}
    # Project only the desktop fields; keep the full runtime model and saved values.
    for name in list(groups):
        groups[name] = [field for field in groups[name] if field['name'] not in hidden_buff_fields]
        source_group = COLLECTION_GROUPS.get(name, (task, name))[1]
        if source_group in ('switch_soul', 'switch_soul_config'):
            del groups[name]
        elif source_group == 'scheduler':
            # The enable field drives the task-title toggle, not the scheduler form.
            groups[name] = [field for field in groups[name]
                            if field['name'] in ('enable', 'next_run', 'priority', 'float_time')]
        elif source_group == 'general_battle_config':
            groups[name] = [field for field in groups[name]
                            if field['name'] in ('lock_team_enable', 'random_click_swipt_enable')]
        if not groups.get(name):
            groups.pop(name, None)
    return groups


def set_task_arg(config, task, group, argument, value):
    from module.config.utils import convert_to_underscore
    if convert_to_underscore(task) == 'memory_scrolls' and group in COLLECTION_GROUPS:
        task, group = COLLECTION_GROUPS[group]
    if group == 'scrolls' and argument == 'scrolls_cd':
        raise ValueError('旧间隔时间已替换为可选的随机 5～25 秒切换间隔')
    if group == 'scrolls' and argument == 'scrolls_threshold':
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 30:
            raise ValueError('突破券阈值必须为 0～30 的整数')
    if argument == 'scrolls_transition_delay_enable' and type(value) is not bool:
        raise ValueError('切换间隔开关必须为布尔值')
    return config.model.script_set_arg(task, group, argument, value)


def ticket_count(result):
    """Reject unrecognized OCR such as (0, 0, 0), never treat it as empty."""
    if not isinstance(result, (tuple, list)) or len(result) != 3:
        return None
    current, rest, total = result
    # OCR uses Python integer counters. Reject bools and fractional values.
    if any(isinstance(value, bool) or not isinstance(value, int) for value in result):
        return None
    if total != 30 or current < 0 or rest < 0 or current + rest != total:
        return None
    return current


def retry_exploration_counter(task, rule, sleep, log):
    """Recover an icon read as a leading digit, without altering OCR numbers."""
    import cv2
    last, confirmed = None, 0
    for _ in range(3):
        sleep(0.3)
        task.screenshot()
        roi = rule.roi
        # Expand outward only. Trimming the left edge can turn 30/30 into
        # 0/30 with high confidence. Require agreement between expanded ROIs.
        values = []
        for pad_x, pad_y in ((4, 6), (8, 8)):
            x, y, width, height = roi
            left, top = max(0, x - pad_x), max(0, y - pad_y)
            image = rule.pre_process(rule.crop(task.device.image,
                (left, top, x + width + pad_x - left, y + height + pad_y - top)))
            image = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            text, score = rule.model.ocr_single_line(image)
            log(f'绘卷突破券复核：扩边=({pad_x},{pad_y}), 原文={text!r}, 置信度={score:.2f}')
            match = re.fullmatch(r'\s*(0|[1-9][0-9]?)\s*/\s*30\s*', text)
            if score >= max(rule.score, 0.8) and match and int(match[1]) <= 30:
                values.append(int(match[1]))
        value = values[0] if len(values) == 2 and values[0] == values[1] else None
        if value is None:
            last, confirmed = None, 0
            continue
        confirmed = confirmed + 1 if value == last else 1
        last = value
        if confirmed >= 2:
            log(f'绘卷突破券重新确认：{value}/30')
            return value
    return None


class ScrollsCycle:
    def __init__(self, read_config, log, sleep=time.sleep, randint=random.randint):
        self.read_config = read_config
        self.log = log
        self.sleep = sleep
        self.randint = randint
        self.active = False
        self.owner = None
        self.requested = None
        self.depleted = False
        self.interrupted = False
        self.resume_owner = None
        self.resume_phase = 'Exploration'

    def enabled(self):
        config = self.read_config()
        return (config.exploration.scrolls.scrolls_enable
                and getattr(config, self.owner).scheduler.enable)

    def pause(self, seconds):
        # Changes to the manual switch take effect during the wait, too.
        for _ in range(seconds):
            if not self.enabled():
                return False
            self.sleep(1)
        return self.enabled()

    def run(self, owner, run_leg):
        self.owner = owner
        self.active = True
        self.interrupted = False
        phase = self.resume_phase if self.resume_owner == owner else 'Exploration'
        try:
            while self.enabled():
                self.requested = None
                self.depleted = False
                self.log(f'绘卷循环：执行 {phase}')
                if not run_leg(phase):
                    self.resume_owner, self.resume_phase = owner, phase
                    return False
                if self.interrupted:
                    self.resume_owner, self.resume_phase = owner, phase
                    return True
                if not self.enabled():
                    break
                next_phase = ('RealmRaid' if self.requested == 'RealmRaid' else phase)
                if phase == 'RealmRaid' and self.depleted:
                    next_phase = 'Exploration'
                if next_phase != phase:
                    self.log(f'绘卷循环：{phase} → {next_phase}')
                    delay = (self.randint(5, 25) if self.read_config().exploration.scrolls.scrolls_transition_delay_enable else 0)
                    if delay:
                        self.log(f'切换前等待 {delay} 秒')
                    if not self.pause(delay):
                        break
                else:
                    # Raid caps, losses, refresh cooldowns and OCR errors are
                    # not ticket depletion. Stay in raid and retry later.
                    if not self.pause(30 if phase == 'RealmRaid' else 1):
                        break
                phase = next_phase
            self.resume_owner = None
            self.resume_phase = 'Exploration'
            return True
        finally:
            self.active = False
            self.owner = None

    def exploration_check(self, task, scrolls, con):
        from module.exception import TaskEnd
        from tasks.GameUi.page import page_main
        if not self.active:
            return
        if not self.enabled():
            raise TaskEnd
        settings = self.read_config().exploration.scrolls
        entrance = task.appear(task.I_E_EXPLORATION_CLICK) and task.appear(task.I_EXP_CREATE_TEAM)
        if not entrance:
            return
        rule = task.O_REALM_RAID_NUMBER1
        try:
            from diagnostics import save_counter_frame
            save_counter_frame(task, rule)
        except Exception as error:
            self.log(f'保存识别裁片失败：{type(error).__name__}')
        # Even a plausible 11/30 can be a misread. Verify every entrance
        # against expanded regions and fresh frames before comparing limits.
        count = retry_exploration_counter(task, rule, self.sleep, self.log)
        if count is None:
            self.log('章节入口仍无法确认突破券，5 秒后重试，暂不进入探索')
            self.pause(5)
            raise TaskEnd
        if count == 0 or count < settings.scrolls_threshold:
            return
        for target in (task.I_RED_CLOSE, task.I_UI_CANCEL, task.I_UI_CANCEL_SAMLL):
            if task.appear(target):
                task.ui_click_until_disappear(target)
        task.goto_page(page_main)
        if any((con.buff_gold_50_click, con.buff_gold_100_click, con.buff_exp_50_click, con.buff_exp_100_click)):
            task.open_buff()
            task.gold_50(is_open=False)
            task.gold_100(is_open=False)
            task.exp_50(is_open=False)
            task.exp_100(is_open=False)
            task.close_buff()
        self.log(f'突破券 {count} ≥ {settings.scrolls_threshold} 且有券，切换个人突破')
        self.requested = 'RealmRaid'
        raise TaskEnd

    def raid_check(self, task):
        from module.exception import TaskEnd
        if not self.enabled():
            raise TaskEnd
        task.wait_until_appear(task.I_BACK_RED)
        task.screenshot()
        count = ticket_count(task.O_NUMBER.ocr(task.device.image))
        if count == 0 or count is None:
            # Confirm empty after closing any reward overlay; an unreadable
            # frame must never start exploration or trigger a blind attack.
            task.reward_detect_click(True)
            task.screenshot()
            count = ticket_count(task.O_NUMBER.ocr(task.device.image))
        if count is None:
            self.log('突破券识别失败，暂缓挑战并保留突破阶段')
            return False
        self.depleted = count == 0
        if self.depleted:
            self.log('突破券已用完，返回探索')
        return count > 0

    def adapt(self, module, task_name):
        """Adapt each dynamically loaded task, rather than patching source."""
        if not self.active or task_name not in ('Exploration', 'RealmRaid'):
            return module
        cycle = self
        upstream = module.ScriptTask
        if task_name == 'Exploration':
            class Task(upstream):
                def activate_realm_raid(self, scrolls, con):
                    return cycle.exploration_check(self, scrolls, con)

                def set_next_run(self, task, *args, **kwargs):
                    if task not in ('Exploration', 'RealmRaid', 'MemoryScrolls'):
                        return super().set_next_run(task, *args, **kwargs)
        else:
            class Task(upstream):
                def check_ticket(self, base=0):
                    # Once raid starts, consume the remaining tickets even
                    # below the independent personal-raid number_base setting.
                    return cycle.raid_check(self)

                def set_next_run(self, task, *args, **kwargs):
                    if task not in ('Exploration', 'RealmRaid', 'MemoryScrolls'):
                        return super().set_next_run(task, *args, **kwargs)
        module.ScriptTask = Task
        return module


def attach_cycle_loader(cycle):
    import script
    upstream_load = script.load_module

    def load(name, path):
        return cycle.adapt(upstream_load(name, path), Path(path).parent.name)

    script.load_module = load
