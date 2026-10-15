#!/usr/bin/env python3
"""Linux/Docker startup for the existing collector HTTPS endpoint (Python 3.6+)."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    from .nginx_routes import install_include, locations, public_address, tls_server
except ImportError:
    from nginx_routes import install_include, locations, public_address, tls_server

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'apps/waygate'


def command(args, env=None, visible=False):
    p = subprocess.run([str(x) for x in args], cwd=str(ROOT), env=env,
                       stdout=None if visible else subprocess.PIPE,
                       stderr=None if visible else subprocess.PIPE, universal_newlines=True)
    if p.returncode:
        # Never print inspect/config output, which can include credentials.
        raise RuntimeError('命令失败：' + str(args[0]) + ' ' + str(args[1]) +
                           ('\n' + (p.stderr or '')[-1800:] if not visible else '（见上方输出）'))
    return p.stdout or ''


def read_json(url, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('接口检查 HTTP ' + str(exc.code) + '：' + url) from None
    except (OSError, ValueError) as exc:
        raise RuntimeError('无法读取接口：' + url + '（' + type(exc).__name__ + '）') from None


def collector_info(container=None):
    if container is None:
        ids = command(['docker', 'ps', '--filter', 'label=com.docker.compose.service=collector-console',
                       '--format', '{{.ID}}']).split()
        if len(ids) != 1:
            raise RuntimeError('需要恰好一个运行中的 collector；有多个时加 --collector-container 容器名')
        container = ids[0]
    info = json.loads(command(['docker', 'inspect', container]))[0]
    mount = next((m for m in info['Mounts'] if m['Destination'] == '/data' and m['Type'] == 'bind'), None)
    ports = info['NetworkSettings']['Ports'].get('8790/tcp') or []
    port = next((p for p in ports if p['HostIp'] in ('127.0.0.1', '0.0.0.0', '')), None)
    if not info['State']['Running'] or not mount or not port:
        raise RuntimeError('collector 必须运行，/data 需为本机目录挂载，8790 需发布到本机 IPv4')
    data = Path(mount['Source'])
    token = (data / 'console.token').read_text().strip()
    if len(token) < 16:
        raise RuntimeError('现有 console.token 无效')
    return data, token, 'http://127.0.0.1:' + port['HostPort'], info['Config']['Image']


def nginx_target(nginx, address):
    command([nginx, '-t'])
    dumped = command([nginx, '-T'])
    paths = re.findall(r'^# configuration file (.+):\s*$', dumped, re.M)
    matches = []
    for name in dict.fromkeys(paths):
        path = Path(name).resolve()
        text = path.read_text()
        for block in tls_server(text, address):
            matches.append((path, text, block))
    if len(matches) != 1:
        raise RuntimeError('未找到唯一匹配的 HTTPS server_name；请确认本机 nginx 承载该域名，或用 --nginx 指定其可执行文件')
    return matches[0]


def wait_inbox(base, token, timeout=45):
    deadline, error = time.monotonic() + timeout, ''
    while time.monotonic() < deadline:
        try:
            data = read_json(base + '/v1/inbox', token)
            if data.get('last_poll') and not data.get('error') and isinstance(data.get('nodes'), list):
                return data
            error = 'Waygate 尚未成功读取 collector 状态'
        except RuntimeError as exc:
            error = str(exc)
        time.sleep(1)
    raise RuntimeError(error)


def publish_routes(nginx, target, include, snippet, address, backup, check):
    path, original, block = target
    if path.read_text() != original:
        raise RuntimeError('nginx 配置在构建期间被修改，已保留新配置；请重新执行启动命令')
    updated = install_include(original, block, include, address)
    previous = include.read_bytes() if include.exists() else None
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copy2(str(path), str(backup / 'vhost.conf'))
    if previous is not None:
        (backup / 'waygate-locations.conf').write_bytes(previous)
    (backup / 'paths.json').write_text(json.dumps({'vhost': str(path), 'include': str(include)}))
    reload_attempted = False
    try:
        include.write_text(snippet)
        include.chmod(0o644)
        # Writing in place preserves the vhost's ownership/mode/SELinux context.
        if updated != original:
            path.write_text(updated)
        command([nginx, '-t'])
        reload_attempted = True
        command([nginx, '-s', 'reload'])
        return check()
    except BaseException:
        path.write_text(original)
        if previous is None:
            if include.exists():
                include.unlink()
        else:
            include.write_bytes(previous)
        command([nginx, '-t'])
        if reload_attempted:
            command([nginx, '-s', 'reload'])
        raise


def main():
    p = argparse.ArgumentParser(description='启动 Waygate 并接入现有 collector HTTPS 地址')
    p.add_argument('--public-url', required=True)
    p.add_argument('--collector-container')
    nginx = shutil.which('nginx') or next((x for x in (
        '/usr/sbin/nginx', '/usr/local/nginx/sbin/nginx',
        '/usr/local/openresty/nginx/sbin/nginx') if Path(x).is_file()), '/usr/sbin/nginx')
    p.add_argument('--nginx', default=nginx)
    p.add_argument('--ssh-dir', type=Path, default=Path.home() / '.ssh')
    args = p.parse_args()
    address = args.public_url.rstrip('/')
    public_address(address)
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('请在现有主控的 Linux 服务器上以 root 执行')
    os.umask(0o077)
    import fcntl
    runtime = APP / '.runtime'
    runtime.mkdir(exist_ok=True)
    lock = (runtime / 'deploy.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('另一次 Waygate 启动正在执行，请等待完成') from None
    data, token, collector_url, base_image = collector_info(args.collector_container)
    fleet = read_json(collector_url + '/api/fleet', token)
    status = read_json(collector_url + '/api/status', token)
    public_status = read_json(address + '/api/status', token)
    if not status.get('instance_id') or status.get('instance_id') != public_status.get('instance_id'):
        raise RuntimeError('该公网地址与本机 collector 实例不一致，未修改任何部署')
    target = nginx_target(args.nginx, address)
    for folder in (runtime, runtime / 'config', runtime / 'data', runtime / 'nginx'):
        folder.mkdir(parents=True, exist_ok=True)
    # nginx's master needs traversal access to the non-secret include directory.
    runtime.chmod(0o755)
    (runtime / 'nginx').chmod(0o755)
    config_path = runtime / 'config/config.json'
    previous_config = config_path.read_bytes() if config_path.exists() else None
    config = json.loads(previous_config.decode()) if previous_config else json.loads((APP / 'config.example.json').read_text())
    if previous_config is None:
        config['ssh'] = {}
        for port in range(8791, 8811):
            with socket.socket() as s:
                try:
                    s.bind(('127.0.0.1', port))
                    break
                except OSError:
                    continue
        else:
            raise RuntimeError('8791–8810 均被占用；未启动服务')
        config['port'] = port
    config.update(listen='127.0.0.1', public_url=address, collector_url=collector_url,
                  collector_token_file='/collector-data/console.token', data_dir='/data')
    ssh_dir = args.ssh_dir.resolve()
    if not ssh_dir.is_dir():
        ssh_dir = runtime / 'ssh'
        ssh_dir.mkdir(exist_ok=True)
    for node in fleet['nodes']:
        host = urlsplit(node.get('base_url') or '').hostname
        key = 'local' if node.get('local') else host
        if key:
            config['ssh'].setdefault(key, {'host': '127.0.0.1' if node.get('local') else host,
                                           'user': 'root', 'port': 22})
    include = runtime / 'nginx/waygate-locations.conf'
    # Reject overlapping locations before building or changing any service.
    install_include(target[1], target[2], include, address)
    env = dict(os.environ, WAYGATE_BASE_IMAGE=base_image, WAYGATE_COLLECTOR_DATA=str(data),
               WAYGATE_SSH_DIR=str(ssh_dir))
    compose = ['docker', 'compose', '-p', 'waygate', '-f', str(APP / 'compose.yml')]
    old_ids = command(compose + ['ps', '-aq', 'waygate'], env=env).split()
    if len(old_ids) > 1:
        raise RuntimeError('发现多个 Waygate 容器，未继续更新')
    old = json.loads(command(['docker', 'inspect', old_ids[0]]))[0] if old_ids else None
    if old is not None and previous_config is None:
        raise RuntimeError('已有 Waygate 容器但缺少本入口的旧配置，未替换服务')
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    config_path.chmod(0o600)
    def restore_service():
        if old is not None:
            config_path.write_bytes(previous_config)
            command(['docker', 'tag', old['Image'], old['Config']['Image']])
            restore_env = dict(env)
            for mount in old['Mounts']:
                if mount['Destination'] == '/collector-data':
                    restore_env['WAYGATE_COLLECTOR_DATA'] = mount['Source']
                if mount['Destination'] == '/root/.ssh':
                    restore_env['WAYGATE_SSH_DIR'] = mount['Source']
            command(compose + ['up', '-d', '--no-build', '--force-recreate'], env=restore_env, visible=True)
        else:
            command(compose + ['stop'], env=env)
    print('构建并启动 Waygate；现有 collector 容器继续运行。', flush=True)
    try:
        command(compose + ['build'], env=env, visible=True)
    except BaseException:
        if previous_config is not None:
            config_path.write_bytes(previous_config)
        raise
    try:
        command(compose + ['up', '-d', '--no-build'], env=env, visible=True)
        wait_inbox('http://127.0.0.1:' + str(config['port']), token)
    except BaseException:
        restore_service()
        raise
    backup = runtime / 'backups' / datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')
    print('检查并接入 nginx；保留原 /collector 和其他站点配置。', flush=True)
    def verify_public():
        inbox = wait_inbox(address, token, timeout=20)
        if read_json(address + '/api/status', token).get('instance_id') != status['instance_id']:
            raise RuntimeError('原 collector 状态检查未通过')
        return inbox
    try:
        inbox = publish_routes(args.nginx, target, include, locations(address, config['port']),
                               address, backup, verify_public)
    except BaseException:
        restore_service()
        raise
    print('\nWaygate 已启动，公网接口检查通过。')
    print('App 地址：' + address)
    print('Token：继续使用原 collector 的 console.token（不输出、不复制）。')
    print('节点：%d；待处理：%d' % (len(inbox['nodes']), len(inbox['items'])))
    print('配置：' + str(config_path))
    print('nginx 备份：' + str(backup))
    print('日志：docker logs -f --tail=100 $(docker ps -q --filter label=com.docker.compose.project=waygate --filter label=com.docker.compose.service=waygate)')
    print('人工验证需主控现有 SSH key/known_hosts 可登录目标节点；未配置的节点需补齐 SSH 凭据。')
    print('华为推送按原配置启用；本命令不会创建验证码会话或恢复采集任务。')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print('启动未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
