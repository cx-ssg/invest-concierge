# -*- coding: utf-8 -*-
"""M3 薄封装层 —— **所有外部取数/生成只经由这里**，便于离线 monkeypatch。

⚠️ 这是测试打桩的唯一落点：`monkeypatch.setattr(orchestrator.adapters, "_fetch_quote", ...)`。
拆文件前这些函数在 `graph.py` 里（审计后按「模块 ≤500 行」约束拆分）。

设计取舍（为什么 data_fetch 与 analyze 不重复取数）：
`data.diagnosis.build_diagnosis_payload` 本身就是「一次性统一拉 6 引擎」且带 24h TTL 缓存；
若 `data_fetch` 也全量拉，条件边就失去意义 ⇒ `data_fetch` 只做轻量取数 + 财报齐否判据。
"""
from typing import Any, Dict, List

from .state import BRANCH_FALLBACK, DiagnosisState

_FIN_KEYS = ("income", "balance", "cashflow")


def _append(state: DiagnosisState, key: str, *items: Any) -> List[Any]:
    """显式追加（替代 `operator.add` reducer —— 后者无法被新一轮 `trace: []` 重置）。"""
    return list(state.get(key) or []) + list(items)


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


def _summarize_table(df) -> Dict[str, Any]:
    """把（可能的）DataFrame 归一成**可序列化摘要**。

    ⚠️ 不能把 DataFrame 直接放进 langgraph state：检查点要序列化，pandas 对象会出问题。
    判据只需要「这张表有没有数据」，所以只留行数 + 列名。
    """
    if df is None:
        return {}
    shape = getattr(df, "shape", None)
    if shape is not None:
        try:
            rows = int(shape[0])
        except Exception:  # noqa: BLE001
            rows = 0
        cols = []
        try:
            cols = [str(c) for c in list(getattr(df, "columns", []))[:32]]
        except Exception:  # noqa: BLE001
            cols = []
        return {"rows": rows, "cols": cols}
    if isinstance(df, dict):
        return {"rows": len(df), "cols": [str(k) for k in list(df.keys())[:32]]}
    if isinstance(df, (list, tuple)):
        return {"rows": len(df), "cols": []}
    return {}


def _fetch_financials(stock_code: str) -> Dict[str, Any]:
    """三表可用性探针，归一成 `{"income": {...}, "balance": {...}, "cashflow": {...}}`。

    ⚠️ **2026-10-02 审计 F1（阻断）修复**：原先用的是
    `data.stock_fundamentals.get_stock_financial_data` —— 它返回**扁平标量 dict**
    （roe / gross_margin / ...），**不含任何三表键** ⇒ 判据恒 False ⇒
    **analyze 分支在生产结构性不可达（永久降级）**。
    真正的三表源是 `data.financial_report.get_financial_reports`
    （键 `profit_sheet` / `balance_sheet` / `cashflow_sheet`，值 DataFrame|None）。
    """
    from data.financial_report import get_financial_reports

    try:
        raw = get_financial_reports(stock_code)
    except Exception:  # noqa: BLE001 - 单源失败不该让整条链断掉
        return {}
    if not isinstance(raw, dict):
        return {}

    return {
        "income": _summarize_table(raw.get("profit_sheet")),
        "balance": _summarize_table(raw.get("balance_sheet")),
        "cashflow": _summarize_table(raw.get("cashflow_sheet")),
    }


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
        # ⚠️ F5（2026-10-02 审计）：真实检索项带 `title` / `published_at`，
        # 而原映射取 `source`（值其实是类型串 "notice"）与不存在的 `date`
        # ⇒ 报告引用恒显示「notice（日期不明）」。按真实字段优先映射。
        items.append({
            "chunk_id": it.get("chunk_id"),
            "source": (it.get("title") or it.get("doc_title")
                       or it.get("source") or it.get("doc_id")),
            "date": it.get("published_at") or it.get("date"),
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

    # ⚠️ F6（2026-10-02 审计）：以下四项原先「只写不读」——状态里有、报告里没有。
    #    加进报告后，它们才真正参与用户可见输出（否则等于白算）。
    moneyflow = state.get("moneyflow") or {}
    if moneyflow:
        lines.append("## 资金流")
        for k in ("main_net", "main_net_pct", "retail_net", "date"):
            if moneyflow.get(k) is not None:
                lines.append("- {}：{}".format(k, moneyflow[k]))
        lines.append("")

    if state.get("revision_note"):
        lines.append("> 本轮按用户批注重跑：{}".format(state["revision_note"]))
        lines.append("")

    # ⚠️ 合并三类错误来源时去重，避免同一句在报告里出现两遍
    errs = list(state.get("errors") or [])
    for src in ((engines or {}).get("errors") or [], state.get("fetch_errors") or [],
                state.get("engine_errors") or []):
        for e in src:
            if e not in errs:
                errs.append(e)
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
