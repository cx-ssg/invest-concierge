# -*- coding: utf-8 -*-
"""M3 编排层回归锁 —— §5.3 验收 **C1**（节点 / 条件边 / 重试 / 轮次上限 / 检查点）。

⚠️ 全部**离线**：所有外部取数薄封装（`graph._fetch_*` / `_run_engines` / `_retrieve_evidence`）
   用 monkeypatch 注入 ⇒ 不触网、不依赖 `kb.db`、不调 LLM。
⚠️ 设计依据：`docs/COVERAGE_DESIGN.md` §5.1（图结构）/ §5.2（四条决策）/ §10.5（实现要点）。
"""
import sys

import pytest

from utils.orchestrator import flags
from utils.orchestrator import graph as g
from utils.orchestrator.state import (
    BRANCH_ANALYZE,
    BRANCH_FALLBACK,
    MAX_REVIEW_ROUNDS,
    NODE_NAMES,
)


# ======================================================================
# 1. feature flag（§5.2-1）
# ======================================================================
def test_flag_default_is_legacy():
    """未设置/空串 ⇒ legacy（**默认不改变现有行为**）。"""
    assert flags.orchestrator_mode(None) == "legacy"
    assert flags.orchestrator_mode("") == "legacy"
    assert flags.use_graph(None) is False


def test_flag_reads_graph_case_insensitively():
    assert flags.orchestrator_mode("graph") == "graph"
    assert flags.orchestrator_mode(" GRAPH ") == "graph"
    assert flags.use_graph("graph") is True


def test_flag_invalid_value_falls_back_not_raises():
    """非法值**不得抛异常**（拼错的 env 不该把服务打挂），但必须能被观测到回落。"""
    assert flags.orchestrator_mode("banana") == "legacy"
    mode, fell_back = flags.orchestrator_mode_source("banana")
    assert mode == "legacy" and fell_back is True
    # 合法值不算回落
    assert flags.orchestrator_mode_source("graph") == ("graph", False)


# ======================================================================
# 2. 条件边判据 —— 必须是可离线测的纯函数（§5.1 那个分支）
# ======================================================================
def test_resolve_reporting_period_detects_incomplete():
    """财报缺失 ⇒ False（走 fallback）。"""
    assert g.resolve_reporting_period({}) is False
    assert g.resolve_reporting_period({"income": {}}) is False
    assert g.resolve_reporting_period(
        {"income": {"revenue": None}, "balance": {}, "cashflow": {}}) is False


def test_resolve_reporting_period_detects_complete():
    """三表关键字段齐 ⇒ True（走 analyze）。"""
    complete = {
        "income": {"revenue": 1.0, "net_profit": 2.0},
        "balance": {"total_assets": 3.0},
        "cashflow": {"operating_cashflow": 4.0},
    }
    assert g.resolve_reporting_period(complete) is True


def test_resolve_reporting_period_ignores_non_dict():
    """脏数据不得抛异常（评测/上游都可能给 None）。"""
    assert g.resolve_reporting_period(None) is False
    assert g.resolve_reporting_period("not-a-dict") is False


# ======================================================================
# 3. 图结构（§5.1 六个节点）
# ======================================================================
def test_graph_exposes_all_nodes():
    """六个节点一个都不能少（漏一个条件边就断链）。"""
    compiled = g.build_graph()
    nodes = set(getattr(compiled, "nodes", {}) or {})
    for name in NODE_NAMES:
        assert name in nodes, f"缺少节点 {name}；实际 {sorted(nodes)}"


def test_graph_nodes_are_the_designed_six():
    """不得悄悄多出节点（多出来的节点意味着设计漂移）。"""
    compiled = g.build_graph()
    nodes = {n for n in (getattr(compiled, "nodes", {}) or {}) if not n.startswith("__")}
    assert nodes == set(NODE_NAMES), f"节点集合与 §5.1 不一致：{sorted(nodes)}"


# ======================================================================
# 4. 条件边分流（端到端，节点依赖全部注入）
# ======================================================================
def _stub_all(monkeypatch, *, financials, **kw):
    """把所有外部依赖替换为纯桩；`trace` 由真实节点函数产生。"""
    monkeypatch.setattr(g, "_fetch_quote", lambda code: {"code": code, "price": 1.0})
    monkeypatch.setattr(g, "_fetch_financials", lambda code: financials)
    monkeypatch.setattr(g, "_fetch_moneyflow", lambda code: {"main_net": 0.0})
    monkeypatch.setattr(g, "_run_engines", lambda state: {"fundamental": "ok"})
    monkeypatch.setattr(g, "_retrieve_evidence",
                        lambda code: {"items": [{"chunk_id": 1, "source": "doc", "text": "t"}],
                                      "level": "weak", "note": "证据不足档"})
    monkeypatch.setattr(g, "_synthesize_report", lambda state: "报告正文")
    for k, v in kw.items():
        monkeypatch.setattr(g, k, v)


