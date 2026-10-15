ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
# The existing collector image supplies Python and CA certificates. Install SSH
# once in a cached layer; source-only updates reuse this layer.
RUN python -c 'import os,pathlib; root=pathlib.Path("/etc/apt"); mirror=os.environ["DEBIAN_MIRROR"].rstrip("/"); files=list(root.glob("sources.list"))+list(root.glob("sources.list.d/*.sources"))+list(root.glob("sources.list.d/*.list")); [(p.write_text(p.read_text().replace("http://deb.debian.org",mirror).replace("https://deb.debian.org",mirror).replace("http://security.debian.org",mirror).replace("https://security.debian.org",mirror))) for p in files]'
RUN apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/waygate
COPY apps/waygate/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --timeout 30 -r requirements.txt
COPY apps/waygate/hub ./apps/waygate/hub
COPY scripts/ops/verify_http_backfill.py ./scripts/ops/verify_http_backfill.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "apps.waygate.hub.service", "--config", "/config/config.json"]
