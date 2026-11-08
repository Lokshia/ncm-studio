"""Build local Windows portable and source archives. Never publishes anything."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncmstudio import __version__

FILES = ['main.py', 'README.md', 'THIRD_PARTY.md', 'LICENSE', '.gitignore',
         'requirements.txt', 'requirements-dev.txt', 'requirements-lock.txt']
FOLDERS = ['ncmstudio', 'tests', 'tools', 'LICENSES', '.github']


def source_files():
    paths = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in ('.py', '.yml', '.txt', '.md'))
    return sorted(paths)


def collect_notices(app):
    target = app / 'licenses'
    target.mkdir(exist_ok=True)
    records = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        records.append({'name': name, 'version': dist.version,
                        'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                        'links': dist.metadata.get_all('Project-URL') or []})
        for item in dist.files or []:
            # Copy notices from wheel metadata, never arbitrary user files.
            parts = item.parts
            if parts and parts[0].endswith('.dist-info') and any(
                    token in item.name.lower() for token in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    dest = target / name / Path(*parts[1:])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    import _soundfile_data
    sound_license = Path(_soundfile_data.__file__).parent / 'COPYING'
    if sound_license.exists():
        shutil.copy2(sound_license, target / 'libsndfile-COPYING')
    for candidate in [Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE']:
        if candidate.exists():
            shutil.copy2(candidate, target / 'Python-LICENSE.txt')
            break
    records.append({'name': 'Python', 'version': sys.version})
    (target / 'DEPENDENCIES.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ['README.md', 'LICENSE', 'THIRD_PARTY.md']:
        shutil.copy2(ROOT / name, app / name)
    shutil.copytree(ROOT / 'LICENSES', target / 'project-notices', dirs_exist_ok=True)


def archive():
    app = ROOT / 'dist' / 'NCMStudio'
    collect_notices(app)
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    portable = release / f'NCMStudio-{__version__}-windows-x64.zip'
    source = release / f'NCMStudio-{__version__}-source.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in source_files():
            bundle.write(path, 'ncm-studio/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(app.rglob('*')):
            relative = path.relative_to(app)
            if path.is_file() and '.ncm-studio' not in relative.parts:
                bundle.write(path, 'NCMStudio/' + relative.as_posix())
        bundle.write(source, 'NCMStudio/source/' + source.name)
    sums = []
    for path in [portable, source]:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        sums.append(f'{digest}  {path.name}')
        print(f'{path.name}: {path.stat().st_size / 1024**2:.1f} MiB')
    (release / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='ascii')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive-only', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    if not args.archive_only:
        if sys.platform != 'win32':
            raise SystemExit('Build on Windows x64 with Python 3.12.')
        subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], check=True)
        # Do not pick up unrelated app DLLs (for example an old UCRT) from a developer's PATH.
        build_env = os.environ.copy()
        system_root = Path(os.environ['SystemRoot'])
        build_env['PATH'] = os.pathsep.join(map(str, [Path(sys.executable).parent, Path(sys.base_prefix),
                                                     system_root / 'System32', system_root]))
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
                        '--name', 'NCMStudio', '--noupx', '--specpath', 'build',
                        '--collect-data', '_soundfile_data', '--hidden-import', '_cffi_backend',
                        str(ROOT / 'main.py')], check=True, env=build_env)
    archive()
