# 闲时 HTTP 回补控制台

这是独立的研究采集应用：不依赖 GUI，以每分钟最多一次来源请求起步，手机网页查看状态、暂停与手动探测。数据保存在自己的 SQLite 和 raw 目录，不写生产 `collector.db`，也不自动送入训练。当前阶段不承诺来源长期放行，更不承诺拥有二十年历史。

## 本地启动

需要 Python 3.11+；urllib 模式没有额外依赖，curl 模式需要系统 curl。

```bash
cd MyResearcher-DataCollector
python3 -B apps/http_backfill/server.py --host 127.0.0.1 --port 8790
```

打开 `http://127.0.0.1:8790/`。首启创建 `apps/http_backfill/data/console.token`，文件权限为 0600；复制其中的令牌到登录框。令牌不进入浏览器 localStorage、代码仓库或服务日志。也可以通过 `BACKFILL_TOKEN` 环境变量配置至少 24 字符的随机令牌。

创建任务时指定股票、上海时区起止日期、请求间隔和客户端。默认不启动；检查配置后点击“开始”。所有列表、详情和重定向共用一个请求间隔，最少 60 秒；控制台每几秒刷新一次不算来源请求。单页约有几十篇帖子，逐项获取正文需要相应数量的分钟；吞吐应以实际完整正文数衡量。

## 阻断与恢复

- 确认的身份核实页、403/429、未知结构、错误编码和网络异常会暂停并保留原始响应/失败事实。
- 存在普通验证码脚本资源不等于受拦截；收到真实 payload 也不能掩盖同时出现的挑战信号。
- “重试一次”仅排队一次真实来源探测，仍受全局间隔与 `Retry-After` 约束。探测成功后仍暂停，由用户决定是否继续。失败不会进入循环重试，也不清空旧阻断记录。
- 进程异常中断时，不把未确认请求当成功；保留断点和未知结果。重启应用不会自动清除 blocked/error。
- HTTP 不执行页面 JavaScript，动态生成的滑块不可直接观测。报告区分“收到的响应无明确挑战且数据有效”与“永久无验证码”；仅靠原始 HTTP 不能确定服务端黑名单的粒度或原因。

## 数据与范围

每页列表建立正文队列，处理后继续下一页。源 ID 去重，保留跨股票关联；原始空正文单独计数，删除与失败分别记录。用户指定历史日期边界后，应用显示列表覆盖与正文完成度；来源已到尽头但还没达到日期边界，会显示覆盖不足。页码推进无法证明已删除历史帖全部可得。

`data/` 包含隔离数据库、请求台账对应的 raw 响应和控制台令牌。迁移前停服务并整体备份此目录，不只复制 SQLite 主文件。`api/posts` 是带原始来源/范围状态的研究查询，不是向生产库晋升数据的接口。批次覆盖是否完整应单独检查。

## 服务器部署

沿用 labelapp 的服务器和现有 HTTPS，新增入口 `https://testapi.zuzurent.com.cn/collector/`。以下命令由服务器执行；仓库包含部署文件，尚未自动修改远程服务器。应用使用独立端口 8790 和 SQLite，不需要 labelapp 的 MySQL 配置。

先进入服务器上存放项目的目录，首次部署：

```bash
git clone git@github.com:solio/MyResearcher-DataCollector.git
cd MyResearcher-DataCollector/apps/http_backfill
docker compose -f compose.yml up -d --build
docker compose -f compose.yml ps
curl -fsS http://127.0.0.1:8790/healthz
```

若服务器已有 Collector checkout，进入该目录 `git pull --ff-only origin main`，再进入 `apps/http_backfill` 执行 Compose 命令即可。健康检查返回 `{"ok": true}`。首启处于暂停状态，不会因健康检查或网页刷新请求股吧。

基础镜像默认复用 labelapp 使用的 `fangzuzu-docker-registry-vpc.cn-guangzhou.cr.aliyuncs.com/fangzuzu/python:3.12-slim`。应用镜像直接在服务器构建，不需要推送镜像仓库；已有私有仓库登录和基础镜像缓存可以沿用。首次应用构建仍需访问 Debian 软件包源安装 curl 和 CA 证书。其他环境可设置 `BACKFILL_BASE_IMAGE=python:3.12-slim` 后运行 Compose。应用没有额外 pip 依赖。

容器内绑定 0.0.0.0，宿主仅发布 `127.0.0.1:8790`；应用目录的 `./data` 挂载为容器 `/data`，数据库 `experiment.sqlite3`、raw 响应、队列和登录令牌都保存在这里。配置使用 `/collector/` cookie path，网页登录通过 nginx 入口进行。直接本机启动不设置此前缀，使用根路径即可。Dockerfile 的专属 ignore 只发送源码和本应用，不发送生产数据、历史 raw、凭据或 Git 目录。

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
docker compose -f compose.yml exec -T collector-console cat /data/console.token
```

登录后创建股票和日期范围明确的任务，客户端选 curl、间隔选 60 秒，再点击“开始”。关闭手机或 SSH 不影响后台采集。列表和详情共用每分钟一次额度；遇到验证码、限流或异常会暂停。手动“重试一次”只发一次探测，成功后仍需点击“开始”继续。

后续更新：先在控制台暂停，等当前请求结束，再在应用目录执行：

```bash
git pull --ff-only origin main
docker compose -f compose.yml up -d --build
docker compose -f compose.yml logs --tail=30 collector-console
```

重建容器保留宿主机 `data/`；进程重启后任务安全暂停，重新登录并点击“开始”继续。若有未确认的中断请求，会显示错误并要求单次探测。备份时停止服务并整体备份 `data/`，随后启动服务；不要只复制运行中的 SQLite 主文件。后续涉及数据库结构变更的版本应先停服务并备份，代码回退也需要确认数据库格式兼容。

同样的 nginx 片段保存在 [deploy/nginx.conf.example](deploy/nginx.conf.example)。

不用 Docker 时可用 [deploy/collector-console.service](deploy/collector-console.service)，按实际路径与服务用户修改模板，准备可写的数据目录并安装系统 curl（如果选 curl 客户端）。服务和网页可重启，数据与暂停原因均持久化。

## 验证与长时间试验

```bash
cd MyResearcher-DataCollector
python3 -B -m unittest discover -s apps/http_backfill/tests -v
```

离线测试用于验证间隔、暂停、断点、原始证据和接口行为；不证明股吧长期可用。首轮真实试验应保持一个确定的客户端、60 秒间隔和固定任务配置，记录首次请求、成功请求、暂停时间及累计正文。运行一晚或更久后，以真实台账判断一分钟间隔是否足够；没有发生阻断的小段试验只能支持该观测时段。
