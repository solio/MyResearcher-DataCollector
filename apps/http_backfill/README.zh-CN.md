# 闲时 HTTP 回补控制台

这是独立的研究采集应用：不依赖 GUI，以每分钟最多一次来源请求起步，手机网页查看状态、暂停与手动探测。本机采集数据、任务、请求与恢复台账统一保存在本应用 `data/collector.db`；原有 `posts` 查询结构沿用浏览器的 `SimplePostStore`，raw 响应继续留存。不写根目录的生产 `data/collector.db`，也不自动送入训练。当前阶段不承诺来源长期放行，更不承诺拥有二十年历史。

## 本地启动

需要 Python 3.11+；urllib 模式没有额外依赖，curl 模式需要系统 curl。

```bash
cd MyResearcher-DataCollector
python3 -B apps/http_backfill/server.py --host 127.0.0.1 --port 8790
```

打开 `http://127.0.0.1:8790/`。首启创建 `apps/http_backfill/data/console.token`，文件权限为 0600；复制其中的令牌到登录框。令牌不进入浏览器 localStorage、代码仓库或服务日志。也可以通过 `BACKFILL_TOKEN` 环境变量配置至少 24 字符的随机令牌。

创建任务时指定股票、上海时区起止日期、请求间隔和客户端。默认不启动；检查配置后点击“开始”。所有列表、详情和重定向共用一个请求间隔，最少 60 秒；控制台每几秒刷新一次不算来源请求。列表与所需详情交替处理，详情规则沿用已有采集器：去掉标题首尾空白后长度 ≥40 才排入正文队列。短标题帖子仍保留列表文本，不丢帖子，也不把标题当作已核实正文。

## 配置编辑与任务管理

暂停并等待当前请求保存后，直接修改表单并点击“保存修改”。运行时先点“暂停后编辑”；页面会等待当前请求结束。可以修改股票、日期范围、请求间隔和客户端，保存后保持暂停。仅修改间隔或客户端保留原队列；增删股票保留其他股票进度；修改日期范围则从已留存的列表证据重新计算范围，复用已完成正文，并从第一页重新核对相应股票的列表。

每个股票卡片可“移除”，也可“删除当前任务”。删除是归档并取消待采队列，保留帖子、raw、请求台账和配置版本；移除最后一只股票会归档当前任务。历史任务在页面下方查看。这些操作不清除实例阻断或冷却时间：即使任务已删除，仍需对原失败目标完成一次人工探测，成功后才能创建新任务。

## 阻断与恢复

- 确认的身份核实页、403/429、未知结构、错误编码和网络异常会暂停并保留原始响应/失败事实。
- 存在普通验证码脚本资源不等于受拦截；收到真实 payload 也不能掩盖同时出现的挑战信号。
- “重试一次”仅排队一次真实来源探测，仍受全局间隔与 `Retry-After` 约束。探测成功后仍暂停，由用户决定是否继续。失败不会进入循环重试，也不清空旧阻断记录。
- 进程异常中断时，不把未确认请求当成功；保留断点和未知结果。重启应用不会自动清除 blocked/error。
- HTTP 不执行页面 JavaScript，动态生成的滑块不可直接观测。报告区分“收到的响应无明确挑战且数据有效”与“永久无验证码”；仅靠原始 HTTP 不能确定服务端黑名单的粒度或原因。

## 数据与范围

一条源帖子在 `posts` 中始终是一行，键为 `(source, source_item_id)`。列表采集
保存这行的 `title`，详情补充更新同一行的 `content`，保留标题和帖子身份。
“已补详情”是已采帖子的子集，补充详情不会增加帖子总数。例如已采帖子 293、
其中已补详情 52、未触发补详情 241、待补详情 0，表示 293 个帖子均保留列表
记录，其中 52 个还取得了正文；241 不是标题总数。未触发只是当前标题长度规则
的结果，不能据此断言来源没有正文或短标题已完整。

每页列表保留日期范围内的普通帖子，仅给列表标题长度 ≥40 的帖子建立正文队列，优先获取所需详情，再推进下一页。这里的列表回补与正文 enrich 在同一个长任务中交替进行，详情不会等整个历史列表抓完才开始。源 ID 去重，保留跨股票关联；原始空正文单独计数，删除与失败分别记录。