def test_branch_fallback_when_financials_incomplete(monkeypatch):
    """财报缺 ⇒ `branch == fallback`，且**不得**调用 analyze 引擎。"""
    called = {"engines": 0}

    def _engines(state):
        called["engines"] += 1
        return {"fundamental": "ok"}

    _stub_all(monkeypatch, financials={}, _run_engines=_engines)
    out = g.run_diagnosis_graph("600519", thread_id="t-fallback")

    assert out["branch"] == BRANCH_FALLBACK
    assert called["engines"] == 0, "fallback 分支不应跑 6 引擎"


def test_branch_analyze_when_financials_complete(monkeypatch):
    """财报齐 ⇒ `branch == analyze`，引擎被调用，且有检索证据进报告。"""
    called = {"engines": 0}

    def _engines(state):
        called["engines"] += 1
        return {"fundamental": "ok", "minefield": "clean"}

    complete = {"income": {"revenue": 1.0}, "balance": {"total_assets": 1.0},
                "cashflow": {"operating_cashflow": 1.0}}
    _stub_all(monkeypatch, financials=complete, _run_engines=_engines)
    out = g.run_diagnosis_graph("600519", thread_id="t-analyze")

    assert out["branch"] == BRANCH_ANALYZE
    assert called["engines"] >= 1
    assert out["report"]


# ======================================================================
# 5. 人审轮次硬上限（§5.2-3：防死循环烧 token）
# ======================================================================
def test_review_round_is_capped_at_max(monkeypatch):
    """连续 `revise` 到上限后必须停（`review_round <= MAX_REVIEW_ROUNDS`）。"""
    _stub_all(monkeypatch, financials={"income": {"revenue": 1.0}})

    out = g.run_diagnosis_graph("600519", thread_id="t-cap")
    rounds = 0
    # 每次都给「要求修改」，看它是否会在上限处停下来
    while out.get("review_status") == "revise" and rounds <= MAX_REVIEW_ROUNDS + 2:
        rounds += 1
        out = g.run_diagnosis_graph("600519", thread_id="t-cap",
                                    resume="再改一版", review="revise")
    assert rounds <= MAX_REVIEW_ROUNDS + 1, f"人审轮次未受控：跑了 {rounds} 轮"
    assert (out.get("review_round") or 0) <= MAX_REVIEW_ROUNDS


# ======================================================================
# 6. 检查点 / 断点续跑（C3 的基础：已完成节点不重跑）
# ======================================================================
def test_completed_nodes_are_not_rerun_on_resume(monkeypatch, tmp_path):
    """第二次进程带着同一 `thread_id` 续跑时，**已完成的节点不得重跑**（trace 只追加新节点）。"""
    db = str(tmp_path / "cp.db")
    fetched = {"n": 0}

    def _q(code):
        fetched["n"] += 1
        return {"code": code, "price": 1.0}

    _stub_all(monkeypatch, financials={"income": {"revenue": 1.0}}, _fetch_quote=_q)

    cp, closer = g.make_checkpointer(db)
    try:
        out1 = g.run_diagnosis_graph("600519", thread_id="resume-1", checkpointer=cp)
        first_trace = list(out1.get("trace") or [])
        n_after_first = fetched["n"]

        # 续跑（同一 thread_id）：已完成节点不应再取数
        g.run_diagnosis_graph("600519", thread_id="resume-1", checkpointer=cp,
                              resume="ok", review="approve")
        assert fetched["n"] == n_after_first, (
            "续跑时 `data_fetch` 被重跑（取数次数增加）")
        assert "data_fetch" in first_trace
    finally:
        closer()


def test_checkpoint_db_is_created(tmp_path):
    """检查点确实落到 SQLite 文件（§10.5-1），不是内存假象。"""
    import os

    db = str(tmp_path / "cp2.db")
    cp, closer = g.make_checkpointer(db)
    try:
        assert os.path.exists(db), "checkpointer 未创建 SQLite 文件"
    finally:
        closer()


