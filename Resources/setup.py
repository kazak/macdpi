#!/usr/bin/python3
"""Install a per-user local proxy; save and restore the selected network service's PAC."""
import json
import os
import pathlib
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

SOURCE = pathlib.Path(__file__).resolve().parent
APP = pathlib.Path.home() / 'Library/Application Support/LocalYouTubeBypass'
LABEL = 'local.youtube-bypass'
PLIST = pathlib.Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
STATE = APP / 'previous-network.json'
PAC_URL = 'http://127.0.0.1:10889/proxy.pac'
DOMAIN = 'gui/' + str(os.getuid())


def run(*args, check=True):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'Не удалось выполнить команду')
    return p.stdout


def network(*args):
    return run('/usr/sbin/networksetup', *args)


def set_network(*args):
    return run('/usr/bin/sudo', '/usr/sbin/networksetup', *args)


def find_service():
    listing = network('-listnetworkserviceorder')
    services = []
    name = None
    for line in listing.splitlines():
        m = re.match(r'^\(\d+\) (.+)$', line)
        if m:
            name = m.group(1)
        device = re.search(r'Device: ([^)]+)', line)
        if name and device and not name.startswith('*'):
            services.append((name, device.group(1)))
            name = None
    # Global IPv4 state normally identifies the physical service even with a VPN active.
    state = subprocess.run(['/usr/sbin/scutil'], input='show State:/Network/Global/IPv4\nquit\n',
                           text=True, stdout=subprocess.PIPE).stdout
    match = re.search(r'PrimaryInterface\s*:\s*(\S+)', state)
    if not match:
        route = run('/sbin/route', '-n', 'get', 'default', check=False)
        match = re.search(r'interface:\s*(\S+)', route)
    if match:
        for service, device in services:
            if device == match.group(1):
                return service
    connected = []
    for service, device in services:
        info = network('-getinfo', service)
        if re.search(r'^IP address: (?!none|0\.0\.0\.0)\d+\.\d+\.\d+\.\d+', info, re.M):
            connected.append(service)
    if len(connected) == 1:
        return connected[0]
    raise RuntimeError('Не удалось однозначно определить подключение. Подключитесь к Wi-Fi и повторите.')


def snapshot(service):
    text = network('-getautoproxyurl', service)
    url = re.search(r'^URL:\s*(.*)$', text, re.M)
    enabled = re.search(r'^Enabled:\s*(.*)$', text, re.M)
    discovery = network('-getproxyautodiscovery', service)
    for option in ('-getwebproxy', '-getsecurewebproxy', '-getsocksfirewallproxy'):
        if re.search(r'^Enabled:\s*Yes', network(option, service), re.M | re.I):
            raise RuntimeError('У подключения уже есть ручной прокси. Автоматическая замена отменена.')
    return {'service': service, 'url': url.group(1).strip() if url else '',
            'enabled': bool(enabled and enabled.group(1).strip().lower() == 'yes'),
            'discovery': bool(re.search(r':\s*On\s*$', discovery, re.I))}


def restore(previous):
    service = previous['service']
    if previous['url'] and previous['url'] not in ('(null)', 'null'):
        set_network('-setautoproxyurl', service, previous['url'])
    set_network('-setautoproxystate', service, 'on' if previous['enabled'] else 'off')
    set_network('-setproxyautodiscovery', service, 'on' if previous['discovery'] else 'off')


def bootout():
    run('/bin/launchctl', 'bootout', DOMAIN + '/' + LABEL, check=False)


def wait_ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open('http://127.0.0.1:10889/health', timeout=1) as r:
                if json.load(r).get('running'):
                    with socket.create_connection(('127.0.0.1', 10888), timeout=1):
                        return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError('Прокси не запустился. Смотрите журнал в папке приложения.')


def record(status, **details):
    # Contains setup status only; never passwords or browsing data.
    try:
        (SOURCE / 'result.json').write_text(json.dumps({'status': status, **details}, ensure_ascii=False, indent=2))
    except OSError:
        pass


def enable():
    APP.mkdir(parents=True, exist_ok=True)
    previous = json.loads(STATE.read_text()) if STATE.exists() else snapshot(find_service())
    if not STATE.exists():
        STATE.write_text(json.dumps(previous, ensure_ascii=False, indent=2))
        STATE.chmod(0o600)
    print('Подключение:', previous['service'])
    print('Выбранные сервисы будут использовать локальный прокси на этом Mac.')
    print('macOS может запросить пароль администратора; символы пароля не отображаются.')
    subprocess.run(['/usr/bin/sudo', '-v'], check=True)
    try:
        bootout()
        for filename in ('tpws', 'agent.py', 'service-presets.json', 'SOURCE.txt', 'LICENSE.txt'):
            shutil.copy2(SOURCE / filename, APP / filename)
        (APP / 'tpws').chmod(0o755)
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        config = {'Label': LABEL, 'ProgramArguments': ['/usr/bin/python3', str(APP / 'agent.py')],
                  'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
                  'StandardOutPath': str(APP / 'service.log'),
                  'StandardErrorPath': str(APP / 'service.log')}
        with PLIST.open('wb') as f:
            plistlib.dump(config, f)
        PLIST.chmod(0o644)
        run('/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST))
        wait_ready()
        set_network('-setautoproxyurl', previous['service'], PAC_URL)
        set_network('-setautoproxystate', previous['service'], 'on')
        set_network('-setproxyautodiscovery', previous['service'], 'off')
        actual = network('-getautoproxyurl', previous['service'])
        if PAC_URL not in actual or not re.search(r'^Enabled:\s*Yes', actual, re.M | re.I):
            raise RuntimeError('macOS не подтвердила включение автоматического прокси.')
    except (Exception, KeyboardInterrupt):
        restore(previous)
        bootout()
        if PLIST.exists():
            PLIST.unlink()
        raise
    record('enabled', service=previous['service'])
    print('\nГотово. Перезапустите браузер и проверьте воспроизведение YouTube.')
    print('Для возврата прежних настроек запустите «Отключить.command».')


def disable():
    if not STATE.exists():
        print('Сохранённых настроек нет: этот комплект не был включён.')
        return
    previous = json.loads(STATE.read_text())
    print('Возвращаю прежние настройки подключения:', previous['service'])
    subprocess.run(['/usr/bin/sudo', '-v'], check=True)
    restore(previous)
    bootout()
    if PLIST.exists():
        PLIST.unlink()
    STATE.unlink()
    record('disabled', service=previous['service'])
    print('Прокси и автозапуск отключены. Прежние сетевые настройки восстановлены.')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('enable', 'disable'):
            raise RuntimeError('Используйте enable или disable')
        (enable if sys.argv[1] == 'enable' else disable)()
    except Exception as error:
        record('error', message=str(error))
        print('\nОшибка:', error, file=sys.stderr)
        sys.exit(1)
