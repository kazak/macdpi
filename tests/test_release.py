import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_migration_cannot_select_unrelated_processes(self):
        binary = ROOT / 'build/migration-check'
        subprocess.run(['/usr/bin/xcrun', 'swiftc', str(ROOT / 'Sources/ManagedWorker.swift'),
                        str(ROOT / 'tests/migration_check.swift'), '-module-cache-path',
                        str(ROOT / 'build/test-swift-cache'), '-o', str(binary)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run([str(binary)], check=True, stdout=subprocess.PIPE)

    def test_runtime_has_no_removed_service_or_personal_paths(self):
        files = [ROOT / 'Sources/main.swift', ROOT / 'Sources/agent.py',
                 ROOT / 'Resources/setup.py', ROOT / 'Resources/service-presets.json']
        for path in files:
            text = path.read_text().lower()
            for forbidden in ['instagram', 'cdninstagram', 'fbcdn.net', 'ig.me', '/users/nick/']:
                self.assertNotIn(forbidden, text, str(path))

    def test_catalog_defaults_and_udp_limitation(self):
        items = json.loads((ROOT / 'Resources/service-presets.json').read_text())
        self.assertEqual([item['id'] for item in items if item['defaultEnabled']], ['youtube', 'discord'])
        discord = next(item for item in items if item['id'] == 'discord')
        self.assertIn('UDP', discord['note'])
        all_domains = [domain for item in items for domain in item['domains']]
        self.assertNotIn('cloudflare.com', all_domains)
        self.assertNotIn('cloudfront.net', all_domains)

    def test_upstream_archive_checksum(self):
        manifest = json.loads((ROOT / 'vendor/manifest.json').read_text())
        self.assertEqual(hashlib.sha256((ROOT / 'vendor' / manifest['archive']).read_bytes()).hexdigest(),
                         manifest['sha256'])

    def test_app_version_architectures_and_resources(self):
        app = ROOT / 'dist/Tube Switch.app/Contents'
        with (app / 'Info.plist').open('rb') as f:
            info = plistlib.load(f)
        self.assertEqual(info['CFBundleShortVersionString'], (ROOT / 'VERSION').read_text().strip())
        binary = app / 'MacOS/TubeSwitch'
        architectures = subprocess.check_output(['/usr/bin/lipo', '-archs', str(binary)], text=True)
        self.assertEqual(set(architectures.split()), {'arm64', 'x86_64'})
        subprocess.run([str(binary), '--check-bundle'], check=True, stdout=subprocess.PIPE)
        self.assertEqual((app / 'Resources/service-presets.json').read_bytes(),
                         (ROOT / 'Resources/service-presets.json').read_bytes())

    def test_release_archives_exclude_local_state_and_match_checksums(self):
        for line in (ROOT / 'dist/SHA256SUMS.txt').read_text().splitlines():
            digest, filename = line.split('  ')
            path = ROOT / 'dist' / filename
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            with zipfile.ZipFile(path) as z:
                self.assertIsNone(z.testzip())
                for name in z.namelist():
                    self.assertNotIn(Path(name).name, ['control-token', 'mode.json', 'previous-network.json', 'service.log'])
                    self.assertNotIn('/.git/', '/' + name)
        version = (ROOT / 'VERSION').read_text().strip()
        with zipfile.ZipFile(ROOT / 'dist' / ('Tube-Switch-' + version + '-source.zip')) as z:
            for name in ['README.md', 'LICENSE', 'Sources/agent.py', 'tests/test_backend.py', '.github/workflows/release.yml', 'vendor/manifest.json']:
                self.assertIn('Tube-Switch/' + name, z.namelist())


if __name__ == '__main__':
    unittest.main()
