"""Retain notices for the Windows crates when compiler caches are not shipped."""
import argparse
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'licenses/Rust-dependencies.txt'
UPSTREAM = ROOT / 'licenses/upstream'
EGUI = {'ecolor', 'eframe', 'egui', 'egui-winit', 'egui_glow', 'emath', 'epaint', 'epaint_default_fonts'}


def collect():
    homes = [Path(os.environ['CARGO_HOME'])] if os.environ.get('CARGO_HOME') else []
    homes += [ROOT / '.build-tools/cargo', Path.home() / '.cargo']
    registry = next(home / 'registry/src' for home in homes if (home / 'registry/src').is_dir())
    tree = subprocess.check_output(['cargo', 'tree', '--locked', '--offline', '--target',
                                   'x86_64-pc-windows-gnu', '--edges', 'normal,build',
                                   '--prefix', 'none', '--format', '{p}'], cwd=ROOT).decode('utf-8')
    packages = sorted(set(match.groups() for line in tree.splitlines()
                          if (match := re.match(r'([\w-]+) v([^ ]+)', line))))
    chunks = ['Rust dependency notices for the Windows desktop executable.\n'
              'The application is GPL-3.0-only; dependency notices are retained below.\n']
    for name, version in packages:
        if name == 'gangan-mouse':
            continue
        directory = next(registry.glob('*/' + name + '-' + version))
        manifest = (directory / 'Cargo.toml').read_text('utf-8')
        metadata = []
        for key in ('license', 'repository'):
            match = re.search(r'^' + key + r'\s*=\s*(.+)$', manifest, re.MULTILINE)
            if match:
                metadata.append(key + ': ' + match.group(1))
        authors = re.search(r'^authors\s*=\s*(\[.*?\])', manifest, re.MULTILINE | re.DOTALL)
        if authors:
            metadata.append('authors: ' + authors.group(1))
        files = sorted((p for p in directory.iterdir() if p.is_file() and
                        p.name.lower().startswith(('license', 'licence', 'copying', 'notice', 'unlicense'))), key=lambda p: p.name)
        if not files:
            if name in EGUI:
                files = [UPSTREAM / 'egui-LICENSE-MIT.txt', UPSTREAM / 'egui-LICENSE-APACHE.txt']
            elif name in ('gl_generator', 'khronos_api', 'clipboard-win'):
                files = [UPSTREAM / (name + '-LICENSE.txt')]
            elif name == 'winapi-x86_64-pc-windows-gnu':
                parent = next(registry.glob('*/winapi-0.3.9'))
                files = sorted(parent.glob('LICENSE-*'))
        if not files:
            raise ValueError('No retained license text for ' + name + ' ' + version)
        chunks.append('\n' + '=' * 72 + '\n' + name + ' ' + version + '\n' + '\n'.join(metadata) + '\n')
        for path in files:
            chunks.append('\n--- ' + path.name + ' ---\n' + path.read_text('utf-8', errors='replace'))
    chunks.append('\n' + '=' * 72 + '\nEmbedded default font licenses and upstream references\n')
    for path in sorted(UPSTREAM.glob('font-*')):
        chunks.append('\n--- ' + path.name + ' ---\n' + path.read_text('utf-8'))
    chunks.append('\n' + (UPSTREAM / 'SOURCES.txt').read_text('utf-8'))
    return '\n'.join(chunks).replace('\r\n', '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = collect()
    if args.check:
        if OUTPUT.read_text('utf-8') != content:
            raise SystemExit('Rust dependency notices changed; regenerate licenses/Rust-dependencies.txt')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
    print('Rust dependency and embedded font notices verified.')
