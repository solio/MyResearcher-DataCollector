# 本次来源脚本入口发现与初始响应独立复核

Date: 2026-09-30, Asia/Shanghai\
Role: Source Researcher\
Status: SCRIPT_REQUEST_CONSTRUCTION_CONFIRMED; API_ACCESS_NOT_TESTED_BY_THIS_SUBTASK

本文件来自对主研究者本次下载的文件作离线阅读；本子任务没有联网、运行 JavaScript、操作浏览器或读取生产数据库。全部偏移为保留响应原始 bytes 的零基偏移。

## 当前公开列表 API 的完整请求构造

来源文件：`live/006-public-list-js-hop0.body`\
来源请求 URL：`https://gbfek.dfcfw.com/deploy/fd_guba_web2022/work/list.js`\
bytes：681385\
SHA256：`3685bb1d76f5c1b1b6cf91daffeab4304f4155e300f9b565a4f45309fb11bbad`

重新计算响应 hash 与 `live/006-public-list-js-hop0.json` 的记录一致。无需凭印象构造接口：该脚本的 `ce(e,t,n)` 助手实际使用同域 `POST /api/getData`，把读取路径和参数作为表单字段送出。

byte offset 139656 的精确代码：

```javascript
i={param:r,plat:"Web",path:e,env:k.isRelease?2:n||1,origin:"",version:"2022",product:"Guba"},o="/api/getData"
```

同一函数随后用 `window.BARCODE.barCode` 和 `e` 在查询字符串追加 `code` 与 `path`。`/api/getData` 按当前来源文档的 origin 解析为 `https://guba.eastmoney.com/api/getData`；这是标准相对地址解析。脚本的运行环境判断在 byte offset 126180 开始：localhost 为开发环境，含 `test` 的 host 为测试环境，其他 host 为 release；当前 `guba.eastmoney.com` 因此对应 `env=2`。

byte offset 140558 的实际发送代码：

```javascript
o.open("POST",e),o.withCredentials=!0,o.setRequestHeader("Content-Type","application/x-www-form-urlencoded"),o.send(i)
```

发送前用 `s+"="+encodeURIComponent(t[s])` 编码各个外层表单字段。这一 helper 没有构造签名、读取并附加 Cookie、计算设备身份或添加挑战参数；`withCredentials=true` 意味着浏览器在有符合规则的 Cookie 时可能随带它们，不能推断服务端要求 Cookie，也不能推断无 Cookie 会成功。匿名可用性需由主研究者单独实测。

byte offset 212106 的读取路径和 byte offset 212205 的参数构造：

```javascript
e="webarticlelist/api/Article/Articlelist"
t="code=".concat(window.BARCODE.barCode,"&type=").concat(window.pageInfo.listType,"&p=").concat(window.pageInfo.pageNumber,"&ps=").concat(wn,"&sorttype=").concat(window.pageInfo.sortType)
```

随后实际调用 `ce(e,t)`。同脚本 byte offset 210220 明确 `gn=0,vn=0,wn=80`。

### 最新发帖参数的出处

byte offset 668719：

```javascript
var e={listType:"0",sortType:"1",pageNumber:"1"}
```

byte offset 669037：

```javascript
n.indexOf(",f")>-1?e.sortType="0"
```

路由 `_2.html` 等编号从 `/_(\d+)\./` 的匹配提取。普通列表 `list,601012,f.html` 因此解析为 `listType=0, sortType=0, pageNumber=1`；`f_2.html` 解析为相同排序、`pageNumber=2`。默认不含 `,f` 的普通列表对应 `sortType=1`。UI 代码 byte offset 159740 又把 `"0"==window.pageInfo.sortType` 的高亮标签明确写为“最新发帖”；邻接标签把 `sortType=1` 写为“最新评论”。参数语义有当前代码的双重出处。

### 具体候选请求

以下是根据上述源代码组合的可请求候选，不是本子任务已执行的 API 成功证据：

```text
POST https://guba.eastmoney.com/api/getData?code=601012&path=webarticlelist/api/Article/Articlelist
Content-Type: application/x-www-form-urlencoded

param=code=601012&type=0&p=1&ps=80&sorttype=0
plat=Web
path=webarticlelist/api/Article/Articlelist
env=2
origin=
version=2022
product=Guba
```

外层字段必须 form-urlencode；尤其 `param` 是一个完整的内层字符串，不能让它的 `&` 意外变成外层字段。示意命令（未执行）：

```bash
curl -q --silent --show-error --connect-timeout 10 --max-time 25 \
  --user-agent 'MyResearcher-HTTP-Research/20260930 (anonymous bounded public-source study)' \
  --header 'Accept: application/json' \
  --data-urlencode 'param=code=601012&type=0&p=1&ps=80&sorttype=0' \
  --data-urlencode 'plat=Web' \
  --data-urlencode 'path=webarticlelist/api/Article/Articlelist' \
  --data-urlencode 'env=2' \
  --data-urlencode 'origin=' \
  --data-urlencode 'version=2022' \
  --data-urlencode 'product=Guba' \
  'https://guba.eastmoney.com/api/getData?code=601012&path=webarticlelist/api/Article/Articlelist'
```

线上若执行，仍需使用本 run 的预算、间隔、源站范围和挑战即停规则，保存每次原始响应，不把 HTTP 200 或 JSON 可解析当作数据验证通过。

### 其他列表分支

