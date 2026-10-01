# 离线公开入口核对

Date: 2026-09-30, Asia/Shanghai\
Role: Source Researcher\
Status: OFFLINE_ENTRY_REFERENCES_CONFIRMED; CURRENT_ACCESS_UNTESTED

本子任务没有发起网络请求、使用浏览器、读取生产数据库或修改生产代码。只读取项目契约、当前 scope、SOURCE_SPEC、历史研究文档和 `data/raw/eastmoney_guba/*.body` 的保留来源文档。线上请求和总预算由主研究者唯一执行。

## 可供主研究者请求的脚本地址

在历史保留来源文档中逐字确认了两个脚本引用；`https:` 是对协议相对地址的 HTTPS 解析，不改变路径或添加参数：

1. `https://gbfek.dfcfw.com/deploy/fd_guba_web2022/work/list.js`
2. `https://gbfek.dfcfw.com/deploy/fd_guba_web2022/work/news.js`

这些地址仅证明历史来源文档实际引用了它们，不能证明 2026-09-30 仍可取、当前代码一致或其中 API 匿名可用。下一步应保存本次脚本的原始响应与 SHA256，再从实际代码确定 API 主机、方法、参数和值。

## 精确来源引文与完整性

偏移均为文件原始 bytes 的零基偏移。SHA256 在本次离线读取时重新计算，与 `.body` 文件名一致。

### list.js

文件：`data/raw/eastmoney_guba/0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03.body`\
文件 bytes：317881\
SHA256：`0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03`\
byte offset：311415

```html
<script type="text/javascript" src="//gbfek.dfcfw.com/deploy/fd_guba_web2022/work/list.js">
```

同样引用也存在于先提供给主研究者的 `data/raw/eastmoney_guba/6a1a7f2811faf0556e192a0caf26d7553268195b4245e346d7289a7ccc5d8a3d.body`（331040 bytes，SHA256 同文件名）。

### news.js

文件：`data/raw/eastmoney_guba/0b3cb7de28c4382cb39cd33af4310e7f4ad95c3b88b720215cc5ba623980ad1d.body`\
文件 bytes：167321\
SHA256：`0b3cb7de28c4382cb39cd33af4310e7f4ad95c3b88b720215cc5ba623980ad1d`\
byte offset：160623

```html
<script type="text/javascript" src="//gbfek.dfcfw.com/deploy/fd_guba_web2022/work/news.js">
```

同样引用也存在于先提供给主研究者的 `data/raw/eastmoney_guba/9caa2466a4bad7e00f8fcb1bc2bef676a85e347e89eceb8b383de51ce4bd4518.body`（158518 bytes，SHA256 同文件名）。

### 已直接引用的匿名栏目 API 候选

文件：与上面的 `0500aef...body` 相同。\
SHA256：`0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03`\
byte offset：313432

```html
<script type="text/javascript" src="//gbcdn.dfcfw.com/gbapi/webarticlelist_api_HighQuality_Articlelist.js?ps=20&amp;p=1&amp;code=601012&amp;callback=quality_content" id="quality_content">
```

按 HTML 实体解码并解析 HTTPS 后的精确候选：

```text
https://gbcdn.dfcfw.com/gbapi/webarticlelist_api_HighQuality_Articlelist.js?ps=20&p=1&code=601012&callback=quality_content
```

`ps=20`、`p=1`、`code=601012`、`callback=quality_content` 均来自已保留文档，不是自行猜测。但路径和 callback 指向 HighQuality 栏目；尚未验证字段、排序或覆盖范围，不能以它替代完整列表、最新发帖分页或标准详情正文。更应优先研究 `list.js` 的真正完整列表调用。

## 历史列表 API 记录的可用信息与缺口

文件：`runs/phase-01-round-01/research-evidence.md:43`\
本次文件 SHA256：`b73f67a4ed5edd9888680e8a250471d5a0a690eb78496ba49d310b04f5870ec8`

精确记录：

> source-owned `list.js` maps `,f` to “最新发帖”, uses page number and a page size of 80 in the observed page, and references `webarticlelist/api/Article/Articlelist` with `code`, `type`, `p`, `ps` and `sorttype` parameters.

该文档只记录相对 API 路径、参数名字和历史排序观察，没有 API 主机、请求方法、`type` / `sorttype` 的精确取值、签名或可重放 API 响应。因此本子任务没有据此拼接“完整列表 API”，没有猜测签名，也没有宣称 API 已失效。

同文件第 46 行只证明历史 `news.js` 中观察到回复字段和 `p` / `ps`；它没有标准详情匿名 API 的完整请求，不能把回复入口当成帖子正文入口。

## 移动页面核对

本次扫描 117 个 `.body` 文件，其中 marker `var article_list=` 出现于 112 个文件，`var post_article=` 出现于 5 个文件；这里是 marker 计数，不代表解析、内容完整性或无挑战计数。

117 个文件均保留以下精确模板标签：

```html
<meta name="mobile-agent" content="">
```

在这些文件中，`mguba`、`m.guba`、`gubaapi` 字面量均为 0 次。对上述历史研究目录与 run 文档的文本搜索也没有找到有出处的完整移动列表或详情链接。结论是“本轮已检查的离线证据没有移动入口”，不是“来源没有移动页面”。可从主研究者本次获取的公开脚本继续发现，但不应凭印象制造移动 URL。

## 证据边界

这些 `.body` 是生产既有保留来源文档。本子任务没有读取数据库以核验它们各自的 acquisition metadata，也不把它们统称为原始 HTTP 响应；浏览器 DOM serialization 可能包含运行时追加的脚本标签。引文足以定位历史来源引用，但不能替代本次普通 HTTP 原始响应证据。

已按 scope 阅读 D-016 / D-017 和 2026-09-24 挑战记录：正常模板的 `em_capt.js` 不能单独证明发生挑战；有 `article_list` / `post_article` 也不能证明没有渲染后的动态挑战。本子任务没有执行 JavaScript，未对这 117 个历史页面宣称“没有验证码”。

## 可重放离线核验

在项目根目录执行，零网络请求：

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
checks = [
    ('0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03', b'//gbfek.dfcfw.com/deploy/fd_guba_web2022/work/list.js'),
    ('0b3cb7de28c4382cb39cd33af4310e7f4ad95c3b88b720215cc5ba623980ad1d', b'//gbfek.dfcfw.com/deploy/fd_guba_web2022/work/news.js'),
    ('0500aef7b343ba922ecf8360c3bc8d5ee3914983b31323bec66683e8243f6d03', b'//gbcdn.dfcfw.com/gbapi/webarticlelist_api_HighQuality_Articlelist.js?ps=20&amp;p=1&amp;code=601012&amp;callback=quality_content'),
]
for expected, needle in checks:
    path = Path('data/raw/eastmoney_guba') / (expected + '.body')
    body = path.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    assert actual == expected, (path, actual)
    assert needle in body, (path, needle)
    print(path, len(body), actual, 'URL_byte_offset=', body.index(needle))
PY
```

只交付本文件；未修改 `src/`、SOURCE_SPEC、配置、生产 DB 或历史来源证据。当前可用性结论留给主研究者的有预算线上实测。
