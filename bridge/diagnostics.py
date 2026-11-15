"""Bounded local diagnostic bundles, with no account configuration files."""
from datetime import datetime
from pathlib import Path
import json
import re
import subprocess
import zipfile
import uuid


def save_counter_frame(task, rule):
    import cv2
    root = Path('log/ocr')
    config = str(getattr(task.config, 'config_name', 'unknown'))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', config):
        config = 'unknown'
    directory = root / config
    directory.mkdir(parents=True, exist_ok=True)
    x, y, w, h = rule.roi
    left, top = max(0, x - 12), max(0, y - 12)
    image = rule.crop(task.device.image, (left, top, w + 24, h + 24))
    filename = datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.png'
    cv2.imwrite(str(directory / filename), image)
    for path in sorted(directory.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)[20:]:
        path.unlink(missing_ok=True)


def export_logs(runtime, app_root, name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('配置名称无效')
    runtime, app_root = Path(runtime).resolve(), Path(app_root).resolve()
    output = app_root / 'runtime/diagnostics'
    output.mkdir(parents=True, exist_ok=True)
    target = output / (f'{name}-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.zip')
    files = []
    for suffix in (name, 'worker'):
        files += sorted((runtime / 'log').glob(f'*_{suffix}.txt'), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
    snippets = []
    try:
        revision = subprocess.check_output(['git', '-C', str(app_root), 'rev-parse', 'HEAD'],
            stderr=subprocess.DEVNULL, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).decode().strip()
    except (OSError, subprocess.SubprocessError):
        revision = 'unknown'
    try:
        with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('version.json', json.dumps({'revision': revision, 'config': name,
                'exported_at': datetime.now().isoformat()}, ensure_ascii=False, indent=2))
            for path in files:
                if path.is_symlink() or not path.resolve().is_relative_to(runtime):
                    continue
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 4 * 1024 * 1024))
                    data = stream.read(4 * 1024 * 1024).decode('utf-8', errors='replace')
                bundle.writestr('log/' + path.name, data)
                snippets.extend(line for line in data.splitlines() if any(key in line for key in
                    ('ERROR', 'WARNING', '突破券', 'REALM_RAID_NUMBER', 'Traceback')))
            directory = runtime / 'log/ocr' / name
            images = sorted(directory.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
            for path in images:
                if not path.is_symlink() and path.resolve().is_relative_to(runtime) and path.stat().st_size <= 1024 * 1024:
                    bundle.writestr('ocr/' + path.name, path.read_bytes())
            bundle.writestr('异常与识别摘要.txt', '\n'.join(snippets[-300:]))
        return {'path': str(target), 'config': name}
    except Exception:
        target.unlink(missing_ok=True)
        raise
