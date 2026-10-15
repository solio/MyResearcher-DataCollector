# 手机直接复用桌面验证命令

用户要复用 `./scripts/ops/verify_backfill.command 36 --resume`。
`36` 已是 `36.138.125.17` 的别名；完整 IP 在原脚本中本就可用。
原流程是 **Mac → SSH → worker 的现用 Mihomo → 来源**。
手机接替 Mac，主控只提供待办、节点信息和 H5；不要求主控登录 worker。

## 当前交付边界

已提供服务端只读描述接口和共享 worker 命令生成函数；手机直连 SSH 尚未接入。
Windows/App 环境完成 SSH 库接入、设备凭据保存和 ArkWeb 接线。不要继续为这条
路径配置主控 ssh 映射、复制私钥到主控或新增 worker HTTP 转发服务。
worker 仍运行原 Python3 操作代码和原 Mihomo，不新增部署；共享 helper 的
既有前提（当前无入口认证 HTTP/mixed 代理、远端 Python3，读取 Mihomo 配置时
已有 PyYAML）保持不变。此交付没有扩展带认证代理的支持范围。

## App 获取节点与待处理目标

用已有主控 token 请求：

```http
GET /v1/incidents/{incident_id}/verification
Authorization: Bearer <现有主控令牌>
```

走现有 `/collector/v1/` nginx 路由，不新增 nginx 配置。响应字段：

| 字段 | 用法 |
| --- | --- |
| `protocol` | 固定 `waygate.direct-ssh.v1` |
| `node_id` | 保存手机节点 SSH 配置的关联标识 |
| `ssh_host_hint` | fleet 登记的主机；local 节点用主控域名作为提示；未知/回环地址为 null |
| `target` | 原 instance/revision/job/stock/task/request_id，SSH audit 后必须匹配 |
| `worker_command` | 通过 SSH exec 原样执行的 Python3 命令，由桌面 `REMOTE_WORKER` 实时生成 |
| `source_domains` | 临时浏览器代理允许的来源域名；中立检查额外允许 `api.ipify.org` |
| `session_ttl_seconds` | 手机本次验证最多 600 秒 |

接口刷新已有 fleet 状态；离线、过期、已解除或非 challenge 待办返回 409。
接口不连接 SSH、不创建或锁定会话、不暂停、不探测、不恢复、不标记验证成功。
返回 host 是提示：NAT/非标准 SSH 端口、主控域名与 SSH 主机不同的部署，按手机
保存的实际 SSH 连接覆盖。不能把 collector HTTP 端口 8790 当成 SSH 端口，
也不能把主控视角的 localhost/容器网关当成手机连接目标。

SSH 的用户名、端口、密钥/密码、主机指纹由手机本地管理。可在“处理”时首次
导入该节点已有登录凭据并保存，后续一键使用，不每次再填；缺省 root/22 只是
UI 默认值。IP 不是 SSH 凭据，Mac 上的密钥也不会因为手机登录了主控就自动存在。
主控响应不包含 SSH 凭据。不要把密码或私钥放 URL、日志或提交到 Git。

## 用同一份远端操作代码

桌面源文件：[verify_http_backfill.py](../../scripts/ops/verify_http_backfill.py)。
`ssh_worker_command()` 将同文件 `REMOTE_WORKER` 编码成 SSH exec 命令；不是
另一份移动版 worker。手机不需要 Bash、Python 或 Playwright。

每个操作新开 SSH exec channel，执行描述接口返回的 `worker_command`，向该
channel stdin 写一个 UTF-8 JSON 对象，然后发送 **stdin EOF**，读取 stdout 和
退出状态。EOF 只关闭写方向，不应先销毁 SSH 会话。不要把 JSON 拼进 shell 命令。

输入 `{"action":"audit"}`；其他操作用下表 payload。返回格式沿用桌面：

```json
{"ok":true,"value":{}}
```

失败返回 `{"ok":false,"error":"具体原因"}`；SSH/进程退出错误单独显示。
未经人工确认只允许准备和中立检查；不能因为连接超时自动重发 probe/resume。

