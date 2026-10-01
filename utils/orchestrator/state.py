# -*- coding: utf-8 -*-
"""M3 图状态与共享常量。

设计依据 `docs/COVERAGE_DESIGN.md` §5.1（图结构）/ §5.2-3（人审轮次硬上限）。
"""
from typing import Any, Dict, List, TypedDict

#: 人审循环硬上限（§5.2-3：防死循环烧 token）
MAX_REVIEW_ROUNDS = 3

#: 条件边两个去向（§5.1：财报齐否）
BRANCH_ANALYZE = "analyze"
BRANCH_FALLBACK = "fallback"

#: 节点名（对外可引用；测试与日志都按这套命名）
NODE_DATA_FETCH = "data_fetch"
NODE_FALLBACK = "fallback"
NODE_ANALYZE = "analyze"
NODE_RETRIEVE = "retrieve"
NODE_SYNTHESIZE = "synthesize"
NODE_HUMAN_REVIEW = "human_review"

NODE_NAMES = (
    NODE_DATA_FETCH,
    NODE_FALLBACK,
    NODE_ANALYZE,
    NODE_RETRIEVE,
    NODE_SYNTHESIZE,
    NODE_HUMAN_REVIEW,
)

#: 人审状态
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REVISE = "revise"

#: 检查点 / 断点续跑用的默认库文件名（与项目 SQLite 同目录惯例）
CHECKPOINT_DB_NAME = "checkpoints.db"


class DiagnosisState(TypedDict, total=False):
    """「股票深度诊断」图的状态（`total=False`：节点只写自己产出的键）。

    ⚠️ 所有键都由**节点显式返回**参与状态合并；`trace` / `errors` 的「追加语义」由
    **节点显式拼接**实现（`graph.py::_append`），**不使用 reducer**
    —— 见下方字段注释里的原因（reducer 无法被新一轮 `trace: []` 重置）。
    """

    # ---- 入口 ----
    stock_code: str

    # ---- data_fetch ----
    quote: Dict[str, Any]          # 行情
    financials: Dict[str, Any]     # 财报
    moneyflow: Dict[str, Any]      # 资金流
    fetch_errors: List[str]        # 各源失败原因（优雅降级，不抛）
    finance_complete: bool         # 条件边判据：财报是否齐
    branch: str                    # BRANCH_ANALYZE / BRANCH_FALLBACK

    # ---- analyze ----
    engines: Dict[str, Any]        # 各引擎结果（基本面/排雷/护城河/估值/…）
    engine_errors: List[str]

    # ---- retrieve ----
    evidence: List[Dict[str, Any]]  # M1 检索到的公告/研报片段（带来源）
    evidence_level: str            # M1 的证据档位（none/weak）—— 必须带进报告，不能只留片段
    evidence_note: str             # M1 的弃权/提示文案（"证据不足档…不要据此推断"）

    # ---- synthesize ----
    report: str                    # 带引用的诊断报告

    # ---- human_review ----
    review_round: int              # 已发生的人审轮次（上限 MAX_REVIEW_ROUNDS）
    review_status: str             # REVIEW_PENDING / REVIEW_APPROVED / REVIEW_REVISE
    revision_note: str             # 用户要求修改时带回来的批示

    # ---- 出口 / 观测 ----
    # ⚠️ 追加语义由**节点显式拼接**实现（`state["trace"] + [node]`），**不用 reducer**：
    #    `Annotated[..., operator.add]` 的问题是新一轮 `invoke` 传进来的 `trace: []`
    #    会被**追加**而不是重置 ⇒ 同一 thread 跑第二次时轨迹翻倍、失去可读性
    #    （2026-10-02 C2 实跑抓到）。显式拼接则行为等价且可重置。
    trace: List[str]
    errors: List[str]