这条 40 字规则是既有的疑似列表截断补正文策略，不能证明短标题就是完整正文。API 与台账明确区分 `list_title` 和 `detail_body`；控制台显示已采帖子总数，以及其中已补详情、待补详情和未触发补详情的帖子数。升级前已经取得的短标题正文会保留；旧版尚未执行的短标题详情队列会转为列表文本记录，已有阻断目标仍保留一次探测入口。

暂停恢复、进程重启和较长的详情处理会触发列表校准。系统保留源帖子 ID 与发布时间锚点，重新定位已观察的区间、补入新发现的详情，比较连续两轮区间观察；一致后才按重新定位的页码继续。校准也是实际来源请求，同样受全局间隔限制，控制台单独显示校准次数、轮次和页码漂移。若列表持续变化就继续核对；锚点缺失、时间顺序未知或不能安全定位时会显示缺口或暂停。部分非普通帖子不按发布时间排列，不能拿这些行的最早时间推断历史位置。

用户指定历史日期边界后，应用显示列表覆盖与正文完成度；来源已到尽头但还没达到日期边界，会显示覆盖不足。两轮局部观察一致只证明该次锚点区间核对结果，不能证明整段历史完整，更无法发现从未观察到且已永久删除的帖子。`coverage_complete` 和 `model_database_eligible` 始终为 false；这批数据继续留在独立研究库。

`data/` 包含本机唯一运行数据库 `collector.db`、请求对应的 `raw/` 响应和控制台令牌 `console.token`。`collector.db` 的 `posts` 保持原 `SimplePostStore` 字段，其他表保存任务、请求、恢复、来源与导出版本；当前核实正文只写 `posts.content`，工作状态不另存一份正文。原来的 `posts` SQL、源 ID、股票代码、发布时间和 enrich 候选规则可以沿用。未取得详情的 `posts.content` 是 NULL；已取得的真实空正文是空字符串。正文来源、跨股票关联和范围缺口保留在同一库的台账/API 中，不会凭局部数据写入完整回补覆盖。raw 和不可变导出版本保留历史证据，因此历史正文版本仍可能出现于证据中。

旧版升级必须执行下方的一次性迁移命令，完整校验后自动删除运行目录中的 `experiment.sqlite3` 及其 WAL/SHM。迁移保留断点与来源阻断，自动生成旧库备份，不请求来源、不移动 raw；重复执行安全。`api/posts` 是带原始来源/范围状态的研究查询，不是向生产库晋升数据的接口。实例拥有持久化 `instance_id`，状态、请求和帖子查询携带该 ID，供后续汇总追踪来源使用。

本版支持一个中央控制台管理本机和多台远程实例，并在后台增量合并数据。每台实例保留自己的 SQLite、队列、请求间隔和阻断记录；中央服务通过鉴权 API 控制任务和同步证据，不直接拼接数据库文件。各实例的股票和时间窗口在同一控制台分别配置。

## 多实例统一控制与合并

现有服务器可以同时作为采集实例和中央服务，无需迁移原任务。手机只打开中央服务器的 `/collector/`。在“采集实例”区域登记其他服务器的实例别名、名称、**IP、端口和访问密钥**，协议默认 HTTP；当前支持本机加最多 16 台远程实例。例如填 IP `10.0.0.12`、端口 `8790`，中央服务直接访问该机的鉴权 API。采集节点不需要域名、nginx 或单独供手机访问的前端。

填写中央服务器能够访问的 IP，可以用同一 VPC/可路由内网的私有地址。安全组/防火墙只需放行中央服务器到采集节点这个 TCP 端口；手机不需要访问节点端口。登记表单的高级“服务 URL”方式保留已有 HTTPS/nginx 路径入口，例如 `https://<已有域名>/collector/`，原登记无需改写。新节点部署命令见下方“只运行采集 API 的节点”。

v5 中央服务支持 v4/v5 采集实例逐台升级；先升级中央服务，再升级其他节点。要删除某节点的旧任务库，在该节点执行迁移命令。登记时中央服务先验证版本和持久化 UUID，同一个实例不能重复登记；复制原 `data/` 会复制实例身份，不能当作新的独立采集机。登记已有独立运行实例可保留它的数据与任务；全新采集机使用自己的空 `data/`。修改登记时访问密钥留空会保留旧密钥；更换密钥后重新验证连接。密钥只存中央服务的私有 `data/fleet/registry.json`（0600），不返回浏览器、不写浏览器本地存储。

