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

## 实际怎样合并和导出

能从主控连到的采集节点，按以下顺序操作：

1. 在主控网页登记节点的 IP、端口和令牌。已运行节点的数据和断点保留。
2. 点击“立即同步全部”。主控读取各节点已有的增量导出记录和原始响应，
   校验哈希后合并，不请求股吧。后台自动同步也执行同一过程。
3. 查看各节点同步状态；连接错误或哈希失败会显示错误，失败批次不会推进
   游标，此前已验证并合并的批次保留。
   “已同步”只说明该次已拥有记录传输完成，不代表历史日期窗口完整。
4. 在“中央合并数据”下载合并结果 CSV 或 JSONL。文件交给手机/浏览器下载，
   保存位置由浏览器决定；服务器上的合并库是主控 `apps/http_backfill/data/fleet/collector.db`，
   不是任意某台节点的原库。

相同 `(source,source_item_id)` 只保留一行帖子，标题和正文是这一行的字段。
先取得列表、后来取得正文，会补充原帖子；另一个节点只有列表时不会清空正文。
每节点观察和 raw 出处仍保存在 `fleet/merge.sqlite3` / `fleet/raw/`。同步游标是
不可变版本的序号，不是帖子数，一个帖子补详情后可以产生新版本。

网页还可下载**主控所在本机**的全部留存帖子。这个按钮不会随选中的远端
实例改变含义；导出某个远端的帖子可先同步到主控，或直接在该节点执行下面
的只读命令。CSV/JSONL 导出来自一个固定数据库快照，涵盖已留存的所有任务，
不是仅当前任务；它只包含兼容 `posts` 字段和缺正文/研究状态标记，不包含
令牌、注册表或任务控制数据。JSONL 保留 NULL 与真实空正文的区别；CSV 使用
`content_missing` 标记区别。帖子文件是当前数据投影，不是完整证据包，不能
用它重建同步游标和 raw 追溯。

在需要导出的服务器 `apps/http_backfill` 目录运行：

```bash
docker compose exec -T collector-console python - \
  --db /data/collector.db --format jsonl \
  --output /data/exports/posts.jsonl < data_export.py
```

文件会出现在宿主机 `./data/exports/posts.jsonl`。换成 `--format csv` 和
`--output /data/exports/posts.csv` 可得到 CSV；主控合并结果将 `--db` 换成
`/data/fleet/collector.db`。同名目标已存在时拒绝覆盖，请另取文件名。该命令
将当前 checkout 的独立脚本送入现有容器执行，无需为了导出重启采集器。

### 节点断网时用证据包合并

节点暂时不能被主控访问时，可在节点运行离线导出：

```bash
docker compose exec -T collector-console python apps/http_backfill/transfer.py \
  export --data-dir /data --output /data/exports/node-evidence.zip
```

得到宿主机 `./data/exports/node-evidence.zip`。将该文件复制到主控的
`./data/imports/node-evidence.zip` 后，可以先导入独立汇总目录，不打断正在
运行的主控或采集器。在主控 `apps/http_backfill` 目录运行：

```bash
docker compose exec -T collector-console python apps/http_backfill/transfer.py \
  merge --bundle /data/imports/node-evidence.zip --data-dir /data/offline-merge
```

独立结果是宿主机 `./data/offline-merge/fleet/collector.db`。不同节点的包都
导入同一目录便会合并；包名可以不同。若要直接进入现有主控的合并库，先
暂停主控进程，再用一次性容器导入，最后恢复主控：

```bash
docker compose stop collector-console
docker compose run --rm --no-deps collector-console \
  python apps/http_backfill/transfer.py \
  merge --bundle /data/imports/node-evidence.zip --data-dir /data
docker compose up -d
```

这会中断主控本机采集与面板服务，其他采集节点可继续运行。导入命令失败时
先查看错误；不要继续执行恢复命令。在线主控同时管理同步写入，离线 CLI
因此拒绝导入一个正在使用的目录。平时直接在面板点“立即同步全部”即可，
不需要停主控、导出包或手动搬数据库。

