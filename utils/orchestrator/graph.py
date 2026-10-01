# -*- coding: utf-8 -*-
"""「股票深度诊断」链路图（M3）。

结构与节点：`docs/COVERAGE_DESIGN.md` §5.1

```
[入口] 股票代码
  ↓
[节点 data_fetch] 拉行情/财报/资金流（现有工具，失败可重试）
  ↓
[条件边] 财报数据是否齐？──否──→ [节点 fallback] 降级为「仅行情诊断」
  ↓                是
[节点 analyze] 6 引擎分析（基本面/排雷/护城河/估值/三表/辩论）
  ↓
[节点 retrieve] M1 检索相关公告与研报
  ↓
[节点 synthesize] 汇总成带引用的诊断报告
  ↓
[节点 human_review] 用户确认 ──修改──→ 回到 analyze（受最大轮次限制）
  ↓
[出口] 报告 + 检查点持久化（可断点续跑）
```

实现要点（§10.5）：
1. checkpointer 用 **SQLite**（`langgraph-checkpoint-sqlite`，无需 Postgres）
2. 人审节点用 **`interrupt()`** 原语，**不手搓**审批中断
3. 并发用 Pregel 超步模型（暂不需并行节点，保留可能性）
4. 版本锁定 LangGraph 1.x；⚠️ `langgraph.prebuilt` 已废弃 → **本文件不使用**
5. `ORCHESTRATOR=legacy|graph` flag（见 `flags.py`）

设计取舍（为什么 data_fetch 与 analyze 不重复取数）：
- `data.diagnosis.build_diagnosis_payload` 本身就是「一次性统一拉 6 引擎」，且带 24h TTL 缓存；
- 若 `data_fetch` 也全量拉，条件边就**失去意义**（贵的那步已经跑完了）；
- 所以 `data_fetch` 只做**轻量取数 + 财报齐否判据**（行情 / 财报可用性 / 资金流），
  `analyze` 才去跑那份带缓存的 6 引擎聚合。
"""
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .state import (
    BRANCH_ANALYZE,
    BRANCH_FALLBACK,
    CHECKPOINT_DB_NAME,
    MAX_REVIEW_ROUNDS,
    NODE_ANALYZE,
    NODE_DATA_FETCH,
    NODE_FALLBACK,
    NODE_HUMAN_REVIEW,
    NODE_RETRIEVE,
    NODE_SYNTHESIZE,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REVISE,
    DiagnosisState,
)

# ======================================================================
# 薄封装层 —— 所有外部取数/生成都只经由这里，**便于离线 monkeypatch**
# ======================================================================
_FIN_KEYS = ("income", "balance", "cashflow")

def _append(state: DiagnosisState, key: str, *items: Any) -> List[Any]:
    """显式追加（替代 `operator.add` reducer —— 后者无法被新一轮 `trace: []` 重置）。"""
    return list(state.get(key) or []) + list(items)


#: 报告里外显的引擎字段（完整 payload 含大量内部字段，整表外显会淹没报告）
_ENGINE_LABELS = (
    ("stock_info", "行情"),
    ("fundamentals", "基本面"),
    ("fundamental_score", "基本面评分"),
    ("adv_risks", "优势与风险"),
    ("financials", "财报三表"),
    ("minefield", "排雷"),
    ("minefield_results", "排雷结果"),
    ("moat", "护城河"),
    ("moat_scores", "护城河评分"),
    ("valuation", "估值"),
    ("percentile", "历史分位"),
)


def _fetch_quote(stock_code: str) -> Dict[str, Any]:
    """行情（轻量；失败由调用方兜底成 errors）。"""
    from data.stock_api import get_stock_info

    return get_stock_info(stock_code) or {}


