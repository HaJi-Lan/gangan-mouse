"""Exercise real HTTP downloads, checksum failures and old-client migration."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import install_prebuilt as prebuilt
from make_binary_manifest import write_manifest


def executable():
    data = bytearray(160)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:70] = b'PE\0\0\x64\x86'
    data[80:] = b'test program'.ljust(80, b'\0')
    return bytes(data)


class PrebuiltTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gangan-prebuilt-')
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        self.binary = executable()
        self.routes = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                value = owner.routes.get(self.path)
                self.send_response(200 if value is not None else 404)
                self.send_header('Content-Length', str(len(value or b'')))
                self.end_headers()
                if value:
                    self.wfile.write(value)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/mirror/'
        self.session = requests.Session()
        self.session.trust_env = False
        self.set_manifest()
        (self.root / 'GanganMouse.exe').write_bytes(b'old program')

    def set_manifest(self, **changes):
        name = prebuilt.executable_name(self.commit)
        manifest = dict(schema=1, commit=self.commit, platform='windows-x64', file=name,
                        size=len(self.binary), sha256=hashlib.sha256(self.binary).hexdigest())
        manifest.update(changes)
        self.routes['/mirror/' + name[:-4] + '.json'] = json.dumps(manifest).encode()
        self.routes['/mirror/' + name] = self.binary

    def tearDown(self):
        self.session.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def stage(self):
        return prebuilt.stage(self.root, self.commit, self.url, self.session)

    def test_exact_commit_download_install_and_verified_cache(self):
        path = self.stage()
        self.assertEqual(path.read_bytes(), self.binary)
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), b'old program')
        self.requests.clear()
        self.assertEqual(self.stage(), path)
        self.assertEqual(len(self.requests), 1)  # Revalidate manifest; reuse verified EXE.
        prebuilt.install(self.root, path)
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), self.binary)
        backups = list((self.root / 'runtime/previous-executables').glob('*.exe'))
        self.assertEqual([p.read_bytes() for p in backups], [b'old program'])
        self.assertFalse((self.root / '.build-tools').exists())

    def test_unpublished_binary_does_not_replace_program(self):
        self.routes.clear()
        with self.assertRaisesRegex(ValueError, '尚未发布'):
            self.stage()
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), b'old program')

    def test_wrong_commit_or_platform_is_rejected_before_binary_download(self):
        for change in ({'commit': 'b' * 40}, {'platform': 'linux-x64'}, {'file': '../other.exe'}):
            with self.subTest(change=change):
                self.set_manifest(**change)
                self.requests.clear()
                with self.assertRaises(ValueError):
                    self.stage()
                self.assertEqual(len(self.requests), 1)

    def test_checksum_failure_keeps_old_program_and_removes_partial_file(self):
        self.set_manifest(sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            self.stage()
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), b'old program')
        self.assertFalse(list((self.root / 'runtime/prebuilt').glob('*')))

    def test_invalid_executable_is_rejected_even_when_hash_matches(self):
        self.binary = b'not an executable'.ljust(160, b' ')
        self.set_manifest()
        with self.assertRaisesRegex(ValueError, 'Windows 程序'):
            self.stage()

    def test_failed_replacement_restores_old_executable(self):
        path = self.stage()
        replace = os.replace

        def fail_pending(source, target):
            if str(source).endswith('.pending.exe'):
                raise OSError('simulated installation failure')
            return replace(source, target)

        with patch.object(prebuilt.os, 'replace', side_effect=fail_pending):
            with self.assertRaises(OSError):
                prebuilt.install(self.root, path)
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), b'old program')

    def test_manifest_writer_matches_download_protocol(self):
        path = self.root / 'built.exe'
        path.write_bytes(self.binary)
        manifest = write_manifest(path, self.commit, self.root / 'publish')
        data = json.loads(manifest.read_text())
        prebuilt.validate_manifest(data, self.commit)
        self.assertEqual(data['sha256'], hashlib.sha256(self.binary).hexdigest())

    def test_repeated_updates_keep_download_cache_and_backups_bounded(self):
        for character in 'abcd':
            self.commit = character * 40
            self.set_manifest()
            prebuilt.install(self.root, self.stage())
        self.assertLessEqual(len(list((self.root / 'runtime/prebuilt').glob('*.exe'))), 2)
        self.assertLessEqual(len(list((self.root / 'runtime/previous-executables').glob('*.exe'))), 2)
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), self.binary)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell compatibility entry point')
    def test_old_client_bootstrap_updates_without_rust_or_build_directory(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('build.ps1', 'install_prebuilt.py'):
            shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
        python_home = Path(sys.executable).parent
        toolkit = self.root / 'toolkit'
        toolkit.mkdir()
        shutil.copyfile(sys.executable, toolkit / 'python.exe')
        for dll in python_home.glob('*.dll'):
            shutil.copyfile(dll, toolkit / dll.name)
        git = shutil.which('git')
        subprocess.run([git, 'init', '-b', 'main'], cwd=self.root, check=True, capture_output=True)
        (self.root / 'source.txt').write_text('fixture')
        subprocess.run([git, 'add', 'source.txt'], cwd=self.root, check=True, capture_output=True)
        subprocess.run([git, '-c', 'user.name=Fixture', '-c', 'user.email=test@example.invalid',
                        'commit', '-m', 'fixture'], cwd=self.root, check=True, capture_output=True)
        self.commit = subprocess.check_output([git, 'rev-parse', 'HEAD'], cwd=self.root).decode().strip()
        self.set_manifest()
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/update-source.json').write_text(json.dumps({'downloads': self.url}))
        windows = Path(os.environ['SystemRoot'])
        env = dict(os.environ, PATH=os.pathsep.join([str(Path(git).parent), str(windows / 'System32')]),
                   PYTHONHOME=str(python_home), PYTHONUTF8='1', PYTHONIOENCODING='utf-8', NO_PROXY='127.0.0.1')
        command = [str(windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                   '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(scripts.resolve() / 'build.ps1'), '-Release']
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, timeout=60,
                                creationflags=0x08000000)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual((self.root / 'GanganMouse.exe').read_bytes(), self.binary)
        self.assertFalse((self.root / '.build-tools').exists())
        self.assertFalse((self.root / 'target').exists())