帖子证据包包含该节点固定导出序列快照、帖子关联的原始响应与哈希清单；不包含登录令牌、
注册表或整个运行数据库。导入使用同一套源 ID/实例/版本/正文合并规则，
校验失败会报错，重复导入幂等。合并目标仍是主控 `fleet/collector.db`。
不要用复制覆盖 SQLite 文件来合并实例，不要把包导入项目根目录的生产 `data/`。
这些新工具需要新版镜像；普通更新后即可使用。导出包完整仅表示已拥有证据
保存完整，所有日期覆盖和模型可用性仍需另外核实。未关联已采帖子的一些
失败、验证码、探测请求不会进入这个包；核验全部源请求间隔应使用下面的
全量台账审计，不能用包内的关联请求替代。包报告同时列出原库帖子数
`source_posts_at_snapshot`、有可导出证据的唯一帖子数 `counts.unique_posts`
和 `unexported_source_posts`；孤立且没有完整关联证据的投影不会被编造来源
或补入包内。单纯查看所有现有帖子，可使用前面的 CSV/JSONL 导出。

## 核验一分钟一次的实际速率

请求间隔针对每台实例的**源 HTTP 请求**，不是帖子数。一页列表可能带回约
80 条记录，因此 5,274 帖子可能来自几十次列表请求；详情请求、分页校准、
重定向、失败和手动探测也占同一来源请求额度。主控网页刷新与实例数据同步
不会请求股吧，不计入来源采集速率。

H5 显示选中实例的近期请求间隔审计及其样本范围。确认整段运行，应该在
该实例上执行全量只读审计。在 `apps/http_backfill` 目录更新 checkout 后，
以下命令把脚本传入**当前正在运行的容器**，不用重建或重启 worker：

```bash
git pull
docker compose exec -T collector-console python - \
  --db /data/collector.db --interval 60 < rate_audit.py
```

输出重点：

| 字段 | 核对内容 |
| --- | --- |
| `db_path`、`legacy_fallback` | 实际读取哪份请求台账；旧布局回退会明确显示 |
| `confirmed_requests`、`by_kind`、`classification` | 确认过的来源请求总数及列表、详情、回扫、重定向、探测口径 |
| `min_finish_to_start_seconds` | 前次完成到下次开始的最小实际秒数 |
| `violations.count`、`violations.items` | 低于 60 秒的次数，以及请求 ID、时间和间隔明细 |
| `unknown` | 未确认网络尝试、未完成、时间缺失、重叠、时钟异常；不能当作正常 |
| `config_policy`、`config_history` | 有记录支持的历史配置审计，与固定 60 秒下限分开 |
| `verdict` | `pass` 仅表示所读快照内的确认请求未发现低于审计下限；`fail` 已发现过快间隔；`unknown` 证据不足 |

退出码分别是 0 / 1 / 2；读取或输入错误是 3。`fail` / `unknown` 输出仍是
有效审计报告，不是容器启动失败。若刚好有进行中的请求，会记录为未完成，
可在响应结束后重跑只读命令。报告时间为带时区的 UTC，不要直接与手机的
上海时间字符串比较。

检查相邻来源请求的“前一次响应结束 → 下一次请求开始”间隔、短于 60 秒的
明细，以及未完成、缺少时间或网络状态未知的记录。时间/状态未知不能记为
合规；按自然分钟分桶只能辅助查看，跨分钟的两次请求也可能只相隔一秒，
应比较真实秒数。审计注明
历史任务配置，不能把配置变更之前的记录冒充当前配置的持续表现。
若仍是 v1–v4 双库布局，将 `--db` 改为 `/data/experiment.sqlite3`，不要对只有
兼容帖子表的旧 `collector.db` 做请求审计。审计不发起任何来源请求。

## 运行记录翻页

“源请求”和“控制事件”各有上一页、下一页、回到最新记录的控制。最新页可每 5 秒刷新；浏览历史页时只更新采集状态，不覆盖历史记录。每次只渲染一页，数据增长不会把全部记录塞进手机页面。

历史页使用固定快照和降序 ID 游标，新请求到达不会挤乱正在浏览的分页。原 `api/requests`、`api/events` 默认数组接口仍兼容；分页查询使用 `?paged=1&limit=30`，返回 `items`、`has_more`、`next_cursor`、`snapshot_id` 和 `total`，下一页带上 `before_id=next_cursor&snapshot_id=...`。

## 服务器部署

沿用 labelapp 的服务器和现有 HTTPS，新增入口 `https://testapi.zuzurent.com.cn/collector/`。以下命令由服务器执行；仓库包含部署文件，尚未自动修改远程服务器。应用使用独立端口 8790 和 SQLite，不需要 labelapp 的 MySQL 配置。

先检查应用的 `data/`：如果已有 `experiment.sqlite3`、旧 WAL/SHM，或存储
迁移尚未完成，必须先用 `git pull && bash migrate-storage.sh`。全新空目录，
或已经迁移为 v5 单库的目录，才使用下面的普通启动/更新命令。