def _fetch_financials(stock_code: str) -> Dict[str, Any]:
    """财报可用性探针，**归一成 `{"income": {...}, "balance": {...}, "cashflow": {...}}`**。

    ⚠️ 条件边判据依赖这个契约；真实数据源的字段名变化由这里吸收，**不要把
    数据源细节漏进 `resolve_reporting_period`**（那样判据就没法离线测了）。
    """
    from data.stock_fundamentals import get_stock_financial_data

    raw = get_stock_financial_data(stock_code)
    if not isinstance(raw, dict):
        return {}

    def _pick(*names):
        for n in names:
            v = raw.get(n)
            if isinstance(v, dict) and v:
                return v
        return {}

    merged = {
        "income": _pick("income", "profit", "income_statement"),
        "balance": _pick("balance", "balance_sheet"),
        "cashflow": _pick("cashflow", "cash_flow", "cashflow_statement"),
    }
    # 有些数据源把三表拍平在一层：退化为「整体当 income」交给判据自行判定
    if not any(merged.values()) and raw:
        merged["income"] = raw
    return merged


def _fetch_moneyflow(stock_code: str) -> Dict[str, Any]:
    """资金流（轻量）。"""
    from data.stock_api import get_stock_moneyflow

    return get_stock_moneyflow(stock_code) or {}


def _run_engines(state: DiagnosisState) -> Dict[str, Any]:
    """6 引擎聚合（复用 `data.diagnosis.build_diagnosis_payload`，带 24h TTL 缓存）。

    ⚠️ 返回**完整 payload**（而不是挑几个字段）：`services/diagnosis_service` 在
    `ORCHESTRATOR=graph` 下要**原样返回同一形状**给前端（§5.1「只图化链路，不重写」），
    挑字段会导致两侧契约漂移。
    """
    from data.diagnosis import build_diagnosis_payload

    payload = build_diagnosis_payload(state["stock_code"])
    return payload if isinstance(payload, dict) else {}


def _retrieve_evidence(stock_code: str) -> Dict[str, Any]:
    """M1 检索：查该股票相关公告/研报。

    ⚠️ **`retrieve_docs` 返回的是 JSON 字符串**（不是 dict/list）——2026-10-02 C2 实跑抓到：
    原先只判 `dict`/`list`，字符串落进 `else` ⇒ `evidence` **恒为 0**。
    检索本身完全正常（返回了真实片段 + `evidence_level`），**是适配层把它丢了**。
    已加回归锁 `test_retrieve_adapter_parses_json_string`。
    """
    import json

    from utils.rag.retrieve import retrieve_docs

    try:
        res = retrieve_docs("{} 最新公告 财报要点".format(stock_code),
                            code=stock_code, top_n=5)
    except Exception:  # noqa: BLE001 - 检索失败不该让整条诊断链断掉
        return {"items": [], "level": "", "note": "检索失败，本次无原文引用"}

    if isinstance(res, str):
        try:
            res = json.loads(res)
        except (ValueError, TypeError):
            return {"items": [], "level": "", "note": "检索结果解析失败，本次无原文引用"}
    if not isinstance(res, dict):
        return {"items": [], "level": "", "note": ""}

    raw_items = res.get("results") or res.get("chunks") or res.get("docs") or []
    items: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        items.append({
            "chunk_id": it.get("chunk_id"),
            "source": it.get("source") or it.get("doc_title") or it.get("title") or it.get("doc_id"),
            "date": it.get("date"),
            "text": (it.get("text") or "")[:400],
        })
    return {
        "items": items,
        "level": str(res.get("evidence_level") or ""),
        "note": str(res.get("message") or ""),
    }