选择实例后，原来的任务配置、暂停、继续、单次探测、股票删除、任务归档和运行记录都作用于选中的实例。卡片显示各机状态、阻断原因、最近连接时间和同步进度；断联保留上次状态并显示连接错误。远端鉴权失败不会退出中央网页登录。移除登记停止后续控制和同步，已合并数据保留，远端正在执行的任务不会因移除登记被自动停止。

后台默认每 60 秒检查新增记录；有积压时分批接续同步，也可以点击“立即同步”。同步只传输采集机已有记录及其原始响应，不发起新的股吧请求。每台实例有独立的不可变导出序列与持久化同步游标，断联或重启后继续传输；哈希或原始响应验证失败会显示同步错误并保留游标，不把失败当成已同步。

中央服务的数据文件如下：

| 文件 | 用途 |
| --- | --- |
| `data/collector.db` | 本机采集、任务、请求、恢复、来源与不可变导出台账 |
| `data/raw/` | 本机原始响应，路径与哈希在迁移后保留 |
| `data/migration-backups/` | 迁移前旧库的离线备份和迁移凭据；运行时不读写旧库 |
| `data/fleet/collector.db` | 多实例汇总后的兼容采集库，沿用原 `SimplePostStore` 表结构 |
| `data/fleet/merge.sqlite3` | 每实例版本、原请求、同步游标、选中记录、范围快照与冲突 |
| `data/fleet/raw/` | 校验过的原始响应，按 SHA-256 保存，相同字节只存一份 |
| `data/fleet/registry.json` | 私有实例连接配置，包含服务器端访问密钥 |

合并以 `(source, source_item_id)` 为源身份：相同帖子在兼容库保留一行，各实例的观察和来源证据保留在合并台账。已取得正文优先于未取得正文；后来的列表记录或详情不可用记录不会清空已有正文。合法空正文仍是空字符串，缺正文仍是 NULL。若身份字段或正文事实不同，保留所有版本并登记冲突；兼容库选择一条完整观察，不把不同版本的字段和文本拼接起来。选择顺序为有正文优先，再按实际观察时间、实例 UUID 和序列确定；这不是内容质量判断。控制台显示冲突数，不能据此把数据标为完整或自动送入训练。

本版集中管理各实例自己的任务，股票/日期窗口由你在对应实例中配置。某机验证码阻断后保留该机的停机状态，不自动把失败请求交给另一 IP；远程控制指令超时会提示结果未确认，不自动重复提交。

## 运行记录翻页

“源请求”和“控制事件”各有上一页、下一页、回到最新记录的控制。最新页可每 5 秒刷新；浏览历史页时只更新采集状态，不覆盖历史记录。每次只渲染一页，数据增长不会把全部记录塞进手机页面。

历史页使用固定快照和降序 ID 游标，新请求到达不会挤乱正在浏览的分页。原 `api/requests`、`api/events` 默认数组接口仍兼容；分页查询使用 `?paged=1&limit=30`，返回 `items`、`has_more`、`next_cursor`、`snapshot_id` 和 `total`，下一页带上 `before_id=next_cursor&snapshot_id=...`。

## 服务器部署

沿用 labelapp 的服务器和现有 HTTPS，新增入口 `https://testapi.zuzurent.com.cn/collector/`。以下命令由服务器执行；仓库包含部署文件，尚未自动修改远程服务器。应用使用独立端口 8790 和 SQLite，不需要 labelapp 的 MySQL 配置。

先进入服务器上存放项目的目录，首次部署：

```bash
git clone git@github.com:solio/MyResearcher-DataCollector.git
cd MyResearcher-DataCollector/apps/http_backfill
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8790/healthz
```

若服务器已有旧版数据，进入 `MyResearcher-DataCollector/apps/http_backfill` 执行 `git pull && bash migrate-storage.sh`；这次会切换到单库。以后普通更新再使用 `git pull && docker compose up -d --build`。`compose.yml` 是自动识别的文件名，无需 `-f`；命令要在这个应用目录执行。健康检查返回 `{"ok": true}`。首启处于暂停状态，不会因健康检查或网页刷新请求股吧。

