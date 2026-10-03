# -*- coding: utf-8 -*-
"""`retrieve_docs` 工具回归锁（M1 第 24 个 Agent 工具）。

契约来源：`docs/COVERAGE_DESIGN.md` §3.2 接入层 / §3.3 第 4 条
「无引用 = 不算回答：检索为空时明确回『未找到相关公告』，不允许模型凭空补」。
"""
import json

import pytest

from utils.rag import store as rag_store
from utils.rag.retrieve import retrieve_docs


@pytest.fixture()
def kb(tmp_path):
    """建一个含 1 篇文档 / 2 个块（已带向量）的临时 kb.db。"""
    db = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台2026半年报",
        "url": "https://example.com/a", "published_at": "2026-08-30",
    })
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": "贵州茅台上半年营业收入同比增长百分之十五。", "is_table": False},
        {"seq": 1, "text": "比亚迪新能源汽车销量创新高。", "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids, [[1.0, 0.0], [0.0, 1.0]])
    conn.close()
    return db


def test_retrieve_docs_hits_with_citation_fields(kb):
    """命中时必须带齐引用渲染所需字段（设计 §3.2 生成层要求 [1][2] + 来源 + 日期）。"""
    out = json.loads(retrieve_docs("茅台上半年营收", db_path=kb, query_vec=[1.0, 0.0]))
    assert out["results"], "必须至少命中 1 条"
    top = out["results"][0]
    for field in ("text", "title", "url", "published_at", "source", "code"):
        assert field in top, "引用渲染需要字段 {}".format(field)
    assert "茅台" in top["text"]


def test_retrieve_docs_results_carry_chunk_id(kb):
    """每条结果必须带 `chunk_id` —— 这是引用溯源（前端把 [1] 链回具体段落）与
    离线评测 Recall 判定的**唯一锚点**。

    2026-09-17 实测踩到：返回体缺 `chunk_id` 时评测侧无法判定 gold 是否被召回
    （33 条正例全被误判为「未召回」）——真实缺陷，不是测试洁癖。
    """
    out = json.loads(retrieve_docs("茅台上半年营收", db_path=kb, query_vec=[1.0, 0.0]))
    assert out["results"], "必须至少命中 1 条"
    conn = rag_store.get_conn(kb)
    try:
        exist = {row[0] for row in conn.execute("SELECT id FROM chunks")}
    finally:
        conn.close()
    for r in out["results"]:
        assert "chunk_id" in r, "引用溯源需要 chunk_id，缺了前端无法回跳、评测无法判召回"
        assert r["chunk_id"] in exist, \
            "chunk_id 必须能在 chunks 表里查到，实得 {}".format(r["chunk_id"])
    ids = [r["chunk_id"] for r in out["results"]]
    assert len(ids) == len(set(ids)), "同一 chunk_id 不得在结果里重复"


def test_retrieve_docs_none_level_still_returns_candidates(kb):
    """`none` 档**不得物理清空结果**（2026-09-17 产线缺陷修复）。

    独立审计实测证据：holdout 21 条正例走**裸检索**（不带闸门）`Recall@5 = 21/21`，
    但其中 `rel-0015` 的 gold 排在 **rank 1** 仍被判 `none`（`rel-0014` 在 rank 4）
    → 闸门把**已经检索到的正确证据丢掉了**；报告的 `Recall@5 0.905` 与满分的差距
    **全部**由此造成（`over_abstain` 不是小瑕疵，是召回的全部缺口）。

    新契约：`evidence_level` 照算（评测与警示都用它），但候选块照给 ——
    是否采信交由 message 的警示语与模型判断，而不是在检索层物理删除。
    真正「检索为空」时才回 `NO_HIT_MESSAGE`。
    """
    # query 必须与语料**有部分字面交集**（`bm25max > 0`），否则会命中 2026-09-18 新增的
    # 「分层硬停」（零 bigram 交集 → 仍物理回空，见 hybrid.py）。这里要测的是「有交集但判据不通过」。
    out = json.loads(retrieve_docs("茅台明天的股价是多少", db_path=kb, query_vec=[0.6, 0.8]))
    assert out["evidence_level"] == "none", "该查询应判证据不足档"
    assert out["results"], \
        "none 档仍须返回检索到的候选块 —— 物理清空会丢掉已检索到的正确证据（实测有 gold 排 rank 1 的案例）"
    assert "未找到" in out["message"], "警示语仍须明确说「未找到」，防止模型硬答"
    # 审计 B 的 P2-2：none 档候选**不可引用**（设计 §3.3「无引用 = 不算回答」的新执行者）
    assert all(r["url"] is None and r["title"] is None for r in out["results"]), \
        "none 档必须剥掉引用凭据（url/title），否则模型仍可据此产出带引用的回答"


def test_retrieve_docs_hard_stops_on_zero_lexical_overlap(kb):
    """**分层硬停**（2026-09-18 新增）：`none` 档 + 与全库零 bigram 交集 → 仍物理回空。

    动机：撤销无条件硬停后，`agent_core.AGENT_SYSTEM_PROMPT` 的防幻觉守则（触发条件=「空数据」）
    对域外查询不再触发、`evidence_level` 又无下游消费者 → A3a 从「机器强制」退化为「模型自觉」。
    实测（holdout）：该条件挡住 **5/20** 域外负例、**误杀正例 0/21**（`rel-0014/0015` 的 bm25max 均 > 0）。
    """
    out = json.loads(retrieve_docs("量子计算最新进展", db_path=kb, query_vec=[0.6, 0.8]))
    assert out["evidence_level"] == "none"
    assert out["results"] == [], "零字面交集必须回空（恢复机器强制，不依赖模型自觉）"
    assert "未找到" in out["message"]


def test_retrieve_docs_irrelevant_query_returns_no_hit(kb):
    """无关查询（相似度全为 0）必须返回 0 条 + 明确提示，**不得凭空补**。

    ⚠️ 与上一条的区别：这里是**零向量**（语义路全 0 分）→ 被 `_top_k` 的
    `> min_score` 过滤 → 检索**真的**为空。而 `none` 档只标注证据不足，不负责清空。
    """
    out = json.loads(retrieve_docs("量子计算最新进展", db_path=kb,
                                   query_vec=[0.0, 0.0]))
    assert out["results"] == []
    assert "未找到" in out["message"]


def test_retrieve_docs_empty_index_returns_no_hit(tmp_path):
    """空索引（尚未 ingest）不得抛异常，同样返回明确的无结果提示。"""
    db = str(tmp_path / "empty.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    conn.close()
    out = json.loads(retrieve_docs("茅台", db_path=db, query_vec=[1.0, 0.0]))
    assert out["results"] == []
    assert "未找到" in out["message"]


def test_retrieve_docs_filters_by_code(kb):
    """给定 code 时只检索该标的的块（跨标的串味是 RAG 的典型错法）。"""
    out = json.loads(retrieve_docs("销量", db_path=kb, code="000001",
                                   query_vec=[0.0, 1.0]))
    assert out["results"] == [], "code 不匹配时不得返回别的标的的块"


# ==================== F0a：`code=None` 时的查询侧标的识别（情形 B 修复） ====================
# 定因证据（report-F0a.md 第一步，真实链路 5 条带明确标的问题）：
#   LLM **2 条没传 `code`**（五粮液/山西汾酒），其中「五粮液今年一季度的营业收入是多少」
#   的 top-5 = 000858×3 + **600519 + 000568** ⇒ 产线**真实污染**。
# 因此修检索链路：`code=None` 时先识别查询点名的标的，识别到就走**与显式 code 相同的池过滤**。


@pytest.fixture()
def kb_multi(tmp_path):
    """**两条标的**的临时 kb（F0a 回归用）：600519 与 000568 各 2 块，均含「分红」「白酒」。

    标题用 `scripts/rag_ingest.py` 的 `公司名:标题` 形态 —— 公司名靠它推导，
    与真实语料（`贵州茅台:贵州茅台2026年半年度报告`）同构。
    """
    db = str(tmp_path / "kb_multi.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_a = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台:2025年度分红派息实施公告",
        "url": "https://example.com/maotai-div", "published_at": "2026-06-20",
    })
    ids_a = rag_store.insert_chunks(conn, doc_a, [
        {"seq": 0, "text": "贵州茅台2025年度分红派息实施方案：每10股派发现金红利276.24元。",
         "is_table": False},
        {"seq": 1, "text": "贵州茅台白酒业务毛利率保持稳定，直销渠道占比继续提升。",
         "is_table": False},
    ])
    doc_b = rag_store.upsert_document(conn, {
        "code": "000568", "source": "notice", "title": "泸州老窖:2025年度分红派息实施公告",
        "url": "https://example.com/lzlj-div", "published_at": "2026-07-01",
    })
    ids_b = rag_store.insert_chunks(conn, doc_b, [
        {"seq": 0, "text": "泸州老窖2025年度分红派息实施方案：每10股派发现金红利13.0元。",
         "is_table": False},
        {"seq": 1, "text": "泸州老窖白酒业务毛利率同比提升，中高档酒占比提高。",
         "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids_a + ids_b,
                              [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]])
    conn.close()
    return db


def test_auto_scope_ticker_named_query_not_contaminated(kb_multi):
    """**必补回归 ①**：带明确标的的查询（「茅台的分红方案」）top-5 **全为本标的**。

    红（修复前）：`code=None` 走全库池，top-5 里出现 000568 的块
    —— 与真实链路实测形态一致（`五粮液…营收` → 混入 600519/000568）。
    绿（修复后）：查询点名的标的被识别 ⇒ 池收窄到 600519。
    """
    out = json.loads(retrieve_docs("茅台的分红方案是什么", db_path=kb_multi,
                                   query_vec=[0.7, 0.3]))
    assert out["results"], "必须命中本标的的块（不能因为收窄而检索为空）"
    codes = [r["code"] for r in out["results"]]
    assert set(codes) == {"600519"}, \
        "带明确标的的查询不得混入其它标的（实得 codes={}）".format(codes)
    assert out["scope"] == {"mode": "auto", "codes": ["600519"]}, \
        "`scope` 必须如实报告本次实际生效的检索范围（可观测，不静默）"


def test_auto_scope_untargeted_query_still_searches_full_corpus(kb_multi):
    """**必补回归 ②**：无标的查询（「白酒行业对比」）仍须**跨标的**返回。

    这是任务书 §2 的硬约束 —— 不得用「硬过滤」把这类查询锁死在一个标的上。
    """
    out = json.loads(retrieve_docs("白酒行业对比", db_path=kb_multi,
                                   query_vec=[0.7, 0.3]))
    codes = {r["code"] for r in out["results"]}
    assert len(codes) >= 2, \
        "无标的查询必须仍能跨标的返回（实得 codes={}）".format(sorted(codes))
    assert out["scope"] == {"mode": "full", "codes": []}, \
        "未识别到标的 ⇒ scope 必须报 full（全库口径）"


def test_auto_scope_multi_target_query_keeps_both_tickers(kb_multi):
    """点名**两个**标的时取并集：跨标的对比查询不得被单标的硬过滤掉一家。"""
    out = json.loads(retrieve_docs("茅台和泸州老窖的分红方案对比", db_path=kb_multi,
                                   query_vec=[0.7, 0.3]))
    codes = {r["code"] for r in out["results"]}
    assert codes == {"600519", "000568"}, \
        "多标的查询必须同时保留两家（实得 codes={}）".format(sorted(codes))


def test_explicit_code_scope_reported(kb_multi):
    """显式 `code` 的 `scope.mode` 必须是 `explicit`（与自动识别可区分）。"""
    out = json.loads(retrieve_docs("分红方案", db_path=kb_multi, code="000568",
                                   query_vec=[0.0, 1.0]))
    assert out["scope"] == {"mode": "explicit", "codes": ["000568"]}
    assert {r["code"] for r in out["results"]} == {"000568"}

