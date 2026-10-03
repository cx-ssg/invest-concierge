# -*- coding: utf-8 -*-
"""M3 编排层回归锁 —— §5.3 验收 **C1**（节点 / 条件边 / 重试 / 轮次上限 / 检查点）。

⚠️ 全部**离线**：所有外部取数薄封装（`graph._fetch_*` / `_run_engines` / `_retrieve_evidence`）
   用 monkeypatch 注入 ⇒ 不触网、不依赖 `kb.db`、不调 LLM。
⚠️ 设计依据：`docs/COVERAGE_DESIGN.md` §5.1（图结构）/ §5.2（四条决策）/ §10.5（实现要点）。
"""
import inspect
import os
import sys

import pytest

from utils.orchestrator import flags
from utils.orchestrator import adapters as ad
from utils.orchestrator import graph as g
from utils.orchestrator import nodes as N
from utils.orchestrator.state import (
    BRANCH_ANALYZE,
    BRANCH_FALLBACK,
    NODE_ANALYZE,
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
    monkeypatch.setattr(ad, "_fetch_quote", lambda code: {"code": code, "price": 1.0})
    monkeypatch.setattr(ad, "_fetch_financials", lambda code: financials)
    monkeypatch.setattr(ad, "_fetch_moneyflow", lambda code: {"main_net": 0.0})
    monkeypatch.setattr(ad, "_run_engines", lambda state: {"fundamental": "ok"})
    monkeypatch.setattr(ad, "_retrieve_evidence",
                        lambda code: {"items": [{"chunk_id": 1, "source": "doc", "text": "t"}],
                                      "level": "weak", "note": "证据不足档"})
    monkeypatch.setattr(ad, "_synthesize_report", lambda state: "报告正文")
    for k, v in kw.items():
        monkeypatch.setattr(ad, k, v)   # 节点走 adapters 晚绑定


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
def test_after_review_forces_end_at_cap():
    """条件边层：`revise` 但已达上限 ⇒ 必须 END（不得再回 analyze）。

    ⚠️ 2026-10-02 审计 F3：原上限测试是**死测试**（首轮 `pending` ⇒ 循环 0 次迭代），
    把两层保险同时改坏仍全绿。这里对**条件边本身**下断言 ⇒ 上限反转会被抓住。
    """
    assert g._after_review({"review_status": "revise", "review_round": 0}) == NODE_ANALYZE
    assert g._after_review({"review_status": "revise",
                            "review_round": MAX_REVIEW_ROUNDS}) == "__end__"
    assert g._after_review({"review_status": "approved",
                            "review_round": 1}) == "__end__"


def test_human_review_node_cap_does_not_interrupt(monkeypatch):
    """节点层：round 已达上限时**不得调用 `interrupt()`**，直接判 approved。

    ⚠️ 用一个「一被调用就炸」的 interrupt 来证明它没被碰过 —— 这样节点层的保险
    被停用（M4 变异）时本条必红。
    """
    import langgraph.types as lt

    def _boom(*a, **kw):
        raise AssertionError("已达上限却仍调用了 interrupt()")

    monkeypatch.setattr(lt, "interrupt", _boom)
    monkeypatch.setattr(N, "interrupt", _boom)

    # 入口判断：round=MAX-1 时"本轮之后即达上限" ⇒ 必须直接收口
    out = g._node_human_review({"stock_code": "600519", "trace": [],
                                "review_round": MAX_REVIEW_ROUNDS - 1,
                                "errors": []})
    assert out["review_status"] == "approved", out
    assert "上限" in " ".join(out.get("errors") or [])

    # 未到上限时必须**继续** interrupt（否则人审形同虚设）
    seen = {"n": 0}

    def _capture(payload):
        seen["n"] += 1
        return {"review": "approve"}

    monkeypatch.setattr(N, "interrupt", _capture)
    out2 = g._node_human_review({"stock_code": "600519", "trace": [],
                                 "review_round": 0, "errors": []})
    assert seen["n"] == 1, "未到上限却没有 interrupt"
    assert out2["review_status"] == "approved"


def test_review_loop_stops_even_if_user_keeps_asking_revise(monkeypatch):
    """端到端：用户**反复**要求修改，轮次也必须在上限处停住（不无限循环）。

    ⚠️ 修 2026-10-02 审计 F3 的第二半：原循环条件只看 `=="revise"`，
    而首轮返回 `pending` ⇒ 一次都没进循环。改为「pending 或 revise 都要继续推进」。
    """
    db = str(os.path.join(os.environ.get("TEMP", "."), "m3_cap_test.db"))
    if os.path.exists(db):
        os.remove(db)
    _stub_all(monkeypatch, financials={"income": {"revenue": 1.0}})
    cp, closer = g.make_checkpointer(db)
    observed = []
    try:
        out = g.run_diagnosis_graph("600519", thread_id="cap-loop", checkpointer=cp)
        observed.append(out.get("review_status"))
        for _ in range(MAX_REVIEW_ROUNDS + 3):
            if out.get("review_status") == "approved":
                break
            out = g.run_diagnosis_graph("600519", thread_id="cap-loop", checkpointer=cp,
                                        resume="再改一版", review="revise")
            observed.append(out.get("review_status"))
    finally:
        closer()

    assert observed[0] == "pending", f"首轮应为 pending（原测试死在这里）：{observed}"
    assert out.get("review_status") == "approved", f"未在上限处收敛到 approved：{observed}"
    assert int(out.get("review_round") or 0) <= MAX_REVIEW_ROUNDS, \
        f"轮次越界：{out.get('review_round')}"
    assert len(observed) <= MAX_REVIEW_ROUNDS + 2, f"循环次数异常：{observed}"


# ======================================================================
# 9b. F2：适配层「真实形态」契约锁（不再 monkeypatch 掉被测对象）
# ======================================================================
def test_fetch_financials_normalizes_dataframe_frames(monkeypatch):
    """`_fetch_financials` 必须把三表 DataFrame 归一成**可序列化摘要**并能被判据采信。

    ⚠️ 2026-10-02 审计 F1（阻断）/F2：原先取的是 `stock_fundamentals.get_stock_financial_data`
    （扁平标量 dict，**无三表键**）⇒ 判据恒 False ⇒ analyze 生产不可达。
    本条**直接喂真 DataFrame**（不 monkeypatch `_fetch_financials` 本身），
    所以「适配层写成 no-op」（M5 变异）会立刻红。
    """
    import pandas as pd
    import data.financial_report as fr

    good = pd.DataFrame({"营业收入": [90703260964.48], "净利润": [44516880421.86]})
    monkeypatch.setattr(fr, "get_financial_reports",
                        lambda code: {"profit_sheet": good,
                                      "balance_sheet": good,
                                      "cashflow_sheet": good})

    fin = g._fetch_financials("600519")
    assert set(fin.keys()) == {"income", "balance", "cashflow"}, fin
    for k in ("income", "balance", "cashflow"):
        assert fin[k].get("rows") == 1, f"{k} 未归一出行数：{fin[k]}"
    # ⚠️ 必须是可序列化的（DataFrame 直接进 state 会在检查点序列化时出问题）
    import json
    json.dumps(fin)
    assert g.resolve_reporting_period(fin) is True


def test_fetch_financials_empty_frames_are_not_complete(monkeypatch):
    """三表全 None（真实环境常见：数据源挂）⇒ 判据 False ⇒ **正确降级**（这才是真的降级）。"""
    import data.financial_report as fr

    monkeypatch.setattr(fr, "get_financial_reports",
                        lambda code: {"profit_sheet": None, "balance_sheet": None,
                                      "cashflow_sheet": None})
    fin = g._fetch_financials("600519")
    assert g.resolve_reporting_period(fin) is False


def test_branch_analyze_reachable_with_real_shaped_source(monkeypatch):
    """**关键回归**：数据源「可用」时，链路必须真的走到 `analyze`（而不是永久降级）。

    ⚠️ 这是 F1 的直接反例锁：修复前，即便喂完美数据源也恒 fallback。
    """
    import pandas as pd
    import data.financial_report as fr

    good = pd.DataFrame({"营业收入": [1.0]})
    monkeypatch.setattr(fr, "get_financial_reports",
                        lambda code: {"profit_sheet": good, "balance_sheet": good,
                                      "cashflow_sheet": good})
    engines_called = {"n": 0}

    def _engines(state):
        engines_called["n"] += 1
        return {"fundamental": "ok"}

    monkeypatch.setattr(ad, "_fetch_quote", lambda code: {"code": code, "price": 1.0})
    monkeypatch.setattr(ad, "_fetch_moneyflow", lambda code: {})
    monkeypatch.setattr(ad, "_run_engines", _engines)
    monkeypatch.setattr(ad, "_retrieve_evidence",
                        lambda code: {"items": [], "level": "", "note": ""})
    monkeypatch.setattr(ad, "_synthesize_report", lambda state: "报告")

    out = g.run_diagnosis_graph("600519", thread_id="real-shape-analyze")
    assert out["branch"] == BRANCH_ANALYZE, f"完美数据源仍降级 ⇒ 永久降级未修：{out['branch']}"
    assert engines_called["n"] >= 1


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
        ("data.financial_report", "get_financial_reports"),
        ("data.diagnosis", "build_diagnosis_payload"),
        ("utils.rag.retrieve", "retrieve_docs"),
    ):
        m = importlib.import_module(mod)
        assert hasattr(m, fn), f"底层符号不存在：{mod}.{fn}"

    src = inspect.getsource(ad)
    for needle in (
        "from data.stock_api import get_stock_info",
        "from data.stock_api import get_stock_moneyflow",
        "from data.financial_report import get_financial_reports",
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


def _stub_graph_sources(monkeypatch):
    """把真图的外部数据源桩在 **`adapters`** 上（节点经 `adapters.X` 晚绑定）。

    ⚠️ C-R1（审计 C-F3）：旧版这个测试把桩打在 `graph` 模块
    （`monkeypatch.setattr(gmod, "_retrieve_evidence", ...)`），而 `_node_retrieve`
    实际调用的是 `adapters._retrieve_evidence`（`nodes.py:148`）⇒ 打桩**完全无效**，
    真检索照跑：`retrieve_docs(无 db_path)` → `store.get_conn(None)` →
    默认 `config._DATA_DIR/kb.db` —— 即**读生产语料**，且在无库的机器上会**新建空库**。
    `graph.py` 顶部的 re-export 只是向后兼容，不是打桩落点（见其文件头注释）。
    """
    monkeypatch.setattr(ad, "_fetch_quote", lambda code: {"code": code})
    monkeypatch.setattr(ad, "_fetch_financials", lambda code: {})
    monkeypatch.setattr(ad, "_fetch_moneyflow", lambda code: {})
    monkeypatch.setattr(ad, "_run_engines", lambda state: {})
    monkeypatch.setattr(ad, "_retrieve_evidence",
                        lambda code: {"items": [], "level": "", "note": ""})
    monkeypatch.setattr(ad, "_synthesize_report", lambda state: "报告")


def test_graph_response_keys_match_legacy_on_reachable_branch(monkeypatch):
    """**不打桩 `run_diagnosis_graph`** 的契约测试：走真图（只桩化数据源）时，
    graph 响应键集必须与 legacy 逐字一致。

    ⚠️ 2026-10-02 hermes 审计 A7：原契约测试把 `run_diagnosis_graph` 整个打桩
    ⇒ 只测了映射代码、不测**真实可达分支**（fallback 只返回 5 个键，前端 13 个字段全缺）。
    ⚠️ 2026-10-03 C-R1 审计 C-F3：桩必须打在 `adapters`（见 `_stub_graph_sources`）。
    """
    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc
    from data.diagnosis import empty_diagnosis_payload

    _stub_graph_sources(monkeypatch)

    out = svc.get("600519")
    expected = set(empty_diagnosis_payload("600519").keys()) | {"ok", "_orchestrator"}
    missing = expected - set(out.keys())
    assert not missing, f"graph 响应缺失键（前端契约会断）：{sorted(missing)}"
    assert out["ok"] is True
    assert out["_orchestrator"]["branch"] == "fallback", out["_orchestrator"]


def _kb_fingerprint(path):
    """生产 kb.db 的指纹；文件不存在返回 None（区分「没被创建」与「被改了」）。"""
    if not os.path.exists(path):
        return None
    import hashlib
    with open(path, "rb") as fh:
        digest = hashlib.sha1(fh.read()).hexdigest()
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns, digest)