基础镜像默认复用 labelapp 使用的 `fangzuzu-docker-registry-vpc.cn-guangzhou.cr.aliyuncs.com/fangzuzu/python:3.12-slim`。应用镜像直接在服务器构建，不需要推送镜像仓库；已有私有仓库登录和基础镜像缓存可以沿用。首次应用构建仍需访问 Debian 软件包源安装 curl 和 CA 证书。其他环境可设置 `BACKFILL_BASE_IMAGE=python:3.12-slim` 后运行 Compose。应用没有额外 pip 依赖。

容器内绑定 0.0.0.0，宿主仅发布 `127.0.0.1:8790`；应用目录的 `./data` 挂载为容器 `/data`，`collector.db`、raw 响应、队列和登录令牌都保存在这里。配置使用 `/collector/` cookie path，网页登录通过 nginx 入口进行。直接本机启动不设置此前缀，使用根路径即可。Dockerfile 的专属 ignore 只发送源码和本应用，不发送生产数据、历史 raw、凭据或 Git 目录。

在现有 `server_name testapi.zuzurent.com.cn` 的 **HTTPS server 块内部**加入以下两个 location（同一块中的 `/labeler/` 保持原配置）：

```nginx
location = /collector { return 301 /collector/; }
location ^~ /collector/ {
    proxy_pass http://127.0.0.1:8790/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_cache off;
    proxy_read_timeout 35s;
}
```

