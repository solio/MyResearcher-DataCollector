# 闲时 HTTP 回补控制台

这是独立的研究采集应用：不依赖 GUI，按你设定的请求间隔采集，手机网页查看状态、暂停与手动探测。本机采集数据、任务、请求与恢复台账统一保存在 `apps/http_backfill/data/collector.db`；原有 `posts` 查询结构沿用浏览器的 `SimplePostStore`，raw 响应继续留存。采集 worker 和主控同步继续使用各自的运行目录；需要合入现有训练数据时，由你单独执行下方的导入命令，默认目标是**仓库根目录 `data/collector.db`**。当前阶段不承诺来源长期放行，更不承诺拥有二十年历史。

## 本地启动

需要 Python 3.11+；urllib 模式没有额外依赖，curl 模式需要系统 curl。

```bash
cd MyResearcher-DataCollector
python3 -B apps/http_backfill/server.py --host 127.0.0.1 --port 8790
```

打开 `http://127.0.0.1:8790/`。首启创建 `apps/http_backfill/data/console.token`，文件权限为 0600；复制其中的令牌到登录框。令牌不进入浏览器 localStorage、代码仓库或服务日志。也可以通过 `BACKFILL_TOKEN` 环境变量配置至少 24 字符的随机令牌。

创建任务时指定股票、上海时区起止日期、请求间隔和客户端。默认不启动；检查配置后点击“开始”。所有列表、详情和重定向共用你设定的请求间隔：请求完成后等待该秒数再请求下一项，支持非负整数或小数；0 表示不额外等待，仍逐项串行。60 仅是未填写时的默认值，没有 60 秒下限。控制台每几秒刷新一次不算来源请求。列表与所需详情交替处理，详情规则沿用已有采集器：去掉标题首尾空白后长度 ≥40 才排入正文队列。短标题帖子仍保留列表文本，不丢帖子，也不把标题当作已核实正文。

## 每股独立控制与 Mihomo 出口

“股票覆盖”中每股右上角都有 **采集、暂停、探测**。状态、原阻断目标、
冷却、网络退避和出口按股票分别保存：A 股被验证码拦截时，B 股仍可采集；
A 股暂停或等待网络重试也不会暂停 B 股。节点仍一次只发送一个来源请求，
所有股票、详情、定位、校准和探测共用原来的全局请求间隔；逐股独立不增加
并发或请求速率。数据库、raw 写入和同步耐久性错误仍保护暂停整个节点。

“采集”只继续可运行的本股；本股仍有来源阻断时，先“探测”原失败目标。
探测只安排一次请求，成功后仍暂停，再手动采集。原目标和原冷却保留，
换代理不会消除阻断。上方“批量采集／批量暂停”是便捷操作，批量采集会
保留其他股票的阻断；有多个可探测目标时按卡片分别探测。重启后原活动股票
仍暂停，已知阻断保留到对应卡片；移除或归档的阻断股票仍显示原任务探测
入口，不复活旧队列。旧节点不支持专用逐股接口时，卡片按钮禁用并提示升级，
不会把股票参数发给旧全局接口。原目标无法定位的旧阻断保持显式节点保护
暂停，需要恢复原任务及请求证据，不能猜测目标继续。

保留的原任务卡片也可更换出口，只用于原失败目标单次探测，不恢复旧采集队列；
Mihomo 分流下载同时包含当前任务及仍未解除的原任务监听。

展开卡片的“本股出口”，可选择继承节点默认、直连、已有 HTTP/mixed 代理
或 Mihomo。保存会只暂停本股并等待本股正在执行的请求结束，其他股票继续
调度；保存后本股仍暂停。继承蚂蚁／青果动态代理时，使用同一节点的供应商
提取额度，不为每股复制每日上限。HTTP 用户名和密码只写入节点私密配置，
页面不回显，留空保留；明确清除才删除旧认证。

Mihomo 每股使用 **不同的 HTTP 监听端口**，并将该监听的 `proxy` 绑定到
一个具体出站节点。可以在 Mihomo GLOBAL 模式下使用，不需要切换日常
GLOBAL 选择或原来的 mixed 端口。配置步骤：

1. 每股选择 Mihomo，填写该采集节点可达的专用端点，例如
   Mac Docker 的 `http://host.docker.internal:17890`，第二股用 `17891`；
   原生 Python 可用 `http://127.0.0.1:17890`。日常 mixed 端口以软件配置
   为准，本机已观察为 `7897`，这里使用额外端口。
2. 填写 Mihomo 配置中具体节点的完整名称。若采集器在 Docker 中，监听
   地址需接受容器连接，通常填 `0.0.0.0`；同机原生程序可填 `127.0.0.1`。
   远端服务器应填其可访问的代理主机地址，`host.docker.internal` 只指向
   该容器自己的宿主机，不指向控制台所在 Mac。Linux Docker 仍需原部署
   文档里的宿主地址映射。保存本股出口。
