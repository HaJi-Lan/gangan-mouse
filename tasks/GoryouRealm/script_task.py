# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import datetime, timedelta
import re
from time import monotonic, sleep
from module.exception import TaskEnd, GamePageUnknownError
from module.logger import logger
from random import choice
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_shikigami_records, page_goryou_realm
from tasks.GoryouRealm.assets import GoryouRealmAssets
from tasks.GoryouRealm.config import GoryouClass


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, GoryouRealmAssets):

    def run(self):
        try:
            self._run()
        except GamePageUnknownError as error:
            self._retry_later(str(error))

    def _retry_later(self, reason):
        logger.warning(f'GoryouRealm incomplete: {reason}; retry in 5 minutes')
        self.set_next_run(task='GoryouRealm', success=False, server=False,
                          target=datetime.now() + timedelta(minutes=5))
        raise TaskEnd

    def read_ticket_count(self, previous=None):
        # Use raw text and confidence: generic Digit converts an unreadable ROI
        # to zero and joins strings such as "89 3" into 893.
        rule = self.O_GR_TICKET
        last, confirmations = None, 0
        for attempt in range(5):
            if attempt:
                sleep(0.3)
            self.screenshot()
            value = None
            if self.appear(self.I_GR_FIRE):
                image = rule.pre_process(rule.crop(self.device.image, rule.roi))
                text, score = rule.model.ocr_single_line(image)
                text = text.strip()
                if score >= rule.score and re.fullmatch(r'0|[1-9][0-9]*', text):
                    value = int(text)
                logger.info(f'GoryouRealm ticket sample: text={text!r}, score={score:.2f}')
            # Each challenge consumes one ticket. Reject both upward jumps and
            # dropped digits; never "correct" an uncertain sample to zero.
            if previous is not None and value != max(previous - 1, 0):
                value = None
            if value is None:
                last, confirmations = None, 0
                continue
            confirmations = confirmations + 1 if value == last else 1
            last = value
            if confirmations >= (3 if value == 0 else 2):
                logger.info(f'GoryouRealm confirmed tickets: {value}')
                return value
        self._retry_later('ticket count could not be confirmed')

    def _run(self):
        con = self.config.goryou_realm
        limit_time = con.goryou_config.limit_time
        self.limit_time: timedelta = timedelta(hours=limit_time.hour, minutes=limit_time.minute,
                                               seconds=limit_time.second)
        if con.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(con.switch_soul_config.switch_group_team)
        if self.goto_page(page_goryou_realm, skip_first_screenshot=False) is False:
            raise GamePageUnknownError('Cannot enter GoryouRealm')

        match_click = {
            GoryouClass.Dark_Divine_Dragon: self.C_GR_C_1,
            GoryouClass.Dark_Hakuzousu: self.C_GR_C_2,
            GoryouClass.Dark_Black_Panther: self.C_GR_C_3,
            GoryouClass.Dark_Peacock: self.C_GR_C_4,
        }
        goryou_class = con.goryou_config.goryou_class
        if goryou_class == GoryouClass.RANDOM:
            goryou_class = choice(list(match_click.keys()))
        entry_started = monotonic()
        while 1:
            if monotonic() - entry_started >= 30:
                raise GamePageUnknownError('GoryouRealm challenge button not found')
            self.screenshot()
            if self.appear(self.I_GR_FIRE):
                logger.info('Enter GoryouRealm')
                break
            if self.click(match_click[goryou_class], interval=1):
                continue
        self.check_lock(con.general_battle_config.lock_team_enable, self.I_GR_LOCK, self.I_GR_UNLOCK)

        # 开始循环
        previous_ticket = None
        screen_started = monotonic()
        while 1:
            self.screenshot()
            if not self.appear(self.I_GR_FIRE):
                if monotonic() - screen_started >= 30:
                    raise GamePageUnknownError('GoryouRealm challenge page not restored')
                continue
            screen_started = monotonic()

            if self.current_count >= con.goryou_config.limit_count:
                logger.info('GoryouRealm count limit out')
                break
            if datetime.now() - self.start_time >= self.limit_time:
                logger.info('GoryouRealm time limit out')
                break
            ticket = self.read_ticket_count(previous_ticket)
            if ticket == 0:
                break

            # 点击挑战
            challenge_started = monotonic()
            while 1:
                if monotonic() - challenge_started >= 30:
                    raise GamePageUnknownError('GoryouRealm challenge did not start')
                self.screenshot()
                if self.appear_then_click(self.I_GR_FIRE, interval=1):
                    pass
                if not self.appear(self.I_GR_FIRE):
                    self.run_general_battle(config=con.general_battle_config)
                    previous_ticket = ticket
                    screen_started = monotonic()
                    break


        if self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_EXPLORATION) is False:
            raise GamePageUnknownError('Cannot leave GoryouRealm')
        logger.info('Back to exploration')
        self.set_next_run(task='GoryouRealm', success=True, finish=True)
        raise TaskEnd




if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