def test_graph_offline_case_does_not_open_production_kb(monkeypatch):
    """C-R1（C-F3）隔离断言：离线用例不得在生产路径**创建/读取/修改** kb.db。

    判据有两层（任一层破都算失败）：
    ① `utils.rag.store.get_conn` 被调用过 —— 只要调用，无论 path 是否显式，
       都说明离线桩没盖住检索层（历史 bug 就是漏打了 `adapters`）；
       spy 直接拒绝打开，保证本用例自身**不会**去碰生产库。
    ② 生产 kb.db 的 (size, mtime_ns, sha1) 指纹前后不变（不存在则必须仍不存在）
       —— 覆盖「无库机器上留下 4096 B 空库」这一形态。
    """
    from utils.rag import store as rag_store

    prod_path = rag_store.db_path(None)          # 生产默认路径 = config._DATA_DIR/kb.db
    before = _kb_fingerprint(prod_path)

    opened = []

    def _refuse(path=None):
        opened.append(path)
        raise RuntimeError("测试隔离：离线用例试图打开 RAG 库 %r" % (path or prod_path,))

    monkeypatch.setattr(rag_store, "get_conn", _refuse)

    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc

    _stub_graph_sources(monkeypatch)
    out = svc.get("600519")

    assert out["ok"] is True
    assert opened == [], (
        "离线用例打开了 RAG 库（path=%r）—— 桩没打在 adapters 上，会读生产语料" % (opened,))
    assert _kb_fingerprint(prod_path) == before, (
        "离线用例改动了生产 kb.db：%s" % prod_path)