def _synthesize_report(state: DiagnosisState) -> str:
    """汇总成带引用的诊断报告（确定性模板，**不依赖 LLM** ⇒ C1/C2 可离线复现）。"""
    code = state.get("stock_code", "")
    lines = ["# {} 深度诊断（{} 路径）".format(code, state.get("branch", "?")), ""]

    quote = state.get("quote") or {}
    if quote:
        lines.append("## 行情")
        for k in ("name", "price", "pct_chg", "change_pct", "market_cap"):
            if quote.get(k) is not None:
                lines.append("- {}：{}".format(k, quote[k]))
        lines.append("")

    engines = state.get("engines") or {}
    if engines:
        lines.append("## 引擎分析")
        for key, label in _ENGINE_LABELS:
            if key in engines:
                lines.append("- {}：{}".format(label, "有数据" if engines.get(key) else "无数据"))
        lines.append("")

    evidence = state.get("evidence") or []
    if evidence:
        lines.append("## 原文引用")
        for i, e in enumerate(evidence, 1):
            src = e.get("source") or "未知来源"
            date = e.get("date") or "日期不明"
            lines.append("- [{}] {}（{}）：{}".format(i, src, date, (e.get("text") or "")[:120]))
        lines.append("")
    # ⚠️ M1 的证据档位与弃权说明必须随报告外显：只给片段而不说"证据不足档"，
    #    等于把 M1 最核心的诚实设计（宁可说没找到）在编排层丢掉了。
    if state.get("evidence_note"):
        lines.append("> 证据档位：**{}** —— {}".format(
            state.get("evidence_level") or "未标注", state["evidence_note"]))
        lines.append("")

    errs = list(state.get("errors") or []) + list((engines or {}).get("errors") or [])
    if errs:
        lines.append("## 数据缺口（如实标注）")
        for e in errs:
            lines.append("- {}".format(e))
        lines.append("")

    if state.get("branch") == BRANCH_FALLBACK:
        lines.append("> ⚠️ 财报数据不齐，本条为**降级诊断**（仅行情维度）。")
    lines.append("")
    lines.append("> 本报告由本地规则汇总，**不构成投资建议**。")
    return "\n".join(lines)


# ======================================================================
# 纯判据（条件边）—— 必须可离线测
# ======================================================================
def resolve_reporting_period(financials: Dict[str, Any]) -> bool:
    """条件边判据：**财报数据是否齐**（§5.1 的那个分支）。

    判据（刻意宽容，因为上游数据源会变）：
    - 必须是 dict；
    - `income` / `balance` / `cashflow` 三者**都要**是非空 dict；
    - 且至少有一个「非 None 的数值」—— 只有 key 名没有值是"拿到了空壳"，不算齐。
    """
    if not isinstance(financials, dict):
        return False
    for key in _FIN_KEYS:
        part = financials.get(key)
        if not isinstance(part, dict) or not part:
            return False
    has_value = False
    for key in _FIN_KEYS:
        for v in (financials.get(key) or {}).values():
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                has_value = True
                break
            # 嵌套结构里也可能有值（如 {"latest": {"revenue": 1}}）
            if isinstance(v, dict):
                for inner in v.values():
                    if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                        has_value = True
                        break
            if has_value:
                break
        if has_value:
            break
    return has_value


def _after_data_fetch(state: DiagnosisState) -> str:
    return BRANCH_ANALYZE if state.get("finance_complete") else BRANCH_FALLBACK


def _after_review(state: DiagnosisState) -> str:
    """人审出口：批准 → END；要求修改且未超上限 → 回 analyze；超上限 → 强制 END。"""
    if state.get("review_status") == REVIEW_REVISE:
        if int(state.get("review_round") or 0) < MAX_REVIEW_ROUNDS:
            return NODE_ANALYZE
    return END


# ======================================================================
# 节点函数（模块级 ⇒ 内部对 `_*` 薄封装做**晚绑定**，测试才能 monkeypatch）
# ======================================================================
def _node_data_fetch(state: DiagnosisState) -> Dict[str, Any]:
    code = state["stock_code"]
    quote, financials, moneyflow = {}, {}, {}
    errors: List[str] = []

    for label, fn, slot in (("行情", _fetch_quote, "quote"),
                            ("财报", _fetch_financials, "financials"),
                            ("资金流", _fetch_moneyflow, "moneyflow")):
        try:
            val = fn(code) or {}
        except Exception as e:  # noqa: BLE001 - 单源失败不影响其他源（§5.1「失败可重试」）
            val = {}
            errors.append("{}获取失败：{}".format(label, e))
        if slot == "quote":
            quote = val
        elif slot == "financials":
            financials = val
        else:
            moneyflow = val

    complete = resolve_reporting_period(financials)
    return {
        "quote": quote,
        "financials": financials,
        "moneyflow": moneyflow,
        "finance_complete": complete,
        "branch": BRANCH_ANALYZE if complete else BRANCH_FALLBACK,
        "fetch_errors": errors,
        "errors": _append(state, "errors", *errors),
        "trace": _append(state, "trace", NODE_DATA_FETCH),
    }


