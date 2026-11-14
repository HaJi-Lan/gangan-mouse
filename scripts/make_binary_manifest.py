"""Write a version-bound manifest for a prebuilt desktop executable."""
import argparse
import json
from pathlib import Path
import shutil

from install_prebuilt import check_executable, executable_name, file_hash, validate_manifest


def write_manifest(executable, commit, output):
    executable, output = Path(executable), Path(output)
    check_executable(executable)
    name = executable_name(commit)
    manifest = dict(schema=1, commit=commit, platform='windows-x64', file=name,
                    size=executable.stat().st_size, sha256=file_hash(executable))
    validate_manifest(manifest, commit)
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    if target.resolve() != executable.resolve():
        shutil.copyfile(executable, target)
    path = output / (name[:-4] + '.json')
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(write_manifest(args.exe, args.commit, args.output))