# ======================================================================
# 7. 薄封装的「真实引用」锁
# ======================================================================
def test_thin_adapters_reference_real_symbols():
    """薄封装引用的底层符号必须真实存在。

    ⚠️ 2026-10-02 真实踩到：离线测试把 `_fetch_financials` monkeypatch 掉了，
    于是"引用了不存在的模块 `data.financial_api`"这件事**全绿通过**——
    直到 Step 4 真跑全链路才会炸。这正是「评测从不经过被测对象」的同族盲区。
    所以本锁**双向**：① 底层符号确实存在；② 薄封装源码里确实引用了它。
    """
    import importlib
    import inspect

    for mod, fn in (
        ("data.stock_api", "get_stock_info"),
        ("data.stock_api", "get_stock_moneyflow"),
        ("data.stock_fundamentals", "get_stock_financial_data"),
        ("data.diagnosis", "build_diagnosis_payload"),
        ("utils.rag.retrieve", "retrieve_docs"),
    ):
        m = importlib.import_module(mod)
        assert hasattr(m, fn), f"底层符号不存在：{mod}.{fn}"

    src = inspect.getsource(g)
    for needle in (
        "from data.stock_api import get_stock_info",
        "from data.stock_api import get_stock_moneyflow",
        "from data.stock_fundamentals import get_stock_financial_data",
        "from data.diagnosis import build_diagnosis_payload",
        "from utils.rag.retrieve import retrieve_docs",
    ):
        assert needle in src, f"薄封装未引用底层符号：{needle}"


def test_graph_node_names_helper_matches_design():
    """`graph_node_names()` 必须屏蔽 langgraph 版本差异，返回设计里的六个节点。"""
    assert g.graph_node_names(g.build_graph()) == sorted(NODE_NAMES)


# ======================================================================
# 8b. 检索适配器：JSON **字符串** 必须能解析出证据
# ======================================================================
def test_retrieve_adapter_parses_json_string(monkeypatch):
    """`retrieve_docs` 返回的是 JSON 字符串 —— 适配器必须解析，否则 evidence 恒为 0。

    ⚠️ 2026-10-02 C2 实跑抓到：原适配器只判 dict/list ⇒ 字符串落进 else ⇒ evidence 恒 0
    （检索其实完全正常）。这是「看起来在岗、其实不在岗」的第二次实证，故上锁。
    """
    import json as _json

    payload = _json.dumps({
        "query": "600519 最新公告",
        "code": "600519",
        "message": "检索到的内容与问题只有字面弱相关（证据不足档）",
        "evidence_level": "weak",
        "results": [{"rank": 1, "score": 0.0286, "chunk_id": 23, "text": "贵州茅台 600519 主要会计数据"}],
    }, ensure_ascii=False)

    import utils.rag.retrieve as rr
    monkeypatch.setattr(rr, "retrieve_docs", lambda *a, **kw: payload)

    out = g._retrieve_evidence("600519")
    assert out["level"] == "weak"
    assert "证据不足档" in out["note"]
    assert len(out["items"]) == 1
    assert out["items"][0]["chunk_id"] == 23
    assert "贵州茅台" in out["items"][0]["text"]


def test_retrieve_adapter_survives_bad_payload(monkeypatch):
    """检索返回脏数据/异常 ⇒ 空证据 + 可读说明，**不得抛**（链路不能因此断掉）。"""
    import utils.rag.retrieve as rr

    monkeypatch.setattr(rr, "retrieve_docs", lambda *a, **kw: "not-json{{{")
    out = g._retrieve_evidence("600519")
    assert out["items"] == [] and out["note"]

    def _boom(*a, **kw):
        raise RuntimeError("检索炸了")

    monkeypatch.setattr(rr, "retrieve_docs", _boom)
    out = g._retrieve_evidence("600519")
    assert out["items"] == [] and out["note"]


def test_retrieve_node_writes_level_and_note_into_state(monkeypatch):
    """检索档位与弃权说明必须进 state（否则报告无法如实标注证据强度）。"""
    _stub_all(monkeypatch, financials={"income": {"revenue": 1.0}})
    out = g.run_diagnosis_graph("600519", thread_id="t-evidence-meta")
    assert out["evidence_level"] == "weak"
    assert "证据不足档" in (out["evidence_note"] or "")
    assert len(out["evidence"]) == 1