3. 点击“下载 Mihomo 采集分流配置”。新节点优先下载私密的
   `collector-mihomo-extension.js`。在代理主机的 Clash Verge 中打开
   **订阅 → 全局扩展脚本**，若原脚本只是空的 `main`，可粘贴下载全文并
   保存；已有自定义逻辑时，将下载 `main` 中的采集监听处理合并到原
   `main` 的 `return config` 之前，保留原逻辑，不覆盖原脚本。扩展脚本
   修改最终配置的 `listeners`，只替换或追加确切名称的采集监听，保留
   其他监听、日常 `mode`、mixed 端口及规则；订阅更新后仍会应用。
   使用入口与执行方式见官方[扩展教程](https://www.clashverge.dev/guide/extend.html)
   和[全局扩展脚本说明](https://www.clashverge.dev/guide/script.html)。
   旧节点仅返回 YAML 时会下载 `collector-mihomo-listeners.yaml`，需将其
   `listeners` 条目合并到代理配置后加载；已有顶层 `listeners` 时合并
   条目，不另建重复键。下载文件包含各股生成的入口认证，私密保存，
   不进入 Git、任务导出或训练数据。
4. 在对应股票卡片点击“探测”，根据实际来源响应确认监听可用；成功后
   手动“采集”。生成／保存／下载配置本身不会请求股吧，也不证明监听已
   加载。卡片显示各股出口最近来源成功、候选及实际状态；节点名称不同
   仍不能证明公网出口 IP 不同，`exit_identity` 未实际核实时保留 unknown。

本应用不自动修改 Mihomo 的日常配置、规则、模式或系统代理。远端节点的
路由设置和私密下载均经当前主控转发，不需要节点前端、域名或 nginx。
节点私密路由保存在运行目录 `task-proxy.json`，认证权限为 0600。

## 请求 UA 与 Referer

本版 `chrome-referer.v1` 请求策略让 curl 和 urllib 共用固定桌面 Linux Chrome 154 UA：

```text
Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36
```

- 列表第一页使用 `https://www.google.com/` 作为 Referer。
- 列表第 N 页（N > 1）使用同一股票第 N−1 页的完整列表 URL。
- 详情任务首次请求独立随机选择：60% 使用最近一次实际包含该帖子的已核实
  列表页，30% 使用 `https://www.google.com/`，10% 使用 `https://www.baidu.com/`。
  优先同一任务/股票的新观察，找不到时沿用该帖的原列表证据，再回退到任务
  保存的列表页码。暂停后校准得到的新观察可更新首次详情请求的关联页。
- 同一任务的手动重试与显式重定向沿用首次选定的 Referer。升级前已经失败的
  任务，下次手动探测使用新策略，并保留之前请求的历史记录。

搜索来源使用根地址，是按浏览器默认 `strict-origin-when-cross-origin` 的
跨站规则模拟来源站点。无需关键词池，也不会向 Google/百度额外发起搜索
请求。此策略未观测两家搜索服务当前页面的实际策略，不能证明真的来自搜索。
参考 [MDN Referrer-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy)。

在“运行记录 → 请求”展开“请求特征”，可查看该次请求的 UA、Referer、选择
来源和关联列表请求 ID；阻断证据与新导出版本也保留这些字段。旧请求不补造
请求头。本次没有改变数据库表字段，无需另做数据迁移。

采集节点更新后新请求才会应用此策略；只更新主控页面不能改变远端采集器的
请求头。所有实例统一在 `apps/http_backfill` 下执行
`git pull && docker compose up -d --build`。
重启后按既有规则暂停，在控制台继续；已有阻断时先按原流程单次探测。
Chrome UA 与来源头是否能改善长时间采集，需要之后的真实运行记录判断。

## 动态代理与现有本机代理

在上方选好“当前控制节点”，展开“出站代理”，配置就保存在该采集节点，
无需再去节点改环境变量。默认为直连，可选通用 HTTP/mixed 代理、蚂蚁或青果动态
IP；curl 与 urllib 都支持。代理仅用于股吧来源请求，主控连接节点、登录、
健康检查、同步和导出仍走原来的连接。股票、日期、列表/详情队列及原有
限速共用原任务；切换出口不会清除验证码证据或 `Retry-After`。

蚂蚁动态 IP 使用你在供应商后台生成的 **HTTP 提取 API URL**，需要选
每次 1 个（`num=1`）、JSON（`type=2`）、HTTP（`mode=1`）。地址中的密钥
由节点私密保存，页面不回显。可从[供应商提取接口生成器](https://daili.mayihttp.com/get-api)
复制账户实际生成的地址。若返回每个候选的 `user/pass`，使用该候选认证；
未返回时才使用面板中显式填写的代理用户名与密码。程序读取返回的 `expire_time`，复用当前租约
并在剩余时间不足以完成请求时更换；也可按使用秒数或来源请求次数提前
轮换，填 0 表示关闭该条件。不会默认每次抓一页就购买新 IP。
“每日提取上限”必须填写正整数；提取失败、重复候选也会消耗这个上限，
按上海日期计数并跨重启保留。新 IP 在来源验证通过前显示为未验证。

青果选择 **“青果动态 IP”**，填写后台生成的短效代理新接口完整 URL，例如：

```text
https://share.proxy.qg.net/get?key=YOUR_KEY&num=1
```

也支持国内合并业务的 `https://share.proxy.qg.net/aggregate/get`。
`num` 可省略（官方默认 1）或填 1，
默认返回 JSON，不需要蚂蚁的 `type/mode` 参数。程序连接响应的 `server`，
`proxy_ip` 仅作为供应商报告的出口 IP；`deadline` 是实际到期时间，按本集成的
北京时间约定处理。官方文档未明确这个无时区字符串的时区，实际账户租约仍需
运行核实。接口和字段见[短效提取说明](https://www.qg.net/doc/2255.html)、
[响应字段定义](https://www.qg.net/doc/1839.html)。旧版 `proxy.qg.net/allocate`、
长效和海外产品不使用本模式；海外短效与长效共用提取 URL/响应结构，无法
单靠地址区分，而长效的 `deadline` 含义不同，见
[全球长效说明](https://www.qg.net/doc/6637.html)。本模式明确限定国内短效。

青果使用账号密码代理授权时，将后台 **Authkey / Authpwd** 分别填入现有
“代理用户名 / 密码”；使用供应商侧节点出口 IP 白名单时，两项留空，有旧
认证需勾选明确移除。提取 API 自己的 `pwd` 参数保留在完整 URL 内，它与
代理密码分开，程序不会互相代填。见[代理授权](https://www.qg.net/doc/1574.html)
和[提取 API 鉴权](https://www.qg.net/doc/2283.html)。切换蚂蚁与青果时必须输入
新供应商 API；同供应商空值保留既有私密设置。两家共用原本节点的每日提取
台账，切换供应商不会清零已用次数，避免切换配置绕过上限。

**动态代理提取上限耗尽或取不到可用 IP 时，自动退回所选采集节点原来的直连出口。**
不用另开开关，也不会把已保存的动态代理配置改掉。本节点上限耗尽后当日不再提取，
下一北京时间自然日才再尝试；临时提取失败、无 IP、返回冷却中的重复 IP 或
有效期不足时，至少等 5 分钟（或更长的恢复冷却）才再次提取，期间按原间隔
直连采集。认证/响应格式错误也先直连，但需修正并保存配置或手动更换后才
再次提取。每天上限限制的是本节点的提取调用，失败也计数；供应商自己的
额度限制若返回提取失败，也按无可用 IP 处理，不从消息文本猜额度或余额。
青果 `EXTRACT_LIMIT_EXCEEDED` 随套餐可能表示分钟或每日限制，按临时失败
冷却处理，不直接假定必须等次日；明确 `BALANCE_INSUFFICIENT` 则先直连，
暂停自动提取，充值后保存配置或手动更换以重新提取。

控制台显示“动态 IP · 已回退直连”、回退原因及下次提取时间；运行记录同时
保留已配置供应商和该次实际直连的信息。curl/urllib 明确绕过环境 HTTP 代理，
这里的直连是程序使用该节点原来的出站网络；应用不会修改宿主机的 TUN、
VPN 或网络路由。已通过代理发出来源请求或 CONNECT 的同一轮不会再直连重试。
回退直连一旦遇到验证码、403/429，仍暂停，自动恢复也不会继续撞同一出口；
需人工处理后安排单次探测。节点私密存储失败或配置竞态仍停止发送。

蚂蚁示例 URL 若是 `num=2`，在填入“动态 IP 提取 API”前改为 `num=1`；
`time`、`package_type`、`auth` 等账户生成的参数保留原值，不需要把 IP 有效期
另外填入面板。“按使用时间更换”和“按来源尝试次数更换”都可填 `0`，正常
到期更换仍生效。成功 JSON 可以只有 `success: true` 和单项 `data`，不要求
存在 `code`；若存在，必须是整数 `200`。有候选 `user/pass` 时面板认证字段
可留空，不要把供应商展示的示例 IP、示例密码或示例到期时间填成固定代理。

默认遇到阻断仍暂停。可开启“自动恢复”，自行填写冷却秒数（至少 60）
和最大连续恢复尝试次数（正整数）。验证码、来源 403/429 或可恢复代理
连接故障后，等原冷却与所设冷却都结束，再换候选、单次探测原目标；
有效响应才继续原任务并做原有页码校准。来源阻断的候选在冷却期排除，
供应商若再次返回它，改走上述直连回退，不会在同一次请求里反复提取。
连续恢复次数耗尽、直连出口来源阻断或存储错误需人工处理，不会到午夜或
重启就偷偷继续。提取额度耗尽本身不再暂停采集。成功来源验证重置连续恢复计数。

复用已有 Clash 或其他软件，只需选择 HTTP/mixed 并填写该节点能访问的
代理地址。**macOS Docker Desktop 本机**通常填
`http://host.docker.internal:7890`，直接在 macOS 跑 Python 通常填
`http://127.0.0.1:7890`；端口以实际软件配置为准。Linux Docker 的
`127.0.0.1` 是容器本身，需要可达的宿主地址；使用 `host.docker.internal`
时在原 `compose.yml` 的该服务里加 `extra_hosts: ["host.docker.internal:host-gateway"]`，
并确保宿主代理接受该接口连接。不支持 SOCKS-only 端口。
软件以后更换时改地址即可。应用不改系统代理、TUN 或软件选节点规则；
Clash 规则如果选择 DIRECT，经过本地代理端口仍可能是本机出口。

保存前会暂停并等待当前请求结束，保存后保持暂停；填写过的密码或 API
URL 留空会保留，只有勾选明确清除才删除。保存本身不请求供应商或股吧。
无阻断时点“开始/继续”生效；有阻断时点“更换出口 / 单次探测”，探测
通过后再继续。运行中“更换出口”只影响后续请求；可随时先暂停并改回
直连，仍要遵守原冷却。人工暂停、手动探测和重启都会撤销自动恢复许可。

私密配置保存为节点运行目录的 `proxy.json`（Docker 内 `/data/proxy.json`，
权限 0600），不进入任务、帖子导出或训练库。运行记录保留该次的模式、
路由 ID、候选地址与实际结果；供应商提取和代理认证失败不会当作股吧封禁。
代理无法保证免验证码，是否稳定仍由后续真实采集台账判断。

主控和**需要使用代理的采集节点**都要更新，命令仍是：

```bash
cd MyResearcher-DataCollector/apps/http_backfill
git pull && docker compose up -d --build
```

无需新增部署文件、改 nginx 或数据迁移；原基础镜像和 apt 安装层未变。
重启按原规则保留阻断或暂停，在控制台继续。旧采集节点没有代理能力时，
面板会提示先更新；只更新主控不会改变远端请求出口。

## 配置编辑与任务管理

暂停并等待当前请求保存后，直接修改表单并点击“保存修改”。运行时先点“暂停后编辑”；页面会暂停本节点各股并等待当前请求结束。可以修改股票、日期范围、请求间隔和客户端，保存后保持暂停。仅修改间隔或客户端保留原队列；增删股票保留其他股票进度；修改日期范围则从已留存的列表证据重新计算范围，复用已完成正文，并重新定位或校准相应股票的列表入口。卡片只修改本股出口时无需暂停其他股票。

每个股票卡片可“移除”，也可“删除当前任务”。删除是归档并取消待采队列，保留帖子、raw、请求台账和配置版本；移除最后一只股票会归档当前任务。历史任务在页面下方查看。这些操作不清除本股阻断或冷却时间：即使任务已删除，仍保留原任务卡片用于对原失败目标单次探测，不能通过重新创建同股任务绕过原阻断。其他股票可独立运行。

## 阻断与恢复

- 确认的验证码、身份核实、认证拒绝或限流会阻断对应股票并保留原始响应/失败事实；来源解析异常保护暂停对应股票，数据库／raw 等本地存储异常保护暂停整个节点。
- TLS 错误、超时等暂时网络异常不进入来源阻断。原目标保留为待处理，自动退避重试，退避期间仍可正常暂停；手动暂停后不会自动恢复。控制台显示“等待网络重试”、连续失败次数、失败原因和下次请求时间，失败不会计作无数据或已完成。每次重试仍受原全局请求间隔约束，不增加并发或立即切换出口重复请求。
- 存在普通验证码脚本资源不等于受拦截；收到真实 payload 也不能掩盖同时出现的挑战信号。
- “重试一次”仅排队一次真实来源探测，仍受全局间隔与 `Retry-After` 约束。探测成功后仍暂停，由用户决定是否继续。失败不会进入循环重试，也不清空旧阻断记录。
- 进程异常中断时，不把未确认请求当成功；保留断点和未知结果。升级时仅将可明确识别的旧传输异常停机标记转为待重试，仍保持暂停；验证码、身份核实等既有来源阻断及无法安全继续的异常不会因重启自动清除。
- HTTP 不执行页面 JavaScript，动态生成的滑块不可直接观测。报告区分“收到的响应无明确挑战且数据有效”与“永久无验证码”；仅靠原始 HTTP 不能确定服务端黑名单的粒度或原因。

## 数据与范围

“隔离数据产出”标题行同时显示采集数据库状态、路径、最近同步与当前节点
CSV/JSONL 导出。点击数据库可展开完整路径、同步日期、格式和存储说明；
写入异常会直接显示。手机端按宽度换行，导出仍包含所选节点全部留存任务。

一条源帖子在 `posts` 中始终是一行，键为 `(source, source_item_id)`。列表采集
保存这行的 `title`，详情补充更新同一行的 `content`，保留标题和帖子身份。
“已补详情”是已采帖子的子集，补充详情不会增加帖子总数。例如已采帖子 293、
其中已补详情 52、未触发补详情 241、待补详情 0，表示 293 个帖子均保留列表
记录，其中 52 个还取得了正文；241 不是标题总数。未触发只是当前标题长度规则
的结果，不能据此断言来源没有正文或短标题已完整。

任务先请求一次新首页作为当前时间参考，再利用已有历史位置提示、倍增探测和二分定位查找结束日期所在的列表区间；首页已在目标日期范围内时直接复用该次响应，不重复请求首页。定位后从相邻前一页进入顺序回补，逐页采集到开始日期边界；定位探测不会把跳过的页面当成已采集，也不证明目标窗口完整。控制台逐股显示目标结束日期、探测次数、当前源页码和定位后的采集入口，运行记录把这类请求标为“日期定位列表”。日期定位、顺序列表、正文和校准共享你设定的全局间隔；验证码或认证拒绝仍阻断，暂时网络异常仍按退避策略重试。

顺序回补的每页列表保留日期范围内的普通帖子，仅给列表标题长度 ≥40 的帖子建立正文队列，优先获取所需详情，再推进下一页。这里的列表回补与正文 enrich 在同一个长任务中交替进行，详情不会等整个历史列表抓完才开始。源 ID 去重，保留跨股票关联；原始空正文单独计数，删除与失败分别记录。

这条 40 字规则是既有的疑似列表截断补正文策略，不能证明短标题就是完整正文。API 与台账明确区分 `list_title` 和 `detail_body`；控制台显示已采帖子总数，以及其中已补详情、待补详情和未触发补详情的帖子数。升级前已经取得的短标题正文会保留；旧版尚未执行的短标题详情队列会转为列表文本记录，已有阻断目标仍保留一次探测入口。

暂停恢复或进程重启时，未完成的校准从已保存的当前页继续，保留同一轮次、已经核对的页面和两轮比较记录，不回到旧起点。TLS／超时重试也保留原请求和退避时间，不被新校准替换。普通前进暂停后，先单次重读最后成功采集的那一页：例如最后完成 141，核对 141 未变后继续 142。只有末页变化或缺少可靠基线时才转为区间两轮校准。详情处理等造成超过正常配置间隔的额外五分钟列表延迟、已观察到分页漂移、定期检查或到达日期／来源尾页边界也会触发核对；正常配置等待不计作额外延迟。明确非置顶普通帖的完整有序源 ID 与发布时间完全一致、时间降序有效且已知来源计数未下降时，直接继续该物理页码的下一页。这只确认导航锚点稳定，不把它记作两轮区间核验或完整覆盖；已经发现漂移、来源计数下降、锚点不明确或到达日期／来源尾页边界时仍做区间校准。

区间校准保留源帖子 ID 与发布时间锚点，重新定位已观察的区间、补入新发现的详情，比较连续两轮区间观察；一致后才按重新定位的页码继续。来源插入或删除帖子会移动页码，只有响应显示锚点移位、或开始必要的第二轮扫描时才会往较小页码走；暂停按钮本身不会重置校准。每次校准仍受原全局来源请求间隔约束。每轮恢复累计最多 64 次有效校准响应、6 轮完整扫描，用量在暂停、重启和策略回退后保留；预算耗尽仍未完成时显示明确缺口／异常并停止，不无限回扫。控制台分别展示触发原因、末页单次检查或区间两轮方式、响应与完整扫描预算用量，以及回退原因。旧节点缺少预算字段时不推测用量。锚点缺失、时间顺序未知或不能安全定位时仍显示缺口或暂停；部分非普通帖子不按发布时间排列，不能拿这些行的最早时间推断历史位置。

校准预算耗尽后的有效响应及帖子仍保留，停止的是后续导航。人工单次探测成功后，会记录原用量、保留缺口，并允许随后手动继续时开始新一轮有界校准；验证码或传输失败不能续预算，也不会自动继续采集。

用户指定历史日期边界后，应用显示列表覆盖与正文完成度；来源已到尽头但还没达到日期边界，会显示覆盖不足。两轮局部观察一致只证明该次锚点区间核对结果，不能证明整段历史完整，更无法发现从未观察到且已永久删除的帖子。采集与汇总状态中的 `coverage_complete` 和 `model_database_eligible` 始终为 false；人工导入训练库也不把这些标记改为完整或已通过模型数据审查。

股票卡片分别显示“目标窗口”和“窗口内帖子”的发布时间范围；帖子及正文子项按当前配置窗口统计，不包含修改配置后已在窗口外的旧记录。“已发现窗口内帖子”包含定位采样和顺序采集发现的帖子，不能据此声称两端之间连续覆盖。定位／列表观察的时间范围另行保留，可能包括首页或旧前缀中的窗口外日期，标明不代表采集覆盖；顺序列表页面也可能跨越窗口边界。旧采集节点未提供这些明确口径时，控制台只标注旧节点列表观察与旧统计，不将其宣称为窗口内范围。

历史记录不在当前窗口或发布时间无法核实时，卡片显示从本任务统计排除的数量。这不删除已有记录；“导出当前节点帖子”仍沿用该节点全部留存任务的导出范围，不随当前任务统计窗口缩小。

`data/` 包含本机唯一运行数据库 `collector.db`、请求对应的 `raw/` 响应和控制台令牌 `console.token`。`collector.db` 的 `posts` 保持原 `SimplePostStore` 字段，其他表保存任务、请求、恢复、来源与导出版本；当前核实正文只写 `posts.content`，工作状态不另存一份正文。原来的 `posts` SQL、源 ID、股票代码、发布时间和 enrich 候选规则可以沿用。未取得详情的 `posts.content` 是 NULL；已取得的真实空正文是空字符串。正文来源、跨股票关联和范围缺口保留在同一库的台账/API 中，不会凭局部数据写入完整回补覆盖。raw 和不可变导出版本保留历史证据，因此历史正文版本仍可能出现于证据中。

旧版升级必须执行下方的一次性迁移命令，完整校验后自动删除运行目录中的 `experiment.sqlite3` 及其 WAL/SHM。迁移保留断点与来源阻断，自动生成旧库备份，不请求来源、不移动 raw；重复执行安全。`api/posts` 是带原始来源/范围状态的研究查询；写入仓库根目录训练库使用下方独立的 `training_import.py`，不由查询接口自动执行。实例拥有持久化 `instance_id`，状态、请求和帖子查询携带该 ID，供后续汇总追踪来源使用。

本版支持一个中央控制台管理本机和多台远程实例，并在后台增量合并数据。每台实例保留自己的 SQLite、队列、请求间隔和阻断记录；中央服务通过鉴权 API 控制任务和同步证据，不直接拼接数据库文件。各实例的股票和时间窗口在同一控制台分别配置。

## 合入仓库根目录的训练数据库

这里的合并目标是 `MyResearcher-DataCollector/data/collector.db`，即已有的训练数据库。
`apps/http_backfill/data/collector.db` 是采集器运行库，
`apps/http_backfill/data/fleet/collector.db` 是主控的可选汇总库；
它们都不会由后台 worker 或同步程序自动写入根目录训练库。
使用独立的 [training_import.py](training_import.py) 人工导入，直接保留原 `posts`
表的 15 个字段和 `(source, source_item_id)` 主键，不需要先配置 fleet 汇总。
目标必须是已存在、具有原 `posts` 结构的训练数据库。

已经用唯一主控同步各节点时，最便捷的路径是：在控制台确认同步完成，下载
“合并帖子 JSONL”，然后用本节 `--jsonl` 命令一次合入训练库。这份主控汇总
文件已经包含各已同步节点的去重帖子，无需逐节点下载；它保留文件出处，
不包含 raw。需要帖子关联的完整原始证据时，使用节点 ZIP。主控汇总是可选
的中间步骤，最终人工导入目标仍是根目录训练库。

### 本机已采数据直接导入

在 **`MyResearcher-DataCollector` 仓库根目录**执行。先预览，再决定正式导入：

```bash
python3 apps/http_backfill/training_import.py \
  --source-dir apps/http_backfill/data --dry-run
python3 apps/http_backfill/training_import.py \
  --source-dir apps/http_backfill/data
```

`--source-dir` 只读取得节点的一致快照和相关证据，不启动采集、不请求股吧，
不修改节点任务或来源阻断。默认目标由脚本定位到仓库根目录 `data/collector.db`。
`--dry-run` 只预览新增帖子、补充字段/正文、冲突和证据数量，不修改目标库，
也不创建训练库备份。输入中未能导出的帖子会显示 `unexported_source_posts`，
不能把证据包所含记录数当成原采集库的全部帖子数。

### 远端节点导出 ZIP，再传回本地导入

推荐 ZIP 证据包，包内保留帖子版本、相关 raw 响应和哈希清单。先在**远端节点
的 `MyResearcher-DataCollector/apps/http_backfill` 目录**执行：

```bash
docker compose exec -T collector-console python apps/http_backfill/transfer.py \
  export --data-dir /data --output /data/exports/node-a-20261002.zip
```

Compose 命令从宿主机应用目录执行，但**容器工作目录是 `/opt/collector`**，
所以容器内脚本路径必须保留 `apps/http_backfill/transfer.py`。
导出文件位于远端宿主机的
`MyResearcher-DataCollector/apps/http_backfill/data/exports/node-a-20261002.zip`。
同名文件已存在时另取文件名；导出不会重启或启停采集器。

然后在**本地仓库根目录**传回文件并导入；把示例登录用户、节点 IP 和远端
仓库绝对路径替换为实际值：

```bash
mkdir -p runtime/imports
scp 'user@10.0.0.12:/path/to/MyResearcher-DataCollector/apps/http_backfill/data/exports/node-a-20261002.zip' runtime/imports/
python3 apps/http_backfill/training_import.py \
  --bundle runtime/imports/node-a-20261002.zip --dry-run
python3 apps/http_backfill/training_import.py \
  --bundle runtime/imports/node-a-20261002.zip
```

其他节点的包按相同步骤导入同一训练库即可；重复导入相同输入是幂等操作。
不需要先把包导入 `fleet/`，也不需要停远端采集进程。

### 已从 H5 下载 JSONL

在唯一主控的“中央合并数据”下载“导出合并帖子 JSONL”，把文件放到本地
`runtime/imports/`。在仓库根目录执行一次命令，即可导入已同步的各节点帖子；
下面的 `posts.jsonl` 替换为实际下载文件名：

```bash
python3 apps/http_backfill/training_import.py \
  --jsonl runtime/imports/posts.jsonl --dry-run
python3 apps/http_backfill/training_import.py \
  --jsonl runtime/imports/posts.jsonl
```

JSONL 保留原文件作为输入来源，并保留 NULL 与真实空正文的区别；它没有
帖子关联的 raw 响应，导入记录会明确标记 `linked_raw_supplied=false`。
从“导出当前节点帖子 JSONL”下载的文件也可用同一命令导入，它只含点击时
选中的采集节点的留存帖子；选择本机则导出主控本机，选择远端则导出该远端。
需要 raw 追溯时使用前面的 ZIP。CSV 用于查看数据，这个导入器接收 ZIP、
节点数据目录或 JSONL。三个输入选项每次只使用一个。
需要指定其他已有训练库时，在任一命令追加
`--target-db /absolute/path/to/collector.db`。

### 写入规则、备份和导入记录

整个输入校验通过后，正式写入前自动使用 SQLite 备份接口保存目标库，包含
已提交的 WAL 数据；默认备份位于 `data/collector.db.import-backups/`。
原始输入保存在 `data/collector.db.imports/inputs/`，ZIP 中的关联 raw 证据
随原包保留。命令输出包含目标路径、备份路径、输入路径、导入时间和计数；
目标库中的 `collector_import_runs`、`collector_import_versions`、
`collector_import_conflicts` 分别保存导入记录、输入版本和冲突事实。
这三个新增表用于溯源，当前帖子仍只有原来的 `posts` 表，没有第二套帖子表。
使用 `--target-db` 时，备份与输入证据目录位于指定目标库旁，文件分别按导入
记录和输入 SHA-256 命名；相同输入重复导入且没有改动时，不创建新备份。

新源 ID 插入新行；已有源 ID 只补充 NULL 字段和缺失正文，保留原来非 NULL
值。已采到的真实空正文是空字符串，不能当作缺正文。标题、作者名称、股票、
发布时间、URL 或作者 ID 等身份字段的非 NULL 值不一致时，保留目标库旧值，
阻止该帖子补字段或正文，并记录双方事实供后续核对。单独正文内容冲突时，
保留旧正文并记录双方内容，不用新正文覆盖它。
原帖子的采集/来源时间保留，人工导入时间单独记录。

导入只合入帖子和导入来源，不复制 HTTP 任务、运行队列或来源阻断，不修改
浏览器采集的 `backfill_resume`、`backfill_page_anchors`、`backfill_coverage`。
部分历史数据进入训练库不会被标成整段日期覆盖完整。

## 多实例统一控制与可选主控汇总

下面的 fleet 功能用于手机集中管理和主控汇总，输出位于应用运行目录。
要把已有采集数据合入仓库根目录训练库，使用上一节的人工导入命令。

现有服务器可以同时作为采集实例和中央服务，无需迁移原任务。手机只打开中央服务器的 `/collector/`。在“采集实例”区域登记其他服务器的实例别名、名称、**IP、端口和访问密钥**，协议默认 HTTP；当前支持本机加最多 16 台远程实例。例如填 IP `10.0.0.12`、端口 `8790`，中央服务直接访问该机的鉴权 API。采集节点不需要域名、nginx 或单独供手机访问的前端。

填写中央服务器能够访问的 IP，可以用同一 VPC/可路由内网的私有地址。安全组/防火墙只需放行中央服务器到采集节点这个 TCP 端口；手机不需要访问节点端口。登记表单的高级“服务 URL”方式保留已有 HTTPS/nginx 路径入口，例如 `https://<已有域名>/collector/`，原登记无需改写。新节点部署命令见下方“只运行采集 API 的节点”。

v5 中央服务支持 v4/v5 采集实例逐台升级；先升级中央服务，再升级其他节点。要删除某节点的旧任务库，在该节点执行迁移命令。登记时中央服务先验证版本和持久化 UUID，同一个实例不能重复登记；复制原 `data/` 会复制实例身份，不能当作新的独立采集机。登记已有独立运行实例可保留它的数据与任务；全新采集机使用自己的空 `data/`。修改登记时访问密钥留空会保留旧密钥；更换密钥后重新验证连接。密钥只存中央服务的私有 `data/fleet/registry.json`（0600），不返回浏览器、不写浏览器本地存储。

选择实例后，原来的任务配置、暂停、继续、单次探测、股票删除、任务归档和运行记录都作用于选中的实例。卡片显示各机状态、阻断原因、最近连接时间和同步进度；断联保留上次状态并显示连接错误。远端鉴权失败不会退出中央网页登录。移除登记停止后续控制和同步，已合并数据保留，远端正在执行的任务不会因移除登记被自动停止。

后台默认每 60 秒检查新增记录；有积压时分批接续同步，也可以点击“立即同步”。同步只传输采集机已有记录及其原始响应，不发起新的股吧请求。每台实例有独立的不可变导出序列与持久化同步游标，断联或重启后继续传输；哈希或原始响应验证失败会显示同步错误并保留游标，不把失败当成已同步。

中央服务的数据文件如下，表内 `data/` 均指应用的 `apps/http_backfill/data/`
（容器内 `/data`），不是仓库根目录训练数据目录：

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

## 可选的主控汇总与网页导出

这一节操作的是主控的 fleet 汇总库。下载得到的 JSONL 可再通过前面的
`training_import.py --jsonl` 合入根目录训练库；网页同步本身不执行该导入。

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

网页的“导出当前节点帖子”跟随上方“当前控制实例”的选择：选本机下载主控
本机的数据，选远端下载该节点的数据。手机只连接主控，由主控使用登记的
地址与令牌转发下载，不需要先同步到中央合并库。文件名和页面提示标明点击时
选择的节点，下载中切换视图不会改变这份文件的来源。节点不可达、鉴权失败
或导出接口不存在时显示错误，不会改为下载主控数据；老节点若缺少导出接口
需升级该节点。独立的“导出合并帖子”仍下载主控已同步的中央合并库。
CSV/JSONL 导出来自一个固定数据库快照，涵盖该节点已留存的所有任务，
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

### 节点断网时导入可选 fleet 汇总库

若目的是补充根目录训练数据库，按前面的“远端节点导出 ZIP，再传回本地
导入”操作即可。下面的 `transfer.py merge` 仅用于主控 fleet 汇总。

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
校验失败会报错，重复导入幂等。这个 `transfer.py merge` 命令的目标是主控
`fleet/collector.db`，不要把它的 `--data-dir` 指向仓库根目录 `data/`。
向根目录训练库合入 ZIP 使用 `training_import.py --bundle`。
不要用复制覆盖 SQLite 文件来合并实例。
这些新工具需要新版镜像；普通更新后即可使用。导出包完整仅表示已拥有证据
保存完整，所有日期覆盖和模型可用性仍需另外核实。未关联已采帖子的一些
失败、验证码、探测请求不会进入这个包；核验全部源请求间隔应使用下面的
全量台账审计，不能用包内的关联请求替代。包报告同时列出原库帖子数
`source_posts_at_snapshot`、有可导出证据的唯一帖子数 `counts.unique_posts`
和 `unexported_source_posts`；孤立且没有完整关联证据的投影不会被编造来源
或补入包内。单纯查看所有现有帖子，可使用前面的 CSV/JSONL 导出。

## 核验设定间隔与实际速率

请求间隔针对每台实例的**源 HTTP 请求**，不是帖子数。一页列表可能带回约
80 条记录，因此 5,274 帖子可能来自几十次列表请求；详情请求、分页校准、
重定向、失败和手动探测也占同一来源请求额度。主控网页刷新与实例数据同步
不会请求股吧，不计入来源采集速率。

H5 按当前配置间隔核对选中实例的近期请求及其样本范围，支持 0 和小数。
确认整段运行，应该在
该实例上执行全量只读审计。在 `apps/http_backfill` 目录更新 checkout 后，
以下命令把脚本传入**当前正在运行的容器**，不用重建或重启 worker。
示例阈值为 60；将 `--interval` 改为希望核对的秒数：

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
| `violations.count`、`violations.items` | 低于本次审计阈值的次数，以及请求 ID、时间和间隔明细 |
| `unknown` | 未确认网络尝试、未完成、时间缺失、重叠、时钟异常；不能当作正常 |
| `config_policy`、`config_history` | 有记录支持的历史配置审计，与本次指定阈值分开 |
| `verdict` | `pass` 仅表示所读快照内的确认请求未发现低于审计下限；`fail` 已发现过快间隔；`unknown` 证据不足 |

退出码分别是 0 / 1 / 2；读取或输入错误是 3。`fail` / `unknown` 输出仍是
有效审计报告，不是容器启动失败。若刚好有进行中的请求，会记录为未完成，
可在响应结束后重跑只读命令。报告时间为带时区的 UTC，不要直接与手机的
上海时间字符串比较。

检查相邻来源请求的“前一次响应结束 → 下一次请求开始”间隔、短于指定秒数的
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

容器内绑定 0.0.0.0，宿主默认发布 `127.0.0.1:8790`；应用目录的 `./data` 挂载为容器 `/data`，`collector.db`、raw 响应、队列和登录令牌都保存在这里。Cookie 路径默认 `/`，直接打开 `http://127.0.0.1:8790/` 即可登录，也兼容原 nginx `/collector/` 入口，无需 nginx 才能登录。已有特定子路径隔离需求时可选设置 `BACKFILL_COOKIE_PATH`。H5 默认启用；已有 `.env` 中明确设置 `BACKFILL_API_ONLY=1` 时仅提供 API。Dockerfile 的专属 ignore 只发送源码和本应用，不发送生产数据、历史 raw、凭据或 Git 目录。

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

登录后创建股票和日期范围明确的任务，客户端选 curl、间隔填写你希望的秒数，再点击“开始”。关闭手机或 SSH 不影响后台采集。列表和详情共用设定间隔；遇到验证码、认证拒绝或明确限流会暂停对应股票，暂时网络异常按退避策略重试。手动“重试一次”只发一次探测，成功后仍需点击“开始”继续。

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

### 多实例使用同一部署入口

主控、本机和远端采集器都使用现有 `compose.yml`，不需要额外的节点 Compose
文件，不需要在节点登记主控地址。远端没有 nginx 或域名也可由主控通过
IP/端口访问鉴权 API。访问密钥首启自动生成并保存；股票、日期、频率和
开始/暂停全部由主控配置。节点附带的网页可以不用打开。

在节点的 `apps/http_backfill` 目录，**已有 v1–v4 数据先执行**：

```bash
git pull && bash migrate-storage.sh
```

**全新空目录或已有 v5 单库**，启动和以后更新使用原来的命令：

```bash
git pull && docker compose up -d --build
```

首次登记时取出自动生成的密钥：

```bash
docker compose exec -T collector-console cat /data/console.token
```

主控面板登记节点的 IP、端口 `8790` 和这个密钥即可；节点不用再登记主控，
也不用配置任何采集任务。安全组/防火墙允许主控访问该端口。不同实例各用
自己的 `data/`，不要复制其他实例的数据目录。统一入口仍使用同一服务名和
`./data:/data`，保留现有数据、令牌和实例 UUID。全新实例保持暂停，登记不会
自动开始来源请求。

监听地址和端口继续沿用已有 `.env` 的 `BACKFILL_BIND_ADDRESS` /
`BACKFILL_PORT`。默认 Compose 仍为 `127.0.0.1:8790`；需让远端主控通过 IP
连接的实例，应在现有 `.env` 中使用 `BACKFILL_BIND_ADDRESS=0.0.0.0` 或可路由
的本机接口 IP。此前使用旧节点预设且没有 `.env` 的机器，切换前需保留这个
监听设置；旧预设曾默认监听 `0.0.0.0`。已有 `BACKFILL_API_ONLY` 和
`BACKFILL_FLEET_SYNC_ENABLED` 设置继续被同一 Compose 支持，均不是部署的
必填项。旧迁移命令的 `--node` 参数仅作兼容，使用统一入口和已有配置。

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
