#!/usr/bin/env python3
"""Build, sign and package the macOS Universal app from pinned local source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
DIST = ROOT / 'dist'


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    names = ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'VERSION', '.gitignore']
    files = [ROOT / name for name in names]
    for directory in ['Sources', 'Resources', 'scripts', 'tests', 'vendor', 'docs', '.github']:
        files += [p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return sorted(files)


def zip_source(destination, prefix=''):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, arcname=prefix + str(path.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--notarize-profile', help='Existing notarytool Keychain profile')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('A macOS host with Xcode Command Line Tools is required')
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('VERSION must contain a three-part numeric version')
    identity = os.environ.get('CODESIGN_IDENTITY', '-')
    if args.notarize_profile and identity == '-':
        parser.error('Notarization requires CODESIGN_IDENTITY with Developer ID Application')
    BUILD.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    vendor = json.loads((ROOT / 'vendor/manifest.json').read_text())
    archive = ROOT / 'vendor' / vendor['archive']
    if sha256(archive) != vendor['sha256']:
        raise RuntimeError('Vendored tpws source checksum mismatch')
    extracted = BUILD / 'vendor-source'
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise RuntimeError('Unsafe path in source archive')
        tar.extractall(extracted)
    run('/usr/bin/make', '-C', extracted / 'tpws', 'mac')
    for architecture in ['arm64', 'x86_64']:
        run('/usr/bin/xcrun', 'swiftc', ROOT / 'Sources/main.swift', ROOT / 'Sources/ManagedWorker.swift', '-O',
            '-target', architecture + '-apple-macos13.0',
            '-module-cache-path', BUILD / 'swift-cache', '-framework', 'AppKit',
            '-framework', 'SystemConfiguration', '-o', BUILD / ('TubeSwitch-' + architecture))
    app = DIST / 'Tube Switch.app'
    if app.exists():
        shutil.rmtree(app)
    contents = app / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    run('/usr/bin/lipo', '-create', BUILD / 'TubeSwitch-arm64', BUILD / 'TubeSwitch-x86_64',
        '-output', contents / 'MacOS/TubeSwitch')
    shutil.copy2(extracted / 'tpws/tpws', resources / 'tpws')
    shutil.copy2(ROOT / 'Sources/agent.py', resources / 'agent.py')
    for path in (ROOT / 'Resources').iterdir():
        if path.is_file():
            shutil.copy2(path, resources / path.name)
    for name in ['Включить.command', 'Отключить.command', 'tpws']:
        (resources / name).chmod(0o755)
    shutil.copy2(ROOT / 'vendor/ZAPRET-LICENSE.txt', resources / 'LICENSE.txt')
    shutil.copy2(ROOT / 'LICENSE', resources / 'APP-LICENSE.txt')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', resources / 'THIRD_PARTY_NOTICES.md')
    shutil.copy2(archive, resources / 'tpws-source.tar.gz')
    metadata = ('Tube Switch ' + version + '\nSource engine: ' + vendor['source'] +
                '\nCommit: ' + vendor['commit'] + '\nSource archive SHA256: ' + vendor['sha256'] +
                '\nBuilt tpws SHA256: ' + sha256(resources / 'tpws') + '\n')
    (resources / 'SOURCE.txt').write_text(metadata)
    zip_source(resources / 'AppSource.zip')
    info = {
        'CFBundleIdentifier': 'local.tube-switch', 'CFBundleName': 'Tube Switch',
        'CFBundleDisplayName': 'Tube Switch', 'CFBundleExecutable': 'TubeSwitch',
        'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': version,
        'CFBundleVersion': ''.join(version.split('.')), 'CFBundleIconFile': 'AppIcon',
        'LSUIElement': True, 'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True, 'NSAllowsArbitraryLoads': True},
        'NSHumanReadableCopyright': 'Tube Switch contributors; tpws: bol-van and contributors'
    }
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump(info, f)
    signing = ['/usr/bin/codesign', '--force', '--deep', '--sign', identity]
    if identity != '-':
        signing += ['--options', 'runtime', '--timestamp']
    run(*signing, app)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    run(contents / 'MacOS/TubeSwitch', '--check-bundle')
    application_zip = DIST / ('Tube-Switch-' + version + '-universal.zip')
    if application_zip.exists():
        application_zip.unlink()
    run('/usr/bin/ditto', '-c', '-k', '--keepParent', app, application_zip)
    if args.notarize_profile:
        run('/usr/bin/xcrun', 'notarytool', 'submit', application_zip,
            '--keychain-profile', args.notarize_profile, '--wait')
        run('/usr/bin/xcrun', 'stapler', 'staple', app)
        application_zip.unlink()
        run('/usr/bin/ditto', '-c', '-k', '--keepParent', app, application_zip)
    source_zip = DIST / ('Tube-Switch-' + version + '-source.zip')
    zip_source(source_zip, prefix='Tube-Switch/')
    sums = ''.join(sha256(path) + '  ' + path.name + '\n' for path in [application_zip, source_zip])
    (DIST / 'SHA256SUMS.txt').write_text(sums)
    (DIST / 'BUILD-INFO.json').write_text(json.dumps({
        'version': version, 'architectures': ['arm64', 'x86_64'],
        'tpws_commit': vendor['commit'], 'tpws_source_sha256': vendor['sha256'],
        'developer_id_signed': identity != '-', 'notarized': bool(args.notarize_profile)
    }, indent=2) + '\n')
    print('\nRelease artifacts:', DIST)


if __name__ == '__main__':
    main()
