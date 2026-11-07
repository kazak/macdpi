#!/usr/bin/python3
"""Local PAC and authenticated control API for the existing tpws service."""
import hmac
import http.server
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parent
SOCKS_PORT = int(os.environ.get('YT_BYPASS_SOCKS_PORT', '10888'))
HTTP_PORT = int(os.environ.get('YT_BYPASS_HTTP_PORT', '10889'))
OWNER_PID = int(os.environ.get('TUBE_SWITCH_OWNER_PID', '0'))
PRESETS = json.loads((ROOT / 'service-presets.json').read_text())
SERVICE_IDS = [item['id'] for item in PRESETS]
DEFAULT_SERVICES = [item['id'] for item in PRESETS if item['defaultEnabled']]
DOMAINS = sorted({domain for item in PRESETS for domain in item['domains']})
PAC = '''function FindProxyForURL(url, host) {
  host = host.toLowerCase();
  var domains = %s;
  for (var i = 0; i < domains.length; i++) {
    var d = domains[i];
    if (host === d || host.slice(-(d.length + 1)) === "." + d) {
      return "SOCKS5 127.0.0.1:%d; SOCKS 127.0.0.1:%d; DIRECT";
    }
  }
  return "DIRECT";
}
''' % (json.dumps(DOMAINS), SOCKS_PORT, SOCKS_PORT)


def private_write(path, text):
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))


def read_token():
    path = ROOT / 'control-token'
    try:
        token = path.read_text().strip()
        if len(token) == 64:
            os.chmod(str(path), 0o600)
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)
    private_write(path, token)
    return token


class Proxy:
    def __init__(self):
        self.lock = threading.RLock()
        self.child = None
        self.enabled = True
        self.services = DEFAULT_SERVICES[:]
        try:
            saved = json.loads((ROOT / 'mode.json').read_text())
            if not isinstance(saved, dict):
                raise ValueError('Invalid saved settings')
            if type(saved.get('enabled')) is bool:
                self.enabled = saved['enabled']
            if isinstance(saved.get('services'), list):
                self.services = [sid for sid in SERVICE_IDS if sid in saved['services']]
        except (OSError, ValueError, KeyError):
            pass
        self.start(self.enabled, self.services)

    def stop_child(self):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()

    def start(self, enabled, services):
        args = [str(ROOT / 'tpws'), '--bind-addr=127.0.0.1', '--port=' + str(SOCKS_PORT),
                '--socks', '--debug=0', '--maxconn=512']
        domains = sorted({domain for item in PRESETS if item['id'] in services for domain in item['domains']})
        if enabled and domains:
            private_write(ROOT / 'active-domains.txt', '\n'.join(domains) + '\n')
            args += ['--hostlist=' + str(ROOT / 'active-domains.txt'), '--ipcache-hostname=1',
                     '--split-pos=1,midsld', '--disorder']
        self.child = subprocess.Popen(args)
        for _ in range(60):
            if self.child.poll() is not None:
                raise RuntimeError('tpws завершился при запуске')
            try:
                with socket.create_connection(('127.0.0.1', SOCKS_PORT), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        self.stop_child()
        raise RuntimeError('tpws не открыл локальный порт')

    def set_config(self, enabled=None, services=None):
        with self.lock:
            previous = (self.enabled, self.services[:])
            enabled = self.enabled if enabled is None else enabled
            services = self.services[:] if services is None else services
            if (enabled, services) == previous and self.child.poll() is None:
                return
            self.stop_child()
            try:
                self.start(enabled, services)
                private_write(ROOT / 'mode.json', json.dumps({'enabled': enabled, 'services': services}))
                self.enabled = enabled
                self.services = services[:]
            except Exception:
                self.stop_child()
                self.start(*previous)
                self.enabled, self.services = previous
                raise

    def status(self):
        with self.lock:
            return {'running': self.child is not None and self.child.poll() is None,
                    'enabled': self.enabled, 'services': self.services[:], 'api': 2}

    def close(self):
        with self.lock:
            self.stop_child()


def main():
    token = read_token()
    proxy = Proxy()
    stopped = threading.Event()

    def watchdog():
        while not stopped.wait(2):
            if OWNER_PID > 1 and os.getppid() != OWNER_PID:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            with proxy.lock:
                if proxy.child.poll() is not None:
                    try:
                        proxy.start(proxy.enabled, proxy.services)
                    except Exception:
                        print('Не удалось перезапустить tpws; следующая попытка через 2 секунды.', flush=True)

    threading.Thread(target=watchdog, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get('Host', '') in (
                '127.0.0.1:' + str(HTTP_PORT), 'localhost:' + str(HTTP_PORT))

        def reply(self, status, body, mime='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, {'error': 'host'})
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == '/proxy.pac':
                self.reply(200, PAC.encode(), 'application/x-ns-proxy-autoconfig')
            elif path in ('/health', '/api/status'):
                self.reply(200, proxy.status())
            else:
                self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if not self.valid_host() or not hmac.compare_digest(
                    self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.reply(403, {'error': 'unauthorized'})
                return
            if self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.reply(403, {'error': 'browser origin'})
                return
            if self.path not in ('/api/enabled', '/api/services'):
                self.reply(404, {'error': 'not found'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 256:
                    raise ValueError('length')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('object required')
                if self.path == '/api/enabled':
                    if type(data.get('enabled')) is not bool:
                        raise ValueError('enabled must be boolean')
                    change = {'enabled': data['enabled']}
                else:
                    services = data.get('services')
                    if (not isinstance(services, list) or any(type(s) is not str or s not in SERVICE_IDS for s in services)
                            or len(services) != len(set(services))):
                        raise ValueError('unknown service')
                    change = {'services': [sid for sid in SERVICE_IDS if sid in services]}
            except (ValueError, TypeError):
                self.reply(400, {'error': 'invalid request'})
                return
            try:
                proxy.set_config(**change)
                self.reply(200, proxy.status())
            except Exception:
                self.reply(500, {'error': 'Не удалось изменить режим'})

        def log_message(self, *args):
            pass

    try:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), Handler)
        server.daemon_threads = True
        def stop(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.25)
        server.server_close()
    finally:
        stopped.set()
        proxy.close()


if __name__ == '__main__':
    main()