`proxy_pass` 末尾的 `/` 必须保留，将 `/collector/api/status` 转为后端 `/api/status`。`^~` 避免现有通用静态文件正则 location 抢走 JS/CSS 请求。Host 保留端口以便同源控制校验；`X-Forwarded-Proto` 使 HTTPS 登录 cookie 带 Secure。此配置按 labelapp 文档中的宿主机 nginx 方式；若 nginx 在另一个容器中运行，应使用容器能访问的 upstream，不能用该容器自身的 127.0.0.1。

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -fsS https://testapi.zuzurent.com.cn/collector/healthz
```

取出首启自动生成的令牌，在手机打开上述 `/collector/` 网址并填入登录框：

```bash
docker compose exec -T collector-console cat /data/console.token
```

登录后创建股票和日期范围明确的任务，客户端选 curl、间隔选 60 秒，再点击“开始”。关闭手机或 SSH 不影响后台采集。列表和详情共用每分钟一次额度；遇到验证码、限流或异常会暂停。手动“重试一次”只发一次探测，成功后仍需点击“开始”继续。

从 v1–v4 升级为单库 v5，在当前 `http_backfill` 目录只执行这一条：

```bash
git pull && bash migrate-storage.sh
```

脚本先构建新镜像，再停止旧 worker；在挂载的 `/data` 上离线迁移，保存 SQLite 一致性备份（包括 WAL 中已提交的数据），校验旧台账、帖子与全部已记录 raw 哈希后发布 `collector.db`，删除原 `experiment.sqlite3` 及其 sidecar，再启动服务并检查健康状态。无需手动删文件，也无需改 nginx。校验失败不会删除旧库，服务保持停止，修复后重新执行同一命令。断电中断可依据迁移凭据续办；成功后重复运行不重复搬运数据。

任务、股票配置、历史请求、验证码阻断、冷却时间、实例 UUID 和既有导出同步序号保留。迁移过程不自动运行任务；重启后保持来源阻断或安全暂停，在手机控制台确认后继续，原页位置会按现有规则校准。登录令牌、raw 与 `fleet/` 保留。备份文件位于 `data/migration-backups/`，程序不再使用其中的旧库；不要把备份当成新的采集实例。

如果不用 Docker，先停止自己的服务，然后在仓库根目录运行：

```bash
python3 -B apps/http_backfill/migrate_storage.py --data-dir apps/http_backfill/data
```

迁移工具拒绝正在使用的 worker 目录、未确认归属的现有库、越界路径及损坏证据。迁移成功后再启动新版服务。回退旧代码需要先停服务并恢复迁移前的两份对应数据库，不能让旧代码读取单库布局。

本版保留了原基础镜像与安装 curl 的 apt 指令，没有新增 apt/pip 依赖；已成功构建的服务器通常会复用这一层。构建日志应显示该 `RUN apt-get ...` 步骤 `CACHED`。Docker 默认构建网络仍是隔离网络；缓存是否复用取决于基础镜像和前面的构建指令是否改变。新增根目录 `.dockerignore` 作为兼容后备，避免旧构建器发送采集数据。

同样的 nginx 片段保存在 [deploy/nginx.conf.example](deploy/nginx.conf.example)。

### 只运行采集 API 的节点

节点只启动 Docker 采集服务，使用自己的数据目录和自动生成的访问密钥，不配置域名或 nginx。在节点的 `apps/http_backfill` 目录创建本地 `.env`，以下 `10.0.0.12` 必须替换为**这台节点实际拥有的内网 IP**：

```dotenv
BACKFILL_BIND_ADDRESS=10.0.0.12
BACKFILL_PORT=8790
BACKFILL_API_ONLY=1
BACKFILL_FLEET_SYNC_ENABLED=0
```

示例也保存在 [deploy/node.env.example](deploy/node.env.example)，可复制为 `.env` 后修改 IP。`BACKFILL_PORT` 是宿主机端口，容器内仍用 8790。`BACKFILL_API_ONLY=1` 关闭节点的静态网页和浏览器登录，只保留鉴权 API 与健康检查；`BACKFILL_FLEET_SYNC_ENABLED=0` 关闭该节点自身的后台汇总，主控仍能控制和读取其数据。`.env` 保留在节点本地，后续更新命令会继续使用它。

新节点运行：

```bash
git pull && docker compose up -d --build
docker compose exec -T collector-console cat /data/console.token
```

已有 v1–v4 数据的节点先执行 `git pull && bash migrate-storage.sh`，完成单库迁移；已有 v5 数据使用上述普通更新命令。全新节点保持暂停，登记不会自动开始来源请求。

在主控服务器验证到节点的网络连通：

```bash
curl --connect-timeout 3 --max-time 5 -fsS http://10.0.0.12:8790/healthz
```

返回 `{"ok": true}` 后，在**中央手机网页**点“登记远端实例”，填这个 IP、端口、节点令牌。股票/日期、开始/暂停、历史查看和增量合并全部在中央网页操作；节点不用再登记主控地址。主控负责向节点发起连接，所以安全组放行的是主控到节点，而不是手机到节点。主控与节点必须已有可路由网络连接；没有路由或端口被防火墙拦截时，控制台会显示连接错误。

中央服务器继续使用默认 `BACKFILL_API_ONLY=0`、`BACKFILL_FLEET_SYNC_ENABLED=1` 和原 `127.0.0.1:8790`/nginx 配置；本次升级无需修改中央 nginx。

新增接口均需现有控制台鉴权：`GET api/fleet` 查看实例与汇总状态，`POST api/fleet/nodes` 登记，`PATCH/DELETE api/fleet/nodes/{id}` 修改/移除，`POST api/fleet/sync` 以 `{node_id:"all"}` 或指定实例安排后台同步。直接登记请求使用 `{id,name,host,port,scheme,token}`，`host` 为裸 IPv4/IPv6、`port` 为 1–65535 整数、`scheme` 默认 `http`；旧 `{id,name,base_url,token}` 接口继续兼容，不能同时提交两种地址。`api/nodes/{id}/...` 转发受限的现有采集接口；`GET api/federation/export` 与 `GET api/federation/raw` 提供可校验的增量证据。所有转发都在服务器端完成，浏览器不直接连接采集机，也无需跨域配置。

不用 Docker 时可用 [deploy/collector-console.service](deploy/collector-console.service)，按实际路径与服务用户修改模板，准备可写的数据目录并安装系统 curl（如果选 curl 客户端）。服务和网页可重启，数据与暂停原因均持久化。

## 验证与长时间试验

```bash
cd MyResearcher-DataCollector
python3 -B -m unittest discover -s apps/http_backfill/tests -v
```

离线测试用于验证间隔、暂停、断点、原始证据和接口行为；不证明股吧长期可用。首轮真实试验应保持一个确定的客户端、60 秒间隔和固定任务配置，记录首次请求、成功请求、暂停时间及累计正文。运行一晚或更久后，以真实台账判断一分钟间隔是否足够；没有发生阻断的小段试验只能支持该观测时段。
