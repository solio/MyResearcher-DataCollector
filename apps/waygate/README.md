# Waygate

鸿蒙上的采集控制与人工验证入口。待办、现有 H5 控制台、临时同出口验证、
单次探测和通知放在一起。手机仅配置主控地址和当前 `console.token`。

**接手入口：[runs.md](runs.md)**。包含 Windows/DevEco 环境、Linux 主控部署、
Push Kit 开通、验证顺序、已验证和待验证内容，以及提交约定。

- `harmony/`：原生 ArkTS / ArkUI / ArkWeb 工程，最低 API 20。
- `hub/`：主控附属服务，复用已有节点注册和桌面验证保护。
- `deploy/`：一个 systemd 服务和一个 nginx location 示例。
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
