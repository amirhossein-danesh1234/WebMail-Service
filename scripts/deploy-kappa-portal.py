#!/usr/bin/env python3
"""Install the isolated Kappa portal without restarting the shared portal or mail server."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import pwd
import secrets
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'deploy'
PYTHON = '/opt/webmail-portal-venv/bin/python'
SERVICE = 'kappa-webmail-portal'
DATA = Path('/var/lib/kappa-webmail-portal')
CONFIG = Path('/etc/kappa-webmail-portal')
SITES = Path('/etc/nginx/sites-available')
LINK = Path('/etc/nginx/sites-enabled/kappa-webmail-portal')
USER_HOST = 'mail.kappaut.ir'
ADMIN_HOST = 'mail-admin.kappaut.ir'


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def activate(changes, backup):
    previous = {path: path.read_bytes() if path.exists() else None for path in changes}
    for path, content in previous.items():
        if content is not None:
            (backup / path.name).write_bytes(content)
    had_link = LINK.is_symlink()
    try:
        for path, content in changes.items():
            path.write_text(content)
        if not had_link:
            LINK.symlink_to(SITES / SERVICE)
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
    except Exception:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        if not had_link:
            LINK.unlink(missing_ok=True)
        raise


def portal(backup, admin):
    try:
        account = pwd.getpwnam(SERVICE)
    except KeyError:
        run('useradd', '--system', '--home-dir', str(DATA), '--shell', '/usr/sbin/nologin', SERVICE)
        account = pwd.getpwnam(SERVICE)
    DATA.mkdir(mode=0o700, exist_ok=True)
    os.chown(DATA, account.pw_uid, account.pw_gid)
    CONFIG.mkdir(mode=0o700, exist_ok=True)
    config = CONFIG / 'portal.env'
    if not config.exists():
        key = subprocess.check_output(
            [PYTHON, '-c', 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'],
            text=True).strip()
        config.write_text(
            f'PORTAL_TENANT=kappa\nPORTAL_DATA_DIR={DATA}\nPORTAL_MAILBOX_KEY={key}\n'
            f'PORTAL_SECRET_KEY={secrets.token_urlsafe(60)}\n')
    db = DATA / 'portal.sqlite3'
    if db.exists():
        with sqlite3.connect(db) as source, sqlite3.connect(backup / 'portal.sqlite3') as target:
            source.backup(target)
    env = os.environ.copy()
    env.update(line.split('=', 1) for line in config.read_text().splitlines())
    env['PYTHONDONTWRITEBYTECODE'] = '1'

    def manage(*args):
        run('runuser', '-u', SERVICE, '--', PYTHON, str(ROOT / 'portal/manage.py'), *args, env=env)

    manage('check')
    manage('migrate', '--noinput')
    manage('collectstatic', '--noinput')
    if admin:
        password = sys.stdin.readline().rstrip('\r\n')
        if not password:
            raise SystemExit('Missing bootstrap password on standard input')
        env['DJANGO_SUPERUSER_PASSWORD'] = password
        manage('createsuperuser', '--noinput', '--username', admin, '--email', '')
        env.pop('DJANGO_SUPERUSER_PASSWORD')

    static = Path('/var/www/kappa-webmail-static')
    shutil.copytree(DATA / 'static', static, dirs_exist_ok=True)
    for path in (static, *static.rglob('*')):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    unit = (DEPLOY / 'webmail-portal.service').read_text()
    unit = unit.replace('Description=Invite-only webmail account portal', 'Description=Managed Kappa mail portal')
    unit = unit.replace('/var/lib/webmail-portal', str(DATA))
    unit = unit.replace('/etc/webmail-portal', str(CONFIG))
    unit = unit.replace('User=webmail-portal', 'User=' + SERVICE).replace('Group=webmail-portal', 'Group=' + SERVICE)
    unit = unit.replace('127.0.0.1:8091', '127.0.0.1:8092')
    (Path('/etc/systemd/system') / (SERVICE + '.service')).write_text(unit)
    for filename in ('kappa-mailbox-create.socket', 'kappa-mailbox-create@.service'):
        target = Path('/etc/systemd/system') / filename
        shutil.copyfile(DEPLOY / filename, target)
        os.chmod(target, 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'kappa-mailbox-create.socket')
    run('systemctl', 'enable', SERVICE)
    run('systemctl', 'restart', SERVICE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--acme-only', action='store_true')
    parser.add_argument('--bootstrap-admin')
    args = parser.parse_args()
    if os.geteuid() != 0 or ROOT != Path('/opt/webmail-service'):
        raise SystemExit('Run as root from the deployed repository')
    if args.acme_only and LINK.exists():
        raise SystemExit('A Kappa vhost is already active')
    os.umask(0o077)
    backup = Path('/root/kappa-webmail-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir()
    http = (DEPLOY / 'nginx-kappa-mail-http.conf').read_text()
    if args.acme_only:
        activate({SITES / SERVICE: http.replace('@HOSTS@', ADMIN_HOST)}, backup)
        return

    certificates = {USER_HOST: 'webmail-portal', ADMIN_HOST: 'kappa-webmail-admin'}
    for certificate in certificates.values():
        if not Path('/etc/letsencrypt/live', certificate, 'fullchain.pem').is_file():
            raise SystemExit('Missing webmail TLS certificate: ' + certificate)
    portal(backup, args.bootstrap_admin)
    tls = (DEPLOY / 'nginx-kappa-mail-tls.conf').read_text()
    config = http.replace('@HOSTS@', f'{USER_HOST} {ADMIN_HOST}')
    for host, certificate in certificates.items():
        config += '\n' + tls.replace('@HOST@', host).replace('@CERTIFICATE@', certificate)
    changes = {SITES / SERVICE: config}
    for filename in ('webmail', 'webmail-portal-tls'):
        path = SITES / filename
        lines = path.read_text().splitlines(keepends=True)
        changes[path] = ''.join(line.replace(' ' + USER_HOST, '')
                                if line.lstrip().startswith('server_name ') else line for line in lines)
    activate(changes, backup)
    hook = Path('/etc/letsencrypt/renewal-hooks/deploy/kappa-webmail-admin')
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEPLOY / 'renew-kappa-webmail-certificate.sh', hook)
    os.chmod(hook, 0o755)
    print('Kappa portal activated; backup: ' + str(backup))


if __name__ == '__main__':
    main()
