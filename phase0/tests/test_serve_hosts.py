"""The server answers only loopback and explicitly named forwarding hosts (e.g. GitHub Codespaces)."""
import argparse
import http.client
import json
import threading

import pytest

from phase0.sar.serve import create_server, public_host

PUBLIC = 'demo-8766.app.github.dev'


@pytest.fixture
def server(tmp_path):
    servers = []

    def start(**kw):
        s = create_server(0, tmp_path / 'cache', **kw)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
        return s
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def call(s, method, path, host, origin=None, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', s.server_port, timeout=10)
    headers = {'Host': host, 'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    conn.request(method, path, body=json.dumps(body).encode() if body else None, headers=headers)
    status = conn.getresponse().status
    conn.close()
    return status


def test_named_forwarding_host_is_accepted_over_https(server):
    s = server(public_hosts={PUBLIC})
    assert call(s, 'GET', '/api/health', PUBLIC) == 200
    assert call(s, 'POST', '/api/discover', PUBLIC, f'https://{PUBLIC}', {'mode': 'smiles', 'query': 'CCO'}) == 200
    assert call(s, 'POST', '/api/discover', PUBLIC, f'http://{PUBLIC}', {'mode': 'smiles', 'query': 'CCO'}) == 403
    assert call(s, 'POST', '/api/discover', PUBLIC, 'https://evil.example', {'mode': 'smiles', 'query': 'CCO'}) == 403
    assert call(s, 'GET', '/api/health', 'other-8766.app.github.dev') == 403
    assert call(s, 'GET', '/api/health', f'127.0.0.1:{s.server_port}') == 200


def test_default_server_stays_loopback_only(server):
    s = server()
    assert call(s, 'GET', '/api/health', PUBLIC) == 403
    assert s.server_address[0] == '127.0.0.1'


@pytest.mark.parametrize('bad', ['*.app.github.dev', f'{PUBLIC}:443', f'https://{PUBLIC}', 'localhost', ''])
def test_public_host_must_be_a_plain_hostname(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        public_host(bad)
    assert public_host(' Demo-8766.APP.github.dev ') == PUBLIC