def test_trace_does_not_accumulate_across_fresh_invokes(monkeypatch, tmp_path):
    """同一 thread_id 连续两次 `invoke`（无 resume）⇒ trace 必须是**干净的一轮**，不得翻倍。

    ⚠️ 2026-10-02 C2 实跑抓到：`operator.add` reducer 会让新一轮传入的 `trace: []`
    被**追加**而不是重置 ⇒ 轨迹翻倍、C3 赖以作证的节点序列不可读。
    改为节点显式拼接后，新 invoke 的 `trace: []` 正常覆盖。
    """
    db = str(tmp_path / "cp-trace.db")
    _stub_all(monkeypatch, financials={"income": {"revenue": 1.0}})
    cp, closer = g.make_checkpointer(db)
    try:
        o1 = g.run_diagnosis_graph("600519", thread_id="trace-t1", checkpointer=cp)
        o2 = g.run_diagnosis_graph("600519", thread_id="trace-t1", checkpointer=cp)
    finally:
        closer()

    t1, t2 = list(o1["trace"]), list(o2["trace"])
    assert t1.count("data_fetch") == 1, f"首轮 trace 异常：{t1}"
    assert t2.count("data_fetch") == 1, f"第二次 invoke 的 trace 累积了：{t2}"
    assert len(t2) <= len(NODE_NAMES) + 1, f"trace 超出单轮规模：{t2}"


# ======================================================================
# 8. 服务层分流（§5.2-1：flag 并存，**默认 legacy 行为不变**）
# ======================================================================
def test_service_rejects_bad_code():
    from services import diagnosis_service as svc

    for bad in ("", None, "60051", "6005199", "abcdef"):
        out = svc.get(bad)
        assert out.get("ok") is False and "error" in out, f"未拒绝非法代码 {bad!r}"


def _spy(svc, monkeypatch):
    hits = {"legacy": 0, "graph": 0}
    monkeypatch.setattr(svc, "_get_legacy",
                        lambda c: (hits.__setitem__("legacy", hits["legacy"] + 1),
                                   {"ok": True, "code": c})[1])
    monkeypatch.setattr(svc, "_get_via_graph",
                        lambda c: (hits.__setitem__("graph", hits["graph"] + 1),
                                   {"ok": True, "code": c})[1])
    return hits


def test_service_default_routes_to_legacy(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR", raising=False)
    from services import diagnosis_service as svc

    hits = _spy(svc, monkeypatch)
    assert svc.get("600519")["ok"] is True
    assert hits == {"legacy": 1, "graph": 0}, "未设 flag 时必须走 legacy"


def test_service_graph_mode_routes_to_graph(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc

    hits = _spy(svc, monkeypatch)
    svc.get("600519")
    assert hits == {"legacy": 0, "graph": 1}


def test_service_invalid_flag_routes_to_legacy(monkeypatch):
    """拼错的 flag ⇒ 回落 legacy（不得 500，也不得抛异常）。"""
    monkeypatch.setenv("ORCHESTRATOR", "banana")
    from services import diagnosis_service as svc

    hits = _spy(svc, monkeypatch)
    svc.get("600519")
    assert hits == {"legacy": 1, "graph": 0}


def test_graph_path_keeps_response_contract(monkeypatch):
    """graph 路径必须保持响应契约：同一个 6 引擎 payload 形状 + `ok`，元信息挂 `_orchestrator`。"""
    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc
    import utils.orchestrator.graph as gmod

    fake_payload = {"code": "600519", "stock_info": {"price": 1.0}, "errors": []}
    monkeypatch.setattr(gmod, "run_diagnosis_graph", lambda code, thread_id=None, **kw: {
        "engines": fake_payload,
        "branch": BRANCH_ANALYZE,
        "trace": ["data_fetch", "analyze"],
        "review_status": "pending",
        "evidence": [{"text": "t"}],
        "report": "报告",
        "errors": [],
    })

    out = svc.get("600519")
    assert out["ok"] is True
    assert out["code"] == "600519" and out["stock_info"] == {"price": 1.0}
    assert out["_orchestrator"]["mode"] == "graph"
    assert out["_orchestrator"]["branch"] == BRANCH_ANALYZE
    assert out["_orchestrator"]["evidence_count"] == 1
    assert out["_orchestrator"]["report_chars"] == 2


def test_graph_path_degrades_when_graph_raises(monkeypatch):
    """图内部抛异常 ⇒ 返回可读错误（`ok=False`），**不得 500**。"""
    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc
    import utils.orchestrator.graph as gmod

    def _boom(*a, **kw):
        raise RuntimeError("图炸了")

    monkeypatch.setattr(gmod, "run_diagnosis_graph", _boom)
    out = svc.get("600519")
    assert out["ok"] is False and "error" in out
