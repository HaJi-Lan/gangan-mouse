"""Run with GANGAN_TEST_NATIVE pointing to a freshly built executable."""
import os
from pathlib import Path
import sys
import json
import tempfile
import subprocess
import shutil
import threading
import queue
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bridge'))
from native_backend import NativeClient


@unittest.skipUnless(os.environ.get('GANGAN_TEST_NATIVE'), 'requires a freshly built native worker')
class NativeBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = NativeClient(os.environ['GANGAN_TEST_NATIVE'])

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def compare(self, source, template):
        score, location = self.client.match(source, template)
        _, reference, _, expected = cv2.minMaxLoc(cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED))
        self.assertAlmostEqual(score, reference, delta=0.0001)
        self.assertEqual(location, expected)

    def test_random_frames_match_opencv_with_and_without_exact_target(self):
        rng = np.random.default_rng(32)
        for _ in range(12):
            source = rng.integers(0, 256, (23, 31, 3), dtype=np.uint8)
            self.compare(source, source[7:15, 11:20])
            self.compare(source, rng.integers(0, 256, (8, 9, 3), dtype=np.uint8))

    def test_existing_game_frames_match_at_the_same_coordinates(self):
        frames = list((ROOT / 'tests/fixtures').rglob('*.png'))
        self.assertGreater(len(frames), 3)
        for path in frames:
            with self.subTest(frame=path.name):
                frame = cv2.imread(str(path))
                h, w = frame.shape[:2]
                source = frame[max(0,h//2-16):h//2+16, max(0,w//2-22):w//2+22]
                template = source[3:-3, 4:-4]
                if np.std(template, axis=(0,1)).max() > 0:
                    self.compare(source, template)

    def test_protocol_recovers_after_invalid_handle_and_invalid_frame(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid window handle'):
            self.client.request('capture', hwnd=0, width=20, height=10, client=True)
        with self.assertRaises(RuntimeError):
            self.client.request('match', width=20, height=10, template_width=2, template_height=2)
        self.assertEqual(self.client.request('ping')[0]['protocol'], 1)

    def test_window_capture_matches_python_gdi(self):
        import win32gui
        from types import SimpleNamespace
        sys.path.insert(0, str(ROOT / 'runtime/upstream'))
        from module.device.method.windows_impl import Window
        # A hidden test-owned window; never capture or click the user's game.
        hwnd = win32gui.CreateWindow('STATIC', 'GanganMouse capture test', 0,
                                    -10000, -10000, 23, 17, 0, 0, 0, None)
        try:
            window = SimpleNamespace(is_desktop_window=False, screenshot_size=(23,17), screenshot_handle_num=hwnd)
            expected = Window.screenshot_window_background(window)
            _, data = self.client.request('capture', hwnd=hwnd, width=23, height=17, client=False)
            actual = np.frombuffer(data, np.uint8).reshape(17,23,3)
            np.testing.assert_array_equal(actual, expected)
        finally:
            win32gui.DestroyWindow(hwnd)

    def test_adb_arguments_and_png_transport_without_touching_any_device(self):
        # Python's -s flag lets a test script stand in for adb's serial argument.
        # The real ADB executable and user devices are never invoked by this test.
        with tempfile.TemporaryDirectory(prefix='gangan native adb ') as temp:
            stub = Path(temp) / 'fake adb.py'
            stub.write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
            fields = dict(executable=sys.executable, serial=str(stub))
            _, data = self.client.request('adb', action='tap', x=123, y=456, **fields)
            self.assertEqual(json.loads(data), ['shell', 'input', 'tap', '123', '456'])
            _, data = self.client.request('adb', action='swipe', x=1, y=2,
                                         end_x=3, end_y=4, duration_ms=250, **fields)
            self.assertEqual(json.loads(data), ['shell', 'input', 'swipe', '1', '2', '3', '4', '250'])
            frame = np.arange(18, dtype=np.uint8).reshape(2,3,3)
            _, png = cv2.imencode('.png', frame)
            stub.write_text(f'import sys\nsys.stdout.buffer.write({png.tobytes()!r})\n', encoding='utf-8')
            _, data = self.client.request('adb', action='capture', **fields)
            np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), frame)
            stub.write_text('import sys\nsys.stderr.write("test ADB failure")\nsys.exit(7)\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'test ADB failure'):
                self.client.request('adb', action='tap', x=1, y=2, **fields)

    def test_python_adapter_uses_native_match_and_preserves_coordinates(self):
        code = '''
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
from native_backend import NativeClient, install_native_backend
from module.atom.image import RuleImage
calls = []
original_client = NativeClient.match_map
def observed(self, source, template, mask=None):
    calls.append(True)
    return original_client(self, source, template, mask)
NativeClient.match_map = observed
original = RuleImage.match
source = np.random.default_rng(71).integers(0, 256, (23,31,3), dtype=np.uint8)
rule = RuleImage((0,0,9,8), (0,0,31,23), 'Template matching', 0.8, 'native-test.png')
rule.roi_back = [0,0,31,23]
rule._image = source[7:15,11:20].copy()
assert original(rule, source)
reference = rule.last_score
install_native_backend()
assert rule.match(source)
assert calls == [True]
assert rule.roi_front[:2] == [11,7]
assert abs(reference-rule.last_score) < 0.0001
from native_ocr import NativeTextSystem
from module.ocr.models import get_ocr_model
assert isinstance(get_ocr_model(), NativeTextSystem)
'''
        env = dict(os.environ, GANGAN_NATIVE_EXE=os.environ['GANGAN_TEST_NATIVE'], GANGAN_NATIVE_MODE='rust')
        result = subprocess.run([sys.executable, '-c', code, str(ROOT/'bridge')],
            cwd=ROOT/'runtime/upstream', env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mumu_sdk_uses_requested_instance_and_keeps_logs_off_protocol(self):
        compiler = ROOT/'.build-tools/w64devkit/bin/gcc.exe'
        compiler = str(compiler) if compiler.is_file() else shutil.which('gcc')
        if not compiler:
            self.skipTest('requires the native OCR build compiler')
        with tempfile.TemporaryDirectory(prefix='gangan mumu sdk ') as temp:
            directory = Path(temp)/'shell/sdk'
            directory.mkdir(parents=True)
            source = Path(temp)/'sdk.c'
            source.write_text('''
#include <stdio.h>
#include <wchar.h>
#include <windows.h>
__declspec(dllexport) int nemu_connect(const wchar_t *folder,int instance) {
    printf("test SDK diagnostic\\n"); fflush(stdout); return instance+7;
}
__declspec(dllexport) int nemu_disconnect(int id) { return 0; }
__declspec(dllexport) int nemu_capture_display(int id,int display,int len,int *w,int *h,unsigned char *data) {
    if(display==99) Sleep(2000);
    *w=3; *h=2;
    if(len) { for(int i=0;i<24;i++) data[i]=(unsigned char)(i+id+display); }
    return 0;
}
''', encoding='utf-8')
            subprocess.run([compiler,'-shared','-o',str(directory/'external_renderer_ipc.dll'),str(source)],check=True,capture_output=True)
            client = NativeClient(os.environ['GANGAN_TEST_NATIVE'])
            try:
                reply,data = client.request('nemu_capture',folder=temp,instance=1,display=2)
                self.assertEqual((reply['width'],reply['height']),(3,2))
                self.assertEqual(data,bytes(range(10,34)))
                self.assertEqual(client.request('ping')[0]['protocol'],1)
                with self.assertRaisesRegex(RuntimeError,'timed out'):
                    client.request('nemu_capture',folder=temp,instance=1,display=99,timeout=0.05)
                self.assertEqual(client.request('ping')[0]['protocol'],1)
            finally:
                client.close()

    def test_desktop_press_messages_only_reach_test_owned_window(self):
        import win32gui
        import win32con
        handles = queue.Queue()
        messages = []
        complete = threading.Event()
        def window_thread():
            def callback(hwnd,msg,wparam,lparam):
                if msg in (win32con.WM_LBUTTONDOWN,win32con.WM_LBUTTONUP,win32con.WM_CAPTURECHANGED,win32con.WM_MOUSEMOVE):
                    messages.append((msg,wparam,lparam))
                    if msg == win32con.WM_MOUSEMOVE:
                        complete.set()
                    return 0
                if msg == win32con.WM_CLOSE:
                    win32gui.DestroyWindow(hwnd)
                    return 0
                if msg == win32con.WM_DESTROY:
                    win32gui.PostQuitMessage(0)
                    return 0
                return win32gui.DefWindowProc(hwnd,msg,wparam,lparam)
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = f'GanganNativePress{os.getpid()}'
            wc.lpfnWndProc = callback
            atom = win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindow(atom,'Native press test',0,-10000,-10000,30,20,0,0,0,None)
            handles.put(hwnd)
            try:
                win32gui.PumpMessages()
            finally:
                win32gui.UnregisterClass(atom,0)
        thread = threading.Thread(target=window_thread,daemon=True)
        thread.start()
        hwnd = handles.get(timeout=5)
        try:
            self.client.request('desktop_press',hwnd=hwnd,x=12,y=9,seconds=0.01)
            self.assertTrue(complete.wait(2))
            self.assertEqual([m[0] for m in messages],
                [win32con.WM_LBUTTONDOWN,win32con.WM_LBUTTONUP,win32con.WM_CAPTURECHANGED,win32con.WM_MOUSEMOVE])
            self.assertEqual(messages[0][1:],(1,(9<<16)|12))
            self.assertEqual(messages[1][1:],(0,(9<<16)|12))
        finally:
            win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
            thread.join(timeout=3)
