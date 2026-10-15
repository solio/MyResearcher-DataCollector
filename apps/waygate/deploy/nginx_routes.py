"""Add Waygate routes inside one existing TLS vhost; keep collector routes intact."""
import re
from urllib.parse import urlsplit


def public_address(value):
    u = urlsplit(value.rstrip('/'))
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or
            u.query or u.fragment or not re.fullmatch(r'[A-Za-z0-9.-]+', u.hostname) or
            not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', u.path)):
        raise ValueError('请提供现有主控 HTTPS 地址，例如 https://你的域名/collector')
    return u


def blocks(text):
    # Parse braces, strings and comments so braces inside regex/quoted values do
    # not become server boundaries. Never rewrite the original block contents.
    pattern = r'''\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\$\{[^}]*\}|[^\s{};\#"']+|[{};]'''
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(pattern, text)
              if not m.group().startswith('#')]
    stack, pending, found = [], [], []
    for value, start, end in tokens:
        if value == '{':
            item = {'name': [p.strip('\"\'') for p in pending], 'open': end,
                    'directives': [], 'children': []}
            if stack:
                stack[-1]['children'].append(item)
            stack.append(item)
            pending = []
        elif value == '}':
            if not stack or pending:
                raise ValueError('无法安全解析 nginx 配置块')
            item = stack.pop()
            item['close'] = start
            found.append(item)
        elif value == ';':
            if stack:
                stack[-1]['directives'].append([p.strip('\"\'') for p in pending])
            pending = []
        else:
            pending.append(value)
    if stack or pending:
        raise ValueError('nginx 配置块不完整')
    return found


def tls_server(text, address):
    u = public_address(address)
    matches = []
    for b in blocks(text):
        if b['name'] != ['server']:
            continue
        names = [v for d in b['directives'] if d and d[0] == 'server_name' for v in d[1:]]
        listens = [d[1:] for d in b['directives'] if d and d[0] == 'listen']
        port = str(u.port or 443)
        if u.hostname in names and any(v and v[0].rsplit(':', 1)[-1] == port for v in listens):
            matches.append(b)
    return matches


def install_include(text, block, include_path, address):
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(include_path)):
        raise ValueError('部署目录路径需仅含英文字母、数字、下划线、横线和点')
    directive = ['include', str(include_path)]
    if directive in block['directives']:
        return text
    prefix = public_address(address).path
    reserved = {prefix + '/v1/', prefix + '/console/', prefix + '/health'}
    for child in block['children']:
        if child['name'] and child['name'][0] == 'location' and any(x in reserved for x in child['name'][1:]):
            raise ValueError('该站点已有 Waygate 同名路由；保留现状，请先核对配置')
    insertion = '\n    # Waygate app API and console; managed by deploy/start.sh\n    include ' + str(include_path) + ';\n'
    return text[:block['open']] + insertion + text[block['open']:]


def locations(address, port):
    prefix = public_address(address).path
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Waygate 本机端口无效')
    out = ['# Managed by Waygate deploy/start.sh; original collector location is unchanged.\n']
    for modifier, path in [('^~', '/v1/'), ('^~', '/console/'), ('=', '/health')]:
        out.append('''location %s %s%s {
    proxy_pass http://127.0.0.1:%d%s;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
    proxy_buffering off;
    client_max_body_size 64k;
    access_log off;
}
''' % (modifier, prefix, path, port, path))
    return '\n'.join(out)
