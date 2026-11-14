"""Statically verify Windows PE imports before executing a build tool or release."""
import argparse
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.build-tools/python-packages'))
import pefile


def check(path, search, seen, missing):
    path = path.resolve()
    if path in seen:
        return
    seen.add(path)
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[1, 13])
    imports = list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', []))
    for item in imports:
        name = item.dll.decode('ascii')
        if name.lower().startswith(('api-ms-', 'ext-ms-')):
            continue
        found = next((folder / name for folder in [path.parent, *search] if (folder / name).is_file()), None)
        if found is None:
            missing.add(f'{path.name}: {name}')
        elif found.parent != Path(os.environ['SystemRoot']) / 'System32':
            check(found, search, seen, missing)
    pe.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('--search', action='append', type=Path, default=[])
    args = parser.parse_args()
    missing = set()
    seen = set()
    check(args.executable, [Path(os.environ['SystemRoot']) / 'System32', *args.search], seen, missing)
    print(f'Checked {len(seen)} local PE files')
    for item in sorted(missing):
        print('MISSING:', item)
    raise SystemExit(bool(missing))
