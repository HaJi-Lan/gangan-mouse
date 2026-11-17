"""Rust device/vision adapter; original Python methods remain available for comparison."""
import atexit
import json
import os
import subprocess
import threading
import queue


class NativeClient:
    def __init__(self, executable):
        self.executable = executable
        self.lock = threading.Lock()
        self._spawn()
        atexit.register(self.close)
        self.request('ping')

    def _spawn(self):
        self.process = subprocess.Popen([self.executable, '--native-worker'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=None,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.responses = queue.Queue()
        process, responses = self.process, self.responses

        def read():
            try:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        raise RuntimeError('Rust device worker exited')
                    response = json.loads(line)
                    chunks = bytearray()
                    remaining = response['bytes']
                    while remaining:
                        part = process.stdout.read(remaining)
                        if not part:
                            raise RuntimeError('Rust device worker returned an incomplete frame')
                        chunks.extend(part)
                        remaining -= len(part)
                    responses.put((response, bytes(chunks)))
            except Exception as error:
                responses.put(error)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def _stop(self, kill=False):
        if kill and self.process.poll() is None:
            self.process.kill()
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdout.close()

    def request(self, command, payload=b'', timeout=30, **fields):
        with self.lock:
            if self.process.poll() is not None:
                self._stop()
                self._spawn()
            request = dict(fields, command=command, bytes=len(payload))
            self.process.stdin.write(json.dumps(request).encode('utf-8') + b'\n')
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            try:
                received = self.responses.get(timeout=timeout)
            except queue.Empty:
                self._stop(kill=True)
                if command == 'desktop_press' or command == 'adb' and fields.get('action') in ('tap', 'swipe'):
                    from module.exception import RequestHumanTakeover
                    raise RequestHumanTakeover('Rust input timed out; stop to avoid duplicate clicks')
                raise RuntimeError(f'Rust {command} timed out after {timeout}s')
            if isinstance(received, Exception):
                self._stop(kill=True)
                if command == 'desktop_press' or command == 'adb' and fields.get('action') in ('tap', 'swipe'):
                    from module.exception import RequestHumanTakeover
                    raise RequestHumanTakeover('Rust input connection lost; stop to avoid duplicate clicks')
                raise received
            response, data = received
            if not response['ok']:
                raise RuntimeError(response['error'])
            return response, data

    def close(self):
        atexit.unregister(self.close)
        self._stop()

    def match(self, source, template):
        response, _ = self.request('match', source.tobytes() + template.tobytes(),
            width=source.shape[1], height=source.shape[0],
            template_width=template.shape[1], template_height=template.shape[0])
        return response['score'], (response['x'], response['y'])

    def match_map(self, source, template, mask=None):
        import numpy as np
        payload = source.tobytes() + template.tobytes() + (b'' if mask is None else mask.tobytes())
        reply, data = self.request('match_map', payload, width=source.shape[1], height=source.shape[0],
            template_width=template.shape[1], template_height=template.shape[0],
            channels=1 if source.ndim == 2 else source.shape[2], masked=mask is not None)
        return np.frombuffer(data,dtype='<f4').reshape(reply['height'],reply['width']).copy()


def install_native_backend():
    executable = os.environ.get('GANGAN_NATIVE_EXE')
    mode = os.environ.get('GANGAN_NATIVE_MODE', 'rust')
    if not executable or mode == 'python':
        return
    import cv2
    import numpy as np
    from module.atom import image as rule_images
    from module.device.method.adb import Adb, retry
    from module.device.method.windows_impl import Window
    from module.device.method.nemu_ipc import NemuIpcImpl, retry as nemu_retry
    from module.device.method.desktop_impl import dpi_awareness
    from module.logger import logger
    from module.exception import RequestHumanTakeover
    from win32gui import GetClientRect

    client = NativeClient(executable)
    original_capture = Window.screenshot_window_background
    original_desktop = Window.screenshot_desktop_bitblt
    original_shell = Adb.adb_shell
    original_adb = Adb.screenshot_adb
    original_press = Window._desktop_press
    original_nemu = NemuIpcImpl.screenshot

    class RuleVision:
        def __getattr__(self, key):
            return getattr(cv2, key)

        def matchTemplate(self, source, template, method, result=None, mask=None):
            supported = (method == cv2.TM_CCOEFF_NORMED and result is None
                and source.dtype == np.uint8 and template.dtype == np.uint8
                and source.ndim == template.ndim and source.ndim in (2,3)
                and source.shape[0] >= template.shape[0] and source.shape[1] >= template.shape[1]
                and (source.ndim == 2 or source.shape[2] == template.shape[2] == 3)
                and (mask is None or mask.dtype == np.uint8 and mask.shape == template.shape[:2]))
            if not supported:
                return cv2.matchTemplate(source, template, method, result=result, mask=mask)
            actual = client.match_map(source, template, mask)
            if mode == 'compare':
                reference = cv2.matchTemplate(source, template, method, mask=mask)
                a = np.nan_to_num(actual,nan=-1,posinf=-1,neginf=-1)
                b = np.nan_to_num(reference,nan=-1,posinf=-1,neginf=-1)
                if not np.allclose(a,b,atol=0.0001,rtol=0.0001):
                    logger.warning(f'Rust template comparison: max difference={np.max(np.abs(a-b)):.6f}')
                return reference
            return actual

    def capture(window):
        desktop = getattr(window, 'is_desktop_window', False)
        if desktop:
            window.desktop_window_restore_if_minimized()
            window._desktop_assert_window_pos(window.root_handle_num)
            with dpi_awareness():
                rect = GetClientRect(window.screenshot_handle_num)
                width, height = rect[2]-rect[0], rect[3]-rect[1]
            if width <= 0 or height <= 0:
                raise RequestHumanTakeover('Desktop client window is minimized, screenshot unavailable. 请恢复游戏窗口')
        else:
            width, height = window.screenshot_size
        _, data = client.request('capture', hwnd=window.screenshot_handle_num,
                                 width=width, height=height, client=desktop)
        image = np.frombuffer(data, np.uint8).reshape(height, width, 3).copy()
        target = tuple(window.screenshot_size)
        if desktop and (width, height) != target:
            image = cv2.resize(image, target)
        if mode == 'compare':
            reference = original_desktop(window) if desktop else original_capture(window)
            logger.info(f'Rust capture comparison: shape={image.shape}, mean difference={np.abs(image.astype(float)-reference).mean():.3f}')
            return reference
        return image

    def shell(device, cmd, *args, **kwargs):
        # Keep existing retry decorators, random paths, press timing and non-ADB
        # control methods. Never retry an uncertain click through another backend.
        if (mode != 'compare' and not getattr(device.config, 'DEVICE_OVER_HTTP', False)
                and isinstance(cmd, (list, tuple)) and not args and not kwargs
                and ((len(cmd) == 4 and list(cmd[:2]) == ['input', 'tap'])
                     or (len(cmd) == 7 and list(cmd[:2]) == ['input', 'swipe']))):
            action = cmd[1]
            fields = dict(zip(('x', 'y', 'end_x', 'end_y', 'duration_ms'), map(int, cmd[2:])))
            _, data = client.request('adb', executable=str(device.adb_binary),
                serial=str(device.serial), action=action, **fields)
            return data.decode('utf-8').rstrip()
        return original_shell(device, cmd, *args, **kwargs)

    @retry
    def screenshot_adb(device):
        if mode == 'compare' or getattr(device.config, 'DEVICE_OVER_HTTP', False):
            return original_adb(device)
        _, data = client.request('adb', executable=str(device.adb_binary),
                                serial=str(device.serial), action='capture')
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError('Rust ADB returned an invalid PNG screenshot')
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def desktop_press(window, x, y, seconds):
        if mode == 'compare':
            return original_press(window, x, y, seconds)
        window.desktop_window_restore_if_minimized()
        window.move_desktop_window_message(x, y)
        px, py = window.desktop_message_coord(x, y)
        client.request('desktop_press', hwnd=window.root_handle_num,
                       x=int(px), y=int(py), seconds=float(seconds), timeout=max(30,seconds+5))

    @nemu_retry
    def nemu_capture(implementation):
        reply, data = client.request('nemu_capture', folder=implementation.nemu_folder,
            instance=implementation.instance_id, display=implementation.display_id, timeout=0.5)
        implementation.width, implementation.height = reply['width'], reply['height']
        frame = np.frombuffer(data,np.uint8).reshape(reply['height'],reply['width'],4).copy()
        if mode == 'compare':
            reference = original_nemu(implementation)
            logger.info(f'Rust MuMu comparison: mean difference={np.abs(frame.astype(float)-reference).mean():.3f}')
            return reference
        return frame

    # Restrict the numerical backend to RuleImage; other modules retain cv2.
    rule_images.cv2 = RuleVision()
    Window.screenshot_window_background = capture
    Window.screenshot_desktop_bitblt = capture
    Adb.adb_shell = shell
    Adb.screenshot_adb = screenshot_adb
    Window._desktop_press = desktop_press
    NemuIpcImpl.screenshot = nemu_capture
    from native_ocr import install_native_ocr
    install_native_ocr(client, mode)
    logger.info(f'Rust device/vision backend active: {mode}; template maps and local OCR normalization/inference/CTC/box filtering use Rust; SIFT remains Python')
