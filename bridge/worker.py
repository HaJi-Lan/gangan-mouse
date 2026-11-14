"""Private JSON-lines task adapter. Upstream source stays unmodified."""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import traceback

from task_policy import install_runtime_policy, task_selection
from scrolls_cycle import install_config_models, task_schema, set_task_arg


def run_task(root, name, emit):
    os.chdir(root)
    sys.path.insert(0, root)
    # A spawned task must never write to the parent's JSON protocol pipe.
    sys.stdout = sys.stderr
    install_config_models()
    from module.logger import set_file_logger, set_func_logger
    set_file_logger(name=name)
    set_func_logger(lambda text: emit(dict(event='log', config=name, text=str(text))))
    Script = install_runtime_policy()
    script = Script(config_name=name)
    class StateQueue:
        def put(self, value):
            emit(dict(event='task', config=name, data=value))
    script.state_queue = StateQueue()
    script.loop()


class Runtime:
    def __init__(self, root, emit):
        self.root = str(Path(root).resolve())
        os.chdir(self.root)
        sys.path.insert(0, self.root)
        install_config_models()
        from module.config.config import Config
        os.chdir(self.root)
        self.Config = Config
        self.menu, self.removed_tasks = task_selection()
        self.emit = emit
        self.processes = {}

    def config(self, name):
        self.check_name(name)
        if name not in self.list_configs():
            raise ValueError('配置不存在')
        # Reload before each edit so task-written schedules are not replaced by
        # an old GUI snapshot.
        return self.Config(name)

    def list_configs(self):
        # Upstream all_script_files() recreates oas1 when empty. Listing here
        # must be read-only, including after the last config is deleted.
        return sorted(path.stem for path in (Path(self.root) / 'config').glob('*.json'))

    @staticmethod
    def check_name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            raise ValueError('配置名称只允许字母、数字、下划线和短横线')

    def dispatch(self, request):
        command = request.get('command')
        name = request.get('config', '')
        if command == 'hello':
            # Upstream ignores its generated JSON translation file. Keep the
            # shared UI text available in a fresh source clone as well.
            translations = json.loads(Path(__file__).with_name('translations.upstream.zh-CN.json').read_text('utf-8'))
            for path in ['module/config/i18n/zh-CN.json', 'assets/i18n/zh-CN.json']:
                file = Path(self.root) / path
                if file.exists():
                    translations.update(json.loads(file.read_text('utf-8')))
            translations.update(json.loads(Path(__file__).with_name('translations.zh-CN.json').read_text('utf-8')))
            return dict(protocol=1, configs=self.list_configs(), menu=self.menu,
                        translations=translations)
        if command == 'schema':
            self.check_task(request['task'])
            config = self.config(name)
            return dict(config=name, task=request['task'], groups=task_schema(config, request['task']))
        if command == 'set':
            self.check_task(request['task'])
            config = self.config(name)
            ok = set_task_arg(config, request['task'], request['group'], request['argument'], request['value'])
            if not ok:
                raise ValueError('上游配置模型拒绝了此修改')
            return dict(config=name, task=request['task'], groups=task_schema(config, request['task']))
        if command == 'create':
            numbers = [int(match.group(1)) for item in self.list_configs()
                       if (match := re.fullmatch(r'oas(\d+)', item))]
            name = f'oas{max(numbers, default=0) + 1}'
            config_dir = Path(self.root) / 'config'
            config_dir.mkdir(parents=True, exist_ok=True)
            template = config_dir / 'template.json'
            if template.is_file():
                content = template.read_bytes()
            else:
                from module.config.config import ConfigModel
                data = ConfigModel().model_dump(mode='json')
                data['config_name'] = name
                content = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
            with (config_dir / f'{name}.json').open('xb') as file:
                file.write(content)
            return dict(configs=self.list_configs(), selected=name)
        if command == 'delete':
            self.check_name(name)
            names = self.list_configs()
            if name not in names:
                raise ValueError('配置不存在')
            remaining = [item for item in names if item != name]
            config_dir = Path(self.root) / 'config'
            source = config_dir / f'{name}.json'
            if source.is_symlink() or source.resolve().parent != config_dir.resolve():
                raise ValueError('不能删除指向其他位置的配置')
            self.stop(name)
            source.unlink()
            return dict(configs=remaining, deleted=name)
        if command == 'start':
            self.config(name)
            if name in self.processes:
                raise ValueError('该配置已在运行')
            from module.server.setting import State
            if State.deploy_config.StartOcrServer:
                from module.ocr.rpc import ensure_ocr_server_started
                if not ensure_ocr_server_started():
                    raise RuntimeError('OCR 服务未就绪，请检查日志')
            process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                                        '--root', self.root, '--task', name],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                       errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            events = queue.Queue()

            def read_output(stream, protocol=False):
                for line in stream:
                    try:
                        data = json.loads(line) if protocol else dict(event='log', config=name, text=line.rstrip())
                    except ValueError:
                        data = dict(event='log', config=name, text=line.rstrip())
                    events.put(data)

            readers = [threading.Thread(target=read_output, args=(process.stdout, True), daemon=True),
                       threading.Thread(target=read_output, args=(process.stderr,), daemon=True)]
            for reader in readers:
                reader.start()
            self.processes[name] = (process, events, readers)
            self.emit(dict(event='state', config=name, state=1))
            return dict(pid=process.pid)
        if command == 'stop':
            self.stop(name)
            return True
        if command == 'shutdown':
            self.close()
            return True
        raise ValueError('未知命令: ' + str(command))

    def check_task(self, task):
        from module.config.utils import convert_to_underscore
        if convert_to_underscore(task) in self.removed_tasks:
            raise ValueError('日常任务和每周任务已从此版本移除')

    def stop(self, name):
        item = self.processes.pop(name, None)
        if item:
            process, events, readers = item
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for reader in readers:
                reader.join(timeout=1)
            process.stdout.close()
            process.stderr.close()
        self.emit(dict(event='state', config=name, state=0))

    def poll(self):
        for name, (process, events, readers) in list(self.processes.items()):
            # Bound work so a chatty task cannot starve incoming commands.
            for _ in range(100):
                try:
                    self.emit(events.get_nowait())
                except queue.Empty:
                    break
            if process.poll() is not None:
                code = process.returncode
                self.stop(name)
                self.emit(dict(event='log', config=name, text=f'任务进程结束，退出码 {code}'))

    def close(self):
        for name in list(self.processes):
            self.stop(name)
        # Never import OCR just to close an idle adapter: loading its native
        # inference libraries is expensive and may block process shutdown.
        ocr = sys.modules.get('module.ocr.rpc')
        if ocr is not None:
            ocr.shutdown_ocr_server()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--task')
    args = parser.parse_args()
    protocol = sys.stdout
    sys.stdout = sys.stderr

    def emit(value):
        protocol.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')
        protocol.flush()

    runtime = None
    try:
        if args.task:
            run_task(args.root, args.task, emit)
            return 0
        runtime = Runtime(args.root, emit)
        emit(dict(event='ready', protocol=1))
        incoming = queue.Queue()

        def read_requests():
            for line in sys.stdin:
                incoming.put(line)
            incoming.put(None)

        threading.Thread(target=read_requests, daemon=True).start()
        while True:
            try:
                line = incoming.get(timeout=0.05)
            except queue.Empty:
                runtime.poll()
                continue
            if line is None:
                break
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError('请求必须是 JSON 对象')
                result = runtime.dispatch(request)
                emit(dict(event='response', command=request.get('command'), id=request.get('id'), ok=True, data=result))
                if request.get('command') == 'shutdown':
                    break
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                emit(dict(event='response', command=request.get('command') if isinstance(request, dict) else None,
                          ok=False, error=str(exc)))
            runtime.poll()
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        if args.task:
            emit(dict(event='log', config=args.task, text='任务失败：' + str(exc)))
        else:
            emit(dict(event='fatal', error=str(exc)))
        return 1
    finally:
        if runtime is not None:
            runtime.close()
    return 0


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