| 顺序 | action / 载荷 | 复用行为 |
| --- | --- | --- |
| 1 | `audit` | 读取真实节点状态；确认 instance/revision 及 target 的 job/stock/task/request_id |
| 2 | `prepare`, `target` | 原函数暂停仍在运行的任务、等待在途请求结束，保留原阻断和检查点 |
| 3 | `exit`, `stock`, `job` | 得到该任务当前 `route` 和 `exit_ip`，核对 route signature |
| 4 | 手机 SSH 转发 + ArkWeb | 转发目标使用 **SSH worker 视角**的 `route.host:route.port`，对应原 `ssh -L` |
| 5 | 手机浏览器检查出口 | 浏览器访问中立 IP 检查，必须与步骤 3 一致；之后打开 audit 返回的原失败 URL |
| 6 | 人工完成并点击继续 | 再检查浏览器/服务器出口和 route signature；把 `exit_ip` 写入原 target |
| 7 | 关闭 Web、代理、转发 | 只清理本次创建的资源，日常系统代理/Mihomo 规则不动 |
| 8 | `probe`, `target` | 原函数核对归属及出口，提交原请求一次探测，返回 `before_request_id` |
| 9 | `result`, `target`, `before_request_id` | 只读等候这一任务的一次探测完成，复用桌面默认 120 秒等待期限 |
| 10 | `resume`, `target` | 仅成功 outcome 为 real_data/detail_unavailable 时调用，只继续本股 |

audit 的完整 target 含原 URL 和 route；之后所有操作保留同一对象，不用新快照
偷偷替换原目标。若与待办 identity 不一致，刷新待办再处理，不能验证另一项。
其他股票由 prepare 暂停后保持暂停，不能做节点 start 或一键全部恢复。
本流程与桌面一样需要操作客户端持有 SSH 权限；不暴露公网开放代理。

## App 接线位置与真机验收

- [Index.ets](harmony/entry/src/main/ets/pages/Index.ets) 当前 `verify(item)`
  请求旧 `/sessions`，改为描述接口 + 手机 SSH 编排；保留现有 ArkWeb proxy override。
- [LocalProxy.ets](harmony/entry/src/main/ets/services/LocalProxy.ets) 当前把本地
  TCP 连接发到主控 WSS。改为 SSH direct-tcpip 到原任务 `route.host:route.port`，
  沿用 CONNECT 域名限制、背压和结束清理。不需要在手机安装 Mihomo 或开系统 VPN。
- [SessionRuntime.ets](harmony/entry/src/main/ets/services/SessionRuntime.ets) 后台/
  关闭时停止手机拥有的 SSH/代理资源，不再调用旧 hub cancel。
- 手机防重复点击，每节点仅一个本地会话；probe 发送前持久标记 dispatching。
  断线/进程退出后只核对原结果，不重发探测；如果未收到 before_request_id，应
  展示结果未知并让操作者到控制台核对。此描述接口不提供跨手机会话互斥，不能
  声称旧 hub 全局 lease 已自动迁移。
- 手机 SSH 实现须支持 exec stdin/EOF、direct-tcpip、密钥认证、主机指纹核对和
  可取消的连接。使用适配 HarmonyOS 的实际依赖；当前仓库没有此依赖，不要把
  空壳接口或 TS 模拟测试作为 SSH 已完成。Windows 环境需记录实际编译/真机结果。

验收关键：主控 `ssh` 配置为空、无法 SSH 登录 worker 时，手机仍能读取待办，
直连 worker、人工完成验证、只探测原请求并只恢复本股。取消/切后台不触发来源
探测，手机日常网络不变。Mihomo 不重启、不改节点选择、不新增公网代理端口。

服务端升级用 runs.md 中已有启动命令；旧 App 仍走旧 hub SSH，必须由 Windows
完成上述接线并重新打包才能使用新路径。服务端接口通过不等于手机功能完成。
