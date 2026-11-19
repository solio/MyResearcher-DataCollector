"""帖子处置结论（Decision）：整条链路的唯一事实源。

约定：

* **判定只发生在采集层**。投影、合并、校验、导出只允许做两件事——
  契约校验（这个 status/reason 组合是否合法）和引用校验（证据对象是否还在），
  不允许再用自己的输入重新推导一遍结论。
* 结论落库一次，跨层传同一个结构，带 schema 版本号。
* 每种状态必须有出口：写入方要能说清"什么条件下清掉它"。

这个模块只做构造与校验，不碰数据库、不碰网络，便于在任何一层复用。
"""
import time

VERSION = 1

#: 一条帖子在某一刻只可能处于其中一种状态。
STATUSES = {
    "pending",       # 已入库，等正文
    "complete",      # 正文已取得
    "out_of_scope",  # 判定不该采（跨吧、非普通帖、读不出内容）
    "removed",       # 来源明确表示帖子不存在/不可访问
    "list_only",     # 列表型帖子，按策略不需要正文
}

#: 结论的理由。新增理由时必须同时出现在这里，否则任何一层都拒收。
REASONS = {
    "awaiting_body": "普通帖，等待正文",
    "body_acquired": "正文已完整取得",
    "foreign_bar": "详情地址属于非股票吧（基金/财富号模板）",
    "non_ordinary": "详情类型不是普通帖",
    "content_unreadable": "详情页读不出正文",
    "source_missing": "来源明确返回帖子不存在/不可访问",
    "not_needed": "按策略不需要正文",
    "interrupted": "进程中断，来源状态未知",
}

#: 证据种类。value 的语义随 kind 而定，validate 只检查非空与格式。
EVIDENCE_KINDS = {
    "url_bar": "详情 URL 里的吧标识（非 6 位股票代码即为跨吧）",
    "detail_post_type": "详情页 post_article.post_type",
    "http_status": "来源返回的状态码",
    "source_page": "来源明确的不存在/不可访问页面",
    "raw_body": "已落盘的正文来源响应",
    "none": "无证据（尚未判定或中断）",
}

#: 每种状态允许的证据种类；空集合表示"这类状态不需要证据"。
_ALLOWED_EVIDENCE = {
    "pending": {"none"},
    "complete": {"raw_body"},
    "out_of_scope": {"url_bar", "detail_post_type"},
    "removed": {"source_page", "http_status"},
    "list_only": {"none"},
}

#: 每种状态允许的理由，防止"跨吧"被写成 complete 这类错配。
_ALLOWED_REASONS = {
    "pending": {"awaiting_body", "interrupted"},
    "complete": {"body_acquired"},
    "out_of_scope": {"foreign_bar", "non_ordinary", "content_unreadable"},
    "removed": {"source_missing"},
    "list_only": {"not_needed"},
}


class DecisionError(ValueError):
    """构造阶段就拒绝非法组合，避免脏结论落库。"""


def build(post_id, status, reason, *, evidence=None, at=None):
    """唯一构造入口。非法组合在这里就被拒绝，不会流到下游。"""
    decision = {
        "v": VERSION,
        "post_id": str(post_id),
        "status": status,
        "reason": reason,
        "evidence": dict(evidence or {"kind": "none", "value": None}),
        "at": float(at if at is not None else time.time()),
    }
    problems = validate(decision)
    if problems:
        raise DecisionError("; ".join(problems))
    return decision


def validate(decision):
    """校验结论是否符合契约，返回问题列表（空列表表示通过）。

    下游（投影/合并/校验）统一调用它，不再自己对 HTML 或 URL 重新推导。
    """
    problems = []
    if not isinstance(decision, dict):
        return ["结论不是对象"]
    if decision.get("v") != VERSION:
        problems.append(f"schema 版本不是 {VERSION}")
    post_id = decision.get("post_id")
    if not isinstance(post_id, str) or not post_id:
        problems.append("缺少 post_id")
    status = decision.get("status")
    if status not in STATUSES:
        problems.append(f"未知状态 {status!r}")
        return problems
    reason = decision.get("reason")
    if reason not in REASONS:
        problems.append(f"未知理由 {reason!r}")
    elif reason not in _ALLOWED_REASONS[status]:
        problems.append(f"状态 {status} 不允许理由 {reason}")
    evidence = decision.get("evidence")
    if not isinstance(evidence, dict):
        problems.append("缺少 evidence")
        return problems
    kind = evidence.get("kind")
    if kind not in EVIDENCE_KINDS:
        problems.append(f"未知证据种类 {kind!r}")
    elif kind not in _ALLOWED_EVIDENCE[status]:
        problems.append(f"状态 {status} 不允许证据 {kind}")
    elif kind != "none":
        value = evidence.get("value")
        if value in (None, ""):
            problems.append(f"证据 {kind} 缺少 value")
    if not isinstance(decision.get("at"), (int, float)):
        problems.append("缺少时间戳")
    return problems


def is_consistent(a, b):
    """两条结论是否指向同一个事实（用于跨层比对，替代"各算一遍再比字符串"）。"""
    if not a or not b:
        return False
    if a.get("post_id") != b.get("post_id"):
        return False
    if a.get("status") != b.get("status"):
        return False
    if a.get("reason") != b.get("reason"):
        return False
    ea, eb = a.get("evidence") or {}, b.get("evidence") or {}
    return ea.get("kind") == eb.get("kind") and str(ea.get("value")) == str(eb.get("value"))


def detail_scope_decision(item, payload, *, old_request=None, new_request=None, post_id=None):
    """跨吧/非普通帖的判定入口。

    采集层、投影层、合并层都只能调用它，不允许各自读 post_type 或 URL 再算一遍。
    返回 (decision, None) 或 (None, 冲突描述)。
    """
    from urllib.parse import urlparse

    post_id = str(post_id or (payload or {}).get("post_id") or getattr(item, "source_item_id", ""))
    url = (new_request or {}).get("url") or ""
    parts = urlparse(url).path.split(",")
    bar = parts[1] if len(parts) > 1 else ""
    if bar and not (len(bar) == 6 and bar.isdigit()):
        return build(post_id, "out_of_scope", "foreign_bar",
                     evidence={"kind": "url_bar", "value": bar}), None

    raw_type = (payload or {}).get("post_type")
    try:
        post_type = int(raw_type)
    except (TypeError, ValueError):
        return None, f"帖子 {post_id} 的详情类型缺失或无效：post_type={raw_type!r}"
    if post_type == 0:
        return None, None
    return build(post_id, "out_of_scope", "non_ordinary",
                 evidence={"kind": "detail_post_type", "value": post_type}), None
