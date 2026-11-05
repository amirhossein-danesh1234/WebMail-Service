#!/usr/bin/env python3
"""Provision the isolated portal from this checkout; never restart Stalwart.

Run as root AFTER git pull and offline wheel installation into the documented
venv. Proxy activation is a separate explicit flag, after runtime E2E checks.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DATA = Path('/var/lib/webmail-portal')
CONFIG = Path('/etc/webmail-portal')
PYTHON = '/opt/webmail-portal-venv/bin/python'


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-proxy', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or ROOT != Path('/opt/webmail-service'):
        raise SystemExit('Run as root in /opt/webmail-service after git pull')
    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/root/webmail-portal-backup-' + stamp)
    backup.mkdir(mode=0o700)
    for source in ['/etc/nginx/sites-available/webmail', '/etc/nginx/sites-available/webmail-admin',
                   '/etc/nginx/snippets/webmail-portal-locations.conf', '/etc/systemd/system/webmail-portal.service']:
        source = Path(source)
        if source.exists():
            shutil.copy2(source, backup / source.name)
    if (DATA / 'portal.sqlite3').exists():
        with sqlite3.connect(DATA / 'portal.sqlite3') as src, sqlite3.connect(backup / 'portal.sqlite3') as dst:
            src.backup(dst)
    if CONFIG.exists():
        shutil.copytree(CONFIG, backup / 'configuration')
    try:
        account = pwd.getpwnam('webmail-portal')
    except KeyError:
        run('useradd', '--system', '--home-dir', str(DATA), '--shell', '/usr/sbin/nologin', 'webmail-portal')
        account = pwd.getpwnam('webmail-portal')
    DATA.mkdir(mode=0o700, exist_ok=True)
    CONFIG.mkdir(mode=0o750, exist_ok=True)
    os.chmod(CONFIG, 0o750)
    os.chown(DATA, account.pw_uid, account.pw_gid)
    os.chown(CONFIG, 0, account.pw_gid)
    config_file = CONFIG / 'portal.env'
    if not config_file.exists():
        code = 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
        key = subprocess.check_output([PYTHON, '-c', code], text=True).strip()
        config_file.write_text('PORTAL_SECRET_KEY=' + secrets.token_urlsafe(60) + '\n'
                               'PORTAL_MAILBOX_KEY=' + key + '\nPORTAL_DATA_DIR=' + str(DATA) + '\n')
    os.chmod(config_file, 0o640)
    os.chown(config_file, 0, account.pw_gid)
    env = os.environ.copy()
    env.update(line.split('=', 1) for line in config_file.read_text().splitlines() if line and not line.startswith('#'))
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for command in [('check',), ('migrate', '--noinput'), ('collectstatic', '--noinput')]:
        run('runuser', '-u', 'webmail-portal', '--', PYTHON, str(ROOT / 'portal/manage.py'), *command, env=env)
    # This private bootstrap credential is not copied into the portal service.
    credentials = Path('/root/webmail-portal-admin-credentials.json')
    if not credentials.exists():
        sys.path.insert(0, str(ROOT / 'portal'))
        os.environ.update(env)
        os.environ['DJANGO_SETTINGS_MODULE'] = 'mailportal.settings'
        import django
        django.setup()
        from django.contrib.auth.models import User
        if User.objects.filter(is_superuser=True).exists():
            raise SystemExit('Existing administrator preserved; recover its credential manually')
        secret = secrets.token_urlsafe(32)
        User.objects.create_superuser('mail-admin', password=secret)
        credentials.write_text(json.dumps({'username': 'mail-admin', 'secret': secret}))
        os.chmod(credentials, 0o600)
    for path in DATA.glob('portal.sqlite3*'):
        os.chown(path, account.pw_uid, account.pw_gid)
        os.chmod(path, 0o600)
    static = Path('/var/www/webmail-portal-static')
    shutil.copytree(DATA / 'static', static, dirs_exist_ok=True)
    os.chmod(static, 0o755)
    for path in static.rglob('*'):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    shutil.copy2(ROOT / 'deploy/webmail-portal.service', '/etc/systemd/system/webmail-portal.service')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', 'webmail-portal.service')
    run('systemctl', 'restart', 'webmail-portal.service')
    if args.install_proxy:
        # Preserve custom server configuration: only replace the known root redirect.
        webmail = Path('/etc/nginx/sites-available/webmail')
        before = webmail.read_text()
        include = 'include /etc/nginx/snippets/webmail-portal-locations.conf;'
        old = 'location = / { return 302 /webmail/; }'
        if include not in before and before.count(old) != 1:
            raise SystemExit('Unexpected webmail configuration; no proxy change made')
        snippet = Path('/etc/nginx/snippets/webmail-portal-locations.conf')
        admin = Path('/etc/nginx/sites-available/webmail-admin')
        link = Path('/etc/nginx/sites-enabled/webmail-admin')
        previous = {p: p.read_bytes() if p.exists() else None for p in (webmail, snippet, admin)}
        had_link = link.exists()
        try:
            shutil.copy2(ROOT / 'deploy/nginx-portal-locations.conf', snippet)
            shutil.copy2(ROOT / 'deploy/nginx-mail-admin.conf', admin)
            webmail.write_text(before.replace(old, include) if include not in before else before)
            if not had_link:
                link.symlink_to(admin)
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx')
        except Exception:
            for path, content in previous.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            if not had_link:
                link.unlink(missing_ok=True)
            raise
    print('Portal provisioned; backup: ' + str(backup))
    print('Credentials remain in a root-only file. Public DNS/HTTPS must be verified separately.')


if __name__ == '__main__':
    main()
