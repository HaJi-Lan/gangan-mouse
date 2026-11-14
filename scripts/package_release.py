"""Build the portable Windows base ZIP from a clean commit and local runtimes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://github.com/HaJi-Lan/gangan-mouse.git'


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd).decode().strip()


def copy_tree(source, destination, excluded=()):
    def ignore(directory, names):
        return [name for name in names if name in excluded or name in ('__pycache__', '.pytest_cache')
                or name.endswith(('.pyc', '.pyo'))]
    shutil.copytree(source, destination, ignore=ignore)


def stage(destination):
    if git('status', '--porcelain'):
        raise RuntimeError('Commit source changes before packaging')
    if destination.exists():
        raise RuntimeError('Stage already exists; choose a new directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', '--no-local', '--branch', 'main', str(ROOT), str(destination)], check=True)
    git('remote', 'set-url', 'origin', SOURCE, cwd=destination)
    git('config', 'credential.helper', '', cwd=destination)
    git('config', 'core.autocrlf', 'true', cwd=destination)
    # A fresh clone has no credentials, developer identity, old OAS history, or local agent files.
    shutil.copy2(ROOT / 'GanganMouse.exe', destination / 'GanganMouse.exe')
    copy_tree(ROOT / 'runtime/upstream/toolkit', destination / 'toolkit',
              ('Git', 'fluentui.dll', 'FramelessHelperCore.dll', 'FramelessHelperQuick.dll'))
    git_runtime = Path(shutil.which('git')).resolve().parents[1]
    if not (git_runtime / 'LICENSE.txt').is_file():
        raise RuntimeError('A complete Git for Windows runtime with LICENSE.txt is required')
    copy_tree(git_runtime, destination / 'toolkit/Git')
    tools = destination / '.build-tools'
    tools.mkdir()
    cargo = tools / 'cargo'
    (cargo / 'bin').mkdir(parents=True)
    for name in ('rustup.exe', 'cargo.exe', 'rustc.exe', 'rustdoc.exe'):
        shutil.copyfile(ROOT / '.build-tools/cargo/bin' / name, cargo / 'bin' / name)
    copy_tree(ROOT / '.build-tools/cargo/registry', cargo / 'registry')
    rustup = tools / 'rustup'
    rustup.mkdir()
    shutil.copy2(ROOT / '.build-tools/rustup/settings.toml', rustup / 'settings.toml')
    source = ROOT / '.build-tools/rustup/toolchains'
    # Keep the compiler, standard libraries and GNU linker; omit optional LLVM analysis tools.
    optional = {'llc.exe', 'opt.exe', 'llvm-nm.exe', 'llvm-objdump.exe', 'llvm-link.exe',
                'llvm-readobj.exe', 'llvm-profdata.exe', 'llvm-cov.exe', 'llvm-size.exe',
                'llvm-strip.exe', 'llvm-objcopy.exe'}
    copy_tree(source, rustup / 'toolchains', optional)
    copy_tree(ROOT / '.build-tools/python-packages', tools / 'python-packages')
    # Python and Git retain their bundled component licenses; Rust/crate sources retain theirs.
    (destination / 'runtime').mkdir()
    (destination / 'runtime/gangan-core-revision').write_text(git('rev-parse', 'HEAD'), encoding='ascii')
    print(json.dumps({'stage': str(destination), 'commit': git('rev-parse', 'HEAD')}, ensure_ascii=False), flush=True)


def archive(source, output):
    if git('status', '--porcelain', cwd=source):
        raise RuntimeError('Stage source differs from its commit')
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if rel.parts[:2] == ('.build-tools', 'previous-executables'):
            continue
        if path.name.startswith(('.global-cache', '.package-cache')):
            continue
        if rel.parts[0] in ('target', 'log', 'runtime') or '__pycache__' in rel.parts or path.suffix in ('.pyc', '.pyo'):
            if rel.as_posix() != 'runtime/gangan-core-revision':
                continue
        if rel.parts[0] == 'config' and path.name != 'template.json':
            continue
        if rel.parts[0] == '.git' and (rel.parts[1] in ('logs', 'hooks') or path.name in ('FETCH_HEAD', 'ORIG_HEAD', 'COMMIT_EDITMSG')):
            continue
        paths.append(path)
    assert not any('.git-credentials' in p.parts or p.name in ('credentials', 'credentials.toml', '.env') for p in paths)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True, strict_timestamps=False) as bundle:
        for index, path in enumerate(sorted(paths)):
            bundle.write(path, 'GanganMouse/' + path.relative_to(source).as_posix())
            if index % 2000 == 0:
                print(f'Packed {index}/{len(paths)} files', flush=True)
    with zipfile.ZipFile(output) as bundle:
        assert bundle.testzip() is None
        names = set(bundle.namelist())
        for required in ('GanganMouse.exe', 'README.md', 'LICENSE', '.git/HEAD',
                         'toolkit/python.exe', 'toolkit/Git/cmd/git.exe', '.build-tools/cargo/bin/cargo.exe'):
            assert 'GanganMouse/' + required in names, required
    hasher = hashlib.sha256()
    with output.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(json.dumps({'zip': str(output), 'bytes': output.stat().st_size, 'sha256': digest}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('stage', 'zip'))
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.operation == 'stage':
        stage(args.stage.resolve())
    else:
        if not args.output:
            parser.error('--output is required for zip')
        archive(args.stage.resolve(), args.output.resolve())
