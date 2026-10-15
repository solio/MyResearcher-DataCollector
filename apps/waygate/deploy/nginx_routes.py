"""Render optional nginx routes; never discover or edit server configuration."""
import re
from urllib.parse import urlsplit


def public_address(value):
    u = urlsplit(value.rstrip('/'))
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or
            u.query or u.fragment or not re.fullmatch(r'[A-Za-z0-9.-]+', u.hostname) or
            not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*', u.path)):
        raise ValueError('请提供现有主控 HTTPS 地址，例如 https://你的域名/collector')
    return u


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
