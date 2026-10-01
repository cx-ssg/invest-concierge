# -*- coding: utf-8 -*-
"""「股票深度诊断」链路图（M3）—— **组装层**。

结构与节点：`docs/COVERAGE_DESIGN.md` §5.1

```
[入口] 股票代码
  ↓  [data_fetch] 拉行情/财报/资金流（现有工具，失败可重试）
  ↓  [条件边] 财报数据是否齐？──否──→ [fallback] 降级为「仅行情诊断」
  ↓               是
  ↓  [analyze] 6 引擎分析 → [retrieve] M1 检索 → [synthesize] 汇总带引用报告
  ↓  [human_review] 用户确认 ──修改──→ 回 analyze（受最大轮次限制）
[出口] 报告 + 检查点持久化（可断点续跑）
```

实现要点（§10.5）：SQLite checkpointer / `interrupt()` 人审原语 / Pregel 超步 /
锁定 LangGraph 1.x（**不使用已废弃的 `langgraph.prebuilt`**）/ `ORCHESTRATOR=legacy|graph` flag。

⚠️ 本文件已按「模块 ≤500 行」拆分为三层：
- 外部 IO 与归一 → `adapters.py`
- 节点 / 条件边 / 判据 → `nodes.py`
- 组装（本文件）
下方 re-export 保持向后兼容（`services` 与旧测试仍可从 `graph` 取到这些名字），
但**打桩请 patch `adapters`** —— 节点是通过 `adapters.X` 晚绑定查找的。
"""
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from . import adapters as A
from . import nodes as N
from .state import (
    BRANCH_ANALYZE,
    BRANCH_FALLBACK,
    CHECKPOINT_DB_NAME,
    NODE_ANALYZE,
    NODE_DATA_FETCH,
    NODE_FALLBACK,
    NODE_HUMAN_REVIEW,
    NODE_RETRIEVE,
    NODE_SYNTHESIZE,
    REVIEW_PENDING,
    DiagnosisState,
)

# ---- 向后兼容 re-export（外部/旧测试可从 graph 取到；但 patch 要打在 adapters 上） ----
from .adapters import (  # noqa: F401
    _ENGINE_LABELS,
    _FIN_KEYS,
    _append,
    _fetch_financials,
    _fetch_moneyflow,
    _fetch_quote,
    _retrieve_evidence,
    _run_engines,
    _summarize_table,
    _synthesize_report,
)
from .nodes import (  # noqa: F401
    _after_data_fetch,
    _after_review,
    _node_analyze,
    _node_data_fetch,
    _node_fallback,
    _node_human_review,
    _node_retrieve,
    _node_synthesize,
    resolve_reporting_period,
)

# ======================================================================
# 薄封装层 —— 所有外部取数/生成都只经由这里，**便于离线 monkeypatch**
# ======================================================================


#: 报告里外显的引擎字段（完整 payload 含大量内部字段，整表外显会淹没报告）


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
