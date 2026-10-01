# 研究证据与本地归档索引

整理日期：2026-10-01，Asia/Shanghai。

Git 保留本轮的范围、报告、交接、来源行为调查，以及小型校准/预检/扩展诊断
摘要。完整响应、逐请求元数据、帖子导出、原库只读导出和已被
`apps/http_backfill/` 替代的诊断脚本属于本地实验包，未作为生产源码或
source fixture 提交。单独 Git checkout 不包含完整原始响应，不能声称原始
回放证据已经随代码分发。

## 当前研究结果

- 初始预检取得 11 个有效列表和 9 次详情观察（6 个不同正文），只能支持
  当时有限样本的 HTTP 可取性。根目录 `summary.json` 等属于这个阶段。
- 扩展诊断 78 次来源 HTTP 尝试中，77 个列表有效，第 78 次
  `list,300750,f_18.html` 返回 HTTP 200 身份核实壳；立即停止，扩展正文 0。
  [workload/summary.json](workload/summary.json)、[audit.json](workload/audit.json)
  和 [halt.json](workload/halt.json) 保留结论、完整性审核和失败位置。
  `audit_status=PASS` 是证据一致性通过，不是来源采集或历史回补通过。
- 第 78 次实体 2,834 bytes，SHA-256：
  `d9bc3154679106ea3fe92b69042d743009a13a75813d660405fc2868a6174f5a`。
  实体与逐请求元数据仍在本地归档
  `workload/raw/078-list-300750-p18-hop0.body`、
  `workload/raw/078-list-300750-p18-hop0.json`，归档校验已通过。
- [backfill/inventory.json](backfill/inventory.json) 保留实际 16 股、
  2026-05-04 起点的来源/脚本/日志哈希及只读缺口统计；其中没有完整帖子导出。
  旧 90 页/300 正文配置仅是历史诊断，最新需求以每分钟一次闲时长任务与 H5
  控制台为准，见 [scope.md](scope.md) 与应用 SPEC/HANDOFF。

## 归档位置与完整性

本机归档目录位于仓库外：

```text
<workspace>/.local-archives/data-collector/20261001-git-cleanup/
    manifest.json
    guba-http-research-20260930/   # 整理前的完整实验包，内部路径保持原样
```

`<workspace>` 是包含 `MyResearcher-DataCollector/` 的父目录。
归档包含 350 个文件、575,894,657 bytes；所有文件的大小和 SHA-256 已逐一
比对。`manifest.json` 保存完整的相对路径、字节数和哈希，其 SHA-256 是：

```text
17845b393564f1b249297382450e981fcd6ce5bfbf3c7a95c838f5195d2187ec
```

从工作树移走 331 个生成文件（575,600,715 bytes），其中两份原库只读导出
`backfill/known-db.jsonl` 与 `backfill/pending-db.jsonl` 合计 531,366,050 bytes。
原始数据未销毁；整理前文档/脚本也在归档中保存，历史可追溯。
归档不包含应用运行目录或生产数据库，未移动、删除或迁移它们。

## 需要离线重放时

将完整实验包和 `manifest.json` 显式复制到需要重放的机器，先核验文件大小与
SHA-256，再在**归档实验包目录**运行历史离线 `calibration.py` /
`summarize.py` / `workload/verify.py`。历史校准还依赖报告中列出的原有
本机 corpus；缺少对应外部材料时，只能核验实际拥有的证据，不能补造成功。
不要把实验包恢复到生产数据目录，也不要执行历史 `probe.py` 或批量在线
命令：旧预算不代表新的网络授权，已有验证码阻断也没有因归档而解除。

报告里保留的 `live/`、`session/`、逐请求 JSONL、原始响应和实验脚本路径
均指归档包内的相对路径。Git 中的小型摘要用于跨客户端理解结果与限制；
原始回放必须携带本地归档。
