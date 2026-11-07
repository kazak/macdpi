import json
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class NetworkRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.module = ModuleType('setup_under_test')
        self.module.__file__ = str(ROOT / 'Resources/setup.py')
        exec(compile((ROOT / 'Resources/setup.py').read_text(), self.module.__file__, 'exec'), self.module.__dict__)
        self.module.SOURCE = ROOT / 'dist/Tube Switch.app/Contents/Resources'
        self.module.APP = root / 'app'
        self.module.PLIST = root / 'agents/test.plist'
        self.module.STATE = self.module.APP / 'previous-network.json'
        self.previous = {'service': 'Wi-Fi', 'url': 'http://example.test/original.pac',
                         'enabled': True, 'discovery': True}

    def tearDown(self):
        self.temporary.cleanup()

    def test_partial_enable_failure_restores_original_network(self):
        m = self.module
        calls = []
        failed = [False]

        def change(*args):
            calls.append(args)
            if args == ('-setautoproxystate', 'Wi-Fi', 'on') and not failed[0]:
                failed[0] = True
                raise RuntimeError('injected failure after changing PAC URL')

        with patch.object(m, 'find_service', return_value='Wi-Fi'), \
             patch.object(m, 'snapshot', return_value=self.previous), \
             patch.object(m, 'set_network', side_effect=change), \
             patch.object(m, 'run'), patch.object(m, 'wait_ready'), patch.object(m, 'record'), \
             patch.object(m.subprocess, 'run'):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                m.enable()
        self.assertIn(('-setautoproxyurl', 'Wi-Fi', self.previous['url']), calls)
        self.assertIn(('-setproxyautodiscovery', 'Wi-Fi', 'on'), calls)
        self.assertFalse(m.PLIST.exists())
        self.assertEqual(json.loads(m.STATE.read_text()), self.previous)

    def test_declined_admin_request_changes_no_network_setting(self):
        m = self.module
        with patch.object(m, 'find_service', return_value='Wi-Fi'), \
             patch.object(m, 'snapshot', return_value=self.previous), patch.object(m, 'set_network') as change, \
             patch.object(m.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'sudo')):
            with self.assertRaises(subprocess.CalledProcessError):
                m.enable()
        change.assert_not_called()
        self.assertFalse(m.PLIST.exists())

    def test_existing_manual_proxy_is_not_replaced(self):
        values = {'-getautoproxyurl': 'URL: (null)\nEnabled: No\n',
                  '-getproxyautodiscovery': 'Auto Proxy Discovery: Off\n',
                  '-getwebproxy': 'Enabled: Yes\n'}
        with patch.object(self.module, 'network', side_effect=lambda option, service: values[option]):
            with self.assertRaisesRegex(RuntimeError, 'ручной прокси'):
                self.module.snapshot('Wi-Fi')


if __name__ == '__main__':
    unittest.main()