def test_empty_payload_is_single_source_of_truth(monkeypatch):
    """键骨架必须是**单一事实源**：legacy 与 graph 两条路径共用同一份键集。"""
    from data.diagnosis import empty_diagnosis_payload
    import data.diagnosis as diag

    keys = set(empty_diagnosis_payload("600519").keys())
    src = inspect.getsource(diag)
    assert "empty_diagnosis_payload(stock_code)" in src, "legacy 路径未复用骨架"
    assert "stock_info" in keys and "moat" in keys and "percentile" in keys
    assert len(keys) == 18, f"骨架键数异常：{len(keys)} -> {sorted(keys)}"


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


# ======================================================================
# F4 · 人审 resume 通道（服务层 + 路由）
# ======================================================================
def test_review_rejects_bad_decision(monkeypatch):
    """decision 只允许 approve / revise；非法值必须给可读错误（不是悄悄通过）。"""
    monkeypatch.delenv("ORCHESTRATOR", raising=False)
    from services import diagnosis_service as svc

    for bad in ("", None, "ok", "同意", "yes"):
        out = svc.review("600519", bad)
        assert out.get("ok") is False, f"非法 decision {bad!r} 未被拒：{out}"
        assert "decision" in out.get("error", "")


def test_review_rejects_bad_code(monkeypatch):
    from services import diagnosis_service as svc

    for bad in ("", None, "60051", "abcdef"):
        out = svc.review(bad, "approve")
        assert out.get("ok") is False and "error" in out, f"未拒绝 {bad!r}"


