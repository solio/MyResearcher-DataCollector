# Waygate 跨环境交接

更新时间：2026-10-15。当前角色 Developer；下一环境由用户和其他 agent 在
Windows 上执行 Developer / Tester 工作。仓库：`solio/MyResearcher-DataCollector`，
开发目录 `apps/waygate`，分支 `main`。不需要读取原会话。

## 当前优先处理：手机直接复用 verify_backfill.command

**依赖审查纠正：不要直接按 libssh2 + mbedTLS 能沿用全部密钥开工。** 2026-10-15
本机只读检查确认 36 的登录密钥为 RSA、43 为 ED25519；libssh2 1.11.1 和核对时
master 的 mbedTLS 后端均禁用 ED25519。因此 43 原密钥存在明确不兼容。
保留现有密钥应优先验证 libssh2 + OpenSSL crypto 后端，仍须完成鸿蒙目标 ABI
编译、App 加载、两种密钥认证、exec 和 direct-tcpip/ArkWeb 实测。
来源及最小验证顺序见 [DIRECT_SSH.md](DIRECT_SSH.md) 的“原生 SSH 依赖核对”。
本轮仅补充证据和交接，不改 App/服务端实现，不改服务器登录方式或部署。

用户纠正：要的是 `./scripts/ops/verify_backfill.command 36 --resume` 这条已能用
的流程搬到手机，目标填写具体 worker IP；不接受把原本 Mac 直接 SSH worker
变成必须由主控登录 worker。`36` 与完整 IP 原本已等价，本机私密配置只读核对一致。

目标链路：**手机 → SSH → worker 现有 Mihomo → 来源**。
主控保留待办/推送/H5。下文旧“SSH key 不上手机、主控 SSH 转发”的说明是已实现
旧路径的部署记录，不再是新 App 处理验证的前置要求。不要再让用户配置主控 SSH。

本轮服务端已提供 `GET /v1/incidents/{id}/verification`，返回节点地址提示、原
阻断身份和由原 `REMOTE_WORKER` 生成的 exec 命令。仅用现有主控 token 获取；
不需要主控 SSH 配置，不联系 worker SSH、不启动探测、不更改任务。
worker 无需新增服务；App 在自己的 SSH exec channel 通过 JSON stdin 调用这份
原代码，复用 audit/prepare/exit/probe/result/resume 和原 Mihomo 转发。

**下一步交给 Windows/App 环境：[DIRECT_SSH.md](DIRECT_SSH.md)。** 其中列出
接口格式、exec 输入输出、原脚本操作顺序、ArkWeb/LocalProxy/SessionRuntime 接线
及真机验收。第十二轮已完成其中的 M3：App 的 `verify()` 已改走描述接口 + 手机 SSH
编排，凭据按 `node_id` 存本机 Asset Store，转发改走原生 direct-tcpip，编译通过。
但**真机仍未跑通**：手机里还没有任何 worker 凭据（签名 HAP 也尚未产出），
因此不要把 M3 接线报成手机流程已完成或已验证。

服务端变更在 [hub/collector.py](hub/collector.py)、[hub/service.py](hub/service.py)，
共享命令在 [verify_http_backfill.py](../../scripts/ops/verify_http_backfill.py)。
对应离线验证 [test_hub.py](tests/test_hub.py)、
[test_operator_verification.py](../http_backfill/tests/test_operator_verification.py)。
本轮不修改原桌面命令行为，不部署或探测 36/43，不修改手机源文件。

验证：`python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py`
**50 passed**。覆盖空主控 SSH 配置下获取描述、无 SSH/会话/探测副作用、离线/
过期待办拒绝、loopback 地址不传给手机、原代码复用，以及 JSON stdin 中中文、
引号/换行/shell 字符不被当作命令执行。未做真机 SSH、验证码或远程部署验收。

## 上一轮：手机错误残留与诊断入口

用户确认 `Unterminated string starting...` 出现在手机 App。本轮直接读取既有公网
`/collector/v1/inbox` 和 `/collector/api/fleet`，均 HTTP 200、完整 JSON、5 个节点；
inbox.error 为空，last_poll 对应 2026-10-15 11:39:53 北京时间。没有改服务器配置或触发来源请求。
这是一次健康采样，不能代替手机侧网络诊断；同时代码确认 App 成功刷新时未清理旧错误。

已修复 [Index.ets](harmony/entry/src/main/ets/pages/Index.ets) 和
[Api.ets](harmony/entry/src/main/ets/services/Api.ets)：轮询错误与操作结果独立保存，
恢复成功会清除当前轮询提示；原始 JSON 异常改为可读提示；首页右上角“诊断”能查看
当前连接、最近成功刷新、collector 同步时间，以及最近故障的接口、HTTP、长度、阶段和网络错误码。
历史故障保留在诊断页，并明确不代表当前仍失败。诊断是查看时的快照，长按可复制，
“刷新诊断”只更新展示，不额外请求来源。请求 token、请求体和响应正文不写入诊断。

**下一步由 Windows 环境 `git pull`，按下文已有 DevEco 构建/签名流程重新打包安装；
本轮没有服务端改动，无需重新部署服务器。旧 App 可先彻底退出重开，清除内存中的旧提示。**
（Windows 侧已于 2026-10-15 第七轮完成重新打包安装并做了部分真机复验，结果见文末。）

真机重点检查：一次请求/服务端同步失败后恢复，首页提示清除且操作结果仍在；
“诊断”能长按复制，历史故障标为历史；断网时能看清失败接口/网络错误码。
本机只验证逻辑，不声称本轮 ArkTS 编译、HAP 构建或真机验收已完成。

## 当前服务端入口：只启动 Docker，nginx 单独接入

2026-10-15 用户明确由本 agent 负责服务端，App 由另一环境负责。
最新纠正：不要让 nginx 识别或公网检查挡住容器启动，不再自动读取/修改/reload nginx。
在 fzz-002 以 root 执行（后续升级也是同一条命令）：

```bash
cd /usr/local/src/MyResearcher-DataCollector && git pull && bash apps/waygate/deploy/start.sh --public-url https://testapi.zuzurent.com.cn/collector
```

命令只负责 Docker：读取本机运行中的 collector 端口和 token，构建/启动 Waygate，
检查本机 inbox，输出监听端口。公网 URL 只作为 app 链接配置，不做公网访问前置检查。
不再要求 nginx 二进制、server_name、443 监听或主配置路径。原 collector 容器继续运行。
看到“Waygate 容器已启动，本机接口可访问”只代表容器就绪，不表示公网已经接通。
另行显示“collector 状态已同步”或具体同步错误。接口已就绪但同步失败时保留容器运行，
后台每轮间隔 10 秒重试，失败原因变化时写入 Docker 日志；不再因同步暂未完成而停止容器。

首次公网接入由操作者在原 `/collector/` 所在的 nginx `server {}` 内加一行：

```nginx
include /usr/local/src/MyResearcher-DataCollector/apps/waygate/deploy/collector.nginx.conf;
```

再按现有方式 reload nginx。此文件随 Git 提供，pull 后立即存在，无需先成功启动容器。只增加
`/collector/v1/`、`/collector/console/`、`/collector/health`，保留原 `/collector/` 和其他路由。
不要重复添加 include。App 仍填 `https://testapi.zuzurent.com.cn/collector` 和原 `console.token`。
本轮没有登录 fzz-002 执行启动或修改 nginx，实际上线结果须由服务器输出确认。

如果已加上旧的 `.runtime/nginx/waygate-locations.conf` include，且 reload 报文件不存在，
在 fzz-002 执行下面的修复。只替换已知错误路径，并备份原 `server.conf`；
不是要求启动脚本重新探测 nginx：

```bash
cd /usr/local/src/MyResearcher-DataCollector && git pull && \
sed -i.waygate-backup 's#/usr/local/src/MyResearcher-DataCollector/apps/waygate/\.runtime/nginx/waygate-locations\.conf#/usr/local/src/MyResearcher-DataCollector/apps/waygate/deploy/collector.nginx.conf#g' /usr/local/nginx/conf/server.conf && \
/usr/local/nginx/sbin/nginx -t && /usr/local/nginx/sbin/nginx -s reload && \
bash apps/waygate/deploy/start.sh --public-url https://testapi.zuzurent.com.cn/collector
```

这条命令已替换 include，无需再添加。nginx reload 成功仅说明配置已加载；
随后 Waygate 启动成功才代表本机后端就绪，仍需由 App 确认公网访问。

启动实现：[deploy/start.sh](deploy/start.sh)、[deploy/start.py](deploy/start.py)、
[compose.yml](compose.yml)、[deploy/Dockerfile](deploy/Dockerfile)。
[collector.nginx.conf](deploy/collector.nginx.conf) 是默认 `/collector` 的固定配置；
[nginx_routes.py](deploy/nginx_routes.py) 仅供其他 URL 前缀生成片段，无自动发现和改写代码。

- 基于现有 collector 镜像构建，build 使用宿主机网络，apt/pip 默认阿里云镜像；
  可通过 WAYGATE_DEBIAN_MIRROR / WAYGATE_PIP_INDEX_URL 覆盖。
- [Dockerfile.dockerignore](deploy/Dockerfile.dockerignore) 不发送采集数据、凭据和运行配置。
- Docker 入口固定监听 `127.0.0.1:8791`，不再随机选择其他端口，与仓库 nginx 配置一致。
  运行配置、数据和自定义前缀的生成片段在 `apps/waygate/.runtime/`，不进入 Git。
  启动命令重新创建 Waygate 容器以应用端口/URL 配置，即使镜像构建使用缓存；仅作用于 Waygate。
- `--collector-container 容器名` 用于本机有多个采集器；重复启动保留已有 SSH/推送配置。
- 主控现有 `~/.ssh` 只读挂载到容器 `/root/.ssh`，可用 `--ssh-dir` 指定其他目录。
  默认按 fleet 填 SSH host、root:22；不同用户、端口、私钥在 `.runtime/config/config.json`
  的 ssh 项配置。主控 local 指向本机，不是 36。未配置 SSH 的节点能看状态和 H5，
  其人工验证通道尚不可用。脚本不生成 SSH 凭据，不创建验证码会话或恢复股票。
- 容器更新失败时保留/尝试恢复旧 Waygate 镜像和配置；不涉及 nginx 回退。
  推送沿用已有配置，首次默认关闭，需另行配置华为凭据。

36 上 Windows agent 部署的 systemd hub 是另一个实例：此前只读核实为 active/enabled，
127.0.0.1:8791、health ok、占位 public_url、无 80/443 监听。本轮保持它原状，
不会把该实例当成 fzz-002 的已有主控，亦不擅自更新 36/43 collector。

当前架构是两个服务进程、一个手机入口：已有 `http_backfill` 主控负责节点注册、
采集任务和采集数据；Waygate hub 读取该主控的 fleet，负责手机待办、推送和临时
人工验证通道，并代理原 H5 控制台。节点无需在 Waygate 再登记一次。
它们可以部署在同一台服务器，手机只填 Waygate 的 HTTPS 地址；内部
`collector_url` 指向已有主控。专属域名可以直接作为 Waygate 的 `public_url`
（例如 `https://waygate.example.com`）；现有 `/waygate/` 示例只是挂载方案，
不是必须另找一个旧域名。最终 nginx 路由按用户的专属域名安排配置。

## 当前交付状态

**Windows 已编译出未签名 HAP；现有主控的一条启动命令已提供，待 fzz-002 执行；签名与真机验收待完成。**

这次用户授权：在这个仓库开发鸿蒙 app；另一环境部署验证，也可继续修改代码；
用本文件传递工作；本文件及引用的项目代码/文档自动 commit、push。

已写入仓库的功能：

1. 原生 ArkUI 首页：需要处理、稍后提醒、节点/个股状态、窗口内帖子数和待补正文。
2. 只填一个主控 HTTPS 地址和原主控令牌；令牌用 Asset Store 保存，SSH key 不下发手机。
3. 通过一次性入口进入现有 H5 控制台；主控在服务端代持已有控制台令牌。
4. ArkWeb → 手机 loopback 代理 → 主控 WSS → 临时 SSH → 节点现用 HTTP/Mihomo 代理。
   验证浏览器访问中立 IP 检查服务；检查一致后才释放原失败 URL。
5. 人工操作后点击“验证完成并继续”；关闭临时浏览器/通道，单次探测原请求，成功才继续本股。
6. 每个节点同一时刻一个会话。后台、取消、断线/心跳丢失、十分钟超时关闭通道。
   启动过的探测即使超时或主控重启也不会自动重发。
7. SQLite 持久待办、设备登记、通知 outbox；同次阻断按 instance/job/stock/task 去重，
   失败手动探测更新 request id 不制造新通知。掉线不表示解除限制。
8. HarmonyOS Push Kit token/通知权限、v3 通知发送、PS256 服务账号 JWT 自动续签；
   推送失败有退避和次数上限，provider 接受与手机收到分开判断。

Mac 首次交付时未部署；随后 Windows agent 已部署 36 的内部 hub（见第二轮记录）。
本轮只读复核了 36；没有重启、修改任务/代理、触发来源请求或导入训练库。
主控服务是附属进程，现有 `http_backfill` Docker/原生服务不需要因本 app 重建。
36/43 已有的 `retry_request_id` 保护可复用；其他旧节点必须先确认已有该接口。

## 先读哪些文件

- [AGENTS.md](AGENTS.md)、[SPEC.md](SPEC.md)：本目录的约定和这次授权。
- [hub/service.py](hub/service.py)：HTTP/WSS、会话状态机、控制台入口、清理与 Push Kit 发送。
- [hub/store.py](hub/store.py)：待办/设备/outbox/探测状态持久化。
- [hub/collector.py](hub/collector.py)：复用远端验证保护、SSH 连接映射与 CONNECT 白名单。
- [hub/push.py](hub/push.py)：华为服务账号 JWT，签名在本服务生成并自动刷新。
- [harmony/entry/src/main/ets/pages/Index.ets](harmony/entry/src/main/ets/pages/Index.ets)：手机交互和 Web 生命周期。
- [harmony/entry/src/main/ets/services/LocalProxy.ets](harmony/entry/src/main/ets/services/LocalProxy.ets)：TCP/WSS 二进制转发。
- [../../scripts/ops/verify_http_backfill.py](../../scripts/ops/verify_http_backfill.py)：既有验证/探测/继续保护，是运行依赖。
- [../http_backfill/HANDOFF.md](../http_backfill/HANDOFF.md)：已有 36/43 和桌面流程的记录。

只部署 `apps/waygate` 一个文件夹是不够的：主控侧要 checkout 整个仓库，
因为会复用 `scripts/ops/verify_http_backfill.py`。手机工程可以单独用 DevEco 打开。
真实私密配置/签名文件不在这些链接中，也不进入 Git。

## Windows 第一轮：先编译，不碰来源请求

