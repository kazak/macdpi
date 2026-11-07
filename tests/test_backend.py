import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TPWS = Path(os.environ.get('TPWS_BIN', ROOT / 'dist/Tube Switch.app/Contents/Resources/tpws'))


def unused_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copy2(ROOT / 'Sources/agent.py', self.root / 'agent.py')
        shutil.copy2(ROOT / 'Resources/service-presets.json', self.root / 'service-presets.json')
        shutil.copy2(TPWS, self.root / 'tpws-real')
        # The wrapper records only this test's child PID, avoiding inspection of unrelated processes.
        (self.root / 'tpws').write_text('#!/usr/bin/python3\nimport os, pathlib, sys\n'
            + 'pathlib.Path(' + repr(str(self.root / 'engine.pid')) + ').write_text(str(os.getpid()))\n'
            + 'os.execv(' + repr(str(self.root / 'tpws-real')) + ', ["tpws"] + sys.argv[1:])\n')
        (self.root / 'tpws').chmod(0o755)
        self.socks_port, self.http_port = unused_port(), unused_port()
        while self.socks_port == self.http_port:
            self.http_port = unused_port()
        self.env = dict(os.environ, YT_BYPASS_SOCKS_PORT=str(self.socks_port),
                        YT_BYPASS_HTTP_PORT=str(self.http_port))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.log = (self.root / 'test.log').open('w')
        self.process = None

    def tearDown(self):
        self.stop()
        self.log.close()
        self.temporary.cleanup()

    def start(self):
        self.process = subprocess.Popen(['/usr/bin/python3', str(self.root / 'agent.py')],
                                        env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(80):
            if self.process.poll() is not None:
                self.fail('Service exited: ' + (self.root / 'test.log').read_text())
            try:
                if self.request()[0] == 200:
                    self.token = (self.root / 'control-token').read_text().strip()
                    return
            except OSError:
                pass
            time.sleep(0.05)
        self.fail('Service startup timed out')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=8)

    def request(self, path='/api/status', data=None, auth=False, origin=None):
        req = urllib.request.Request('http://127.0.0.1:' + str(self.http_port) + path)
        if data is not None:
            req.data = json.dumps(data).encode()
            req.add_header('Content-Type', 'application/json')
        if auth:
            req.add_header('Authorization', 'Bearer ' + self.token)
        if origin:
            req.add_header('Origin', origin)
        try:
            with self.opener.open(req, timeout=5) as r:
                raw = r.read()
                return r.status, raw.decode() if path == '/proxy.pac' else json.loads(raw)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_legacy_mode_migrates_without_losing_disabled_state(self):
        (self.root / 'mode.json').write_text('{"enabled": false}')
        self.start()
        state = self.request()[1]
        self.assertFalse(state['enabled'])
        self.assertEqual(state['services'], ['youtube', 'discord'])
        self.assertEqual(state['api'], 2)

    def test_service_selection_updates_real_hostlist_and_survives_restart(self):
        self.start()
        code, state = self.request('/api/services', {'services': ['soundcloud']}, auth=True)
        self.assertEqual(code, 200)
        self.assertEqual(state['services'], ['soundcloud'])
        self.assertEqual(set((self.root / 'active-domains.txt').read_text().splitlines()),
                         {'soundcloud.com', 'soundcloud.cloud', 'sndcdn.com'})
        self.stop(); self.start()
        self.assertEqual(self.request()[1]['services'], ['soundcloud'])

    def test_untrusted_control_requests_cannot_change_selection(self):
        self.start()
        before = self.request()[1]
        self.assertEqual(self.request('/api/services', {'services': []})[0], 403)
        self.assertEqual(self.request('/api/services', {'services': []}, auth=True,
                                      origin='https://example.test')[0], 403)
        for invalid in (['unknown'], ['discord', 'discord'], 'discord', None, [5]):
            self.assertEqual(self.request('/api/services', {'services': invalid}, auth=True)[0], 400)
        self.assertEqual(self.request()[1], before)
        self.assertEqual((self.root / 'control-token').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request('/control-token')[0], 404)

    def test_global_off_preserves_socks_and_service_selection(self):
        self.start()
        self.request('/api/services', {'services': ['discord', 'twitch']}, auth=True)
        state = self.request('/api/enabled', {'enabled': False}, auth=True)[1]
        self.assertTrue(state['running'])
        self.assertFalse(state['enabled'])
        with socket.create_connection(('127.0.0.1', self.socks_port), timeout=2) as sock:
            sock.sendall(b'\x05\x01\x00')
            self.assertEqual(sock.recv(2), b'\x05\x00')
        self.stop(); self.start()
        self.assertFalse(self.request()[1]['enabled'])
        state = self.request('/api/enabled', {'enabled': True}, auth=True)[1]
        self.assertEqual(state['services'], ['discord', 'twitch'])
        self.assertIn('discord.com', (self.root / 'active-domains.txt').read_text().splitlines())

    def test_engine_crash_recovers_with_same_mode(self):
        self.start()
        self.request('/api/enabled', {'enabled': False}, auth=True)
        pid = int((self.root / 'engine.pid').read_text())
        os.kill(pid, signal.SIGKILL)
        for _ in range(100):
            time.sleep(0.05)
            current = int((self.root / 'engine.pid').read_text())
            state = self.request()[1]
            if current != pid and state['running']:
                break
        self.assertNotEqual(current, pid)
        self.assertTrue(state['running'])
        self.assertFalse(state['enabled'])

    def test_worker_stops_if_its_expected_owner_disappears(self):
        self.env['TUBE_SWITCH_OWNER_PID'] = str(os.getpid() + 100000000)
        self.start()
        self.process.wait(timeout=6)
        self.assertEqual(self.process.returncode, 0)

    def test_pac_uses_domain_boundaries_and_excludes_removed_service(self):
        self.start()
        pac = self.root / 'proxy.pac'
        pac.write_text(self.request('/proxy.pac')[1])
        subprocess.run(['/usr/bin/xcrun', 'swift', '-module-cache-path', str(ROOT / 'build/test-swift-cache'),
                        str(ROOT / 'tests/pac_check.swift'), str(pac)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


if __name__ == '__main__':
    unittest.main()
