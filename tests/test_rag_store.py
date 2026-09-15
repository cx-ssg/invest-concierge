# -*- coding: utf-8 -*-
"""kb.db 持久化层回归锁（M1 内核 · store.py）。

重点覆盖**幂等键**：原设计（COVERAGE_DESIGN §3.3 第 5 条）写的是
`(code, source, date)` —— 实测证明它会让**同一天的多份公告互相覆盖**
（2026-09-15 ingest 25 条公告只落库 7 篇，静默丢数据）。
"""
import numpy as np
import pytest

from utils.rag import store as rag_store


@pytest.fixture()
def conn(tmp_path):
    c = rag_store.get_conn(str(tmp_path / "kb.db"))
    rag_store.ensure_schema(c)
    yield c
    c.close()


def _doc(title, url, date="2026-08-30", code="600519"):
    return {"code": code, "source": "notice", "title": title,
            "url": url, "published_at": date}


def test_same_day_multiple_notices_are_distinct_documents(conn):
    """同一天的多份公告必须是**不同**文档（旧键会互相覆盖 → 静默丢数据）。"""
    a = rag_store.upsert_document(conn, _doc("贵州茅台第八届董事会决议", "https://a"))
    b = rag_store.upsert_document(conn, _doc("贵州茅台2026年半年度报告", "https://b"))
    assert a != b, "同一天的两份公告不得共用同一个 document id"
    assert rag_store.stats(conn)["documents"] == 2


def test_same_notice_is_idempotent(conn):
    """同一份公告重复 ingest 不得产生新文档（URL 为唯一标识）。"""
    first = rag_store.upsert_document(conn, _doc("半年度报告", "https://same"))
    second = rag_store.upsert_document(conn, _doc("半年度报告", "https://same"))
    assert first == second
    assert rag_store.stats(conn)["documents"] == 1


def test_chunks_idempotent_by_doc_and_seq(conn):
    """同文档同 seq 重复写只更新不新增。"""
    doc_id = rag_store.upsert_document(conn, _doc("公告A", "https://a"))
    rag_store.insert_chunks(conn, doc_id, [{"seq": 0, "text": "第一版", "is_table": False}])
    rag_store.insert_chunks(conn, doc_id, [{"seq": 0, "text": "第二版", "is_table": False}])
    assert rag_store.stats(conn)["chunks"] == 1
    meta, _ = rag_store.load_index(conn)
    assert meta[0]["text"] == "第二版"


def test_load_index_returns_none_matrix_when_not_embedded(conn):
    """未向量化时 matrix 必须是 None（而不是空数组），调用方据此走降级路径。"""
    doc_id = rag_store.upsert_document(conn, _doc("公告A", "https://a"))
    rag_store.insert_chunks(conn, doc_id, [{"seq": 0, "text": "内容", "is_table": False}])
    meta, matrix = rag_store.load_index(conn)
    assert len(meta) == 1 and matrix is None


def test_save_embeddings_dim_and_roundtrip(conn):
    """向量必须能无损往返（float32 BLOB），且数量不匹配要报错而非静默丢数据。"""
    doc_id = rag_store.upsert_document(conn, _doc("公告A", "https://a"))
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": "甲", "is_table": False},
        {"seq": 1, "text": "乙", "is_table": False},
    ])
    vecs = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    rag_store.save_embeddings(conn, ids, vecs)
    meta, matrix = rag_store.load_index(conn)
    assert matrix.shape == (2, 3)
    assert np.allclose(matrix[0], np.array([0.1, 0.2, 0.3], dtype="float32"), atol=1e-6)

    with pytest.raises(ValueError):
        rag_store.save_embeddings(conn, ids, [[0.1, 0.2, 0.3]])   # 数量不匹配
