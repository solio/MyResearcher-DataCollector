#!/usr/bin/env python3
"""Start the Waygate container; nginx configuration is a separate operator step."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    from .nginx_routes import locations, public_address
except ImportError:
    from nginx_routes import locations, public_address

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


def wait_inbox(base, token, timeout=45):
    deadline, error = time.monotonic() + timeout, ''
    inbox = None
    while time.monotonic() < deadline:
        inbox = None
        try:
            data = read_json(base + '/v1/inbox', token)
            if not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not isinstance(data.get('items'), list):
                raise RuntimeError('Waygate /v1/inbox 返回格式不正确')
            inbox = data
            if data.get('last_poll') and not data.get('error'):
                return data
            error = str(data.get('error') or 'collector 首次状态同步尚未完成').replace(token, '[redacted]')[:400]
        except RuntimeError as exc:
            error = str(exc)
        time.sleep(1)
    if inbox is not None:
        return dict(inbox, error=error)
    raise RuntimeError(error)


def main():
    p = argparse.ArgumentParser(description='启动 Waygate Docker 容器，不读取或修改 nginx')
    p.add_argument('--public-url', required=True)
    p.add_argument('--collector-container')
    p.add_argument('--ssh-dir', type=Path, default=Path.home() / '.ssh')
    args = p.parse_args()
    address = args.public_url.rstrip('/')
    public = public_address(address)
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
    include = APP / 'deploy/collector.nginx.conf'
    if public.path != '/collector':
        # Custom prefixes need rendering, but never depend on Docker succeeding.
        runtime.chmod(0o755)
        (runtime / 'nginx').mkdir(exist_ok=True)
        (runtime / 'nginx').chmod(0o755)
        include = runtime / 'nginx/waygate-locations.conf'
        include.write_text(locations(address, 8791))
        include.chmod(0o644)
    data, token, collector_url, base_image = collector_info(args.collector_container)
    fleet = read_json(collector_url + '/api/fleet', token)
    for folder in (runtime / 'config', runtime / 'data'):
        folder.mkdir(parents=True, exist_ok=True)
    config_path = runtime / 'config/config.json'
    previous_config = config_path.read_bytes() if config_path.exists() else None
    config = json.loads(previous_config.decode()) if previous_config else json.loads((APP / 'config.example.json').read_text())
    if previous_config is None:
        config['ssh'] = {}
    config.update(listen='127.0.0.1', port=8791, public_url=address, collector_url=collector_url,
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
        command(compose + ['up', '-d', '--no-build', '--force-recreate'], env=env, visible=True)
        inbox = wait_inbox('http://127.0.0.1:' + str(config['port']), token)
    except BaseException:
        restore_service()
        raise
    print('\nWaygate 容器已启动，本机接口可访问。')
    if inbox.get('error') or not inbox.get('last_poll'):
        print('collector 状态尚未同步：' + str(inbox.get('error') or '首次同步尚未完成'))
        print('读取地址：' + collector_url + '/api/fleet')
        print('服务保留运行，后台每 10 秒重试同步；具体失败也会写入 Docker 日志。')
    else:
        print('collector 状态已同步。')
    print('本机监听：http://127.0.0.1:' + str(config['port']))
    print('App 地址：' + address)
    print('Token：继续使用原 collector 的 console.token（不输出、不复制）。')
    print('节点：%d；待处理：%d' % (len(inbox['nodes']), len(inbox['items'])))
    print('配置：' + str(config_path))
    print('nginx 接入配置（在原 collector 所在 server {} 内，已有此行时无需重复添加）：')
    print('    include ' + str(include) + ';')
    print('再按你现有方式 reload nginx；完成后 App 才能通过公网地址连接。')
    print('日志：docker logs -f --tail=100 $(docker ps -q --filter label=com.docker.compose.project=waygate --filter label=com.docker.compose.service=waygate)')
    print('人工验证需主控现有 SSH key/known_hosts 可登录目标节点；未配置的节点需补齐 SSH 凭据。')
    print('华为推送按原配置启用；本命令不会创建验证码会话或恢复采集任务。')
    lock.close()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print('启动未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
