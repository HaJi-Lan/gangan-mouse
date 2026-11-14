"""Download an exact-commit Windows binary; no Rust compiler is used on clients."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import uuid

DEFAULT_DOWNLOADS = 'https://github.com/HaJi-Lan/gangan-mouse/releases/download/desktop-builds/'
MAX_BINARY = 64 * 1024 * 1024


def prune(directory, pattern, preserve=None, keep=2):
    try:
        files = sorted((p for p in Path(directory).glob(pattern) if p.is_file() and p != preserve),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in files[max(0, keep - int(preserve is not None)):]:
            try:
                old.unlink()
            except OSError:
                pass  # A still-running previous EXE can be removed on a later update.
    except OSError:
        pass


def executable_name(commit):
    if not isinstance(commit, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit):
        raise ValueError('版本提交编号无效')
    return f'GanganMouse-{commit}-Windows-x64.exe'


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def check_executable(path):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b'MZ':
            raise ValueError('下载文件不是 Windows 程序')
        offset = struct.unpack_from('<I', header, 60)[0]
        if offset > MAX_BINARY - 6:
            raise ValueError('Windows 程序头无效')
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0\x64\x86':
            raise ValueError('下载文件不是 Windows x64 程序')


def validate_manifest(manifest, commit):
    if not isinstance(manifest, dict) or manifest.get('schema') != 1 or manifest.get('commit') != commit:
        raise ValueError('程序清单与目标源码版本不匹配')
    if manifest.get('file') != executable_name(commit) or manifest.get('platform') != 'windows-x64':
        raise ValueError('程序清单的文件名或平台不匹配')
    size = manifest.get('size')
    if type(size) is not int or not 64 <= size <= MAX_BINARY:
        raise ValueError('程序清单的文件大小无效')
    if not isinstance(manifest.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', manifest['sha256']):
        raise ValueError('程序清单的 SHA-256 无效')


def download_session(base):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    session = requests.Session()
    # Resolve Windows/environment proxy settings once and keep that route across
    # GitHub's redirects to its asset CDN. Registry proxy values can omit schemes.
    session.trust_env = False
    session.proxies = {key: requests.utils.prepend_scheme_if_needed(value, 'http')
                       for key, value in requests.utils.get_environ_proxies(base).items()
                       if key in ('http', 'https', 'all') and value}
    session.verify = os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or True
    session.headers['User-Agent'] = 'GanganMouse-Updater/1.0'
    retry = HTTPAdapter(max_retries=Retry(total=2, connect=2, read=0, backoff_factor=0.5,
                                          status_forcelist=(502, 503, 504), allowed_methods=('GET',)))
    session.mount('https://', retry)
    session.mount('http://', retry)
    return session


def stage(root, commit, downloads=DEFAULT_DOWNLOADS, session=None):
    import requests
    name = executable_name(commit)
    base = downloads.rstrip('/') + '/'
    session = session or download_session(base)
    directory = Path(root) / 'runtime' / 'prebuilt'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    temporary = directory / (name + '.' + uuid.uuid4().hex + '.part')
    try:
        with session.get(base + name[:-4] + '.json', timeout=(20, 90), stream=True) as response:
            if response.status_code == 404:
                raise ValueError('该版本的预编译程序尚未发布，请稍后再更新或检查程序下载源')
            if response.status_code != 200:
                raise ValueError(f'无法获取程序清单（HTTP {response.status_code}）')
            content = bytearray()
            for block in response.iter_content(8192):
                content.extend(block)
                if len(content) > 65536:
                    raise ValueError('程序清单过大')
            manifest = json.loads(content)
        validate_manifest(manifest, commit)
        if target.is_file() and target.stat().st_size == manifest['size'] and file_hash(target) == manifest['sha256']:
            check_executable(target)
            prune(directory, 'GanganMouse-*-Windows-x64.exe', preserve=target)
            return target
        digest = hashlib.sha256()
        size = 0
        with session.get(base + name, timeout=(20, 90), stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f'无法下载预编译程序（HTTP {response.status_code}）')
            with temporary.open('xb') as stream:
                for block in response.iter_content(1024 * 1024):
                    size += len(block)
                    if size > manifest['size']:
                        raise ValueError('下载程序大小超出清单')
                    digest.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
        if size != manifest['size'] or digest.hexdigest() != manifest['sha256']:
            raise ValueError('预编译程序大小或 SHA-256 校验失败，已保留当前程序')
        check_executable(temporary)
        os.replace(temporary, target)
        prune(directory, 'GanganMouse-*-Windows-x64.exe', preserve=target)
        return target
    except requests.RequestException as error:
        raise ValueError(f'更新连接失败（{type(error).__name__}），当前程序已保留') from None
    finally:
        temporary.unlink(missing_ok=True)


def install(root, staged):
    directory = Path(root) / 'runtime' / 'previous-executables'
    directory.mkdir(parents=True, exist_ok=True)
    target = Path(root) / 'GanganMouse.exe'
    pending = directory / (uuid.uuid4().hex + '.pending.exe')
    previous = directory / (uuid.uuid4().hex + '.exe')
    check_executable(staged)
    shutil.copyfile(staged, pending)
    existed = target.exists()
    try:
        if existed:
            os.replace(target, previous)
        try:
            os.replace(pending, target)
        except OSError:
            if existed:
                os.replace(previous, target)
            raise
    finally:
        pending.unlink(missing_ok=True)
    prune(directory, '*.exe', preserve=previous if existed else None)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--downloads')
    parser.add_argument('--stage-only', action='store_true')
    args = parser.parse_args()
    downloads = args.downloads
    if downloads is None:
        settings = args.root / 'runtime/update-source.json'
        downloads = json.loads(settings.read_text('utf-8'))['downloads'] if settings.is_file() else DEFAULT_DOWNLOADS
    staged = stage(args.root, args.commit, downloads)
    target = staged if args.stage_only else install(args.root, staged)
    print(json.dumps({'path': str(target), 'commit': args.commit}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
