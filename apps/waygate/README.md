# Waygate

鸿蒙上的采集控制与人工验证入口。待办、现有 H5 控制台、临时同出口验证、
单次探测和通知放在一起。手机仅配置主控地址和当前 `console.token`。
最新纠正：验证要复用桌面命令，改由手机直接 SSH 到 worker 的原有 Mihomo，
不再把主控 SSH 配置作为新路径的前提。服务端描述接口已提供；手机 SSH 和
验证页面接线仍待 Windows/App 环境完成，见 [DIRECT_SSH.md](DIRECT_SSH.md)
及 [runs.md](runs.md) 顶部。首次直连需在手机保存 worker 的已有 SSH 登录凭据；
当前已安装 App 仍执行旧的主控转发流程。
首页右上角“诊断”可查看并长按复制连接故障；同步恢复后首页错误提示自动清除，
历史故障仅保留在当前 App 进程的诊断记录中。手机改动需重新构建安装 HAP。

在现有 Linux/Docker 主控服务器上，以 root 执行一次启动或升级（替换自己的域名）：

```bash
git pull && bash apps/waygate/deploy/start.sh --public-url https://你的域名/collector
```

从仓库根目录执行。脚本只启动独立 Waygate 容器，复用现有 collector 的端口、
令牌和节点注册。需要 Linux、Docker Compose、Python 3；不查、不改、不 reload nginx，
也不要求公网地址先能访问。Waygate 固定监听 `127.0.0.1:8791`。
`/collector` 的 nginx 片段 [deploy/collector.nginx.conf](deploy/collector.nginx.conf) 随 Git 提供，
无需先成功启动容器。首次公网接入时，将
`include /实际仓库/apps/waygate/deploy/collector.nginx.conf;`
加入原 collector 所在的 nginx server 块，再按现有方式 reload。原 H5 路由保留。
已使用旧 `.runtime/nginx/waygate-locations.conf` include 的服务器，替换为上述路径；
其他 URL 前缀由启动脚本在 Docker 操作前生成对应片段。
完整命令和凭据说明见 [runs.md](runs.md)。
启动输出分别显示接口就绪和 collector 同步结果。若接口已就绪但同步失败，
会显示具体原因并保留容器运行，后台每轮间隔 10 秒重试；失败原因也写入 Docker 日志。

**接手入口：[runs.md](runs.md)**。包含 Windows/DevEco 环境、Linux 主控部署、
Push Kit 开通、验证顺序、已验证和待验证内容，以及提交约定。

- `harmony/`：原生 ArkTS / ArkUI / ArkWeb 工程，最低 API 20。
- `android/`：同功能的 Android（Kotlin）移植，SSH 用 sshj、WebView 代理用 androidx.webkit。
  代码已就绪，但本机没有 Android SDK/Gradle，**尚未编译、未在真机验证**。
- `hub/`：主控附属服务，复用已有节点注册和桌面验证保护。
- `deploy/`：现有 Docker 主控的一条启动命令，以及原生 systemd/nginx 部署参考。
- `compose.yml`：仅运行 Waygate；由启动脚本读取 collector 的真实配置后调用。
- `tests/`：不访问股吧的状态、控制、二进制通道和推送签名检查。
- [SPEC.md](SPEC.md)：这次实现的边界和验收要求。

开发验证从仓库根目录运行：

```sh
python -m pip install -r apps/waygate/requirements-dev.txt
python apps/waygate/tools/check_project.py
python apps/waygate/tools/check_android.py
python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
```

`check_project.py` 仅核对工程配置和相对引用，**不是 ArkTS 编译器**。
`check_android.py` 只做静态一致性核对（资源引用、`R.id`,包路径、两平台
`channel.json` 逐节点一致、凭据未被 Git 跟踪），**不是 Kotlin 编译器**。
HAP 编译、签名和真机验收按 `runs.md` 执行。

### Android 移植（`android/`）

与 `harmony/` 同一份能力面，共用同一套内置通道配置：`android/app/src/main/assets/channel.json`
与 `harmony/entry/src/main/resources/rawfile/channel.json` 由 `check_android.py` 逐节点比对，
必须完全一致。实现要点与有意差异：

- SSH 用 **sshj 0.41.1**，Ed25519 依赖自带 BouncyCastle（Android 内置的是精简版，不含它）。
  未选 Apache MINA sshd：其默认传输使用 `AsynchronousSocketChannel`，Android 的 libcore 不实现。
- WebView 进程级代理用 **androidx.webkit `ProxyController`**（需 `WebViewFeature.PROXY_OVERRIDE`），
  与 ArkWeb ProxyController 对应；应用和清除都是异步的，调用方必须等回调。
- 本地代理对每个浏览器连接直接开 `direct-tcpip` 通道（比鸿蒙端少一跳），
  白名单仍在手机侧判定：出口核对前只放行 `api.ipify.org`，`markReady()` 后才放行 `source_domains`。
- 工作流程、错误文案、单次探测语义和 claim 规则与鸿蒙端一致；
  唯一有意增加的字段是 `NodeProfile.pinned`（按“全部已固定指纹”比对，
  避免鸿蒙端“按导入时那一把密钥原始字节比对”在换算法时被误拒）。
- 主控令牌与节点凭据存 `SecureStore`（Android Keystore AES-GCM），
  主控地址、deviceId、待核对 claim 存 SharedPreferences。
- `minSdk 28`：避开 API 26/27 平台自带旧 BouncyCastle 与打入的 1.84 冲突。
- 私钥放 `android/app/src/main/assets/channel/phone-ed25519.pem`，**git-ignored**，仅本地构建带；
  缺该文件时退回手动填写凭据的形式。
- **未编译验证**：本机没有 Android SDK/Gradle；仓库也不含 `gradlew`/`gradle-wrapper.jar`
  （二进制文件），需用 Android Studio 打开 `android/`，或先执行
  `gradle wrapper --gradle-version 8.9` 生成 wrapper 再 `./gradlew assembleDebug`。
  打包后签名与真机验收同样按 `runs.md` 执行。