def test_review_in_legacy_mode_explains(monkeypatch):
    """legacy 编排下没有人审环节 —— 必须**明确说明**，而不是假装成功。"""
    monkeypatch.delenv("ORCHESTRATOR", raising=False)
    from services import diagnosis_service as svc

    out = svc.review("600519", "approve")
    assert out.get("ok") is False
    assert out.get("mode") == "legacy"
    assert "legacy" in out.get("error", "")


def test_review_resumes_suspended_graph(monkeypatch):
    """graph 模式：`review(approve)` 必须真的把挂起的图推进到 approved 且返回同形 payload。"""
    monkeypatch.setenv("ORCHESTRATOR", "graph")
    from services import diagnosis_service as svc
    from data.diagnosis import empty_diagnosis_payload
    import utils.orchestrator.adapters as ad

    # 只桩化数据源，图本身真跑
    monkeypatch.setattr(ad, "_fetch_quote", lambda code: {"code": code})
    monkeypatch.setattr(ad, "_fetch_financials", lambda code: {})
    monkeypatch.setattr(ad, "_fetch_moneyflow", lambda code: {})
    monkeypatch.setattr(ad, "_run_engines", lambda state: {})
    monkeypatch.setattr(ad, "_retrieve_evidence",
                        lambda code: {"items": [], "level": "", "note": ""})
    monkeypatch.setattr(ad, "_synthesize_report", lambda state: "报告")

    first = svc.get("600519")
    assert first["_orchestrator"]["review_status"] == "pending", first["_orchestrator"]

    out = svc.review("600519", "approve")
    assert out["ok"] is True
    assert out["_orchestrator"]["review_status"] == "approved", out["_orchestrator"]
    # 键集仍必须与 legacy 契约一致（A7 的单一事实源）
    expected = set(empty_diagnosis_payload("600519").keys()) | {"ok", "_orchestrator"}
    assert not (expected - set(out.keys())), sorted(expected - set(out.keys()))


def test_review_api_endpoint_is_wired(monkeypatch):
    """POST /api/stocks/{code}/diagnosis/review 必须挂上，且把 body 正确传给服务层。"""
    from fastapi.testclient import TestClient
    from server.main import app
    from services import diagnosis_service as svc

    seen = {}

    def _fake_review(code, decision, note=""):
        seen.update(code=code, decision=decision, note=note)
        return {"ok": True}

    monkeypatch.setattr(svc, "review", _fake_review)
    client = TestClient(app)
    r = client.post("/api/stocks/600519/diagnosis/review",
                    json={"decision": "revise", "note": "再补一段估值"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert seen == {"code": "600519", "decision": "revise", "note": "再补一段估值"}, seen


def test_review_api_rejects_missing_decision():
    """缺 decision ⇒ 422（pydantic 必填），不得进服务层。"""
    from fastapi.testclient import TestClient
    from server.main import app

    client = TestClient(app)
    r = client.post("/api/stocks/600519/diagnosis/review", json={})
    assert r.status_code == 422, r.text
