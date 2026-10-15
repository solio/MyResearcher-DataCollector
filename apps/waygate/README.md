# Waygate

鸿蒙上的采集控制与人工验证入口。待办、现有 H5 控制台、临时同出口验证、
单次探测和通知放在一起。手机仅配置主控地址和当前 `console.token`。

在现有 Linux/Docker 主控服务器上，以 root 执行一次启动或升级（替换自己的域名）：

```bash
git pull && bash apps/waygate/deploy/start.sh --public-url https://你的域名/collector
```

从仓库根目录执行。脚本启动独立 Waygate 容器，复用现有 collector 的实际端口、
令牌和节点注册，自动在当前 HTTPS nginx server 内接入 App 路由；原 H5 地址照常使用。
配置检查或公网验收失败会恢复原 nginx 配置。现有 collector 容器无需重建。
需要本机 Docker Compose、Python 3、宿主机 nginx 和已可用的 HTTPS collector 地址。
不会自动申请域名/证书。实际行为、凭据和回退说明见 [runs.md](runs.md)。

**接手入口：[runs.md](runs.md)**。包含 Windows/DevEco 环境、Linux 主控部署、
Push Kit 开通、验证顺序、已验证和待验证内容，以及提交约定。

- `harmony/`：原生 ArkTS / ArkUI / ArkWeb 工程，最低 API 20。
- `hub/`：主控附属服务，复用已有节点注册和桌面验证保护。
- `deploy/`：现有 Docker 主控的一条启动命令，以及原生 systemd/nginx 部署参考。
- `compose.yml`：仅运行 Waygate；由启动脚本读取 collector 的真实配置后调用。
- `tests/`：不访问股吧的状态、控制、二进制通道和推送签名检查。
- [SPEC.md](SPEC.md)：这次实现的边界和验收要求。

开发验证从仓库根目录运行：

```sh
python -m pip install -r apps/waygate/requirements-dev.txt
python apps/waygate/tools/check_project.py
python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
```

`check_project.py` 仅核对工程配置和相对引用，**不是 ArkTS 编译器**。
HAP 编译、签名和真机验收按 `runs.md` 执行。