def _node_fallback(state: DiagnosisState) -> Dict[str, Any]:
    return {
        "branch": BRANCH_FALLBACK,
        "errors": _append(state, "errors", "财报数据不齐，已降级为「仅行情诊断」"),
        "trace": _append(state, "trace", NODE_FALLBACK),
    }


def _node_analyze(state: DiagnosisState) -> Dict[str, Any]:
    errors: List[str] = []
    try:
        engines = _run_engines(state) or {}
    except Exception as e:  # noqa: BLE001
        engines, errors = {}, ["6 引擎分析失败：{}".format(e)]
    patch: Dict[str, Any] = {"engines": engines,
                             "trace": _append(state, "trace", NODE_ANALYZE)}
    if errors:
        patch["errors"] = _append(state, "errors", *errors)
        patch["engine_errors"] = errors
    return patch


def _node_retrieve(state: DiagnosisState) -> Dict[str, Any]:
    try:
        res = _retrieve_evidence(state["stock_code"]) or {}
    except Exception as e:  # noqa: BLE001
        msg = "检索失败：{}".format(e)
        return {"evidence": [], "evidence_level": "", "evidence_note": msg,
                "errors": _append(state, "errors", msg),
                "trace": _append(state, "trace", NODE_RETRIEVE)}
    if isinstance(res, list):  # 兼容旧形状（裸列表）
        res = {"items": res, "level": "", "note": ""}
    return {
        "evidence": res.get("items") or [],
        "evidence_level": res.get("level") or "",
        "evidence_note": res.get("note") or "",
        "trace": _append(state, "trace", NODE_RETRIEVE),
    }


def _node_synthesize(state: DiagnosisState) -> Dict[str, Any]:
    try:
        report = _synthesize_report(state) or ""
    except Exception as e:  # noqa: BLE001
        return {"report": "",
                "errors": _append(state, "errors", "报告汇总失败：{}".format(e)),
                "trace": _append(state, "trace", NODE_SYNTHESIZE)}
    return {"report": report, "trace": _append(state, "trace", NODE_SYNTHESIZE)}


def _node_human_review(state: DiagnosisState) -> Dict[str, Any]:
    """人审节点：用 `interrupt()`（§10.5-2），**不手搓**中断。

    轮次语义：`review_round` 计「已发生的修改轮次」；达到 `MAX_REVIEW_ROUNDS` 后
    **不再阻塞**，直接判为 approved（硬上限，§5.2-3：防死循环烧 token）。
    """
    round_no = int(state.get("review_round") or 0)
    if round_no >= MAX_REVIEW_ROUNDS:
        return {
            "review_status": REVIEW_APPROVED,
            "trace": _append(state, "trace", NODE_HUMAN_REVIEW),
            "errors": _append(state, "errors",
                              "已达人审轮次上限 {}，强制通过".format(MAX_REVIEW_ROUNDS)),
        }

    decision = interrupt({
        "question": "确认这份诊断报告吗？（approve 通过 / revise 要求修改）",
        "round": round_no,
        "report": state.get("report", ""),
    })

    # decision 来自 Command(resume=...) —— 兼容 str 与 dict 两种写法
    if isinstance(decision, dict):
        action = str(decision.get("review") or decision.get("action") or "approve").lower()
        note = decision.get("note") or decision.get("resume") or ""
    else:
        action = "revise" if str(decision).strip().lower() in ("revise", "修改", "改") else "approve"
        note = "" if action == "approve" else str(decision)

    if action == "revise":
        return {
            "review_status": REVIEW_REVISE,
            "review_round": round_no + 1,
            "revision_note": note,
            "trace": _append(state, "trace", NODE_HUMAN_REVIEW),
        }
    return {
        "review_status": REVIEW_APPROVED,
        "review_round": round_no,
        "trace": _append(state, "trace", NODE_HUMAN_REVIEW),
    }


