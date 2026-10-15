# Waygate 跨环境交接

更新时间：2026-10-15。当前角色 Developer；下一环境由用户和其他 agent 在
Windows 上执行 Developer / Tester 工作。仓库：`solio/MyResearcher-DataCollector`，
开发目录 `apps/waygate`，分支 `main`。不需要读取原会话。

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
看到“Waygate 容器已启动，本机接口正常”只代表容器就绪，不表示公网已经接通。

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
- [ ] 填写 IDE / SDK / 设备型号及系统版本、构建命令、安装结果。（IDE/SDK/构建命令已填，设备与安装待真机。）
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