当前同脚本 byte offset 313525 另有 `webarticlelist/api/article/WebArticleList`；其调用参数为 `code`, `type=0`, `sorttype`, `p`, `ps`，附近组件将 `S=40`。此外有 Intelligence、Hot、Search 分支。它们属于不同 UI 分支，响应可能有嵌套 `post_user` / `post_guba` 及 `ret_ad_type` 等字段。本轮不应将 `Articlelist` 的平坦列表 schema 与 `WebArticleList` 合并后称为同一接口，也不应自行尝试用户/写入/搜索接口来填矩阵。

## 当前详情脚本的边界

来源文件：`live/007-public-news-js-hop0.body`\
来源请求 URL：`https://gbfek.dfcfw.com/deploy/fd_guba_web2022/work/news.js`\
bytes：685076\
SHA256：`e2ebf93d231b09a91d2b66f7814c1b0e891690ce1398670303e809cbb6a9413b`

hash 与对应 JSON 记录一致。离线跟踪实际读取调用没有找到可请求的匿名标准帖子正文 API；主体渲染使用已有 `window.post_article`，byte offset 523845 直接出现：

```javascript
dangerouslySetInnerHTML:{__html:(0,Sn.eZ)(r.post_content)}
```

`content/api/post/GetPostEditText` 虽在 byte offset 541110 出现，但属于“修改”按钮的权限流程：先检查 `abstract/api/PostMod/GetModAuthority`，再允许取得编辑文本。因此它不能作为匿名正文替代入口，不建议为本研究请求它。`reply/api/Reply/JxArticleReplyList`（byte offset 585962）等是回复 API，不是标准帖子正文。未发现不等于不存在其他公开详情入口；本次可靠详情候选仍为真实列表观察到的标准 `news,...html`。

当前两个大脚本中的 `h5` 还会出现在 base64 图片内容，不能用无上下文字符串命中推断移动页面。当前文档明确引用独立 `h5Adaptation.js?r=6`，但本子任务没有其响应，未臆造移动路径。

## 初始列表和对应详情的独立离线复核

本子任务重新从两个 raw body 用独立 `json.JSONDecoder().raw_decode` 提取 `var article_list=` / `var post_article=`，未导入生产 parser 或请求程序的分析函数；重新计算 bytes 与 SHA256 均与 manifest 一致。

| 项目 | 本次初始列表 | 对应详情 |
|---|---|---|
| 文件 | `live/002-initial-list-601012-authorized-network-hop0.body` | `live/003-initial-detail-601012-hop0.body` |
| bytes | 137726 | 22552 |
| SHA256 | `84fda9d84538630915cdeb11db186ced6f50806231cce1e4eb4f1ef7475f3ff9` | `2ab44b353f16f63a8328c0227e2ce01c9190d564af37b704dcbcb26344afbdf3` |
| 关键 payload | `article_list.rc=1`, 81 rows / 81 distinct IDs | `post_article.post_id=1779352996`, `post_type=0` |
| 类型 | type0=78, type20=2, type1=1 | `post_type=0`，标准正文 scope 通过 |
| 原始发布时间 | type0 列表非增，初始候选 `2026-09-30 15:38:17` | 同一发布时间 `2026-09-30 15:38:17` |
| canonical bar | 候选 `601012` | `post_guba.stockbar_code=601012` |

列表首行为 `post_type=1, post_top_status=1` 的置顶项，所以 81 行和全列表开头不按发布时间排列不能直接判为分页失败。78 个 type0 行发布时间非增，其中 31 行的 `post_publish_time` 与 `post_last_time` 不同，支持维持二者不同语义。

请求 URL 中的 ID、列表 ID 和详情 ID 都为 `1779352996`，标题和原始发布时间一致。该详情的 `post_last_time` 从列表观察时的 `15:38:17` 变成详情观察时的 `15:59:15`，这是不同观察时刻的可变字段，不能改写 publish time。

详情 `post_content` 为来源真实返回的 8 字字符串“股民还是很有钱的”，恰与 `post_title` 相同。这里没有用标题填充正文：原始详情 JSON 明确含 `post_content`，原始 HTML 的独立正文容器还明确含以下内容：

```html
<div class="newstext "><div>股民还是很有钱的</div></div>
```

建议后续使用至少一个来源正文更长且异于标题的详情，以增强正文获取证据；这个建议不改变本条真实 `post_content` 的有效性。

## 原始响应挑战证据的复核

上述初始两个 body 各含一次 `em_capt.js`，而 `身份核实`、`访问验证`、`安全验证`、`人机验证`、`验证码`、`滑块`、`拼图`、`emcaptcha`、`fd_guba_validate`、`validate.js` 均为零次。页面标题均为真实股票/帖子标题，payload 和正文存在。对于这两个具体原始响应，记录“没有明确可见挑战证据”合理；不能因模板脚本把它们判成挑战壳。

该结论的可观测对象是已保留的原始响应。HTTP 客户端不执行 JavaScript/CSS，动态生成滑块或渲染后 overlay 仍不可观察；不得改写为“浏览器不会出现验证码”或“永久无验证码”。当前脚本请求构造和上述初始 HTML 响应复核也不是无人值守长期稳定性证明。

## 可重放离线核验方法

在项目根目录只读取上述文件，计算 `hashlib.sha256(Path(...).read_bytes()).hexdigest()`，核对 manifest 的 `sha256` 和 `response_bytes`；随后对本文 exact snippet 的 UTF-8 bytes 用 `.find()` 核对偏移。全部列出的 script byte offset 已在本子任务独立计算。当前线上分母、分页进展、其他股票和独立进程表现由主研究者在最终报告统一统计。

本子任务本次只新增本文件。API 命令为出处完整的复现候选，未替主研究者发起请求。
