# -*- coding: utf-8 -*-
"""M3 编排层（LangGraph 渐进接入）。

设计依据：
- `docs/COVERAGE_DESIGN.md` §5 —— 只图化「股票深度诊断」**一条**链路，其余工具调用保持现有线性循环；
- §10.5 —— 实现要点：SQLite checkpointer / `interrupt()` 人审原语 / 锁定 LangGraph 1.x /
  `langgraph.prebuilt` 已废弃（勿用）。

开关：`ORCHESTRATOR=legacy|graph`（默认 `legacy` ⇒ **不改变现有行为**，见 §5.2-1）。
"""
from .flags import MODE_GRAPH, MODE_LEGACY, orchestrator_mode, use_graph
from .state import MAX_REVIEW_ROUNDS, NODE_NAMES, DiagnosisState

__all__ = [
    "MODE_LEGACY",
    "MODE_GRAPH",
    "orchestrator_mode",
    "use_graph",
    "MAX_REVIEW_ROUNDS",
    "NODE_NAMES",
    "DiagnosisState",
]
