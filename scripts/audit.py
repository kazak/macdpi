#!/usr/bin/env python3
"""Check source and nested release archives without printing matched credentials."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private-or-public-key-block': rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED |SSH2 )?(?:PRIVATE|PUBLIC) KEY-----',
    'ssh-public-key': rb'(?:^|\n)\s*(?:ssh-(?:rsa|ed25519)|ecdsa-sha2-\S+)\s+[A-Za-z0-9+/]{40,}={0,3}',
    'github-credential': rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
    'cloud-access-key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'api-secret': rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}',
}
PRIVATE_NAMES = {'control-token', 'mode.json', 'previous-network.json', 'service.log',
                 '.env', 'id_rsa', 'id_ed25519', 'result.json'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.pub', '.p12', '.p8', '.keychain'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', nargs='*', type=Path, default=[])
    parser.add_argument('--compare-local-runtime', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('project_build', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    live_token = b''
    if args.compare_local_runtime:
        key = Path.home() / 'Library/Application Support/LocalYouTubeBypass/control-token'
        if key.exists():
            live_token = key.read_bytes().strip()
            if len(live_token) < 32:
                raise RuntimeError('Local runtime key is unexpectedly short')
    findings = []
    inspected = 0

    def inspect(data, name, depth=0):
        nonlocal inspected
        inspected += 1
        basename = Path(name).name
        if basename in PRIVATE_NAMES or Path(basename).suffix.lower() in PRIVATE_SUFFIXES or basename.startswith('.env.'):
            findings.append({'path': name, 'rule': 'private-runtime-or-key-file'})
        for rule, pattern in PATTERNS.items():
            if re.search(pattern, data):
                findings.append({'path': name, 'rule': rule})
        if live_token and live_token in data:
            findings.append({'path': name, 'rule': 'live-local-control-key'})
        if depth >= 4:
            return
        stream = io.BytesIO(data)
        if zipfile.is_zipfile(stream):
            with zipfile.ZipFile(stream) as archive:
                for member in archive.infolist():
                    if not member.is_dir():
                        if member.file_size > 50_000_000:
                            raise RuntimeError('Oversized archive entry')
                        inspect(archive.read(member), name + '!' + member.filename, depth + 1)
        elif data.startswith(b'\x1f\x8b'):
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                    for member in archive.getmembers():
                        if member.isfile():
                            if member.size > 50_000_000:
                                raise RuntimeError('Oversized archive entry')
                            inspect(archive.extractfile(member).read(), name + '!' + member.name, depth + 1)
            except tarfile.TarError:
                raise RuntimeError('Unreadable compressed archive')

    files = build.source_files()
    for path in files:
        inspect(path.read_bytes(), str(path.relative_to(ROOT)))
    for path in args.artifacts:
        inspect(path.read_bytes(), 'dist/' + path.name)
    print(json.dumps({'passed': not findings, 'source_files': len(files),
                      'inspected_including_archive_entries': inspected,
                      'live_runtime_key_compared': bool(live_token),
                      'findings': findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
