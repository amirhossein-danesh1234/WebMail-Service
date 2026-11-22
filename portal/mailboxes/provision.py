"""The portal and a socket-activated Stalwart account creator share this small protocol."""
import base64
import json
import os
from pathlib import Path
import re
import socket
import sys
import urllib.request

DOMAIN = 'kappaut.ir'
SOCKET_PATH = '/run/kappa-mailbox-create.sock'
LOCAL_PART = re.compile(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', re.ASCII)
MAX_REQUEST = 512


def valid_local_part(value):
    return isinstance(value, str) and len(value) <= 64 and LOCAL_PART.fullmatch(value) is not None


def request_creation(local_part, password):
    request = json.dumps({'local_part': local_part, 'password': password}).encode() + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(22)
        connection.connect(SOCKET_PATH)
        connection.sendall(request)
        with connection.makefile('rb') as response:
            return response.readline(64).strip().decode('ascii')


def create_account(local_part, password, call):
    domains = call('x:Domain/get', {'properties': ['id', 'name']})['list']
    domain_id = next(item['id'] for item in domains if item['name'] == DOMAIN)
    # The management endpoint only ever receives fixed account type, role and domain.
    result = call('x:Account/set', {'create': {'box': {
        '@type': 'User', 'name': local_part, 'domainId': domain_id,
        'roles': {'@type': 'User'},
        'credentials': {'0': {'@type': 'Password', 'secret': password}},
    }}})
    if 'box' in result.get('created', {}):
        return 'created'
    accounts = call('x:Account/get', {'properties': ['id', 'name', 'domainId']})['list']
    if any(row['name'] == local_part and row['domainId'] == domain_id for row in accounts):
        return 'exists'
    return 'rejected'


def broker():
    received = sys.stdin.buffer.readline(MAX_REQUEST)
    if len(received) >= MAX_REQUEST or not received.endswith(b'\n'):
        return 'invalid'
    data = json.loads(received)
    if not isinstance(data, dict) or set(data) != {'local_part', 'password'}:
        return 'invalid'
    local_part, password = data['local_part'], data['password']
    if not valid_local_part(local_part) or not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{54}', password):
        return 'invalid'

    credentials = json.loads((Path(os.environ['CREDENTIALS_DIRECTORY']) / 'stalwart.json').read_text())
    token = base64.b64encode((credentials['username'] + ':' + credentials['secret']).encode()).decode()

    def call(method, arguments):
        payload = {'using': ['urn:ietf:params:jmap:core', 'urn:stalwart:jmap'],
                   'methodCalls': [[method, arguments, 'kappa']]}
        request = urllib.request.Request('http://127.0.0.1:8080/jmap/', data=json.dumps(payload).encode(),
            headers={'Authorization': 'Basic ' + token, 'Content-Type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=6) as response:
            name, result, _ = json.load(response)['methodResponses'][0]
        if name != method:
            raise ValueError('Stalwart rejected the management request')
        return result

    return create_account(local_part, password, call)


if __name__ == '__main__':
    try:
        print(broker(), flush=True)
    except Exception:
        print('unavailable', flush=True)
