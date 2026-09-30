# Reuse the Python base image already used by labelapp on the Aliyun server.
# Override BASE_IMAGE for a different environment; build the app on the server.
ARG BASE_IMAGE=fangzuzu-docker-registry-vpc.cn-guangzhou.cr.aliyuncs.com/fangzuzu/python:3.12-slim
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/collector
COPY src ./src
COPY apps/http_backfill ./apps/http_backfill
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8790
CMD ["python", "apps/http_backfill/server.py", "--host", "0.0.0.0", "--port", "8790", "--data-dir", "/data"]