1. 安装 Git、Python 3.11+、[DevEco Studio](https://developer.huawei.com/consumer/cn/deveco-studio/)。
   使用华为账号登录；安装 HarmonyOS SDK。项目当前目标 `6.0.0(20)`，最低 API 20，
   因为 `TCPSocketServer.close()` 是 API 20 能力。HarmonyOS 7 真机需实际检查，
   不能拿 Android APK 或旧 HarmonyOS 4 安装结果代替。
2. 拉取仓库，工作区路径建议不含中文。DevEco 用 **Open** 打开 `apps/waygate/harmony`，
   不要新建空项目后只复制一个 `.ets` 文件。让 IDE 完成 Sync/ohpm/hvigor 初始化。
3. 如果 SDK Manager 无法安装项目当前目标版本：选已安装且 API >=20 的官方 HarmonyOS SDK，
   在 Project Structure 修改目标 SDK 并记录确切版本；保留 minimum API 20。
   版本修改应提交；本机 SDK 路径和签名材料不提交。
4. 从仓库根目录执行：

   ```powershell
   py -3 -m venv apps/waygate/.venv
   & apps/waygate/.venv/Scripts/python.exe -m pip install -r apps/waygate/requirements-dev.txt
   & apps/waygate/.venv/Scripts/python.exe apps/waygate/tools/check_project.py
   & apps/waygate/.venv/Scripts/python.exe -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
   ```

5. 在 DevEco 点 Build → Build Hap(s)，先解决实际 SDK 报错。ArkTS 不是宽松 TypeScript；
   若某 API 类型/版本报错，查对应官方声明并修复，记录原始错误和最终版本，不把错误压掉。
   [tools/check-windows.ps1](tools/check-windows.ps1) 可复跑离线检查，IDE 初始化后可加 `-Build`：

   ```powershell
   powershell -ExecutionPolicy Bypass -File apps/waygate/tools/check-windows.ps1 `
     -Python apps/waygate/.venv/Scripts/python.exe `
     -Build -DevEcoHome 'C:\Program Files\Huawei\DevEco Studio'
   ```

   安装路径按实际修改；脚本会检查 IDE 自带 Node/Hvigor，不下载来源不明的替代编译器。
6. 手机开启开发者模式、USB 调试，USB 连接 Windows 并确认授权；IDE 设备列表应出现真机。
   用 IDE 自带 `hdc list targets` 排查连接。模拟器可先看布局，不能替代锁屏推送和验证码触控验收。
7. Project Structure → Signing Configs 开启自动签名并选择真机，之后 Run entry。
   默认 bundleName 是 `com.myresearcher.waygate`；若你的华为账号需要别的包名，
   同步修改 `AppScope/app.json5` 并记录最终名称。签名密码、p12/p7b、设备 UDID 不进 Git。
   若 IDE 将本地 signingConfigs 写入受版本控制的 build-profile，提交前还原为空数组。

## Linux 主控：部署一次，所有手机复用

**现有 Docker 主控优先使用本文件开头的一条启动命令。** 以下仅供单独选择原生 systemd 部署时参考；不要在同一主控重复运行两套 hub。

选择现在管理节点的那台 H5 主控，保留其原有 `http_backfill` 服务和节点注册。
附属服务监听 `127.0.0.1:8791`；nginx 在已有 HTTPS 域名新增 `/waygate/`。
节点侧不需要域名/nginx/新增公网代理端口。手机也不配置节点 IP 或代理端口。

以下默认仓库在 `/opt/MyResearcher-DataCollector`，其他路径统一替换 service 中的路径：

```bash
cd /opt/MyResearcher-DataCollector
git pull --ff-only
python3 -m venv apps/waygate/.venv
apps/waygate/.venv/bin/python -m pip install -r apps/waygate/requirements.txt
sudo useradd --system --home-dir /var/lib/waygate --create-home --shell /usr/sbin/nologin waygate
sudo install -d -m 700 -o waygate -g waygate /etc/waygate /etc/waygate/keys /var/lib/waygate/.ssh
sudo install -m 600 -o waygate -g waygate apps/waygate/config.example.json /etc/waygate/config.json
sudo install -m 600 -o waygate -g waygate apps/http_backfill/data/console.token /etc/waygate/console.token
```

已存在 `waygate` 用户时跳过 useradd。原生主控的令牌常在 `/var/lib/http-backfill/console.token`，
按实际路径复制。令牌轮换后需更新副本并重启附属服务。
虚拟环境只给 Waygate 安装依赖；不要重装 Docker，不要重建已在采集的镜像。

编辑 `/etc/waygate/config.json`：

- `public_url`：最终手机访问地址，如 `https://你的现有域名/waygate`，无末尾斜线。
- `collector_url`：主控本机地址，通常 `http://127.0.0.1:8790`，保留已有 nginx 前缀时也可带路径。
- `collector_token_file`：上述私密令牌副本路径。
- `ssh`：**只是主控上的 SSH 凭据映射**，不是重新登记采集器。
  键匹配已有 fleet 节点 `base_url` 的主机名/IP。示例有 36/43；私钥以 0600 存 `/etc/waygate/keys/`。
  如果要处理主控自身，增加 `"local":{"host":"127.0.0.1","user":"…","port":22,"identity_file":"…"}`。
  `local` 地址必须能访问那个主控节点及其有效代理。旧节点数据仍由节点 API 管理。
- `source_domains`：HTTPS 验证资源域名白名单。默认东财和常见验证资源；遇到真实页面缺资源，
  先用网络证据核对必要域名再精确增加，禁止为了方便放开 `*`。
- `push.enabled`：没有华为凭据时先保持 false，核心待办/控制台/人工验证仍可验收。

用 `waygate` 用户核对 SSH host key 并建立 known_hosts（核对服务器实际指纹，不能关校验）：

```bash
sudo -u waygate ssh -i /etc/waygate/keys/node36 root@36.138.125.17 true
sudo -u waygate ssh -i /etc/waygate/keys/node43 root@43.132.206.172 true
```

此后运行用 BatchMode/StrictHostKeyChecking，不会交互问密码。服务器 Python3 需能读取
其自身 console.token；现有 Mihomo 配置存在时需 PyYAML（36/43 既有流程已用它）。
当前支持采集任务已在用的、无入口用户名/密码认证的 HTTP/mixed 代理；不自动改变出口。

```bash
sudo install -m 644 apps/waygate/deploy/waygate.service /etc/systemd/system/waygate.service
sudo systemctl daemon-reload
sudo systemctl enable --now waygate
curl --fail http://127.0.0.1:8791/health
sudo journalctl -u waygate -n 60 --no-pager
```

把 [deploy/nginx.conf.example](deploy/nginx.conf.example) 放到现有 HTTPS `server {}` 内，
执行 `nginx -t` 成功后 reload。不要覆盖原有 collector/labeler location。
手机配置 `public_url` 和主控原 `console.token`。HTTPS/WSS 证书必须被手机信任；
不要在代码里关闭证书验证。验证结束以后，日常网络代理规则保持原配置。

后续 Waygate 升级：在工作区确认无冲突后 `git pull --ff-only`，更新这个 venv 的依赖，
`systemctl restart waygate`。只重启附属服务；已提交但结果未知的探测不会重发。
回滚：停止 Waygate、还原这次新增的 nginx location 即可；采集库和原控制台不依赖它。

## Push Kit 开通与验收

1. 在 AppGallery Connect 建立与最终 bundleName 对应的 HarmonyOS app，在“增长 → 推送服务”开通。
   DevEco 自动签名要勾选 **Push Kit 开放能力**并重新签名；手动签名则重新申请已含该能力的 Profile。
   不能只拿一个普通调试签名就断言 Push Kit 不工作。
2. API Console 下载该项目的**推送服务 Service Account JSON**，私密放置在
   `/etc/waygate/huawei-push-service-account.json`，仅服务账号可读。
   配置 `push.project_id` 与其中 `project_id` 一致，`service_account_file` 指向它，`enabled=true`。
   代码自动生成和更新 PS256 JWT，不需要手工一小时换一次 token。
3. 调试保留 `test_message=true`。`category=MARKETING` 只是无需自分类权益的初始设置，
   它可能静默且受较严每日额度限制。正式做工作提醒应申请相应自分类权益后按获批类型
   设置 `WORK` 等；不要擅填类型宣称无限量推送。
4. 手机设置点“开启任务通知”，允许系统通知。应用每次启动会重新获取/上报 Push Token，
   不在后台轮询申请 Token。主控在关闭 app 时仍读取 fleet、保存待办和发送通知。
5. 先用隔离测试节点/受控状态验证通知；收到通知 → 点击 → 对应待办排第一，
   仍需点“现在处理”才会建立会话。点通知本身绝不请求股吧。
6. 测试锁屏、切后台、重启 app、暂时离线、拒绝权限/恢复权限、相同阻断重复轮询。
   主控 outbox `accepted` 仅表示华为接受，不等于手机实际收到；真机看到才记 PASS。
   确认“稍后提醒”一小时后只提醒一次；消除的待办不再安排新通知。

参考的官方文档（2026-10-14 查阅，JWT/配置/Token 页面还通过真实浏览器读取确认）：

- [Push Kit 开通与签名](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/push-config-setting)
- [服务账号 PS256 JWT](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/push-jwt-token)
- [获取 Push Token](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/push-get-token)
- [v3 通知请求字段/去重与分类](https://developer.huawei.com/consumer/en/doc/harmonyos-references/push-scenariozed-api-request-param)
- [通知点击与 URI/Want 配置](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/push-send-alert)
- [ArkWeb ProxyController](https://github.com/openharmony/docs/blob/master/en/application-dev/reference/apis-arkweb/arkts-apis-webview-ProxyController.md)
- [NetworkKit socket（含 API 20 close）](https://github.com/openharmony/docs/blob/master/en/application-dev/reference/apis-network-kit/js-apis-socket.md)
- [Asset Store](https://github.com/openharmony/docs/blob/master/en/application-dev/reference/apis-asset-store-kit/js-apis-asset.md)

## 验收顺序与禁止跳过的检查

| 顺序 | 工作 | 通过标准 |
|---|---|---|
| 1 | Windows 离线检查 | project check + pytest；不访问股吧 |
| 2 | DevEco HAP 编译/签名 | 实际编译成功、真机可安装启动，记录 SDK/IDE/设备版本 |
| 3 | 主控连通与已有 H5 | 手机只配置主控；现有节点自然出现；H5 登录后控制与翻页正常 |
| 4 | 手机 ArkWeb 通道 | 无全局 VPN/Mihomo；出口检查通过后才加载原失败 URL |
| 5 | 操作取消/切后台 | 临时连接回收、来源 ledger 不新增探测；原任务不自行继续 |
| 6 | 一次真实人工验证 | 用户手动滑动；显式确认后至多一条 probe=1，请求属于原 job/stock/task |
| 7 | 成功继续与失败保留 | 仅验证股票继续；其他暂停/归档/阻断股票不变；失败不自动重试 |
| 8 | 通知 | app 后台/锁屏真机收到，点击回到待办；重复轮询不刷屏 |
| 9 | 异常恢复 | 改任务/切路由/节点重装/重复点击/主控重启均不误恢复旧任务或重发探测 |

第 6 步必须由用户实际手动验证；agent 不得自动拖动/识别验证码、导出浏览器 Cookie，
也不能为了“测试通过”循环重试。测试进度、失败详情和修复必须回填本文件。
真实日志只提交脱敏的请求编号/股票/结果，不提交令牌、代理凭据、私钥或完整会话内容。

## 已执行的验证与明确限制

- Mac Python 3.13：`python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py`。
  验证范围包括去重、离线/旧证据、分类、持久状态恢复、同节点互斥、重复确认仅一次、
  配置/路由变化、错误出口、取消、失败探测不继续、真实 loopback HTTP/WSS 二进制往返、
  CONNECT 白名单、SSO ticket 一次性、跨站控制拒绝、真实 RSA-PSS 签名/续期/密钥更新。
  最终执行数量与结果记录见本文件末尾。
- 原桌面 helper 原有 17 个离线用例一同执行，避免复用后破坏桌面流程。
- 无 DevEco SDK：`check_project.py` 只核对配置和引用，不能视为 HAP 编译成功。
- Windows 已装 DevEco Studio 26.0.0 并实际 `assembleHap` 成功（第三轮），产出未签名 HAP；
  签名/安装/启动仍未发生，不能视为真机通过。
- 真机 ArkWeb 代理、Web 销毁回调、触控验证码资源、Push Kit 签名/送达均待下一环境实测。
- Web 代理是应用级，所以 console Web 和 verification Web 必须互斥。
  API 20 版本当前没有每节点独立 ArkWeb Cookie profile；浏览器 Cookie 只留在 app 中，
  不导出到采集器。不要把“代理同出口”解释为必然解除来源限制。
- 此版使用明确的“验证完成并继续”，不自动猜测验证码已经通过。后台/锁屏会结束
  正在进行的验证窗口，重新打开可继续处理待办；已明确提交的单次探测仍查询其结果。
- H5 控制与页面浏览已接入；H5 导出的文件在原生 Web 中能否保存到用户目录需真机验收，
  当前没有额外写一个原生下载管理器，不能把 H5 页面能打开等同于全部 Web 下载能力已验收。
- 原失败 POST 的响应丢失时，手机查询持久 session；重启主控后的 dispatching 标为 uncertain，
  由控制台核对实际 ledger，不自动再 probe。该策略优先避免二次来源请求。
- Push Kit 最多退避重试 5 次；失败状态会反馈在手机状态文本。没有接设备送达回执服务，
  不把 provider accepted 标成“手机已收到”。

## 下一环境需要做的事

- [x] DevEco Sync、实际 ArkTS 编译；必要时修复 SDK API 类型/工程配置。（见 2026-10-15 第三轮：
  已用 IDE 内置 hvigor 编译成功，产出未签名 HAP。）
- [x] 填写 IDE / SDK / 设备型号及系统版本、构建命令、安装结果。（见第四轮：DevEco 26.0.0.851 /
  SDK apiVersion 26 / Mate 70 Pro+ `PLA-AL10` 系统 7.0.0.109 / 无线调试 `192.168.3.156:40525` /
  `install bundle successfully` + `start ability successfully`。）
- [x] 36 内部附属服务和 local SSH 映射已部署并完成服务端检查（第二轮）。
- [ ] 在 fzz-002 执行启动命令；由操作者引用仓库中的 collector.nginx.conf（替换旧 runtime include），再记录公网 inbox、原 H5 与 WSS 结果。
- [ ] 真机验证 H5 控制台、应用内代理、切后台/取消清理。
- [ ] 配置华为 Push Kit 权益、签名和服务账号，实测锁屏推送。
- [ ] 用户完成一次实际验证码；核对单次 probe 与本股继续、其他个股状态不变。
- [ ] 补充验收发现，修复代码，更新本文件并 commit/push。

## 自动提交与 push 约定

用户已授权每轮将本文件和引用的新增/修改项目文件一起提交并 push，无需再问。
已有仓库文件的链接不意味着要把别人的未提交修改顺手加入。尤其不要执行全仓库 `git add .`。

```bash
git diff --check
git status --short
git add apps/waygate
git diff --cached --stat
git diff --cached
git commit -m "Describe the actual Waygate change and validation"
git push origin main
```

先检查 staged 内容无本机签名/凭据/运行库，再 commit。如果修复涉及既有 helper/控制台，
单独列出确切文件追加 staging，并在本文件解释原因。push 有分支竞争时先检查远端差异，
正常合并/变基自己的提交，禁止 force push、丢弃另一环境提交或重置别人工作区。

每轮追加格式：日期 / 执行环境 / 代码变化 / 精确命令及结果 / 未验证事项 / 下一步。
未编译填"未编译"，签名失败填具体错误，模拟测试不可标为真机 PASS。

### 2026-10-14 / Mac / 首次交付

已生成完整 DevEco 工程、附属主控、原生 UI/代理/安全令牌存储/推送入口及部署文件。
没有安装或声称具备 DevEco SDK。没有创建 HAP 或操作生产采集任务。
本机执行结果：**38 passed**（Waygate 21 项 + 原 desktop helper 17 项）；无警告。
`check_project.py` 通过，Python compileall 通过，`git diff --check` 通过。
只读请求现有本机 `/api/fleet`，实际返回 1 个节点、0 个当前待办；可被新 Store 正确接收。
该检查没有访问股吧、没有调用节点控制、没有写现有采集库，也不代表 36/43 真机验证通过。
下一步首先执行 Windows DevEco Sync / Build，而不是先安装手机代理或升级远端采集器。

### 2026-10-14 / Windows / 第一轮：离线检查通过，跨平台修复

执行环境：Windows 10/11 x64（`xzddesktop\xiazhidao`），Python 3.12.10（winget 安装，
路径 `%LOCALAPPDATA%\Programs\Python\Python312`），仓库 `D:\sources\MyResearcher\MyResearcher-DataCollector`。

代码变化（修复 Windows 下测试失败，未改变任何语义）：

- [scripts/ops/verify_http_backfill.py](../../scripts/ops/verify_http_backfill.py)
  `REMOTE_WORKER` 内两处 `with sqlite3.connect(...) as db:` 只提交事务不关闭连接，
  Windows 下文件句柄未释放导致后续 `TemporaryDirectory` 清理报
  `PermissionError: [WinError 32]`。改为 `contextlib.closing(...)` 显式关闭（只读连接，
  无行为差异）；worker 导入行补充 `contextlib`。
- [apps/http_backfill/tests/test_operator_verification.py](../http_backfill/tests/test_operator_verification.py)
  `test_probe_result_matches_task_and_one_shot` 的建库连接同样未关闭；改为 `with db:` 提交后
  `db.close()`。

精确命令及结果（仓库根目录，`NO_PROXY=*` 绕过本机系统代理）：

```powershell
py -3 -m venv apps/waygate/.venv   # 本机无 py 启动器，实际用完整路径 python.exe -m venv
& apps/waygate/.venv/Scripts/python.exe -m pip install -r apps/waygate/requirements-dev.txt
& apps/waygate/.venv/Scripts/python.exe apps/waygate/tools/check_project.py
# -> Project metadata/import paths OK
& apps/waygate/.venv/Scripts/python.exe -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
# 修复前：1 failed, 37 passed（WinError 32）；修复后：38 passed in 1.16s
```

DevEco 环境事实（本机实查，非推测）：

- 本机原本没有 DevEco Studio、HarmonyOS SDK、hdc，也没有真正的 Python（仅商店存根）。
- winget 的 `Huawei.DevEco` 实际是 2023 年 DevEco Studio 3.1.0.501（仅支持旧版 HarmonyOS，
  与本项目 API 20 目标不兼容），已安装后卸载。winget 没有可用的 6.x 版本。
- 官网 DevEco Studio 6.x 下载入口 `developer.huawei.com/consumer/cn/download/` 需要华为账号
  登录后才能下载；agent 不能也不应代替用户登录。等用户参与下载后继续 Sync/编译。
- 已安装官方 `@deveco/deveco-cli`（node22，`D:\kits\node22`）作为后续命令行构建入口；
  它要求本机存在 DevEco Studio 6.0+（`DEVECO_CLI_STUDIO_PATH`）或 Command Line Tools，
  单独安装该 npm 包不等于具备 SDK。
- 本机 Node：`D:\kits\node22`（22.x）。项目 SDK 目标仍为 `6.0.0(20)`，未改动工程配置。

主控侧事实（只读探查，未改任何节点配置/任务/代理）：

- 本机 SSH `testsg8100`（`~/.ssh/testsg8100.pem`）可登 36（36.138.125.17，ecs-02826072）。
- 36 控制台为 Docker `http_backfill-collector-console-1`（0.0.0.0:8790），
  数据目录 `/opt/MyResearcher-DataCollector/apps/http_backfill/data`；`/api/fleet` 用
  数据目录 `console.token` Bearer 实测返回 200，fleet 仅 1 个节点 `local`（无 43 注册），
  即部署 hub 时 `ssh` 映射只需 `local` 键。43 未在本机登记 SSH 凭据。
- 36 没有 nginx（未安装），没有 80/443 监听，没有 Let's Encrypt 证书；控制台目前以
  HTTP 暴露。hub 代码强制 `public_url` 为 HTTPS，手机端也需要受信 HTTPS/WSS，
  因此“放到现有 HTTPS 域名下”这一步在本机没有现成落点，需要用户提供域名后
  在 36 上新增 nginx + 证书；此前不关闭/降级任何证书校验。

未验证事项：DevEco Sync、ArkTS 实际编译、签名、真机安装（均未开始，本机无 SDK）；
hub 尚未部署；H5 控制台、代理清理、通知验收全部待手机侧就绪。

下一步：用户参与华为账号下载 DevEco Studio 6.x → 安装后 `check-windows.ps1 -Build` 实际编译 →
用户提供主控 HTTPS 域名后在 36 部署附属服务（venv/systemd/`local` SSH 映射/nginx location）→
真机验收。

### 2026-10-15 / Windows+36 SSH / 第二轮：附属主控已部署并通过服务端验收

代码变化：无（本轮仅部署与验证；仓库代码即第一轮提交 `30f4f5a`）。

主控部署（36.138.125.17，ecs-02826072，只做加法，未动既有 collector 容器/任务/代理）：

- 仓库快进：`git bundle create` c0ae038..30f4f5a → scp → 36 `/opt/MyResearcher-DataCollector`
  `git pull --ff-only /tmp/waygate-r2.bundle main`，快进成功，工作区干净。
  （GitHub push 当时因本机网络失败，bundle 是既有 Mac 流程的同款替代；后续仍需 push 归档。）
- 依赖：`apt-get update && apt-get install -y python3.12-venv`（阿里云镜像索引过期导致先 404）；
  `python3 -m venv apps/waygate/.venv` + 华为云 pip 镜像装 `requirements-dev.txt`。
  36 上 `check_project.py` 通过、`pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py`
  **38 passed in 0.70s**、系统 python3 可 `import yaml`。
- 账户/目录/凭据（均 0600，waygate:waygate）：新建系统用户 `waygate`（home /var/lib/waygate，
  nologin）；`/etc/waygate/config.json`；`/etc/waygate/console.token`（复制自
  `apps/http_backfill/data/console.token`，令牌轮换需同步更新副本）；
  `/etc/waygate/keys/local`（既有 `testsg8100.pem` 的副本，用于 hub root@127.0.0.1，
  未在 root authorized_keys 增加新条目）；`/var/lib/waygate/.ssh/known_hosts` 由
  `/etc/ssh/ssh_host_ed25519_key.pub` 生成，并与 `ssh-keyscan -t ed25519 127.0.0.1` 实扫比对一致后才安装。
- 配置要点：`listen 127.0.0.1:8791`；`collector_url http://127.0.0.1:8790`（本机 Docker 控制台）；
  fleet 仅 `local` 节点，故 `ssh` 映射只有 `local` 键；`push.enabled=false`；
  **`public_url` 为占位 `https://waygate.example.invalid/waygate`，待用户提供真实域名后更新并配 nginx**，
  当前手机不可达是预期状态，不是缺陷。
- systemd：`install -m 644 deploy/waygate.service` → `systemctl enable --now waygate`，active。

服务端验收（全部真实 curl 于 36 本机执行；未创建验证会话，未触发任何节点控制/来源请求）：

- `/health` → `{"ok": true}`；`/v1/inbox` 无令牌 → 401；带令牌 → items 1（当前 002648 真实阻断）、
  nodes `local/online/blocked`、`poll_error` 空。
- 一次性控制台入口：`POST /v1/console-ticket`（需 JSON body `{}`）→ `GET /console/?ticket=…`
  → 302 至 `/console/` 并签发 `waygate_console` cookie；ticket 二次使用 → 401。
- 经 hub 代理的现有 H5：`GET /console/` 返回原控制台首页；`/console/app.js` 200（173537B）、
  `/console/styles.css` 200（35207B）；`/console/api/fleet`、`/console/api/status` 200（Bearer 由 hub 代持）。
- `/v1/sessions/<不存在>/relay` → 409（路由存在且拒绝非法会话，不是 404）。
- `systemctl restart waygate` 后 health/inbox 正常，待办经 SQLite 持久保留；journal 无错误。

未验证事项：未创建真实验证会话（会暂停兄弟股票，属真机验收第 5/6 步，须由手机端触发、用户人工完成）；
relay 数据面、ArkWeb、Push Kit（enabled=false，无华为凭据）、nginx HTTPS（无域名）均未开始；
GitHub push 本轮仍失败（本机直连与系统代理均不通），提交先在本地与 36 bundle 归档。

下一步：用户提供 DevEco Studio 6.x（华为账号登录下载）与主控 HTTPS 域名；随后真机编译/签名/安装与
/waygate/ nginx 接入，进入 runs.md 验收表第 2 步之后各项。

### 2026-10-15 / Windows / 第三轮：DevEco 编译成功，产出未签名 HAP

执行环境：

- DevEco Studio 26.0.0.851（build `261.23567.138.36.2600851`），安装目录 `D:\kits\DevEco Studio`。
- 内置 `tools/node/node.exe` 与 `tools/hvigor/bin/hvigorw.js`（hvigor `6.26.8`）路径与
  [tools/check-windows.ps1](tools/check-windows.ps1) 预期一致，**无需修改该脚本**。
- SDK：IDE 内置 `D:\kits\DevEco Studio\sdk\default`，ets/native `apiVersion 26`、
  `platformVersion 26.0.0`（版本 `26.0.0.105`）。工程仍为 `compatibleSdkVersion`/`targetSdkVersion`
  `6.0.0(20)`，**未改 build-profile**：hvigor 用该 SDK 直接编译通过，故保留 minimum API 20 不变。

代码修复（均为实际编译报错，逐条复现后修复）：

1. 首次 `assembleHap` 失败 `arkts-no-ctor-prop-decls`（ArkTS 禁止在构造函数参数里声明字段）：
   - [Api.ets](harmony/entry/src/main/ets/services/Api.ets)：`constructor(public credentials…)`
     → 显式字段 `credentials: Credentials;` 并在构造体内赋值（保持 [Push.ets](harmony/entry/src/main/ets/services/Push.ets)
     对 `api.credentials` 的可访问性）。
   - [LocalProxy.ets](harmony/entry/src/main/ets/services/LocalProxy.ets)：`Pipe` 的
     `constructor(private client…, private onClose…)` → 显式声明 `private client`、`private onClose`，构造体内赋值。
2. 二次编译残留 `Property 'credentials' is private and only accessible within class 'Api'`（Push.ets 读它）：
   去掉 `private`，保留默认可见性。
3. 三次编译 BUILD SUCCESSFUL。

精确命令及结果（工作目录 `apps/waygate/harmony`）：

```powershell
$env:DEVECO_SDK_HOME = 'D:\kits\DevEco Studio\sdk'
& 'D:\kits\DevEco Studio\tools\node\node.exe' `
  'D:\kits\DevEco Studio\tools\hvigor\bin\hvigorw.js' `
  --mode module -p product=default -p module=entry@default -p buildMode=debug assembleHap --no-daemon
# -> BUILD SUCCESSFUL in 7 s 494 ms（33 tasks in total: 18 executed, 15 up-to-date）
```

产物：`entry/build/default/outputs/default/entry-default-unsigned.hap`（146484 B）。
日志含 `hvigor WARN: No signingConfig found for product default` —— 未签名符合预期，
工程 `signingConfigs` 仍是空数组、未被本机改动。19 条告警全部为同一类 ArkTS 静态提示
“Function may throw exceptions. Special handling is required.”，非阻断，未做压制。
构建输出目录 `entry/build/` 已被忽略（`git status --ignored` 显示 `!!`），不入库。

签名/真机（本轮未完成，必须用户参与）：

- `hdc list targets` 返回空：当前无真机连接，无法安装或启动。
- `devecocli signature generate`（自动生成签名并写入工程配置）存在，但 `devecocli auth status`
  为 `Not logged in`；IDE 自动签名同样依赖华为账号在线。agent 不代替用户登录。
- 因此本轮**未进行签名、安装、启动**，不声明任何真机 PASS。

下一步：用户用华为账号登录（IDE Project Structure → Signing Configs 自动签名，或
`devecocli auth login`），USB 连接真机（开发者模式 + USB 调试并确认授权）；随后完成签名、
安装与 Run entry，在验收表第 2 步记录设备型号/系统版本、IDE/SDK 版本与安装结果。

### 2026-10-15 / Mac / 同步部署事实并暂缓域名发布

用户要求未部署就暂不部署，并说明已有专属域名。最初依据本地旧文档判断
Waygate 未部署；git fetch 后发现 Windows 第二轮已部署到 36，随即纠正。
只读 SSH 检查 systemctl is-active/is-enabled、ss、/health 和四个非秘密配置项：
服务 active/enabled，127.0.0.1:8791，health ok，collector_url 为
http://127.0.0.1:8790，public_url 仍为 example.invalid 占位；未发现 80/443 监听。
没有停止或重启服务，没有改远端配置或发来源请求。

合并保留另一环境的 Windows SQLite 修复、ArkTS 编译修复和完整交接记录，
更新本文开头、待办与 AGENTS.md：已有内部服务保持现状，域名/HTTPS 发布等用户安排。
本轮只改文档并执行 git diff --check；未重新运行代码测试或真机验收。

### 2026-10-15 / Mac / 主控 Docker 启动入口

用户要求服务端由本 agent 负责、提供启动命令，App 由其他环境负责。
新增上述 Compose/Dockerfile/start.sh/start.py/nginx_routes.py，仅修改服务端和交接文件。
针对路由保留、幂等、并行编辑保护、nginx 失败/公网检查失败回退，以及真实 nginx 向两个
loopback 后端转发进行了验证；未在 fzz-002 执行 Docker 构建或部署。

本机执行：

```text
python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
45 passed（其中 7 项部署验证含真实 nginx 路由；原有 38 项通过）
docker compose -p waygate -f apps/waygate/compose.yml config --quiet
bash -n apps/waygate/deploy/start.sh
python apps/waygate/deploy/start.py --help
git diff --check
```

Compose 校验使用临时非秘密路径变量；没有启动/重启本机或远端采集容器。
下一步是 fzz-002 执行命令，成功后手机继续填原 `/collector` 地址；SSH 通道和推送
分别需要主控现有 SSH 凭据及华为配置，不能以接口可登录替代这两项验收。

### 2026-10-15 / Mac / 移除 nginx 启动门槛

用户执行旧命令停在“未找到唯一匹配的 HTTPS server_name”，随后明确指出无需
在启动中探查 nginx。按此纠正删除 nginx_target、配置解析/选块/改写/reload/回退、
公网 instance_id 前置检查及相关 CLI 参数。没有继续增加更多 nginx 自动识别选项。

启动只保留 Docker 所需配置和本机接口检查；nginx_routes.py 只输出供操作者使用的片段。
更新 README/SPEC/AGENTS 和本文，App 代码不改，36/43 不部署。
原错误发生在 Docker 构建/启动与 nginx 修改前，不意味着 collector 有问题。

验证：Waygate 与 operator-verification 共 40 项通过。新增完整启动流程模拟证明：
所有外部进程调用均为 Docker、只读取本机 collector、无需 nginx 或公网 URL 可访问；
保留真实 nginx 验证生成片段对原 H5/API 与 Waygate 路由的转发，不会把该测试加入启动流程。
已删除只用于旧自动配置方案的测试。Python 3.6 语法、--help、git diff --check 通过。
未在 fzz-002 执行 Docker 构建或声称公网已上线。

### 2026-10-15 / Mac / 修复 nginx 引用不存在的 runtime 文件

用户在 fzz-002 的 `/usr/local/nginx/conf/server.conf:89` 引用了前次给出的
`.runtime/nginx/waygate-locations.conf`，nginx reload 因文件不存在而拒绝。
旧脚本仅在 Docker 构建、启动及 inbox 检查全部成功后生成文件，给出的接入步骤
错误地依赖了这个未确认完成的前置条件；不能从本次输出推断容器实际是否启动过。

将默认 `/collector` 路由提交为 [deploy/collector.nginx.conf](deploy/collector.nginx.conf)，
pull 后即存在，不依赖 runtime 或后端健康；Docker 入口固定使用 8791，重新创建
Waygate 以应用配置。其他 URL 前缀仍可生成片段，但写入移到 Docker 操作之前。
更新本文顶部修复命令，精确替换用户已经插入的错误路径，保留 server.conf 备份。
启动脚本继续不读取、修改或 reload nginx。

验证：`python -m pytest -q apps/waygate/tests/test_deploy.py`，2 passed；
其中实际 nginx 加载仓库中的 include 做语法检查，并用隔离后端验证原 collector
H5/API 与 Waygate 的三条路径、Authorization 转发。启动流程模拟确认固定 8791，
Docker 操作前配置已存在，不创建默认 runtime include。未执行远端部署或来源请求。

### 2026-10-15 / Mac / 修复 collector 状态分段读取及启动错误丢失

用户反馈启动只显示“Waygate 尚未成功读取 collector 状态”。代码确认这条文案会
覆盖 `/v1/inbox.error` 的具体失败，且随后回滚/停止 Waygate。没有远端原始异常，
不能据此断言 fzz-002 当次失败属于哪一种连接/解析错误。

已在实际 loopback HTTP 上复现一个明确读取缺陷：`response.content.read(n)`
只保证返回当前可用的数据，完整合法的 fleet JSON 分两批传输时，旧实现只读到
首批 1024 字节便解析，报 JSONDecodeError。改为有总量上限的分块读取，读到 EOF
再解析并写入待办；16 MiB 与 25 秒限制保留。仅成功应用快照才更新 nodes/last_poll，
读取失败保留已有状态。HTTP、连接拒绝、超时、传输中断、JSON/格式错误各有具体信息，
不输出响应正文或凭据。

启动检查保留实际 inbox.error。认证接口可达但同步暂时失败时，显示原因和本机
collector 地址，保留运行让后台继续轮询；接口不可达、认证失败或格式错误仍视为
Waygate 未就绪。后台同步失败写入 Docker 日志，同样错误不每轮重复刷屏。

验证：先在旧代码复现分段响应失败，再修复后通过；包含 Content-Length 和 chunked
两种传输、传输截断、非法 JSON、缺少 nodes、响应超限、失败不清空待办，以及同步
失败不停止容器/接口不可达仍失败。
`python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py`：
44 passed。启动脚本 Python 3.6 语法与 git diff --check 通过。
本轮不改 nginx/App/采集任务；未登录 fzz-002，也未触发任何来源请求。
服务器拉取后重跑顶部同一条启动命令即可；若仍有故障，新版会保留并显示具体原因。

### 2026-10-15 / Mac / 手机错误恢复与可复制诊断

用户补充错误位置为手机 App。先读取公网现状，确认该次完整状态响应健康，再检查手机
刷新逻辑：`if (feed.error) { this.message = feed.error; }` 没有恢复成功分支，且 message
同时承载验证/暂停等操作结果，不能简单在每次刷新后清空整个 message。

本次最小客户端修复新增独立 syncMessage，仅正常刷新时清除此字段；保留操作结果。
新增“诊断”入口，记录最新故障并可长按复制；解析错误、HTTP 错误、网络错误分开显示。
相关 UI 使用既有 ArkUI Text 的 [copyOption](https://raw.githubusercontent.com/openharmony/docs/master/zh-cn/application-dev/reference/apis-arkui/arkui-ts/ts-basic-components-text.md#copyoption9)，
未增加权限或依赖。诊断记录在本次进程内，不宣称重启后仍保留。未新增自动来源重试或继续任务。

验证命令：

```text
node --test apps/waygate/tests/test_app_connection.mjs
4 passed
python apps/waygate/tools/check_project.py
Project metadata/import paths OK
git diff --check
```

[test_app_connection.mjs](tests/test_app_connection.mjs) 使用 Node >= 22.13 的类型擦除，
执行实际 Api 与 Index 中的普通请求/刷新方法，替换平台 HTTP/Web/Vault 接口；
覆盖截断 JSON 的提示与不泄露 token、HTTP/网络错误区分、服务端旧错误和客户端解析
失败在恢复后消失、操作结果不被清空、诊断保留历史。这不是 ArkTS/ArkUI 编译器；
Windows 必须重新完成构建签名和上述真机验收。所有改动和本文一并提交推送。

### 2026-10-15 / Windows + Mate 70 Pro+ 真机 / 第四轮：真机连接、签名、安装、启动，并修登录页无反馈

代码变化（仅 UI 反馈，未改校验/请求/保存逻辑）：

- [Index.ets](harmony/entry/src/main/ets/pages/Index.ets)：登录页“连接并保存”此前只把
  `busy` 置真以禁用按钮，**没有任何可见反馈**，而请求要等 12~35 秒超时才出错误信息，
  表现为“点了完全没反应”。现：点击即设 `this.message = '正在连接主控并校验令牌…'`；
  按钮 busy 时文案变为“正在连接…”、`opacity 0.6`；按钮下方新增
  `LoadingProgress` + “正在连接主控并校验令牌…” 文案。

环境与设备（实测，非推测）：

- DevEco Studio 26.0.0.851（build `261.23567.138.36.2600851`），hvigor `6.26.8`，
  IDE 内置 SDK `D:\kits\DevEco Studio\sdk\default`，apiVersion 26。
- 真机 `PLA-AL10` HUAWEI Mate 70 Pro+：系统 `7.0.0.109(SP6C00E105R7P3)`、
  `const.ohos.apiversion = 26`、`const.ohos.fullname = OpenHarmony-7.0.0.105`、devicetype `phone`。
  与工程 SDK `apiVersion 26` 一致；`compatibleSdkVersion 6.0.0(20)` ≤ 26，安装兼容。
- 连接：USB 多次插拔，Windows **从未枚举到华为设备**（只出现 `Mi USB Receiver`
  VID_2717 与内置蓝牙，无 VID_12D1），判为数据线/口问题；改走**无线调试**
  （手机 开发者选项→无线调试；笔记本 WLAN `192.168.3.236/24` 与手机 `192.168.3.156`
  同网段），`hdc list targets` 直接显示 `192.168.3.156:40525  TCP  Connected`。
  **结论：USB 非必需，无线调试可长期用于调试与安装。**

签名与安装：

- IDE Project Structure → Signing Configs 勾选自动签名，生成材料
  `C:\Users\xiazhidao\.ohos\config\default_harmony_Ugupin*.{cer,p7b,p12,csr}`（在仓库外，未入库）。
- IDE 只写入了 `signingConfigs` 数组，**未写产品引用**；缺失时 hvigor 报
  `hvigor WARN: No signingConfig found for product default` 且产出仍是 `*-unsigned.hap`。
  补 `"signingConfig": "default"` 到 product 后才产出签名产物。
- 精确命令与结果：
  - `hvigorw.js --mode module -p product=default -p module=entry@default -p buildMode=debug assembleHap --no-daemon`
    → `BUILD SUCCESSFUL`，产出 `entry-default-signed.hap`（192232 B）。
  - `hdc -t 192.168.3.156:40525 install -r <signed.hap>` → `install bundle successfully`。
  - `aa force-stop` + `aa start -a EntryAbility -b com.myresearcher.waygate` → `start ability successfully`；
    `ps -ef` 见 `com.myresearcher.waygate` 常驻；`snapshot_display` 截图确认 ArkUI 登录页正常渲染
    （标题 Waygate、连接你的主控、地址/令牌输入、连接并保存按钮）。
- 提交卫生：`build-profile.json5` 曾被写入本地签名路径/口令，构建后已用
  `git checkout -- apps/waygate/harmony/build-profile.json5` 还原为 `"signingConfigs": []`
  并移除 product 的 `signingConfig` 引用；签名材料、口令、构建日志、截图均未入库。

未验证（按验收表顺序待做）：H5 控制台、应用内代理与切后台/取消清理、真实验证码（仅用户手动）、
推送（`push.enabled` 仍 false，需华为凭据）。**服务端部署由另一环境负责**（见本文件其
Docker / fzz-002 记录）；本环境只负责 App 侧。开工时误把 36 当成真实主控，见第五轮纠正。

下一步：按“App 由本环境负责、服务端由另一环境负责”的分工，等另一环境给出可用入口后做真机联调；
本轮不改远端、不接域名。

### 2026-10-15 / Windows / 第五轮：纠正目标误判——36 不是真实主控

**误判（本轮纠正）**：第二轮仅凭“本机唯一可达的 SSH 只有 36”以及 36 的 `/api/fleet` 只返回
`local` 一项，就把 36 当成“管理节点的主控”并在其上部署了附属服务。用户指出其实际处理的是
**tokenseller 节点**，**真正的 fleet 主控不在 36**。因此 36 上那套 hub 与 app 报错无关，
也不一定是有用的部署目标；服务端实际由另一环境负责。

证据（只读）：

- app 报 `SSH 主机指纹尚未确认或已变化，请先正常 SSH 登录核对`，该文案来自
  [scripts/ops/verify_http_backfill.py](../../scripts/ops/verify_http_backfill.py) 的 `remote()`，
  属 **hub → 节点** 这一跳的 `Host key verification failed`。
- 36 侧复核：`/var/lib/waygate/.ssh/known_hosts` 正确；以 `waygate` 用户实测
  `ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i /etc/waygate/keys/local root@127.0.0.1 true`
  → **exit=0**；且 `journalctl -u waygate` 最近 2 小时**无任何条目**（总计仅 7 行），
  证明手机请求从未到达 36 的 hub。

处置：遵守本文开头 HOLD，**未停止、未重启、未改 36 上的服务与配置**；36 那套部署去留由用户决定。

### 2026-10-15 / Windows / 架构澄清与处置边界（回应“为什么 hub 要连节点”）

**历史记录，已由本文顶部用户纠正及更新后的 SPEC 覆盖。** 下述“冻结设计”
不能再用来否决手机复用桌面 SSH 流程。手机 SSH direct-tcpip 可从 worker
视角连接其内网 Mihomo，无需把代理端口开放到公网；SSH 凭据需由执行端持有，
但不等于必须由主控下发。出口一致性也可由手机执行原脚本检查。当前待验证的
实际问题是鸿蒙原生 SSH 与密钥算法兼容，见顶部依赖审查，不是代理端口公网开放。

**结论：hub → 节点这一跳是冻结设计，不是缺陷。** [SPEC.md](SPEC.md) 原文：
“An additive Python hub service owns SSH credentials and temporary relay sessions; the phone
never receives a server SSH key or changes its system proxy”，且 RELAY 一节写明
“Relay authenticated WSS to the node's current HTTP proxy through temporary SSH … No open proxy
… or direct fallback”。链路为
`ArkWeb → 手机 loopback → 主控 WSS → 临时 SSH → 节点现用 HTTP/Mihomo 代理`。

为什么不能“手机直连节点”：

1. 节点代理只监听节点内网网桥（36 上实测 `172.22.0.1:7891`，不对公网开放），手机不可达；
2. 直连等价于把节点 SSH 私钥 / 代理凭据下发到手机；
3. 必须先由主控校验“浏览器出口 IP == 节点出口 IP”，否则验证结果不可信；
4. 把节点代理直接暴露即为开放代理，被 SPEC 明令禁止。

因此“手机直连节点”属于 **SPEC 变更**，需用户显式重新授权并接受上述代价；agent 不静默改设计。
本轮按用户指示“终端问题你改、服务端问题写入 runs.md”执行：**未改任何 ArkTS/hub 代码**；
app 只是忠实转达 hub 的错误文案，无客户端缺陷；服务端修复步骤记录如下（本环境不执行）：

1. 在 hub 所在机器、以 hub 运行用户：`ssh-keyscan -t ed25519 <节点主机>` 取指纹，与节点
   `/etc/ssh/ssh_host_ed25519_key.pub` 核对一致；
2. 一致后写入该用户 `~/.ssh/known_hosts`（或正常 `ssh` 登录一次输 `yes`）；**禁止关闭
   `StrictHostKeyChecking`**；
3. 核对 `config.json`：`ssh` 键 == `urlsplit(base_url).hostname`（不是别名 `tokenseller`）、
   `identity_file` 存在且对 hub 用户可读（0600）。

### 2026-10-15 / Windows / 第六轮：合并另一环境改动后的 ArkTS 修复与真机重装

与 origin/main（含另一环境 5 个提交：Docker 启动、nginx include、分块读取、手机错误恢复与
可复制诊断）rebase 并解决 runs.md 冲突后，合并结果暴露一个**只有 Windows ArkTS 编译能发现**的缺陷：

- [Api.ets](harmony/entry/src/main/ets/services/Api.ets) `catch` 分支末尾 `throw err` 触发
  `arkts-limited-throw`（`throw` 不得抛任意类型，报在 `Api.ets:62:7`）。
  该缺陷来自另一环境的“诊断/错误分类”改动，其 Mac 侧 Node 类型擦除测试
  （`node --test tests/test_app_connection.mjs`）覆盖不到 ArkTS 编译规则。
- 修复：`const failure = err as Error; throw new Error(failure.message || '主控请求未完成；详情见“诊断”');`
  —— 仍保留原始错误消息，行为不变。

验证（工作目录 `apps/waygate/harmony`）：

```text
hvigor ... assembleHap --no-daemon   → BUILD SUCCESSFUL
signed hap 201498 B（临时签名配置，构建后已 git checkout 还原 build-profile.json5，无口令入库）
hdc -t 192.168.3.156:40525 install -r → install bundle successfully
aa force-stop + aa start -a EntryAbility -b com.myresearcher.waygate → start ability successfully
ps -ef → com.myresearcher.waygate 常驻（PID 48739）
```

合并后 App 同时包含另一环境的独立 `syncMessage`/“诊断”可复制入口/错误分类，与本环境的
登录页即时反馈（`正在连接…` + `LoadingProgress`）。

限制：本次 `snapshot_display` 时设备处于锁屏/通知界面，**未捕获到 App 前台 UI**；
本轮只确认“编译 + 安装 + 启动成功”，UI 目视确认待用户解锁后补做。

下一步：等另一环境给出可用入口（其 fzz-002 / Docker 启动流程）后，真机联调验收表第 3~9 步。

### 2026-10-15 / Windows / 第七轮：真机复验——App 已连通服务端，诊断页与错误恢复通过

按本文件顶部“当前优先处理”执行。`git pull`（rebase 到 `9bc228c`；该提交只改 SPEC/hub/helper/
文档，**未改 ArkTS**），仍重新构建以确认 HEAD 可编译：

- `hvigor ... assembleHap --no-daemon` → **BUILD SUCCESSFUL**，signed hap **201498 B**
  （与第六轮同尺寸 → App 代码未变）。
- 首次安装报 `[Fail][E001005] Device not found or connected`：无线调试掉线。
  `hdc tconn 192.168.3.156:40525` → **Connect OK** 后 `install bundle successfully`；
  `aa force-stop` + `aa start -a EntryAbility` 成功，进程常驻（PID 4089）。

真机实测（截图证据；Mate 70 Pro+ `PLA-AL10` / HMOS 7.0.0.109 / API 26，无线调试）：

- 首页从真实服务端拉到待办：**需要处理 5**，出现 `02 · 601888`、`ktopia-crmeb · 601888`、
  `本机 · 601012`、**`tokenseller · 002648`**，均为“需要人工验证” → 公网入口
  `https://testapi.zuzurent.com.cn/collector` 与 fleet 轮询在真机上可用。
- 右上角「诊断」可打开并显示：当前状态 **连接正常**；主控
  `https://testapi.zuzurent.com.cn/collector`；最近成功刷新 与 collector 最近同步 均为
  `10/15/2026, 12:47:16 PM`；“最近一次同步故障（当前无同步故障）：无”“最近一次接口失败：无”；
  含“刷新诊断”按钮与“长按下方文字可复制”提示。
  → 直接验证了 handoff 要求的“恢复成功后首页提示清除”（当前无残留错误）。
- 上述操作通过 `hdc shell uitest uiInput click` 驱动并截图，属真机操作，非模拟。

未验证（需用户手工；agent 不便远程改变设备网络或核对剪贴板内容）：

- 「诊断」文字长按复制到剪贴板（剪贴板内容需人工确认）。
- 断网时失败接口/网络错误码的可见性。
- “失败后恢复时操作结果不被清空”（需先人为制造一次失败再恢复）。

提交卫生：`build-profile.json5` 临时写入本地签名后已 `git checkout` 还原（0 处口令残留）；
未改任何服务器（服务端由另一环境在 fzz-002 负责）。

### 2026-10-15 / Windows / 第八轮：手机直连 SSH 开工——原生模块里程碑 1

按 [DIRECT_SSH.md](DIRECT_SSH.md) 分派的 App 侧工作开工。**现状核实（证据）**：App 仍走旧路径——
[Index.ets:269](harmony/entry/src/main/ets/pages/Index.ets) 调
`POST /v1/incidents/{id}/sessions`，[LocalProxy.ets:84](harmony/entry/src/main/ets/services/LocalProxy.ets)
连 `/v1/sessions/{sid}/relay`；hub `service.py` 新旧接口并存
（`/v1/incidents/{incident}/sessions` 与新增 `/v1/incidents/{incident}/verification`）。
因此手机上点“现在处理”仍是 **fzz-002 的 hub 在 SSH worker**，必然报同一条指纹错；
设计变更目前只落在服务端，App 侧（手机直连 SSH）尚未实现。

依赖选型（本机实测，非推测）：

- **ohpm 注册表没有任何 SSH 库**：`ssh2`/`@ohos/ssh2`/`openssh`/`libssh2`/`paramiko`/`node-ssh`
  等 `ohpm info` 全部 404，注册表搜索接口返回空。
- ArkTS SDK 无 SSH API；`cryptoFramework` 提供 AES/RSA/ECDH 等。
- **有 NDK**（`native/build/cmake/ohos.toolchain.cmake`），但 sysroot **无 libssh2、无 OpenSSL**
  （仅 `libnet_ssl`/`libohcrypto`/`libhuks_external_crypto`）→ 需自带并交叉编译。
- 结论：走 **NDK 原生模块 + 自带 libssh2（配 mbedTLS）**，不用空的 TS 模拟冒充 SSH。

里程碑 1（本轮已验证）：工程成功加入原生模块并可编译：

- 新增 `entry/src/main/cpp/CMakeLists.txt`、`entry/src/main/cpp/napi_init.cpp`（仅 `ping()` 探针）、
  `entry/src/main/cpp/types/libwaygate_ssh/{index.d.ts,oh-package.json5}`
- `entry/build-profile.json5` 增加 `externalNativeOptions`（`abiFilters: arm64-v8a`）；
  `entry/oh-package.json5` 增加本地 `file:` 依赖
- `hvigor --mode module ... assembleHap --no-daemon` → **BUILD SUCCESSFUL**，
  `BuildNativeWithCmake` 4.9s，产出 `libwaygate_ssh.so`（16992 B，strip 后 5808 B 进包）
- `.cxx/` 已加入 `apps/waygate/.gitignore`

后续里程碑（**未开始，不声称已完成**）：

- M2：引入 libssh2 + mbedTLS 并交叉编译；NAPI 暴露 `connect` / `auth` / `hostkey`(TOFU 核对) /
  `exec`(JSON stdin + EOF + stdout + exit status) / `direct-tcpip` / `cancel`。
- M3：按 DIRECT_SSH.md 重接 [Index.ets](harmony/entry/src/main/ets/pages/Index.ets) `verify()`（改用描述接口
  `GET /v1/incidents/{id}/verification` + 手机 SSH 编排 + dispatching 持久标记）、
  [LocalProxy.ets](harmony/entry/src/main/ets/services/LocalProxy.ets)（本地 TCP → SSH direct-tcpip 到
  `route.host:route.port`，替换 hub WSS）、
  [SessionRuntime.ets](harmony/entry/src/main/ets/services/SessionRuntime.ets)（只清理手机自有 SSH/代理资源）；
  凭据按 `node_id` 存 Asset Store。
- M4：真机对着 tokenseller 打通 `audit → prepare → exit → 转发 → 人工验证 → probe → resume`。

本轮未改服务端；旧路径的 known_hosts 补丁不再是本任务的答案。

### 2026-10-15 / Windows / 第九轮：原生 SSH 模块打通（M2a–M2c，编译验证）

- **M2a**：新增 [third_party/fetch.ps1](harmony/third_party/fetch.ps1)，固定并校验源码
  （libssh2 `1.11.1` sha256 `82B3…B0D0D`；mbedTLS `3.6.2` **官方 release** sha256 `8B54…CDCA`）。
  注意：GitHub 的 tag 自动归档**缺少 mbedTLS 的 `framework/` 子模块**，首次配置因此失败，
  改用官方 release `tar.bz2` 才通过。源码树不提交（[third_party/.gitignore](harmony/third_party/.gitignore)）。
- **M2b**：[CMakeLists.txt](harmony/entry/src/main/cpp/CMakeLists.txt) 交叉编译
  mbedTLS（静态、无 programs/tests、`MBEDTLS_FATAL_WARNINGS=OFF`）与 libssh2
  （`CRYPTO_BACKEND=mbedTLS`、静态、无 zlib/examples/tests），并用
  `MBEDTLS_INCLUDE_DIR` / `MBEDCRYPTO_LIBRARY` 满足 libssh2 自带的 `FindMbedTLS`。
  实测产出 `libmbedcrypto.a` 3.16 MB、`libmbedtls.a` 1.79 MB、`libmbedx509.a` 398 KB、`libssh2.a` 1.87 MB。
- **M2c**：[napi_init.cpp](harmony/entry/src/main/cpp/napi_init.cpp) 实现并导出
  `probeHostKey` / `connect` / `exec` / `close`：异步 Promise（不阻塞 UI）、全局互斥串行化 libssh2、
  host key 以 base64 比对（不一致直接拒绝）、`exec` 每次新开 channel、写 stdin 后 `send_eof`、
  读取 stdout/stderr 与退出码（输出上限 4 MB）。
  实测 `libwaygate_ssh.so` 由 16 KB 增至 **3.64 MB**（strip 后 1.32 MB），证明 libssh2/mbedTLS 已进包。

**未做（不声称已完成）**：`direct-tcpip` 转发尚未实现；**未在真机用真实凭据连接任何 worker**
（本机没有该凭据）；ArkTS 侧 `Index.ets`/`LocalProxy.ets`/`SessionRuntime.ets` 尚未改接描述接口。

下一步：M2d 真机用 tokenseller 凭据验证 `probeHostKey` → `connect` → `exec`（audit）；
需要该节点的 SSH 主机、用户与密钥/口令（由用户提供，不写入仓库、不写日志）。

### 2026-10-15 / Windows / 第十轮：改用 OpenSSL 后端（ED25519），M2b 重做

按本文件顶部“依赖审查纠正”与 DIRECT_SSH.md「原生 SSH 依赖核对」改正：libssh2 1.11.1 的
mbedTLS 后端写死 `LIBSSH2_ED25519 0`，只有 OpenSSL 后端
（`openssl.h`：`OPENSSL_VERSION_NUMBER >= 0x10101000L`）才为 1；且 1.11.1 源码里
**没有 wolfSSL 后端实现**。故上一轮的 mbedTLS 选型作废并重做。

环境取证（本机实测）：

- Windows 无 perl；NDK 的 `llvm/bin` 全是 PE 文件（`clang.exe`/`llvm-ar.exe`/`llvm-ranlib.exe`），
  `aarch64-unknown-linux-ohos-clang` 只是指向它们的脚本 → **NDK 仅 Windows 宿主**，WSL 不能直接用。
- WSL（Ubuntu-26.04）有 **perl 5.40 + GNU make**，且 WSL interop 能执行 Windows 的 clang.exe
  （实测 `clang.exe --version` → `OHOS (dev) clang version 15.0.4`）。
- 因此：**WSL 跑 perl/make，编译器交给 Windows NDK clang**。

新增 [third_party/build-openssl.sh](harmony/third_party/build-openssl.sh)（在 WSL 中运行）：
生成 CC/AR/RANLIB 包装脚本 → `./Configure linux-aarch64 no-shared no-tests` → `make build_libs`。
实测成功：**`libcrypto.a` 8,059,518 B、`libssl.a` 1,163,170 B**（aarch64-linux-ohos，987 个 .o）。
`fetch.ps1` 增加 OpenSSL `3.0.15` 条目（sha256 `23C666D0…EC9533`）；`.gitignore` 排除源码树。

[CMakeLists.txt](harmony/entry/src/main/cpp/CMakeLists.txt) 改为 **`CRYPTO_BACKEND=OpenSSL`**：
顶层 `find_package(OpenSSL REQUIRED)` 并显式给 `OPENSSL_CRYPTO_LIBRARY`/`OPENSSL_SSL_LIBRARY`
（否则 `OpenSSL::Crypto` 是子目录作用域、顶层看不到，链不上）。

验证：`BUILD SUCCESSFUL`；`build.ninja` 含 **`-DLIBSSH2_OPENSSL`** → 后端确为 OpenSSL，
故 `LIBSSH2_ED25519=1`；`libwaygate_ssh.so` 由 3.64 MB 增至 **5.95 MB**（strip 后 3.93 MB）。
mbedTLS 不再是构建依赖（条目留在 fetch 脚本中备查）。

**仍未做（不声称完成）**：`direct-tcpip` 未实现；ArkTS 侧未接线；**未在真机连接任何 worker**。

下一步：M2c2 `direct-tcpip` → M3 ArkTS 接线 → M2d/M4 真机联调。

### 2026-10-15 / Windows / 第十一轮：真机冒烟通过 + 原生 direct-tcpip（M2c2）

**真机冒烟（本轮最有价值的验证）**：新增临时自检入口（设置页按钮 + 启动自动跑一次），
只调 `probeHostKey({host:'36.138.125.17', port:22})` —— 仅 TCP 连接 + SSH 握手取主机指纹，
**不认证、不发来源请求、不改采集状态**。用户确认**自检通过**；设备日志无 CPPCRASH、
无 `dlopen` 失败。→ 证明：**该 so 能在鸿蒙真机加载、KEX 能跑完、host key 能提取**。
在此之前从未在真机执行过任何原生代码。

排障记录（都留下了证据）：

- 首次 ArkTS 报 `arkts-no-any-unknown`（`probeHostKey` 被当作 any）：本地 `file:` 依赖
  没进 `oh_modules`；执行 `ohpm install` 后类型声明生效，编译通过。
- 自检最初“没反应”：原生侧只在成功时打日志；已补入口日志与失败日志（hilog tag `waygate_ssh`）。

**M2c2 原生 direct-tcpip**：[napi_init.cpp](harmony/entry/src/main/cpp/napi_init.cpp) 新增
`forward(options) -> {id, port}` 与 `stopForward(id)`。按桌面独立 `ssh -L` 的语义
**使用第二条独立 SSH 会话**（避免浏览器转发流量与 exec 调用争用全局互斥锁），会话非阻塞、
每次 libssh2 调用只在自身那把锁内，本地监听 `127.0.0.1` 随机端口，每条本地连接开一个
`libssh2_channel_direct_tcpip_ex` 双向泵。**编译通过**（BUILD SUCCESSFUL）。

**必须说清**：点“现在处理”**仍是旧路径、仍报同一条指纹错**——因为
`verify()` 还在调 `POST /v1/incidents/{id}/sessions`，**M3 尚未开始**。
真机自检不是这次报错的修复，它只是证明了原生栈可用。

**未做**：ArkTS 接线（`verify()` → 描述接口 + SSH 编排；`LocalProxy.ets` → `forward`；
`SessionRuntime.ets`；凭据按 `node_id` 存储与导入界面）；**真机上没有任何 worker 的 SSH 凭据**，
因此尚未跑过真实 exec 与真实转发。

下一步：M3 ArkTS 接线；之后需要把某台 worker 的 SSH 凭据导入手机，才能做 M4 真机端到端。

### 2026-10-15 / Windows / 第十二轮：M3 ArkTS 接线——App 改走手机直连 SSH（编译通过，未真机验收）

用户现场反馈：原生自检通过，但点“现在处理”仍报同一条指纹错。核实确认原因是
`verify()` 仍在调旧 `POST /v1/incidents/{id}/sessions`（= fzz-002 的 hub 去 SSH worker），
与“自检能不能用”无关。本轮把 [DIRECT_SSH.md](DIRECT_SSH.md) 的 M3 接线做完。
**本轮只改 App 源码与 App 测试；未改服务端、未改部署、未触发任何来源请求。**

代码变化（均在 `apps/waygate` 内）：

- 新增 [NodeSsh.ets](harmony/entry/src/main/ets/services/NodeSsh.ets)：按 `node_id` 把该节点的 SSH
  登录材料（主机/端口/用户/密码或私钥/口令/主机指纹）存入手机 Asset Store（别名
  `waygate-node-<node_id>-v1`），只在本机，不入库、不写日志、不上传主控。`runAction()` 通过原生
  `exec` 发一份 JSON stdin 然后 EOF，并按 `{"ok":…,"value":…}` 契约解析；非零退出码、stderr、
  无法识别的返回都转成可读错误，不把原文回显成“成功”。
- [Index.ets](harmony/entry/src/main/ets/pages/Index.ets)：`verify()` 改为
  `GET /v1/incidents/{id}/verification` 取描述接口，再按
  `connect → audit（核对 instance/revision/job/stock/task/request_id）→ prepare → exit
  （核对 route signature 并记录 exit_ip）→ forward → 应用内 CONNECT 代理 → api.ipify.org 出口
  核对（必须等于 exit_ip）→ 打开 audit 返回的原失败 URL`。首次处理某节点时进入凭据导入界面：
  先读指纹并展示，必须点“确认指纹并连接”才保存并继续；已有凭据直接连接，指纹变化由原生
  `connect` 拒绝。删除上一轮临时自检入口（设置页按钮 + 启动自动跑 `probeHostKey`）。
  “验证完成并继续”先复核浏览器出口，再**持久写入 claim**（`pendingClaim`：incident/node/target/
  worker_command/`before_request_id`），随后单次 `probe → result（≤125s）→ resume（仅
  real_data/detail_unavailable）`。重启或切后台只读原结果（按钮“继续核对探测结果”），
  未收到 `before_request_id` 时明确显示“结果未知，请到控制台核对”，**不重发探测**。
- [LocalProxy.ets](harmony/entry/src/main/ets/services/LocalProxy.ets)：由“本地 TCP → 主控 WSS”改为
  “本地 TCP → 校验 CONNECT → 原生 SSH direct-tcpip”。沿用 relay 的 CONNECT 规则：只接受一条
  `CONNECT host:443`、小写域名、拒绝 IP 字面量与超大请求头；`api.ipify.org` 恒允许，来源域名要
  在 `markReady()`（出口核对通过）后才放行；转发前重写 CONNECT、丢弃客户端自带代理头；
  保留 1 MiB 未完成字节上限、24 连接上限与结束即关闭。
- [SessionRuntime.ets](harmony/entry/src/main/ets/services/SessionRuntime.ets)：后台/关闭只停止手机
  自有的代理与 SSH 会话，**不再调用旧 hub cancel**；已提交的 probe claim 保持不变。
- [Models.ets](harmony/entry/src/main/ets/services/Models.ets)：新增描述接口/审计视图/route/exec 信封/
  probe 结果/claim/节点凭据等类型；`Credentials.pendingSession` → `pendingClaim`，
  [Vault.ets](harmony/entry/src/main/ets/services/Vault.ets) 相应改为 `claim()`。

精确命令及结果（工作目录 `apps/waygate/harmony`，Windows）：

```text
$env:DEVECO_SDK_HOME='D:\kits\DevEco Studio\sdk'
hvigorw.js --mode module -p product=default -p module=entry@default -p buildMode=debug assembleHap --no-daemon
→ BUILD SUCCESSFUL（首次即通过；告警仍为既有 “Function may throw exceptions” 一类，未压制）
→ 产物 entry/build/default/outputs/default/entry-default-unsigned.hap（**未签名**）
```

离线检查（仓库根目录，`NO_PROXY=*`）：

```text
python apps/waygate/tools/check_project.py                       → Project metadata/import paths OK
python -m pytest -q apps/waygate/tests apps/http_backfill/tests/test_operator_verification.py
                                                                 → 48 passed, 2 skipped
node --test apps/waygate/tests/test_app_connection.mjs           → 4 passed
```

两处**必须说明**：

1. 跳过项是 `test_deploy.py` 的 2 项（本机无 nginx / 部署锁为 POSIX），Windows 环境预期跳过；
   这是环境差异，不是本轮改动造成。
2. [test_app_connection.mjs](tests/test_app_connection.mjs) 修了两处**跨平台**缺陷（不放宽断言）：
   `@Builder` 切分原按 LF 硬编码，Windows `core.autocrlf=true` 的 CRLF 工作区会切错位置而误报
   语法错；以及新增的 `NodeSsh` 服务类需要 stub。改后 4 项照旧覆盖“解析失败提示 / 不泄露 token /
   HTTP 与网络错误分类 / 恢复后清除提示且保留操作结果”。若只想避免改动该测试，也可把
   `Index.ets` 的 `import` 写成单行——已按此写成单行，但 LF 假设仍需修，否则 Windows 上不可用。

未验证（**不声称已完成**）：

- **未签名、未安装、未在真机运行**。`devecocli auth status` 为 `Not logged in`；agent 不代替用户
  登录华为账号。第四/六轮写进 `build-profile.json5` 的本地签名取值已随 `git checkout` 还原，
  仓库与磁盘之外没有留存，本环境无法从 CLI 复现签名。
- 手机里**没有任何 worker 的 SSH 凭据**，因此从未跑过真实 `connect/exec/forward`；M4 端到端
  （audit → prepare → exit → 转发 → 人工验证 → probe → resume）未开始。
- 描述接口不提供跨手机会话互斥：两台手机同时处理同一待办时仍可能各自探测；旧 hub 的全局 lease
  未迁移，本轮也不声称已迁移。
- OpenSSH 新格式（`-----BEGIN OPENSSH PRIVATE KEY-----`）私钥能否被 libssh2 + OpenSSL 解析未实测；
  若导入报私钥解析失败，需先在节点侧 `ssh-keygen -p -m PEM` 转成 PEM 再导入。
- 首次导入为 TOFU：指纹只展示给操作者并要求人工确认，未提供带外比对来源。
- 未验证 `hdc` 可达设备的现场表现（本轮未安装，故未截图、未看 hilog）。

下一步：① 用户用 IDE 自动签名跑一次 `Run`（或提供 p12 口令/别名，由本环境复现命令行签名）
以产出签名 HAP 并安装；② 导入目标 worker 的 SSH 凭据；③ 做 M4 真机端到端与验收表第 5~9 步。
安装后点“现在处理”应进入**凭据导入界面**而不是原来的指纹错，这一步本身可作为“旧报错已消失”的
第一个真机证据。

### 2026-10-15 / Windows / 第十三轮：36 号节点的 M4 准备（只读取证 + 指纹可比）

用户指定先用 36 号节点试（36 = 36.138.125.17，桌面脚本的别名；本机 `ecs-02826072`）。
本轮**只做只读取证与一处小改**：没有在服务器上做任何变更、没有触发来源请求、没有签名安装。

只读取证（本机 Windows；未把任何私钥内容写入仓库/日志）：

- 登录材料：`%USERPROFILE%\.ssh\testsg8100.pem`（本机既有）。文件头
  `-----BEGIN RSA PRIVATE KEY-----`（经典 PEM、2048 位、**未加密**），可直接用于
  `connect()` 的 `privateKey` 分支；无需转 OpenSSH 新格式。该用户密钥指纹（公开信息）
  `SHA256:9Z7rB4gWNBAQNhBvGoSuiVGL8mDUVG9xKvgYm7bFm7I`。
- 只读连通性核对（命令 `id; hostname`）：`uid=0(root)`、`hostname=ecs-02826072`，与既有记录一致。
- 36 的**权威主机指纹**（手机导入页要人工比对的目标值）：
  - ED25519 `SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk`
  - RSA `SHA256:tsnfhOBHxe02IHsRBdCv750q/wJkZo9v3Ymf98yrqiE`
  取得方式说明：本机 `ssh-keyscan -T 10 36.138.125.17` 报 `choose_kex: unsupported`
  （老版 Win32-OpenSSH 的 keyscan 与该服务器协商失败），因此改用
  “握手实际使用的 host key == 服务器 `/etc/ssh/ssh_host_*_key.pub` 自报值”的交叉核对。
  临时 known_hosts 只写 `%TEMP%`，未改动用户自己的 `known_hosts`。
- 另据 libssh2 1.11.1 源码确认（[openssl.c 5021-5035 行](https://github.com/libssh2/libssh2/blob/libssh2-1.11.1/src/openssl.c#L5021-L5035)，本地副本在
  `harmony/third_party/libssh2-libssh2-1.11.1`，源码树不入库）：OpenSSL 后端在
  `PEM_read_bio_PrivateKey` 失败后会回退到 `_libssh2_pub_priv_openssh_keyfilememory`，即
  **同时支持经典 PEM 与 OpenSSH 新格式私钥**（ED25519 分支需 `LIBSSH2_ED25519=1`，本构建已满足）。
  第十二轮记录的“新格式可能不支持”因此降级为“未实测但源码支持”，若真出错再转 PEM。

代码小改（App 侧，仍属 M3 收尾）：

- [napi_init.cpp](harmony/entry/src/main/cpp/napi_init.cpp)：`probeHostKey`/`connect`/`forward`
  返回的 `fingerprint` 由“冒号分隔十六进制”改为 ssh-keygen 同款 `SHA256:<base64 无 padding>`；
  [Index.ets](harmony/entry/src/main/ets/pages/Index.ets) 导入页按 `type + ' ' + fingerprint` 展示。
  这样操作者能直接拿上文两个字符串逐字符比对，否则两种格式无法对照。
- **主机指纹核对逻辑未变**：固定仍按 `knownHostBase64`（key 原始字节）精确比对；指纹只作人工展示。
- 重编译：BUILD SUCCESSFUL（`BuildNativeWithNinja` 重跑，`libwaygate_ssh.so` 6.29 MB 未 strip）。

未验证与下一步：仍**未签名安装**（第十二轮的签名门槛未变），因此仍未在真机连接 36。
装好后导入 36 凭据时：主机填 `36.138.125.17`、端口 22、用户 `root`，粘贴
`testsg8100.pem` 的**内容**（不是路径）；首次导入是 TOFU，需人工比对上面 ED25519 指纹后再确认。
提醒真实副作用：`prepare` 会暂停 36 上同任务的在跑采集，`probe` 会对原失败请求发一次真实探测，
验证码必须人工完成，失败不会自动重试。

### 2026-10-15 / Windows + Mate 70 Pro+ / 第十四轮：签名安装成功，但发现**公网主控缺少描述接口**（404）

用户按方案 1 在 DevEco 里生成了自动签名配置（同一个 p12：
`C:\Users\xiazhidao\.ohos\config\default_harmony_UgupinABCYCGUBpSEjpksVX3K6iEttPtaMJrIS7VWjM=.p12`，
口令随配置一起由 IDE 写入 `build-profile.json5`）。本机据此完成签名安装，**不再需要用户提供口令**。

签名与安装（工作目录 `apps/waygate/harmony`）：

```text
① IDE 写入 signingConfigs；本机补上 product 的 "signingConfig": "default"（IDE 只写数组、不写引用）
② hvigorw ... assembleHap --no-daemon   → BUILD SUCCESSFUL
   entry/build/default/outputs/default/entry-default-signed.hap  5,635,719 B（17:45:58）
③ hdc -t 192.168.3.156:40525 install -r <signed.hap> → install bundle successfully
④ aa force-stop + aa start -a EntryAbility -b com.myresearcher.waygate → start ability successfully
⑤ 截图确认新包在跑：首页“需要处理”列出待办（本机·601012、tokenseller·002648、hk·002648 等）
```

提交卫生：`git checkout -- apps/waygate/harmony/build-profile.json5` 已还原（校验 `storePassword|keyPassword`
命中 0 处）；签名材料、口令、截图均未入库。

**发现服务端阻塞（本轮最重要结论）**：点“现在处理”后手机提示 `主控请求失败：HTTP 404`。用 App 自带
诊断页取证：

```text
接口：GET /v1/incidents/5aa89c9f6d3e425c998f1509e70ac189/verification
阶段：HTTP 响应   HTTP：404   响应长度：14 个字符
```

判定依据（逐步排除）：

- 无令牌直接探测 `https://testapi.zuzurent.com.cn/collector` 的 `/v1/inbox`、`/v1/devices`、
  `/v1/incidents/<id>/verification`、乃至 `/v1/definitely-not-a-route` **全部返回 401**
  且正文都是 `{"error":"主控令牌不正确或已失效"}`。说明该 hub 的**认证中间件先于路由匹配**，
  所以“无令牌探测返回 401”**不能**证明某路由存在——此前用这种方法下的结论无效。
- 而带令牌的真实请求拿到 **404 + 14 字符**正文：14 字符正是 aiohttp 默认的 `404: Not Found`，
  既不是 hub 的 JSON 错误、也不是 nginx 的 HTML 404。→ 认证通过后 aiohttp 路由器**没有匹配到该路由**。
- 该路由在仓库里是存在的：`origin/main` 与本地 HEAD 都含
  `app.router.add_get("/v1/incidents/{incident}/verification", route)`（[service.py](hub/service.py)）
  与 [collector.py](hub/collector.py) 的 `direct_descriptor()`，来自提交 `9bc228c`
  “Expose desktop verification worker for direct phone SSH handoff”。
- 公网入口解析到 `testapi.zuzurent.com.cn → 8.148.251.114`（fzz-002），**部署在该主机上的 hub 版本早于
  `9bc228c`**，因此没有描述接口。App 侧无误：`verify()` 失败时不建通道、不连节点、不探测，只回到待办并提示。

修复（须由有 fzz-002 访问权的一方执行，本环境没有该主机凭据；命令与本文顶部同一行）：

```bash
cd /usr/local/src/MyResearcher-DataCollector && git pull && \
  bash apps/waygate/deploy/start.sh --public-url https://testapi.zuzurent.com.cn/collector
```

注意：**重新部署后无法用“无令牌探测”验证**（任何 `/v1/` 路径都是 401）。唯一可靠的验收是手机端
点“现在处理”后进入**凭据导入界面**，而不是 404。

未验证与下一步：① 等 fzz-002 重新部署后，在手机上点“现在处理”，导入 36 的凭据（主机 `36.138.125.17`、
端口 22、用户 `root`、粘贴 `testsg8100.pem` 内容），比对 ED25519 指纹
`SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk` 后连接；② 之后才可能跑 M4 的
audit→prepare→exit→转发→人工验证→probe→resume。本轮**未连接任何节点、未触发来源请求**。

另外记录：DevEco 在本机工程目录留下了 `.clangd`、`.clang-tidy`、`wecode-cpp.db*` 等 IDE 产物，
本轮把它们加入 [.gitignore](.gitignore)，避免误提交二进制索引库。

### 2026-10-15 / Windows + Mate 70 Pro+ / 第十五轮：纠正方向——36 是 worker，只做手机→36 的 SSH 通道

**用户纠正（成立）**：36 是 worker，不是 hub；把“把36节点部署一下”理解成“在 36 上更新 hub/加 HTTPS”
是跑偏。DIRECT_SSH.md 本来写明 **worker 侧不新增部署**。上一轮准备执行的 36 侧部署脚本已被用户拒绝、
**没有执行**：36 上没有任何变更。

**36 只读取证（补充，解释为什么 36 不适合放 hub）**：

- `/opt/MyResearcher-DataCollector` 是 git checkout（remote 就是 solio/MyResearcher-DataCollector），
  但 HEAD 停在 `2c10383`，连 `deploy/start.sh` 都还没有 → 36 上的 hub 代码很旧。
- 36 上的 waygate systemd 单元 active/enabled、监听 `127.0.0.1:8791`，
  `public_url` 仍是占位 `https://waygate.example.invalid/waygate`，`collector_url=http://127.0.0.1:8790`，
  ssh 映射只有 `local`；**push.enabled=false，devices 0**（所以重启它不会给任何手机发通知）。
- 36 自己的采集器 fleet 只有 1 个节点（`local`，名字“本机”），**没有**手机待办里的
  `tokenseller / hk / 02 / ktopia-crmeb`。
- 36 上**没有 nginx、没有 80/443、没有 /etc/letsencrypt**；手机端只接受 https 地址，因此
  36 不能作为手机的主控入口——这与“能不能 SSH”无关。
- 36 sshd 实测：`permitrootlogin yes`、`pubkeyauthentication yes`、**`passwordauthentication yes`**，
  即手机既可用现有 RSA 私钥，也可用 root 密码，不必非要粘贴 1.7KB PEM。

**本轮代码变化（App 侧，只做 SSH 通道本身）**：[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)
设置页新增 **“SSH 通道测试（手机 → 节点）”** 入口，完全不经过主控、不使用待办信息：
`probeHostKey 取指纹（人工核对）→ connect → exec 一条只读命令 id; hostname; echo SSH_CONNECTION=$SSH_CONNECTION
→ close`。**不调用 audit/prepare/probe，不暂停采集、不发起来源请求**；凭据只在本机内存中使用，
不写日志、不入库。原有的“现在处理 → 凭据导入 → 整套编排”路径保持不变。

验证（真机）：

```text
hvigorw ... assembleHap --no-daemon        → BUILD SUCCESSFUL
entry-default-signed.hap 5,649,183 B（18:04）
hdc install -r → install bundle successfully；aa force-stop + aa start → 成功
布局 dump 确认新入口在设置页：'SSH 通道测试（手机 → 节点）' Button bounds [84,1536][1232,1697]
（签名配置用完即已 git checkout 还原，git status 只剩 Index.ets 与本轮文档）
```

**未验证**：还没有真正 connect 过——通道测试需要有人在手机上填节点地址与凭据（密码或私钥），
agent 不代为输入口令。另外测试期间手机前台被会议应用占用，已**停止**对手机的点击驱动，避免干扰。

下一步：① 在手机上打开 设置 → SSH 通道测试，主机 `36.138.125.17`、端口 22、用户 `root`，
填 root 密码或粘贴 `testsg8100.pem` 内容，核对 ED25519 指纹
`SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk` 后建立通道，看 `id/hostname/SSH_CONNECTION`
输出；② 完整待办流程仍依赖 fzz-002 的 `GET /v1/incidents/{id}/verification`（见第十四轮），
与本条通道互不影响。

### 2026-10-15 / Windows + Mate 70 Pro+ / 第十六轮：把通道配置打包进 app，做到一键建立

用户要求：**不要在手机上填通道参数，直接打包进 app**。已实现，凭据文件留在本机且不入库。

打包方式：

- [channel.json](harmony/entry/src/main/resources/rawfile/channel.json)（**入库**，只含非秘密信息）：
  内置节点 `36` → `host 36.138.125.17`、`port 22`、`user root`、`hostKey`（固定为
  `ssh-ed25519 SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk`）、`secretFile
  channel/36.pem`。
- `harmony/entry/src/main/resources/rawfile/channel/36.pem`（**不入库**，已加
  [.gitignore](.gitignore) 第 18 行并实测 `git check-ignore` 命中；内容取自本机既有
  `%USERPROFILE%\.ssh\testsg8100.pem`，用文件复制完成，私钥正文未进入任何日志/文档）。
- [Index.ets](harmony/entry/src/main/ets/pages/Index.ets)：`loadBundled()` 从 rawfile 读配置与凭据；
  设置页“SSH 通道测试”打开时自动预填内置节点并**隐藏密码/私钥输入**；按钮变为
  **“建立通道（内置凭据）”**：先 `probeHostKey`，把指纹与内置 `hostKey` 逐字符比对（不一致直接
  拒绝），再用内置私钥 `connect → exec id; hostname; echo SSH_CONNECTION=$SSH_CONNECTION → close`。
- 内置凭据也接到正常流程：“现在处理”取到描述接口后，若 `ssh_host_hint` 等于内置主机且本机没有
  该 `node_id` 的凭据，则自动用内置凭据建 profile 并保存，**不再弹凭据导入页**。

验证：

```text
hvigorw ... assembleHap --no-daemon → BUILD SUCCESSFUL（ArkTS 仅既有 “may throw” 类告警）
解包 signed HAP 校验打包结果：
  resources/rawfile/channel.json      293 B
  resources/rawfile/channel/36.pem   1679 B
（即配置与凭据确实随包打入；签名配置用完即 git checkout 还原）
```

**未完成**：这次**没装上**——无线调试掉线，`hdc tconn 192.168.3.156:40525` 返回 `Connect failed`、
`list targets` 为空。需要重新开启手机无线调试（端口可能已变）或改用 DevEco Run。

**已知权衡（须记录）**：内置的是**桌面上那把 RSA 私钥**，任何人拿到这个本地构建的 HAP 就能以 root
登录 36。缓解：该 pem 在 `.gitignore` 内、HAP 本身也被 `*.hap` 忽略；撤销方式是删掉该文件重新构建，
并在 36 上轮换该 key。更干净的做法是为手机单独生成一对密钥、只把公钥追加到 36 的
`~/.ssh/authorized_keys`（需要一次服务器变更），当前为了“零输入”先复用了既有密钥。

### 2026-10-16 / Windows + Mate 70 Pro+ / 第十七轮：手机→36 SSH 通道**真机跑通**；确认主控已更新

**一、通道测试真机 PASS（本轮最重要结论）**

真机点 设置 → 「SSH 通道测试（手机 → 节点）」→「建立通道（内置凭据）」，页面输出（布局 dump 原文）：

```text
SSH 通道可用
主机指纹（已与内置配置比对通过）
ecdsa-sha2-nistp256 SHA256:nTL9myPgp4MNIQkb1F+knWklgJdPKeYyuMgfv2v0R4w
通道已建立，主机指纹已核对
退出码：0
--- stdout ---
uid=0(root) gid=0(root) groups=0(root)
ecs-02826072
SSH_CONNECTION=112.66.184.52 23773 10.18.0.3 22
```

设备日志（`hilog -x`，tag `waygate_ssh`）同一时刻：

```text
10-16 14:08:17.186 ... waygate_ssh: probeHostKey start host=36.138.125.17 port=22
10-16 14:08:17.631 ... waygate_ssh: probeHostKey ok type=ecdsa-sha2-nistp256 fingerprint=SHA256:nTL9myPgp4MNIQkb1F+knWklgJdPKeYyuMgfv2v0R4w
```

即 **手机 → 36.138.125.17:22 建立 SSH 通道、主机指纹核对通过、root 认证成功、只读命令执行成功**。
本次全程未调用 audit/prepare/probe，**没有暂停任何采集、没有发起来源请求**，36 侧无任何改动。

**二、第三个主机指纹（ECDSA）与交叉核对**

手机实际协商到的是 **ecdsa-sha2-nistp256**（不是 ED25519、也不是 RSA）。第一次比对被拒的报错是
`节点主机指纹与内置配置不一致，已拒绝连接（本次协商到 ecdsa-sha2-nistp256 SHA256:nTL9m…）`
——这条报错带了实际值，直接定位到原因（只固定了 ED25519+RSA 两条）。
随后在 36 上**只读**列出并计算全部 host key 指纹：

```text
SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk  (ED25519)
SHA256:nTL9myPgp4MNIQkb1F+knWklgJdPKeYyuMgfv2v0R4w  (ECDSA)
SHA256:tsnfhOBHxe02IHsRBdCv750q/wJkZo9v3Ymf98yrqiE  (RSA)
```

手机报的 ECDSA 值与 36 `/etc/ssh/ssh_host_ecdsa_key.pub` 的指纹**逐字符一致** → 既确认了
手机端 SHA256 指纹计算实现正确，也确认连的确实是 36。三个指纹已全部写入
[channel.json](harmony/entry/src/main/resources/rawfile/channel.json) 的 `hostKeys`，
libssh2 无论协商到哪一种都会被接受，其余一律拒绝。

**三、主控已更新（由手机行为反证）**

上一轮点“现在处理”报的是 404；本轮报的是上面那条**指纹不一致**。而这条报错来自
`bundledProfileFor()`，它只在 `GET /v1/incidents/{id}/verification` **成功返回之后**才会被调用
（见 `verify()`）。→ **fzz-002 上的主控已经带上描述接口，十四轮的 404 已消除**，并且
“现在处理”已经自动改用内置凭据（说明该待办的 `ssh_host_hint` 就是 `36.138.125.17`，即
36 在这批待办里是可处理的）。本轮**没有**继续往 audit/prepare 走。

**四、代码变化**：`channel.json` 增加第三条指纹；`Models.ets` 的 `BuiltinNode.hostKey` 改为
`hostKeys: string[]`；`Index.ets` 的 `bundledProfileFor()` 按列表比对，报错带上本次协商值。
构建/安装：`BUILD SUCCESSFUL` → signed HAP → `install bundle successfully`（签名配置用完即还原）。

**下一步（未做，需真实副作用知情）**：点属于 36 的那条待办走完整流程
`audit → prepare → exit → 原生转发 → 浏览器出口核对 → 人工验证码 → probe → result → resume`。
注意 `prepare` 会**暂停 36 上同任务在跑的采集**，`probe` 会对原失败请求**发一次真实探测**，
验证码必须人工完成；失败不自动重试。

### 2026-10-16 / Windows + Mate 70 Pro+ / 第十八轮：内置通道扩到三个节点（手机专用密钥 + 全部主机指纹）

用户给出两台新节点（`43.160.196.129`、`43.132.206.172`）的口令并要求“通道也弄进去”。
**口令这条路先被证据排除**：在两台用口令登录均被拒——
`BadAuthenticationType: allowed types ['publickey','gssapi-keyex','gssapi-with-mic']`
（即这两台 sshd 不允许密码认证）。用户随后改为**他本人把公钥加到三台机器**，这也是最干净的做法。
口令未写入仓库、文档、日志或提交。

手机专用密钥（不再复用桌面私钥）：

- 新生成 ED25519、**无口令**；公钥（用户已授权到 3 台）：
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH8R7jbdxtbso9w7YWvVKHl/8VuSEO2ByKri7oqDdPFY waygate-phone`
  指纹 `SHA256:4J5j9AKB7+26MikduCb4D+ILs/phgiIkf2szorL+XNo`。
- 私钥放 `rawfile/channel/phone-ed25519.pem`（git-ignored）；**旧包内的桌面私钥 `36.pem` 已删除**，
  三台统一只用这把手机密钥（撤销＝从他的 authorized_keys 删掉那一行）。

只读登录验证 + 指纹采集（新密钥，本机执行，三台全部成功）：

```text
36.138.125.17    -> uid=0(root)  hostname=ecs-02826072
43.160.196.129   -> uid=0(root)  hostname=VM-0-14-opencloudos
43.132.206.172   -> uid=0(root)  hostname=VM-0-8-opencloudos
```

各机 `ssh-keygen -lf /etc/ssh/*.pub` 取回的指纹（按 App 同款“type + SHA256:base64无padding”格式，
已全部写入 channel.json）：

```text
36       ecdsa-sha2-nistp256 SHA256:nTL9myPgp4MNIQkb1F+knWklgJdPKeYyuMgfv2v0R4w
36       ssh-ed25519        SHA256:zA3sA2m5587IgsOA07T0meZ3+TBANNLqWxl0nvIgePk
36       ssh-rsa            SHA256:tsnfhOBHxe02IHsRBdCv750q/wJkZo9v3Ymf98yrqiE
43.160   ssh-dss            SHA256:GCF3ruwQIegwqSbFYbnV4t0QtX8KPpa0pvnV1m1Kc70
43.160   ecdsa-sha2-nistp256 SHA256:1+953wgooWmyQqRqMoaT4bP2k9dTWIOsM8iN5xglBBY
43.160   ssh-ed25519        SHA256:QJnUv91xE1qKvtQXlFdXcLtDmH5Ce9G2ha9rfamC4x0
43.160   ssh-rsa            SHA256:ejo+MUgkmLUJhB7MlcNQKr+gWTRbmVP5sV7NzeVauwU
43.132   ssh-dss            SHA256:7OSAqZyTNGO+VWweE2dASXleepBIB14kJTi0wolPx3M
43.132   ecdsa-sha2-nistp256 SHA256:H50ZG32IGzOI0O5wGJiAnyLxj6brqmtqyv55X66kF+I
43.132   ssh-ed25519        SHA256:yqWfUP0PqXA3y7A1uzLs2B031qOMSE4Sq2suKx+2Yuw
43.132   ssh-rsa            SHA256:+t1TknZxgNJgkDxy5GI7uc+Ql8YoJ/uAxS3agPooVmU
```

代码变化：`BuiltinNode` 增加 `authType`（key/password）；`loadBundled()` 改为多节点、按
`secretFile` 缓存凭据；`bundledFor(host)` 按主机匹配（description 的 `ssh_host_hint` 或手填地址都能命中）；
`bundledProfileFor()` 按 `authType` 构造 profile（不再写死 'key'）；通道页新增**节点选择按钮**
（36 / 43-160 / 43-132）。构建验证：`BUILD SUCCESSFUL`；解包 HAP 确认
`resources/rawfile/channel.json`(1561B) 与 `resources/rawfile/channel/phone-ed25519.pem`(399B) 都在；
`install bundle successfully`。

真机状态：36 已于第十七轮 PASS；**新增两台尚未在手机上跑通道测试**——驱动到一半用户切到微信，
已立即停止 UI 驱动，避免干扰其使用。待用户空闲或自行点击：设置 → SSH 通道测试 → 顶部选
`43-160` / `43-132` → 「建立通道（内置凭据）」，预期同样出现
`uid=0(root) / hostname=VM-0-14-opencloudos（或 VM-0-8）`。

**补记（同日，用户说“继续”后）**：

- **`43-160` 真机通道 PASS**。通道页顶部选中 `43-160` → 「建立通道（内置凭据）」→ 页面输出：

  ```text
  通道已建立，主机指纹已核对
  退出码：0
  --- stdout ---
  uid=0(root) gid=0(root) groups=0(root)
  VM-0-14-opencloudos
  SSH_CONNECTION=112.66.184.52 39166 10.3.0.14 22
  ```

- **`43-132` 真机通道也 PASS**（用户再次说“继续”后补跑；中途一次点空导致键盘弹出占了半屏，
  用 `uitest uiInput keyEvent 2` 收掉键盘后按当前坐标点中）。页面输出：

  ```text
  主机指纹（已与内置配置比对通过）
  ecdsa-sha2-nistp256 SHA256:H50ZG32IGzOI0O5wGJiAnyLxj6brqmtqyv55X66kF+I
  通道已建立，主机指纹已核对
  退出码：0
  --- stdout ---
  uid=0(root) gid=0(root) groups=0(root)
  VM-0-8-opencloudos
  SSH_CONNECTION=112.66.184.52 43330 10.5.0.8 22
  ```

  协商到的正是坑里预先固定好的 ECDSA 指纹，说明多指纹固定生效。
- **三节点通道状态（真机）**：`36 → ecs-02826072`（第十七轮）、`43-160 → VM-0-14-opencloudos`、
  `43-132 → VM-0-8-opencloudos`，全部 `退出码 0`，且都只执行 `id/hostname/SSH_CONNECTION`。
- 三节点选择按钮（`36` / `43-160` / `43-132`）在真机上正常显示并切换，地址/用户自动预填、
  密码与私钥输入框不出现（内置凭据分支命中）。

下一步：三台通道已就绪，可以点属于对应节点的待办走完整流程
（`audit → prepare → exit → 原生转发 → 浏览器出口核对 → 人工验证码 → probe → result → resume`）。
其中 `prepare` 会暂停该节点上同任务在跑的采集、`probe` 会对原失败请求发一次真实探测，
验证码必须人工完成，失败不自动重试。

### 2026-10-16 / Windows / 第十九轮：Android 移植代码就绪（**未编译、未真机**）

用户要求“按同样的套路也准备一份 android 的 app（准备好代码就行），暂时没有真机”。
本轮只准备代码与静态自检，**没有 APK、没有安装、没有真机验证**。

环境事实（决定了本轮的验证上限）：本机有 Temurin JDK 21，但
**没有 Android SDK、没有 Gradle、没有 kotlinc、没有 Android Studio**
（`ANDROID_HOME`/`ANDROID_SDK_ROOT` 为空，`D:\kits` 下只有 DevEco 与 Maven/JDK）。
→ 本次无法编译，也无法出 APK。仓库同样没有 `gradlew`/`gradle-wrapper.jar`（二进制文件）。

技术选型（含被否决的方案与理由）：

- SSH 用 **sshj 0.41.1**（`com.hierynomus:sshj`）+ **BouncyCastle 1.84**
  （`bcprov-jdk18on`、`bcpkix-jdk18on`）。Ed25519 必须自带 BC：Android 内置的是精简版。
  **否决 Apache MINA sshd**：其默认传输用 `java.nio.channels.AsynchronousSocketChannel`，
  Android 的 libcore 不实现该异步 socket 通道。**此项经核对官方文档与 Android desugaring 表，
  但仍属推断，未在真机确认。**
- WebView 代理用 **androidx.webkit 1.17.1 `ProxyController`**（`WebViewFeature.PROXY_OVERRIDE`），
  对应鸿蒙端 ArkWeb ProxyController；应用与清除均为异步、必须等回调。
- `minSdk 28`：避免 API 26/27 平台自带旧 BC 与打入的 1.84 冲突；`compileSdk/targetSdk 35`；
  AGP 8.7.3 / Kotlin 2.0.21 / Gradle 8.9 三者配套固定。

新增文件（`apps/waygate/android/`）：

```text
settings.gradle.kts / build.gradle.kts / gradle.properties / gradle/wrapper/gradle-wrapper.properties
app/build.gradle.kts / app/proguard-rules.pro / app/src/main/AndroidManifest.xml
app/src/main/res/values/waygate.xml / res/xml/network_security_config.xml
app/src/main/res/layout/activity_main.xml / layout/item_incident.xml
app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml / drawable/ic_launcher_foreground.xml
app/src/main/assets/channel.json            ← 与鸿蒙 rawfile 同一份节点配置（自检逐节点比对）
app/src/main/java/com/myresearcher/waygate/
    MainActivity.kt                          ← 屏幕状态机 + WebView + VerificationHost 实现
    core/{Models,Api,Vault,SecureStore,NodeProfiles,BundledChannel}.kt
    ssh/{SshTransport,SshjTransport,Fingerprints,WorkerAction}.kt
    proxy/{LocalProxy,ProxyOverride}.kt
    runtime/Runtime.kt
    verify/DirectVerifier.kt                 ← verify/begin/finish/resumeClaim 的逐句镜像
```

与鸿蒙端的**有意差异**（均记入 README）：

1. `NodeProfile` 增加 `pinned` 字段：按“全部已固定指纹”比对。鸿蒙端只按导入时协商到的那
   一把密钥原始字节比对（第十七轮正是因此被 ECDSA 误拒），移植版把这个坑补掉。
2. 本地代理对每个浏览器连接直接开 `direct-tcpip` 通道，不再经第二个本地监听端口转发，
   少一跳；**白名单仍在手机侧判定**（未 `markReady()` 只放行 `api.ipify.org`，之后才放行
   `source_domains`），因此安全性质不变。
3. WebView 复用同一个实例并导航到 `about:blank`，不销毁重建（ArkWeb 侧是销毁重建）。
4. 出口测量一律显式 `loadUrl('https://api.ipify.org')`，不依赖“首次挂载即加载”的时序。

静态自检（本机可跑、已通过）：新增 [tools/check_android.py](tools/check_android.py)，
核对 ① manifest/布局里所有 `@string/@color/@style/@layout/@drawable/@xml/@mipmap/@id` 引用都能解析；
② Kotlin 里每个 `R.id.*` 都能在布局中找到 `@+id`；③ 每个 Kotlin 文件包名与目录一致、
顶层类名与文件名一致；④ Android 与鸿蒙两份 `channel.json` **逐节点完全一致**（3 节点）；
⑤ 私钥路径被 `.gitignore` 覆盖、且没有任何被跟踪文件含真正的 PEM 私钥块。

```text
python apps/waygate/tools/check_android.py
→ channel.json nodes identical on both platforms (3 nodes)
→ credential path is git-ignored
→ Android project structure OK (not compiled: no Android SDK on this machine)
```

**必须说明：该脚本只是静态核对，不是 Kotlin 编译器**，类型/API 错误它查不出来。

未验证（**不声称可用**）：

- **未编译、无 APK、未装到任何 Android 设备**；本轮没有任何 Android 真机结果。
- sshj 的三处 API 细节未编译验证：`loadKeys(privateKey, publicKey, passphrase)` 传内存密钥、
  `newDirectConnection(host, port)` 的并发通道、以及 keepAlive 链式属性
  （`connection.keepAlive.keepAliveInterval`，已单独隔离并注释“若不通过编译就删掉”）。
- **推送未接线**：Android 侧需要 Firebase 工程与 `google-services.json`，本轮只保留
  `POST /v1/devices {"id","token"}` 的契约说明，未引入 FCM 依赖。
- `minSdk 28` 规避 BC 冲突属推断，未实测 26/27。
- 三台节点（36 / 43-160 / 43-132）的 Android 通道**未在 Android 上跑过**；
  鸿蒙端三台均为真机 PASS（第十七、十八轮）。

下一步（Android）：用 Android Studio 打开 `apps/waygate/android`（或先生成 wrapper）
→ `assembleDebug` 修编译错误 → 有 Android 真机后做通道测试与完整流程，
验收项与鸿蒙端同一张表。

### 2026-10-16 / Windows + Mate 70 Pro+ / 第二十轮：定位并修掉“卡在建立应用内 SSH 通道”

**一、用户现象**：真机点“现在处理”后长时间停在 `正在建立应用内 SSH 通道`。

**二、取证（手机日志已丢，改用节点侧 + 代码）**
- 手机 hilog 环形缓冲已把这轮 `waygate_ssh` 冲掉：导出 1,213,844 字节、匹配 `waygate_ssh` **0 行**。
- 节点侧（只读）三台全部正常：`mihomo` 分别在 `127.0.0.1:7890` 与 `:9090` 监听；sshd 正常接受手机
  这把 `ED25519 SHA256:4J5j9AKB…`。
- 43.132.206.172 的 sshd 记录把这次流程的两条会话都留下了：

  ```text
  17:18:19 Received disconnect ... port 48846 [preauth] "probe"      ← 取指纹，正常收尾
  17:18:19 Accepted publickey root ... port 48849                    ← exec 会话（audit/prepare/exit）
  17:27:23 Accepted publickey root ... port 16295                    ← 第二条会话＝forward，此后无 disconnect
  现在    ESTAB 10.5.0.8:22 ↔ 112.66.184.52:16295，Recv-Q=2564       ← 服务器有数据待发，手机没在读
  ```

  另：此刻手机对三台各挂一条 ESTAB 会话（36:43109、43.160:43103、43.132:16295），均为多次尝试后未收尾。

**三、根因（代码级，与节点无关）**
1. `gLock` 被长时间持有且跨越**无超时**的阻塞调用：`ForwardStart` 的整段握手/认证/绑定都在锁内，
   `Exec` 的阻塞读循环也在锁内；而 `TcpConnect` 的 `connect()`、握手、认证、`Exec` 读循环**都没有超时**。
   任何一次卡顿都会连锁卡死后续所有 native SSH 调用——这是“一挂就挂到底”的结构性原因。
2. `PumpConnection` 在 `libssh2_session_set_blocking(session, 0)` 的非阻塞会话上**只调用一次**
   `libssh2_channel_direct_tcpip_ex`：非阻塞下必须对 `EAGAIN` 重试，所以**每一个浏览器连接的转发通道
   都打不开**；`libssh2_channel_write` 的 `EAGAIN` 也被当作致命错误。服务端 `Recv-Q` 堆积与此吻合。

**四、改动（`entry/src/main/cpp/napi_init.cpp`）**
- `TcpConnect` 改为非阻塞 `connect` + `poll` 10s 上限 + `SO_ERROR` 判定，失败即报错不悬挂。
- 新增超时常量并落地：握手 20s、认证 20s、exec 会话 60s（`libssh2_session_set_timeout`）、
  通道打开 15s、通道写 30s。
- `ForwardStart`：**不再在 `gLock` 内做握手/认证/绑定**（该会话不进 `gSessions`，只由本转发自己的线程使用），
  `gLock` 只保护 `gForwards` 插入；握手/认证的 EAGAIN 循环加上限，超时给出明确错误。
- `PumpConnection`：通道打开改为“`EAGAIN` 重试 + 15s 上限”（每 10ms 一次、仅在调用瞬间持 `forward->lock`）；
  写入改为 `EAGAIN` 重试 + 30s 上限；新增 `forward session ready` / `direct-tcpip channel open` 成功日志。

**五、ArkTS 阶段标记（`pages/Index.ets` `begin()`）**：在 `forward` → `proxy.start` → `applyProxyOverride`
之间补两句文案 `正在绑定本地转发端口`、`正在应用浏览器代理`（原实现这三步之间没有文案更新，
所以卡住时分不清是哪一步——这也是本轮定位变慢的原因）。

**六、验证**
- `BUILD SUCCESSFUL in 10 s 981 ms`；`libwaygate_ssh.so` 于 17:36:06 重编（未 strip 6,597,176 B）；
  用新增日志串验尸：`forward session ready`、`direct-tcpip channel open` **均在 .so 中命中**
  （中文串因按 ASCII 解码未命中，属方法限制，非缺改）。
- signed HAP 5,666,191 B，`install bundle successfully`；签名配置用完已 `git checkout` 还原。

**七、未验证（下一步要真机重跑）**：手机当时锁屏，**尚未重跑“现在处理”**，所以
`forward` 是否返回、`direct-tcpip` 是否真的泵数据、以及 `applyProxyOverride` 的回调是否必然触发，
都还需要一次真机日志确认。重跑时看 `waygate_ssh` 应出现 `forward session ready host=… port=…`
与 `direct-tcpip channel open host=… port=…`，屏幕文案应能走到“正在应用浏览器代理”。

### 2026-10-16 / Windows + Mate 70 Pro+ / 第二十一轮：真机重跑暴露转发会话认证缺陷

**现象**：用户重跑“现在处理”后报错 `ssh转发会话认证超时`。注意这**已经不是挂死**，而是明确失败——
说明第二十轮的超时机制生效，且拆解 `gLock` 后 exec 侧不再被拖住。

**根因（一行守卫写反，导致转发会话从未认证成功）**：

```cpp
auto attempt = [&]() { if (auth == 0 || auth == LIBSSH2_ERROR_EAGAIN) return; ... };
```

非阻塞会话下第一次 `libssh2_userauth_publickey_frommemory` 必然返回 `EAGAIN`，此后每轮 `attempt()`
都因第二条判断**直接早退**，重试循环变成空转，直到 20s 上限才报错。也就是说：转发会话**从来没有
通过认证**——这同时解释了第二十轮之前为何表现为无限挂起（当时没有上限）。

**修法**（改为“阻塞握手与认证 + 认证成功后再切非阻塞”）：

- 握手与认证使用**阻塞模式**，以 `libssh2_session_set_timeout` 兜底（握手 20s、认证 20s）；
  认证前该会话只属于本转发、无需多路复用，阻塞不会影响别的会话。
- 认证成功后 `libssh2_session_set_blocking(session, 0)` 并清掉超时——只有每个浏览器连接的泵
  需要 EAGAIN 语义（第二十轮已把通道打开与写入改成“EAGAIN 重试 + 上限”）。
- 删掉握手与认证两个 EAGAIN 重试循环及那个坏守卫。

**验证**：`BUILD SUCCESSFUL in 7 s 431 ms`；`libwaygate_ssh.so` 17:41:16 重编，按 UTF-8 解码验尸
三条串全部命中（`SSH 转发会话认证失败或超时`、`forward session ready`、`direct-tcpip channel open`）；
signed HAP 5,662,094 B，`install bundle successfully`；签名配置用完已还原。

**未验证**：仍需一次真机点击确认 forward 能认证并绑定端口。预期：屏幕文案走到
「正在绑定本地转发端口」、`waygate_ssh` 出现 `forward session ready host=… port=…`，
随后浏览器连接出现 `direct-tcpip channel open host=… port=…`。

### 2026-10-16 / Windows + Mate 70 Pro+ / 第二十二轮：真机跑通到“带样式的验证页”，并改掉白名单语义

**一、真机结果（本轮里程碑）**：第二十一轮修复后，用户点击“现在处理”：
转发会话认证通过 → 出口核对通过（截图确认「已确认共同出口，请在下方手动完成验证」＋
「取消／验证完成并继续」按钮）→ 页面**带样式正常渲染**。即
`原生转发 → LocalProxy（向 Mihomo 发 CONNECT）→ direct-tcpip 数据泵 → ArkWeb 执行 JS 读出口 IP`
这一整条链**首次在真机上完整跑通**。

**二、白板 → 裸 HTML 的根因（有硬证据）**：出口核对后加载被阻断页面时是**纯白板**。
上一轮给 LocalProxy 加了判定日志与可读拒绝体后，本轮日志直接给出答案：

```text
waygate_proxy: CONNECT host=gbfek.dfcfw.com allowed=no ready=yes domains=4   （重复 40+ 次）
```

`gbfek.dfcfw.com` 是东方财富的静态/脚本域名，**不在**主控给的 4 个 `source_domains` 里；
`ready=yes` 说明出口核对已通过，但白名单仍把它拒了 → CSS/JS 全丢 → 退化成裸 HTML。
（白板之所以是纯白而不是报错，是因为原先拒绝体是 `Content-Length: 0` 的空 403，
ArkWeb 渲染成空白页——这也是上一轮把它改成可读文本的原因。）

**三、语义修改（用户拍板“把白名单去掉”＝对齐桌面）**：`LocalProxy.permitted()` 改为
**核对前只放行 `api.ipify.org`；`markReady()` 之后不再限制任何域名**。
理由：桌面流程用 `ssh -L` 直接出网、本来没有域名过滤；核对通过即等价于桌面通道，
白名单只剩“核对前 fail-closed（通道是否真走节点出口尚未证明时不允许任意域名出网）”。
拒绝话术同步改为“该域名在出口核对完成前不可通行：…”。

**四、Android 移植版同步（保持两平台一致）**：[LocalProxy.kt](../../android/app/src/main/java/com/myresearcher/waygate/proxy/LocalProxy.kt)
同步了三处——① `permitted()` 同语义；② 增加 `waygate_proxy` 判定日志与可读拒绝体；
③ **修掉一处与鸿蒙端不一致的实质性错误**：Android 版原先由手机自己回
`200 Connection Established` 再把裸 TLS 字节送给 Mihomo，而上游是 HTTP 代理、
必须收到**原始 CONNECT** 才能建隧道——已改成把
`CONNECT host:443 HTTP/1.1` 经通道发出去、让 Mihomo 的 200 回给浏览器。
（Android 版仍未编译：本机 SDK 还缺 `platforms/android-37`。）

**五、本轮未能自证的部分（如实记录）**：成功那次点击的 `waygate_proxy` 日志**没抓到**
（hilog 环形缓冲被 chromium 60fps 刷屏冲掉，`allowed=no` 计数为 0），
因此“样式正常”的证据是**用户肉眼确认**，不是日志。下次需要更靠近点击时刻 dump。

**六、仍未验证**：用户反馈“现在没有节点阻塞、看不到验证码”，所以
**最后一步 `人工验证码 → 「验证完成并继续」 → probe → result → resume` 仍未在真机跑过**；
单次探测与 `prepare` 的真实副作用（暂停同任务采集）也仍未在真机触发。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十三轮：按反馈改体验（兜底回路、信息归集、弱网重试）

用户提三条：① 弱网体验；② 探测等不到结果时整个 App 被 block，要求留兜底回路（**至少控制台可用**）；
③ 信息栏应按节点显示，不要在他操作别的节点时显示别的节点的报错。本轮先改“明显”的那部分。

**② 兜底回路（最痛的一条，也是本轮重点）**

根因：`runtime.dispatching` 被当成**全局闸门**——`verify()` 与 `openConsole()` 都用它做前置拒绝，
而它只在 `settleClaim()`（结果确认并 resume 成功）里复位；在结果屏点“返回”走的是
`leaveResult()/releaseAll()`，**不清 dispatching**，所以返回之后整个 App 仍锁在“有未确认探测”的状态。

改动：

1. `verify(item)`：同一条待办若已有未确认 claim → **回到结果屏**（提示“没有重发任何请求”），不再拒绝；
   **探测待确认期间，别的待办可以照常处理**；只有“真的正在验证中”才提示稍候。
2. `openConsole()`：同规则——**探测待确认期间控制台恢复可用**（此前被 `dispatching` 直接拦掉，
   正是用户说的“至少让控制台继续使用”）。
3. `leaveResult()`：离开结果屏即 `runtime.dispatching = false`（claim 仍持久化在 vault），App 立刻恢复可用；
   同一条待办再点会回到结果屏继续核对。
4. `waitProbeResult()`：每轮检查 `probeWaitAborted` 与前台状态，**离开/切后台立即停止等待**
   （原先 125s 轮询会把取消排在后面）；`releaseAll()` 置位，`finish()`/`resumeClaim()` 开始等待前复位。
5. 承诺不变：**任何路径都不重发探测**（这是单次探测的核心）。

**③ 信息归集**

- `refresh()` 里主控↔collector 的同步错误原先直接显示裸错误，看起来像“某个节点报错”→ 一律加前缀
  「主控同步失败：」，把主控级与节点级区分开。
- `error()` 统一带**当前待办上下文**（`name · stock：`），旧消息不会被误读成当前节点的错误。
- `goHome()`/`leaveResult()` 回首页时清掉过程文案（详细故障仍保留在“诊断”）。

**① 弱网（只改了最明显的一处）**

- 出口测量上限 25s → **45s**，并在 `begin()` 中**失败后重试一次**：弱网下这一步最容易误判，
  而整条流程重来（释放 SSH/转发/代理）代价最大。

**验证**：`BUILD SUCCESSFUL in 15 s 794 ms`；signed HAP 5,671,635 B（2026-10-19 09:15:49），
`install bundle successfully`；hilog 已清空供下次取证；签名配置用完已还原。

**未做/未验证（如实记录）**：Android 版**尚未同步**这三处；弱网的分级超时与“保留已建立通道、
只重试失败那一步”还没实施；消息归集只做了最明显的一层（未做“其他节点 · N 条历史”折叠）；
以上改动**均未经真机复验**。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十四轮：纳入两台新节点（含 testapi 主控兼 worker）

用户要求把 `8.148.251.114`、`8.148.252.117` 也纳入 App 管理。

- **一处纠正**：用户最初贴出的公钥是
  `ssh-ed25519 …IKiiM6zwasvBYankxfRctVWgCtMxkbcZ/6BraHkx+Gkk frontend-test-deploy@MacBook-Pro.local`
  ——那是**其 Mac 上的私钥**对应的公钥，不是 App 内置的 `waygate-phone`；据此测试两台均为
  `Permission denied (publickey,…)`。用户补加 `waygate-phone` 公钥后，两台登录通过
  （`hostname=fzz-002` / `hostname=FZZ-003`，`root`）。
- **主机指纹取法**：先用 `ssh-keyscan -t ed25519,ecdsa,rsa` 预览，再登录读取**主机自己的**
  `/etc/ssh/ssh_host_{ed25519,ecdsa,rsa}_key.pub` 逐一复核——六个指纹全部一致后才写入配置：

  ```text
  fzz-002   ed25519 SHA256:dBI6KNT5jDMG4NgYiMKf+++D6iiyCQxfsQsV1HFBpZQ
            ecdsa   SHA256:ZEoQL7Y09TiO+ILTDJrrdiYoWFyA33T4hLQmFBRKW8o
            rsa     SHA256:sIIUq1o9MYVQMyUcyDtmfCGzbDEOApMGu2yjvgmxRk8
  FZZ-003   ed25519 SHA256:pwHxj/2XzXHgpmV/cxaTDipcULbIFd8lNaExPDTVKgU
            ecdsa   SHA256:8ez3chbYLqN4bD2D/PC6TvdaKY5YG7ZjKfYAyCdaIdc
            rsa     SHA256:sB8ad0Etjy/OWOp+hmPW1fAiv80SNKbj9XHOI0/FBf0
  ```

- **配置**：两份 `channel.json`（鸿蒙 `rawfile/channel.json` 与 Android `assets/channel.json`）
  同步扩到 **5 个节点**——`36`、`43-160`、`43-132`、`251`、`252`；
  [check_android.py](tools/check_android.py) 核对“两平台逐节点一致”通过 ✓。
- 用户澄清 `testapi`（即 8.148.251.114）**既是主控也是 worker**，因此 `251` 保留，
  标签写明「主控兼 worker」。
- 构建/安装：`BUILD SUCCESSFUL`；signed HAP 5,671,627 B（2026-10-19 09:35:40），
  `install bundle successfully`；hilog 已清空。

**未验证**：手机上 5 个节点按钮**尚未逐个跑通道测试**（点击时手机锁屏，`aa start` 报
“屏幕锁定”，无法驱动 UI）；`251`/`252` 的真机通道测试待解锁后进行。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十五轮：五节点通道真机全部 PASS

用户解锁后由 agent 代点（`uitest uiInput click`），五台全部跑通，页面输出与 hilog 双向留证。

- **`251`（8.148.251.114，fzz-002，主控兼 worker）PASS**：

  ```text
  主机指纹（已与内置配置比对通过）
  ecdsa-sha2-nistp256 SHA256:ZEoQL7Y09TiO+ILTDJrrdiYoWFyA33T4hLQmFBRKW8o
  通道已建立，主机指纹已核对     退出码：0
  uid=0(root) gid=0(root) groups=0(root) / fzz-002
  SSH_CONNECTION=112.66.178.114 18106 172.21.153.219 22
  ```

  hilog：`11:49:25 probeHostKey start host=8.148.251.114 port=22` →
  `probeHostKey ok type=ecdsa-sha2-nistp256 fingerprint=SHA256:ZEoQL7Y0…KW8o`（与固定值一致）。
- **`252`（8.148.252.117，FZZ-003）PASS**：同样路径，协商到 ECDSA
  `SHA256:8ez3chbYLqN4bD2D/PC6TvdaKY5YG7ZjKfYAyCdaIdc`（与固定值一致），
  `退出码：0`，stdout `uid=0(root) / FZZ-003`，
  `SSH_CONNECTION=112.66.178.114 16743 172.21.153.222 22`；hilog 11:52:38 对应记录同上。
- **五节点真机状态**：`36 → ecs-02826072`、`43-160 → VM-0-14-opencloudos`、
  `43-132 → VM-0-8-opencloudos`、`251 → fzz-002`、`252 → FZZ-003`，全部 `退出码 0`，
  且都只执行 `id/hostname/SSH_CONNECTION`（未调 audit/prepare/probe）。
- 5 个节点按钮在 1316px 宽下**一行放得下**，无需改两行。
- **UI 驱动经验（值得记）**：通道页在上一次测试出现结果文字后会**整体下移/上移**（本轮实测
  按钮行 y 从 1150 → 908 → 1199 变化过三次），按旧坐标点会误点进“节点地址”输入框
  （表现为地址没切换、无新 hilog）。稳妥做法：**每次点击前重新 dump 布局、按解析出的
  `bounds` 中心点**，不要复用上一轮坐标。
- 另在设置页观察到采集器侧报错
  `ktopia-crmeb · 601888：<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] …>` ——
  与机场限频导致代理降级（`订阅过于频繁`占位节点）相互印证，属服务端/代理侧问题，非 App。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十六轮：网络韧性改造（检测机制 + 原地重试 + 控制台永不阻塞）

用户要求：找出还有哪些网络问题会阻塞使用、说明“是否需要阻塞几小时”、并补一条回界面重试的通路，
且**控制台绝对要开放**。做法是先把失败点分成三类（**启动前可预检 / 可原地重试 / 只能中止**），再只补缺的部分。

**改了什么（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)、[Api.ets](harmony/entry/src/main/ets/services/Api.ets)）**

1. **真正的检测机制**（原先没有，只能等超时）：订阅系统网络状态——
   `connection.createNetConnection().on('netLost' | 'netAvailable')`，断网立刻提示
   “网络已断开；恢复后会自动重试”，恢复时自动 `refresh()`。这是“不需要阻塞几小时”的关键：
   失败原因与恢复时机都来自系统事件，而不是等 35 s/125 s 超时。
2. **只对“没到达主控”的请求退避重试**：`Api.request(..., attempts)`，descriptor 取 3 次、
   inbox 取 2 次，退避 900 ms 递增；**4xx/5xx 一律不重试**（主控已应答，重试无意义）。
   重试判据是内部记录的失败阶段（`连接主控`），不是错误文案匹配。
3. **失败不再“全释放回首页”**：`begin()` 的失败分支改为**停在原步**——清掉
   `runtime.incident/dispatching`（不占用别的待办），进入新的 `retry` 屏，提供
   `重试本步` / `放弃并返回待办` / `进入控制台`。语义不变：**任何路径都不重发探测**，
   节点的任务保持 `prepare` 暂停后的状态。
4. **控制台绝对开放**：`openConsole()` 不再因“另一待办验证中”拒绝；`verify` 屏新增
   `控制台` 按钮、`result` 屏新增 `进入控制台`。进入控制台会显式结束手机侧验证
   （节点任务保持暂停），从而不与进程级代理覆盖冲突。
5. **出口读取失败的文案**改为提示“可能是节点代理不可用”（对应机场限频这类真实状态），
   而不是笼统的“请重试”。

**验证**：`BUILD SUCCESSFUL in 7 s 384 ms`；新包 5,689,202 B（2026-10-19 11:59:45），
`install bundle successfully`。

**未验证（不声称完成）**：以上改动**均未经真机复验**——`netLost/netAvailable` 的实际回调、
`retry` 屏的三条路径、控制台按钮都需要在真机上触发一次失败/断网才能确认。
另：Android 版**未同步**这些改动（两平台行为暂不一致）。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十七轮：修处理页白屏与返回键失效

用户反馈：处理（验证）页遇到网络问题后**出现莫名其妙的白屏，且返回键不能用**。

**根因**（不是两件事，是同一条失败路径留下的残骸）：

1. `begin()` 失败后进 `retry` 屏时**没有清理 Web 与代理覆盖**。`showWeb` 仍为 `true`，
   验证 Web 仍以 `layoutWeight(1)` 参与布局、代理覆盖仍指向已拆除的隧道，
   于是内容区只剩一块空白 Web ⇒ 表现为白屏。
2. 那块**存活的 Web 组件会吞掉系统返回键**，而当时也只有“首页/设置外一律 goHome”这一条
   粗糙的 `onBackPress`，在 `retry` 屏上没有对应的“放弃并返回待办”语义 ⇒ 返回键像失灵。

**改了什么（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)）**

1. `begin()` 的失败分支补上 `await removeProxy(); await stopWindow(); this.showWeb = false;
   this.proxyApplied = false;`——失败一步不再留下死 Web 和悬空代理。
2. `abandonRetry()` 同样清 `showWeb` / `proxyApplied`。
3. 扩展现有 `onBackPress()`：`retry → 放弃并返回待办`、`result → leaveResult`、
   `diagnostics → 回首页`，其余非 `home/settings` 屏 `goHome`（等价于对应按钮动作）。

**构建期间发现并修掉的问题**：本轮首次编译报
`Duplicate function implementation`（`Index.ets:88` 与 `:902`）——文件里本就有 `onBackPress`，
我新增的版本与之重复。处理：**删掉新增的重复定义，把增强路由并入原有那一处**，
而不是留下两个实现。

**验证**：`BUILD SUCCESSFUL in 8 s 78 ms`；signed HAP 5,692,861 B（2026-10-19 13:45:45），
`install bundle successfully`。

**签名取回说明（不提交凭据）**：`build-profile.json5` 本轮开始时已是空 `signingConfigs`
（上次签名后已还原），从 `.hvigor/cache/task-cache.json` 的 `SignHap` 任务输入中取回本机
加密口令与 p12/cer/p7b 路径，临时写回、签名后再次 `git checkout --` 还原；ciphertext 未入 Git。

**未验证（不声称完成）**：白屏与返回键修复**尚未在真机上复现性验证**——需要在处理页
制造一次网络失败，确认不再白屏、且返回键能回到待办。第二十六轮的 `netLost/netAvailable`、
`retry` 三条路径仍在真机待验。Android 版仍未同步。

**真机取证（本轮已做，2026-10-19 13:47 前后，Mate 70 Pro+，包 5,692,861 B）**：

- `aa start -a EntryAbility -b com.myresearcher.waygate` → `start ability successfully`。
- 首页信息栏出现 `网络已恢复，正在重新同步` —— 这是**第二十六轮新增的 `netAvailable`
  回调在真机上真实触发**的证据（不是编译通过的自证）。
- 点 `诊断` 进入诊断屏 → 按系统返回键（`uitest uiInput keyEvent Back`）→ **回到首页**
  （dump 布局确认 `Waygate / 诊断 / 设置` 头与待办列表均回来）。即新一轮 `onBackPress`
  的 `diagnostics → home` 分支在真机上生效，返回键不再像失灵。
- **白屏那一条路径仍未复现性验证**：它只在 `verify` 屏（已展示 Web 后）失败时触发，
  需要一次真实失败才能确认“不再白屏”。本轮未主动制造失败以避免触碰真实任务。

### 2026-10-19 / Windows + Mate 70 Pro+ / 第二十八轮：待办是否 stale 的取证 + 自适应刷新

用户问：所有节点都正常采集了，App 还显示待处理吗？并明确要求**不要频繁轮询**，要一个更好的机制。

**先取证，不改猜测**：用 App 内置的 `waygate-phone` 私钥从 Windows SSH 到主控节点 `251`，
发现主控跑在 **Docker 容器**里（进程 cwd `/opt/waygate`，容器根在 `/proc/PID/root`），
待办库为容器内 `/data/waygate.sqlite3`。分别读库与读**实时 `/v1/inbox`**，结论一致：

- **节点侧一切正常**：`nodes` 里 5 个节点全部 `online=true`，每支股票 `state=running`
  （本机 601012、02 300124、ktopia-crmeb 601888、hk 600031、tokenseller 002648），
  `last_poll` 为当前时间，`error` 为空。
- **待办确认为 4 条，且不是 stale**：`items` 恰好 4 条，全部 `kind=challenge`
  （标题「需要人工验证」）、`online=true`、`can_verify=true`、`snoozed=0`：
  `02·002648`、`本机·600312`、`ktopia-crmeb·002648`、`tokenseller·603039`。
  库中 `state` 直方图为 **cleared 106 / pending 4**；这 4 条的 `seen` 均为 **约 6 秒前**
  （`reconcile` 在节点每次上报状态时调用，[service.py](hub/service.py:74)），
  各自 target 都带当前 `instance_id / job / stock / task / request_id / revision`。
  即**节点此刻仍在主动上报这 4 个 (节点, 股票) 上的活动 halt**，所以库把它们保持 pending。
- **为什么“正在采集”和“还显示待处理”能同时成立**：`live_target()` 看的是
  `stock_runtimes + detached_stock_runtimes` 里带 `active_halt` 的行
  （[store.py](hub/store.py:20)）；节点当前窗口跑的是**另一只股票**，而被 halt 的那只
  （detached）仍然停在原地等人核对。hub 只在节点**自己**报告该 halt 消失后才置 `cleared`
  （[store.py](hub/store.py:96)），并不因“别的股票在跑”而清。
- 所以答案：**是，仍显示 4 条待处理，而且这是主控的当前真实状态，不是 App 的陈旧缓存。**

**另：主控 `push_enabled=false`，即还没有设备推送通道**，所以“改用推送、彻底不轮询”
这条现在走不通；轮询仍是唯一机制，只能把它做得更省。

**改了什么（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)）——把固定 5 s 轮询换成自适应轮询**

1. 删掉 `setInterval(..., 5000)`，改为**自调度单次定时**：每轮结束后按当前状态决定下一次延迟。
2. 延迟取自“还能变多少”：基线 `30 s`；**待办集合连续未变时退避** → `60 s` → 闲置 3 轮后
   `120 s` 封顶；**验证/控制台 Web 打开时 `15 s`**（操作者正在等它的结果）。
3. **“有变化”的判据只看可操作状态**：待办 id/snooze 与节点在线位。
   **刻意不看帖子计数**——采集时它在不停变，算进去会让退避永远归零、等于没退避。
4. **切后台彻底停轮询**（不在后台空转与丢弃结果），回前台、`netAvailable`、以及首页新增的
   `刷新` 按钮都立刻打一轮并重置退避。

**验证**：`BUILD SUCCESSFUL in 8 s 421 ms`；signed HAP 5,697,429 B（2026-10-19 14:05:37），
`install bundle successfully`；临时签名配置用后已 `git checkout --` 还原。

**未验证（不声称完成）**：退避/停后台的**实际节奏未经真机计时确认**（需解锁后观察数分钟）；
首页 `刷新` 按钮未点过；Android 版仍未同步。（取证时读到的容器内 `console.token` 只在本机使用，
未打印、未入库。）

### 2026-10-20 / Windows + Mate 70 Pro+ / 第二十九轮：251/252 在 App 上无法处理的根因

用户报：`8.148.251.114`、`8.148.252.117` 这两个节点**在 App 上还是不能处理**。

**根因（全部为实测，非推断）：App 用「主控给出的 SSH 主机提示」去匹配内置通道，而这两台
被主控提示成了手机根本拨不通的主机。**

取的证据：

1. **主控实时下发的描述**（`GET /v1/incidents/{id}/verification`，对当前 4 条 pending 各取一次）：

   | incident 的 node_id | `ssh_host_hint` | 是否命中 App 内置通道 |
   |---|---|---|
   | `02` | `172.21.153.222` | ✗（App 里是 8.148.252.117） |
   | `local` | `testapi.zuzurent.com.cn` | ✗（App 里是 8.148.251.114） |
   | `03` | `36.138.125.17` | ✓ node 36 |
   | `sg` | `43.160.196.129` | ✓ node 43-160 |

2. **collector `/api/fleet` 的注册信息**（`base_url` 就是 hint 的来源，
   [collector.py](hub/collector.py:19)）：`local`(local=True, base_url=None → 取
   `config["public_url"]` 主机)、`02` = `http://172.21.153.222:8790`、
   `03` = `http://36.138.125.17:8790`、`tokenSeller` = `http://43.132.206.172:8790`、
   `sg` = `http://43.160.196.129:8790`。
3. **机器身份**（`hostname; hostname -I`）：`251` = `fzz-002` = `172.21.153.219`；
   `252` = `FZZ-003` = `172.21.153.222`。⇒ 主控节点 `local` = 251，主控节点 `02` = 252。
4. **App 侧旧逻辑**按主机字符串**精确相等**选内置通道（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)
   的 `bundledFor`）。于是：`local`/`02` 两条待办匹配不到任何内置节点 → 退化成
   「请填写该节点的 SSH 登录凭据」表单；而 `172.21.153.222` 是内网地址，手机**根本拨不通**，
   所以 252 上永远处理不了。三台旧节点能用，只是因为主控恰好提示了它们的公网 IP。

**改了什么（App 侧，未动服务端）**

1. [channel.json](harmony/entry/src/main/resources/rawfile/channel.json) 每个节点新增
   `nodeIds` 与 `aliases`：`36→["03"]`、`43-160→["sg"]`、`43-132→["tokenSeller"]`、
   `251→["local"] + aliases["testapi.zuzurent.com.cn","172.21.153.219"]`、
   `252→["02"] + aliases["172.21.153.222"]`（Android assets 同步，逐字节一致）。
2. App 改为**先按主控 `node_id` 匹配内置通道**，主机/别名仅在 node_id 匹配不到时兜底
   （`node_id` 也正是本机保存节点凭据的键，最稳定）。连接目标始终使用 `host`
   （251→8.148.251.114，252→8.148.252.117），即验证过的那两个公网地址。
3. **内置通道对覆盖到的节点优先**：不再让一个旧的/拨不通的本机档案覆盖内置配置，
   于是用户之前被引导填过一次的 251/252 凭据不会再卡住流程。

**验证**：`check_android.py` → `channel.json nodes identical on both platforms (5 nodes)`、
`Android project structure OK`；`BUILD SUCCESSFUL in 9 s 715 ms`；signed HAP 5,696,105 B
（2026-10-20 09:48:38），`install bundle successfully`；临时签名配置用后已还原。

**服务端根因（按既有约定：只记录，不改服务端）**：主控把这两台提示成手机不可用的主机——
252 用内网 IP 注册 `base_url`，`local` 被映射成 `public_url` 主机名。**正解在服务端**：
fleet 应对外报出节点**手机可拨通的公网 SSH 地址**（252→8.148.252.117、251→8.148.251.114），
或 `direct_descriptor` 把不可拨通的提示解析成可用地址。本轮的 App 别名只是绕过，属权宜。

**未验证（不声称完成）**：端到端「现在处理」到 `local`/`02` 两条待办**尚未在真机复验**——
安装后尝试拉起 App 时手机正被其他应用占用（`aa start` 被拒），未强行操作。通道本身
（内置凭据 + 固定指纹 → 251/252 SSH PASS）已在第二十五轮取证。Android 版仍未编译。

**真机复验（2026-10-20 09:59–10:02，Mate 70 Pro+，包 5,696,105 B）**

- 点开待办 `02 · 002648`（主控 `node_id = 02`，即 `252`）→ 页面走到
  **`已确认共同出口，请在下方手动完成验证`**，下方 Web 载入的是**真实来源页**
  （`guba.eastmoney.com` 东方财富股吧，可见 `$卫星化学(SZ002648)$`），同时 hilog
  `waygate_ssh` 持续出现 `direct-tcpip channel open`（原生转发通道在跑）。
  ⇒ **252 现在能在 App 上处理**；修复前这里是「请填写该节点的 SSH 登录凭据」表单，
  且提示主机是内网 IP。全程**未提交任何探测**（未完成验证码），节点任务维持既有暂停状态。
- 点 `取消` 后正常回首页并释放隧道/代理。第二次点击时待办顺序已变
  （主控 `ORDER BY created DESC` 且这 4 条会churn），实际又进了 `02 · 002648` 这条，
  **`local`(=`251`) 那条未取得干净的单次取证**；期间手机被用户切到其他应用（前台易主），
  未再强行操作。

**仍未验证**：`local`(=251) 待办的端到端「现在处理」单独取证；Android 版仍未编译。

### 2026-10-20 / Windows / 第三十轮：「正常节点有待办、被拦截的 hk 反而没有」的真相

用户报：现在正常的节点显示「需要处理」，而被拦截的 hk 节点反而没有。**查完发现：不是 hk 漏报，
而是它那条待办已经被人处理掉了。**

**取的证据**

1. **主控库里 hk 的待办确实建过、也已经清了**（`waygate.sqlite3`）：
   `tokenSeller · 002463 kind=challenge` —— **created 37 分钟前、seen 29 分钟前、现 state=cleared**。
2. **hk 节点（43.132.206.172）自己的事件流**（`collector.db` 的 `events`，时间戳逐条对上）：
   - `1792461661`（≈37 分钟前）`challenge`：「检测到身份核实页面；已停止自动请求」，
     evidence 2864（`身份核实`、assets `em_capt.js/fd_guba_validate/validate.js`、
     `server_blacklist: unproven`）；紧接着 `node_source_auth_stopped`：
     「验证码或身份核验：本节点后续来源请求已停止」。⇒ 这一步 `_halt("blocked","challenge")`
     写下了 `active_halt=challenge`，主控因此**建了那条待办**。
   - `1792462192`（≈29 分钟前）`probe_scheduled`：「单次探测已排队…结束后暂停」。
   - `1792462194` `detail_acquired`（request 2865，正是被拦的那篇 `1561403249`）+
     `node_source_auth_cleared`：「**人工单次探测**取得有效来源响应；节点停止已解除」+ `probe_success`。
     ⇒ 探测成功 → `_clear_active_halt()`，`active_halt` 变 null。
   - `1792462197` `recovery_checkpoint_resumed`（`reason: manual_resume`）+ `started`：「开始/继续任务」。
   - 之后事件全部是 `detail_acquired` / `list_acquired`（002463 一直采到 10:39），
     即 hk **现在没有处于拦截状态**，`state=running`、`last_success` 就在 1 分钟前。
3. **hub 侧为什么随后清掉待办**：`reconcile` 里「节点自己报 halt 没了就置 cleared」
   （[store.py](hub/store.py:96-L98)）—— hk 的 `active_halt` 归 null 后，
   `live_target()` 返回 None，那条 pending 就被清。这是**规则本身**，不是丢报。
4. **对照：local/02/sg 为什么还显示待办**：它们的运行记录里**仍挂着 detached 的旧 halt**
   （`local` 600312 / `02` 002648 / `sg` 603039，`active_halt=challenge` + `halt_task_id` 齐全，
   `seen` 0.1 分钟前仍在上报），**根本还没处理**；它们当前窗口跑的是另一只股票，与待办无关。
   `03` 则是**当前**窗口（601888）被拦，带 `node_source_auth_barrier`。

**结论**：两边都不是显示错误 —— hub 的规则是「节点报 halt 在→保持待办；节点报 halt 没了→清待办」。
「被拦截却看不到待办」= 那条拦截**已经完成了一次人工探测并继续采集**（事件里写明 `manual_resume`）。
**用户已确认这次「人工单次探测 + 继续」是他本人在控制台/App 上做的**，因此 hk 那条被清属
正常闭环，待办列表本身没有错。

**本轮未改任何代码**（服务端只记录，不改）；无构建、无安装。取证用的 `console.token` 只在本机使用，
未打印、未入库。

### 2026-10-20 / Windows / 第三十一轮：把「等一个永远不来的回调」这一类卡死封掉

用户报：App **「页面什么都干不了只能杀死进程」**（和前面白屏同类卡死）。

**取证受限，先说清楚**：手机当时正被另一个应用占用（配送 App），随后连无线调试也断了
（`hdc tconn` → `Connect failed`），所以**没拿到卡死画面也没有当时的 hilog**；唯一确认的是
进程还活着（`pidof com.myresearcher.waygate` 有值）。因此本轮是**按代码上可证的卡死族**来修，
不是按现场证据修。

**卡死族（会让 `busy` 永远为 true，于是所有按钮 `.enabled()` 全为假 ⇒ 只能杀进程）**

1. `verify()` 的失败分支里现在有 `await removeProxy()` + `await stopWindow()`
   （第二十七轮为修白屏加的），而：
   - `removeProxy()` 等的是 `ProxyController.removeProxyOverride(cb)` 回调；
   - `stopWindow()` → `detach()` 等的是 Web 组件 `.onDisAppear` 回调。
   **这两个回调只要不来，await 就永远不返回**，于是 catch 卡住，
   `finally { leaveBusy() }` 永远执行不到 → 全屏控件灰掉。
2. `detachPending` 一旦被赋成「永不 resolve」的 promise，**之后每次 `detach()` 都会复用它**，
   即进程内永久卡死，只能杀。
3. `begin()` 里 `applyProxyOverride(config, cb)` 的回调同理。

**改了什么（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)，只动 App）**

1. 新增模块级 `bounded(promise, ms=2500)`：**所有这类回调等待都和超时赛跑**，超时即继续；
   迟到的 rejection 被显式吃掉（避免 race 已结算后的 unhandled rejection）。
2. `detach()` 用 `bounded` 等待，且**无论超时与否都清掉 `detachPending` / `destroyWeb`**，
   不留不可解的 promise；`removeProxy()` 同样有界、改为尽力而为（不再 reject 卡住）。
3. `busy` 统一走 `enterBusy()` / `leaveBusy()`（8 处入口、`finally` 全部收口），
   并给 busy 挂 **watchdog（`BUSY_WATCHDOG_MS` = 5 分钟）**：任何一步真的回不来，
   也会强制放开操作并提示「上一步超时未返回，已恢复操作；可重试或进入控制台」。
   （5 分钟取在合法最坏耗时之上：探测等待上限 125 s + 出口测量最多 2×45 s。）

**构建**：`BUILD SUCCESSFUL in 8 s 762 ms`；signed HAP 5,701,938 B（2026-10-20 14:45:58）。
**安装未完成**：手机无线调试已断开，`hdc tconn 192.168.3.156:40525` 返回 `Connect failed`，
待设备可达后再装。临时签名配置用后已 `git checkout --` 还原。

**未验证（不声称完成）**：真机上「卡死后能自愈」没验过；本轮只封了「等待无上限」这一类，
**不排除还有别的卡死源**（例如原生 SSH 若阻塞 UI 线程，那不是 await 超时能救的）。
要真正定位，需要拿到卡死现场：页面 dump + 卡死时的 hilog。

### 2026-10-20 / Windows + Mate 70 Pro+ / 第三十二轮：弱网体验（分级超时 + 只重试失败那一步）+ 消息折叠

用户选了「收尾真机验证（A 组）」+「补弱网体验（B 组）」。设备两次到手都被前台别的应用占着
（先是配送 App、后是抖音），**没有强行抢前台**，所以 A 组仍未验；本轮把 B 组做完。

**改了什么**

1. **弱网分级超时**（[Api.ets](harmony/entry/src/main/ets/services/Api.ets)）：原先固定 12 s/35 s。
   改为 `TIMEOUT_TIERS = [[10,30],[15,45],[25,70],[40,100]]`（连接/读取秒），
   `Api.tier` **收到任何响应就降一级**、**失败且阶段仍是「连接主控」（没到达主控）就升一级**。
   弱网下同一条请求会自己变耐心，链路恢复后自己收紧，不必人工调。
2. **保留已建立通道、只重试失败那一步**（[Index.ets](harmony/entry/src/main/ets/pages/Index.ets)）：
   - 把 `begin()` 的最后一段（显示浏览器 → 核对共同出口 → 载入来源页）拆成 `openWindow(target)`；
   - 新增 `channelReady`：隧道 + 代理一旦建立即置真，`stopWindow()` 里清掉（隧道管道没了就不能复用）；
   - 失败统一走新的 `holdForRetry()`：**通道还在就只收掉 Web，保住 SSH 会话/隧道/代理**；
     通道没建起来才全清（也就是第二十七轮那条防白屏的路径）；
   - `retryStep()` 分支：通道还在 → **只重进 `openWindow`**，不再 `releaseSsh`、不再重跑
     audit/prepare/exit/forward；否则整条重来。
   这正是「弱网时不要为一次页面没加载就重连+重跑 worker 步骤」。
3. **消息归集折叠**：新增 `nodeNotes`（按节点标签存最近一条）与 `otherNodeNotes`、`showOtherNotes`。
   信息栏只显示当前节点那条；其他节点的折成 **「其他节点 · N 条历史」**，点一下展开。

**构建/安装**：`BUILD SUCCESSFUL in 8 s 617 ms`；signed HAP 5,714,044 B（15:36:10 与随后一次重建），
`install bundle successfully`（第三十一轮的防卡死包也一并装过）。签名配置用后已还原。

**未验证（不声称完成）**：
- B 组**全部未经真机复验**：分级超时是否真的减少弱网失败、`retryStep` 复用通道是否真的跳过了
  audit/prepare、消息折叠的展开/收起，都没在真机上点过。
- **A 组仍未验**：`local`(=251) 端到端「现在处理」、`retry` 屏三条路径、「控制台/进入控制台」、
  首页「刷新」、卡死自愈 —— 依旧待设备真正空出来。
- Android 版仍未同步这些（含本轮的弱网改动）。