# ======================================================================
# 组装与入口
# ======================================================================
def build_graph(checkpointer=None):
    """构建并编译「股票深度诊断」图（六节点 + 两条条件边）。"""
    g = StateGraph(DiagnosisState)
    g.add_node(NODE_DATA_FETCH, _node_data_fetch)
    g.add_node(NODE_FALLBACK, _node_fallback)
    g.add_node(NODE_ANALYZE, _node_analyze)
    g.add_node(NODE_RETRIEVE, _node_retrieve)
    g.add_node(NODE_SYNTHESIZE, _node_synthesize)
    g.add_node(NODE_HUMAN_REVIEW, _node_human_review)

    g.add_edge(START, NODE_DATA_FETCH)
    g.add_conditional_edges(NODE_DATA_FETCH, _after_data_fetch,
                            {BRANCH_ANALYZE: NODE_ANALYZE, BRANCH_FALLBACK: NODE_FALLBACK})
    g.add_edge(NODE_ANALYZE, NODE_RETRIEVE)
    g.add_edge(NODE_FALLBACK, NODE_RETRIEVE)
    g.add_edge(NODE_RETRIEVE, NODE_SYNTHESIZE)
    g.add_edge(NODE_SYNTHESIZE, NODE_HUMAN_REVIEW)
    g.add_conditional_edges(NODE_HUMAN_REVIEW, _after_review,
                            {NODE_ANALYZE: NODE_ANALYZE, END: END})

    if checkpointer is None:
        return g.compile()
    return g.compile(checkpointer=checkpointer)


def make_checkpointer(db_path: Optional[str] = None) -> Tuple[Any, Any]:
    """创建 SQLite checkpointer（§10.5-1），返回 `(checkpointer, closer)`。

    为什么不用 `SqliteSaver.from_conn_string` 的 with 形式：断点续跑要**跨多次
    invoke 保留同一个连接**，所以这里显式管理生命周期，由调用方 `closer()` 释放。
    """
    path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "..", CHECKPOINT_DB_NAME)
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(conn)
    try:
        saver.setup()
    except Exception:  # noqa: BLE001 - 新版会自动建表；老版 setup 幂等
        pass
    return saver, conn.close


def graph_node_names(compiled) -> List[str]:
    """取出编译后图的节点名（屏蔽 langgraph 版本差异，测试与日志共用）。"""
    nodes = getattr(compiled, "nodes", None)
    if not nodes:
        try:
            nodes = compiled.get_graph().nodes
        except Exception:  # noqa: BLE001
            nodes = {}
    return sorted(n for n in (nodes or {}) if not str(n).startswith("__"))


def run_diagnosis_graph(
    stock_code: str,
    thread_id: str = "default",
    *,
    checkpointer=None,
    resume=None,
    review=None,
) -> Dict[str, Any]:
    """跑一次（或续跑一次）深度诊断图，返回最终状态 dict。

    - 不传 `resume`：从 `START` 跑到 `human_review` 处中断，返回**中断前**的状态
      （含 `branch` / `report` / `trace`），并把 `review_status` 标为 `pending`；
    - 传 `resume` + `review`：用 `Command(resume=...)` 从检查点续跑。
    """
    compiled = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}

    own_cp = False
    if checkpointer is None:
        # 无 checkpointer 时 `interrupt()` 会让 invoke 抛错：给一个临时 SQLite 支撑
        checkpointer, closer = make_checkpointer()
        compiled = build_graph(checkpointer=checkpointer)
        own_cp = True

    try:
        if resume is None and review is None:
            init: Dict[str, Any] = {"stock_code": stock_code, "trace": [], "errors": [],
                                    "review_round": 0}
            out = compiled.invoke(init, config)
        else:
            payload = {"review": review or "approve", "note": resume if review == "revise" else ""}
            out = compiled.invoke(Command(resume=payload), config)
    finally:
        if own_cp:
            closer()

    if not isinstance(out, dict):
        out = {}
    # 中断返回时补齐可读状态，便于调用方与测试直接断言
    if out.get("review_status") is None:
        out["review_status"] = REVIEW_PENDING
    return out
