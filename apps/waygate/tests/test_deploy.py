"""Deployment must preserve the old console and recover a failed nginx change."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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

from apps.waygate.deploy.nginx_routes import install_include, locations, public_address, tls_server
from apps.waygate.deploy.start import publish_routes

ADDRESS = 'https://console.example/collector'
VHOST = '''# server { comment }
server { listen 80; server_name console.example; return 301 https://$host$request_uri; }
server {
    listen 443 ssl;
    server_name other.example console.example;
    location = /collector { return 301 /collector/; }
    location ^~ /collector/ { proxy_pass http://127.0.0.1:8790/; }
    location /labeler/ { proxy_pass http://127.0.0.1:8787/; }
    location ~ "^/asset/[0-9]{2}\\.js$" { return 404; }
}
'''


class DeploymentTests(unittest.TestCase):
    def test_matches_tls_not_redirect_or_other_host(self):
        blocks = tls_server(VHOST, ADDRESS)
        self.assertEqual(len(blocks), 1)
        self.assertIn(['listen', '443', 'ssl'], blocks[0]['directives'])
        self.assertEqual(tls_server(VHOST, 'https://absent.example/collector'), [])

    def test_insert_is_idempotent_and_preserves_existing_locations(self):
        block = tls_server(VHOST, ADDRESS)[0]
        updated = install_include(VHOST, block, '/etc/nginx/waygate.conf', ADDRESS)
        self.assertIn('location ^~ /collector/ { proxy_pass http://127.0.0.1:8790/; }', updated)
        self.assertIn('location /labeler/ { proxy_pass http://127.0.0.1:8787/; }', updated)
        self.assertEqual(install_include(updated, tls_server(updated, ADDRESS)[0],
                                        '/etc/nginx/waygate.conf', ADDRESS), updated)

    def test_reject_injection_or_preexisting_route(self):
        for address in ['https://console.example/a;return', 'http://console.example',
                        'https://name:secret@console.example', 'https://console.example/a?x=1']:
            with self.assertRaises(ValueError):
                public_address(address)
        existing = VHOST.replace('listen 443 ssl;', 'listen 443 ssl; location /collector/v1/ { return 404; }')
        with self.assertRaises(ValueError):
            install_include(existing, tls_server(existing, ADDRESS)[0], '/etc/nginx/waygate.conf', ADDRESS)

    def test_failed_nginx_test_restores_files_without_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path, include = directory / 'site.conf', directory / 'waygate.conf'
            path.write_text(VHOST)
            with patch('apps.waygate.deploy.start.command', side_effect=[RuntimeError('bad nginx'), '']) as cmd:
                with self.assertRaisesRegex(RuntimeError, 'bad nginx'):
                    publish_routes('nginx', (path, VHOST, tls_server(VHOST, ADDRESS)[0]), include,
                                   locations(ADDRESS, 8791), ADDRESS, directory / 'backup', lambda: None)
                self.assertEqual(cmd.call_args_list[0].args[0], ['nginx', '-t'])
                self.assertEqual(len(cmd.call_args_list), 2)
            self.assertEqual(path.read_text(), VHOST)
            self.assertFalse(include.exists())
            self.assertEqual((directory / 'backup/vhost.conf').read_text(), VHOST)

    def test_config_edited_during_build_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path, include = directory / 'site.conf', directory / 'waygate.conf'
            changed = VHOST + '\n# newer operator edit\n'
            path.write_text(changed)
            with patch('apps.waygate.deploy.start.command') as cmd:
                with self.assertRaisesRegex(RuntimeError, '构建期间被修改'):
                    publish_routes('nginx', (path, VHOST, tls_server(VHOST, ADDRESS)[0]), include,
                                   locations(ADDRESS, 8791), ADDRESS, directory / 'backup', lambda: None)
                cmd.assert_not_called()
            self.assertEqual(path.read_text(), changed)
            self.assertFalse(include.exists())

    def test_failed_public_check_restores_existing_include_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path, include = directory / 'site.conf', directory / 'waygate.conf'
            path.write_text(VHOST)
            include.write_text('# previous routes\n')
            def failed():
                raise RuntimeError('public unreachable')
            with patch('apps.waygate.deploy.start.command', return_value='') as cmd:
                with self.assertRaisesRegex(RuntimeError, 'public unreachable'):
                    publish_routes('nginx', (path, VHOST, tls_server(VHOST, ADDRESS)[0]), include,
                                   locations(ADDRESS, 8791), ADDRESS, directory / 'backup', failed)
                self.assertEqual([c.args[0] for c in cmd.call_args_list],
                                 [['nginx', '-t'], ['nginx', '-s', 'reload']] * 2)
            self.assertEqual(path.read_text(), VHOST)
            self.assertEqual(include.read_text(), '# previous routes\n')

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


if __name__ == '__main__':
    unittest.main()
