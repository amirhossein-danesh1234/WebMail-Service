"""Read only the email column from the three configured Kappa registration databases."""
import json
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

SOCKET_PATH = '/run/kappa-seminar-recipients.sock'
SOURCES = {
    'nobel': ('سمینار نوبل', 'nobel-seminar.sqlite'),
    'apply': ('سمینار اپلای', 'apply-seminar.sqlite'),
    'quantum': ('سمینار کوانتوم', 'kappa.sqlite'),
}
DATABASE_DIR = Path('/var/lib/kappaut')
MAX_RESPONSE = 8 * 1024 * 1024


def source_emails(source, directory=DATABASE_DIR):
    if source not in SOURCES:
        raise ValueError('Unknown seminar')
    uri = (directory / SOURCES[source][1]).absolute().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
        return [str(row[0] or '')[:255] for row in db.execute('SELECT email FROM registrations ORDER BY id')]


def read_emails(source):
    if source not in SOURCES:
        raise ValueError('Unknown seminar')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(8)
        connection.connect(SOCKET_PATH)
        connection.sendall(source.encode('ascii') + b'\n')
        with connection.makefile('rb') as result:
            raw = result.readline(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE or not raw.endswith(b'\n'):
        raise ValueError('Incomplete recipient inventory')
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(row, str) for row in data):
        raise ValueError('Invalid recipient inventory')
    return data


if __name__ == '__main__':
    try:
        line = sys.stdin.buffer.readline(32)
        if not line.endswith(b'\n'):
            raise ValueError('Incomplete request')
        result = json.dumps(source_emails(line[:-1].decode('ascii')), ensure_ascii=True)
        if len(result) >= MAX_RESPONSE:
            raise ValueError('Recipient inventory exceeds maximum size')
        print(result, flush=True)
    except (OSError, ValueError, sqlite3.Error):
        print('null', flush=True)
