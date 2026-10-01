# -*- coding: utf-8 -*-
"""M3 节点函数 + 条件边 + 判据（纯逻辑，不含外部 IO —— IO 全在 `adapters.py`）。

⚠️ 节点对薄封装做**模块级晚绑定**（`A._fetch_quote`），所以测试要 patch 的是
`utils.orchestrator.adapters` 里的名字（不是 `nodes` 也不是 `graph`）。
"""
from typing import Any, Dict, List

from langgraph.graph import END
from langgraph.types import interrupt

from . import adapters as A
from .state import (
    BRANCH_ANALYZE,
    BRANCH_FALLBACK,
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

def resolve_reporting_period(financials: Dict[str, Any]) -> bool:
    """条件边判据：**财报数据是否齐**（§5.1 的那个分支）。

    判据（刻意宽容，因为上游数据源会变）：
    - 必须是 dict；
    - `income` / `balance` / `cashflow` 三者**都要**是非空 dict；
    - 且至少有一个「非 None 的数值」—— 只有 key 名没有值是"拿到了空壳"，不算齐。
    """
    if not isinstance(financials, dict):
        return False
    for key in A._FIN_KEYS:
        part = financials.get(key)
        if not isinstance(part, dict) or not part:
            return False

    # 形态 A（主力，来自 `A._summarize_table`）：{"rows": n, "cols": [...]}
    #   判「三表都有行」——这才是"财报数据齐"的真实语义。
    if all("rows" in (financials.get(k) or {}) for k in A._FIN_KEYS):
        try:
            return all(int((financials.get(k) or {}).get("rows") or 0) > 0 for k in A._FIN_KEYS)
        except (TypeError, ValueError):
            return False

    # 形态 B（兼容：测试与历史调用方给的数值 dict）
    has_value = False
    for key in A._FIN_KEYS:
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

    for label, fn, slot in (("行情", A._fetch_quote, "quote"),
                            ("财报", A._fetch_financials, "financials"),
                            ("资金流", A._fetch_moneyflow, "moneyflow")):
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
        "errors": A._append(state, "errors", *errors),
        "trace": A._append(state, "trace", NODE_DATA_FETCH),
    }


def _node_fallback(state: DiagnosisState) -> Dict[str, Any]:
    return {
        "branch": BRANCH_FALLBACK,
        "errors": A._append(state, "errors", "财报数据不齐，已降级为「仅行情诊断」"),
        "trace": A._append(state, "trace", NODE_FALLBACK),
    }


def _node_analyze(state: DiagnosisState) -> Dict[str, Any]:
    errors: List[str] = []
    try:
        engines = A._run_engines(state) or {}
    except Exception as e:  # noqa: BLE001
        engines, errors = {}, ["6 引擎分析失败：{}".format(e)]
    patch: Dict[str, Any] = {"engines": engines,
                             "trace": A._append(state, "trace", NODE_ANALYZE)}
    if errors:
        patch["errors"] = A._append(state, "errors", *errors)
        patch["engine_errors"] = errors
    return patch


def _node_retrieve(state: DiagnosisState) -> Dict[str, Any]:
    try:
        res = A._retrieve_evidence(state["stock_code"]) or {}
    except Exception as e:  # noqa: BLE001
        msg = "检索失败：{}".format(e)
        return {"evidence": [], "evidence_level": "", "evidence_note": msg,
                "errors": A._append(state, "errors", msg),
                "trace": A._append(state, "trace", NODE_RETRIEVE)}
    if isinstance(res, list):  # 兼容旧形状（裸列表）
        res = {"items": res, "level": "", "note": ""}
    return {
        "evidence": res.get("items") or [],
        "evidence_level": res.get("level") or "",
        "evidence_note": res.get("note") or "",
        "trace": A._append(state, "trace", NODE_RETRIEVE),
    }


def _node_synthesize(state: DiagnosisState) -> Dict[str, Any]:
    try:
        report = A._synthesize_report(state) or ""
    except Exception as e:  # noqa: BLE001
        return {"report": "",
                "errors": A._append(state, "errors", "报告汇总失败：{}".format(e)),
                "trace": A._append(state, "trace", NODE_SYNTHESIZE)}
    return {"report": report, "trace": A._append(state, "trace", NODE_SYNTHESIZE)}


def _node_human_review(state: DiagnosisState) -> Dict[str, Any]:
    """人审节点：用 `interrupt()`（§10.5-2），**不手搓**中断。

    轮次语义：`review_round` 计「已发生的修改轮次」；达到 `MAX_REVIEW_ROUNDS` 后
    **不再阻塞**，直接判为 approved（硬上限，§5.2-3：防死循环烧 token）。
    """
    round_no = int(state.get("review_round") or 0)
    # ⚠️ 2026-10-02 审计 F3 修复：原判据是 `round_no >= MAX_REVIEW_ROUNDS`，
    # 但**条件边会先在 round=MAX 时 END** ⇒ 本分支永远不可达（用户视角=终态停在 revise，
    # 像"还在等修改"）。改为在**入口**判断"本轮之后即达上限" ⇒ 不再 accept 修改，
    # 直接判 approved 并把原因写明。用户最多拿到 MAX-1 次修改机会（不超上限）。
    if round_no + 1 >= MAX_REVIEW_ROUNDS:
        return {
            "review_status": REVIEW_APPROVED,
            "trace": A._append(state, "trace", NODE_HUMAN_REVIEW),
            "errors": A._append(
                state, "errors",
                "已达人审轮次上限 {}，强制通过（不再接受修改）".format(MAX_REVIEW_ROUNDS)),
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
            "trace": A._append(state, "trace", NODE_HUMAN_REVIEW),
        }
    return {
        "review_status": REVIEW_APPROVED,
        "review_round": round_no,
        "trace": A._append(state, "trace", NODE_HUMAN_REVIEW),
    }


# ======================================================================
# 组装与入口
# ======================================================================
