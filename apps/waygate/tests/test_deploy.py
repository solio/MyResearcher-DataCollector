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


class DeploymentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Linux launcher uses a POSIX deployment lock')
    def test_start_container_without_nginx_or_public_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'waygate'
            app.mkdir()
            (app / 'config.example.json').write_text(json.dumps({
                'source_domains': ['eastmoney.com'], 'ssh': {}, 'push': {'enabled': False}}))
            calls = []
            def docker_only(args, **kwargs):
                self.assertEqual(args[0], 'docker')
                calls.append(args)
                return ''
            with patch.object(start, 'APP', app), \
                    patch.object(start.sys, 'platform', 'linux'), \
                    patch.object(start.os, 'geteuid', return_value=0), \
                    patch.object(start.sys, 'argv', ['start.py', '--public-url', ADDRESS]), \
                    patch.object(start, 'collector_info', return_value=(app, 'test-token-only', 'http://127.0.0.1:8790', 'local-collector')), \
                    patch.object(start, 'read_json', return_value={'nodes': [{'local': True}]}) as read, \
                    patch.object(start, 'wait_inbox', return_value={'nodes': [], 'items': []}) as ready, \
                    patch.object(start, 'command', side_effect=docker_only):
                start.main()
            read.assert_called_once_with('http://127.0.0.1:8790/api/fleet', 'test-token-only')
            self.assertTrue(ready.call_args.args[0].startswith('http://127.0.0.1:'))
            self.assertTrue(any(args[-1:] == ['build'] for args in calls))
            self.assertTrue(any(args[-3:] == ['up', '-d', '--no-build'] for args in calls))
            self.assertIn('/collector/v1/', (app / '.runtime/nginx/waygate-locations.conf').read_text())
            config = json.loads((app / '.runtime/config/config.json').read_text())
            self.assertEqual(config['public_url'], ADDRESS)
            self.assertEqual(config['collector_token_file'], '/collector-data/console.token')
            self.assertNotIn('test-token-only', (app / '.runtime/config/config.json').read_text())

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
                config.write_text('''pid %s/nginx.pid;
error_log %s/error.log;
events {}
http { access_log off; client_body_temp_path %s/body; proxy_temp_path %s/proxy;
server { listen 127.0.0.1:%d;
location /collector/ { proxy_pass http://127.0.0.1:%d/; }
%s
}}
''' % (root, root, root, root, port, old.server_port, locations(ADDRESS, hub.server_port)))
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