先进入服务器上存放项目的目录，全新部署：

```bash
git clone git@github.com:solio/MyResearcher-DataCollector.git
cd MyResearcher-DataCollector/apps/http_backfill
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8790/healthz
```

若服务器已有旧版数据，进入 `MyResearcher-DataCollector/apps/http_backfill` 执行 `git pull && bash migrate-storage.sh`；这次会切换到单库。以后普通更新再使用 `git pull && docker compose up -d --build`。`compose.yml` 是自动识别的文件名，无需 `-f`；命令要在这个应用目录执行。健康检查返回 `{"ok": true}`。首启处于暂停状态，不会因健康检查或网页刷新请求股吧。

若看到容器一直 `Restarting (1)`，先运行
`docker compose logs --tail=40 collector-console`。日志出现
`检测到旧版 experiment.sqlite3` 表示启动前的存储检查拒绝旧布局，需执行上面
的迁移命令。进程立即退出时，`docker ps` 的 Ports 列可能为空，不能据此判断
`.env` 没生效；可用 `docker inspect` 的 `HostConfig.PortBindings` 检查配置。
迁移保留一致性备份，完整校验后才清理运行目录旧库，不要手动删除数据库。

基础镜像默认复用 labelapp 使用的 `fangzuzu-docker-registry-vpc.cn-guangzhou.cr.aliyuncs.com/fangzuzu/python:3.12-slim`。应用镜像直接在服务器构建，不需要推送镜像仓库；已有私有仓库登录和基础镜像缓存可以沿用。首次应用构建仍需访问 Debian 软件包源安装 curl 和 CA 证书。其他环境可设置 `BACKFILL_BASE_IMAGE=python:3.12-slim` 后运行 Compose。应用没有额外 pip 依赖。

容器内绑定 0.0.0.0，宿主默认发布 `127.0.0.1:8790`；应用目录的 `./data` 挂载为容器 `/data`，`collector.db`、raw 响应、队列和登录令牌都保存在这里。Cookie 路径默认 `/`，直接打开 `http://127.0.0.1:8790/` 即可登录，也兼容原 nginx `/collector/` 入口，无需 nginx 才能登录。已有特定子路径隔离需求时可选设置 `BACKFILL_COOKIE_PATH`。本地需要 H5 时 `BACKFILL_API_ONLY=0`；节点预设的仅 API 模式不提供网页登录。Dockerfile 的专属 ignore 只发送源码和本应用，不发送生产数据、历史 raw、凭据或 Git 目录。

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

节点不需要手写 `.env`、填写自身 IP、配置域名或 nginx。节点专用
[compose.node.yml](compose.node.yml) 已准备好运行模式：默认监听宿主机端口
8790，只开放鉴权 API 和健康检查，关闭节点自身的后台汇总。访问密钥首启
自动生成并保存；股票、日期、频率和开始/暂停全部由主控配置。

在节点的 `apps/http_backfill` 目录，**已有 v1–v4 数据先执行**：

```bash
git pull && bash migrate-storage.sh --node
```

这条命令迁移后会以节点模式启动。**全新空目录或已有 v5 单库**，启动和
以后更新使用：

```bash
git pull && docker compose -f compose.node.yml up -d --build
```

首次登记时取出自动生成的密钥：

```bash
docker compose -f compose.node.yml exec -T collector-console cat /data/console.token
```

主控面板登记节点的 IP、端口 `8790` 和这个密钥即可；节点不用再登记主控，
也不用配置任何采集任务。默认端口监听宿主网络接口，安全组/防火墙允许主控
访问该端口。不同节点各用自己的 `data/`，不要复制其他节点的数据目录。

已有 v1–v4 数据的节点先执行 `git pull && bash migrate-storage.sh --node`，完成
单库迁移并以节点模式启动；已有 v5 数据直接用上述更新命令。两份 Compose
使用同一应用目录、服务名和 `./data:/data`，切换节点模式保留现有数据、令牌
和实例 UUID。全新节点保持暂停，登记不会自动开始来源请求。

仅在现有部署要求特定监听 IP 或宿主端口时，才可选使用
`BACKFILL_BIND_ADDRESS` / `BACKFILL_PORT` 覆盖默认值；正常节点无需这些配置。
若此前已经创建过含这两个变量的 `.env`，节点模式继续尊重已有值。

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
