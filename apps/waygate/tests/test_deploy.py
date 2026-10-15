"""Container startup is independent of nginx; optional proxy snippet keeps routes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from apps.waygate.deploy import start
from apps.waygate.deploy.nginx_routes import locations

ADDRESS = 'https://console.example/collector'
TRACKED_INCLUDE = start.APP / 'deploy/collector.nginx.conf'


class DeploymentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Linux launcher uses a POSIX deployment lock')
    def test_start_container_without_nginx_or_public_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'waygate'
            app.mkdir()
            (app / 'deploy').mkdir()
            shutil.copyfile(TRACKED_INCLUDE, app / 'deploy/collector.nginx.conf')
            (app / 'config.example.json').write_text(json.dumps({
                'source_domains': ['eastmoney.com'], 'ssh': {}, 'push': {'enabled': False}}))
            calls = []
            def docker_only(args, **kwargs):
                self.assertEqual(args[0], 'docker')
                self.assertTrue((app / 'deploy/collector.nginx.conf').is_file())
                calls.append(args)
                return ''
            with patch.object(start, 'APP', app), \
                    patch.object(start.sys, 'platform', 'linux'), \
                    patch.object(start.os, 'geteuid', return_value=0), \
                    patch.object(start.sys, 'argv', ['start.py', '--public-url', ADDRESS]), \
                    patch.object(start, 'collector_info', return_value=(app, 'test-token-only', 'http://127.0.0.1:8790', 'local-collector')), \
                    patch.object(start, 'read_json', return_value={'nodes': [{'local': True}]}) as read, \
                    patch.object(start, 'wait_inbox', return_value={'nodes': [], 'items': [], 'last_poll': 0,
                                 'error': 'collector /api/fleet 返回 HTTP 401'}) as ready, \
                    patch.object(start, 'command', side_effect=docker_only):
                start.main()
            read.assert_called_once_with('http://127.0.0.1:8790/api/fleet', 'test-token-only')
            ready.assert_called_once_with('http://127.0.0.1:8791', 'test-token-only')
            self.assertTrue(any(args[-1:] == ['build'] for args in calls))
            self.assertTrue(any(args[-4:] == ['up', '-d', '--no-build', '--force-recreate'] for args in calls))
            self.assertFalse(any('stop' in args for args in calls))
            self.assertFalse((app / '.runtime/nginx').exists())
            config = json.loads((app / '.runtime/config/config.json').read_text())
            self.assertEqual(config['port'], 8791)
            self.assertEqual(config['public_url'], ADDRESS)
            self.assertEqual(config['collector_token_file'], '/collector-data/console.token')
            self.assertNotIn('test-token-only', (app / '.runtime/config/config.json').read_text())

    def test_poll_failure_retains_running_inbox_and_concrete_error(self):
        data = {'nodes': [], 'items': [], 'last_poll': 0, 'error': 'collector /api/fleet 返回 HTTP 401'}
        with patch.object(start, 'read_json', return_value=data), \
                patch.object(start.time, 'monotonic', side_effect=[0, 0, 2]), \
                patch.object(start.time, 'sleep'):
            self.assertEqual(start.wait_inbox('http://127.0.0.1:8791', 'test-only', timeout=1), data)

    def test_unreachable_inbox_still_fails_startup(self):
        with patch.object(start, 'read_json', side_effect=RuntimeError('接口检查 HTTP 401')), \
                patch.object(start.time, 'monotonic', side_effect=[0, 0, 2]), \
                patch.object(start.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                start.wait_inbox('http://127.0.0.1:8791', 'test-only', timeout=1)

    @unittest.skipUnless(shutil.which('nginx'), 'nginx is required for the real routing check')
    def test_real_nginx_preserves_collector_and_proxies_hub_paths(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'backend': self.server.label, 'path': self.path,
                                   'auth': self.headers.get('Authorization')}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        old = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        hub = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        old.label, hub.label = 'collector', 'waygate'
        for server in (old, hub):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                config = root / 'nginx.conf'
                # Parse the actual checked-in include before any deployment exists.
                self.assertEqual(TRACKED_INCLUDE.read_text(), locations(ADDRESS, 8791))
                config.write_text('''pid %s/nginx.pid;
error_log %s/error.log;
events {}
http { access_log off; client_body_temp_path %s/body; proxy_temp_path %s/proxy;
server { listen 127.0.0.1:%d;
location /collector/ { proxy_pass http://127.0.0.1:%d/; }
include "%s";
}}
''' % (root, root, root, root, port, old.server_port, TRACKED_INCLUDE))
                checked = subprocess.run([shutil.which('nginx'), '-t', '-p', str(root), '-c', str(config)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                # Exercise the same file with an isolated test backend port.
                include = root / 'collector.nginx.conf'
                include.write_text(TRACKED_INCLUDE.read_text().replace(':8791', ':' + str(hub.server_port)))
                config.write_text(config.read_text().replace(str(TRACKED_INCLUDE), str(include)))
                proc = subprocess.Popen([shutil.which('nginx'), '-p', str(root), '-c', str(config),
                                         '-g', 'daemon off;'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    for _ in range(40):
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                                break
                        except OSError:
                            time.sleep(.05)
                    for path, backend, upstream in [('/collector/api/status', 'collector', '/api/status'),
                            ('/collector/', 'collector', '/'),
                            ('/collector/v1/inbox', 'waygate', '/v1/inbox'),
                            ('/collector/console/app.js', 'waygate', '/console/app.js'),
                            ('/collector/health', 'waygate', '/health')]:
                        request = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                                         headers={'Authorization': 'Bearer test-only'})
                        with opener.open(request, timeout=3) as response:
                            body = json.load(response)
                        self.assertEqual(body, {'backend': backend, 'path': upstream, 'auth': 'Bearer test-only'})
                finally:
                    proc.terminate()
                    proc.communicate(timeout=5)
        finally:
            for server in (old, hub):
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
