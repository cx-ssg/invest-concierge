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


def test_retrieve_docs_irrelevant_query_returns_no_hit(kb):
    """无关查询（相似度全为 0）必须返回 0 条 + 明确提示，**不得凭空补**。"""
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
