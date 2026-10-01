# -*- coding: utf-8 -*-
"""`run_hybrid` 的**同文档限额**（diversity）—— 2026-10-02 引入。

**为什么**：语料切到 PDF 全文后（75 → 268 块），`doc#6`「2026 半年报」单篇就占 **198 块（74%）**，
同文档的相似块会占满 top-k，把其它文档的正确答案挤出。实测（holdout 21 条正例）：
  · `rel-0029`「被调查或者侦查的事态发展怎么样了？」gold 被挤到 **rank 8**，top-5 **全是 doc#6 的块**
  · `rel-0026`「今年股东大会什么时候召开？」gold 被挤到 **rank 6**，top-5 里金杜律所文书占 3 块
⇒ `Recall@5` 1.000 → 0.857、`MRR@10` 0.702 → 0.593。

**设计**：按 RRF 顺序取块，但每文档最多 `max_per_doc` 块；**只有当"合规块"不足以填满 `k` 时**
才按原顺序回填 —— 多样性不以"返回变少"为代价（那会降召回），但也不该在候选充足时破坏限额。
"""
import numpy as np

from utils.rag.hybrid import run_hybrid

Q = "营业收入"
TEXT = "贵州茅台 营业收入 同比增长"


def _corpus(n_docs, per_doc):
    """构造 `n_docs` 个文档 × `per_doc` 块，**文本完全相同**
    ⇒ BM25 与向量分数一样，顺序只由稳定排序决定（按构造顺序 ⇒ 文档 1 的块在前）。
    """
    meta = []
    for d in range(1, n_docs + 1):
        for j in range(per_doc):
            meta.append({"chunk_id": d * 100 + j, "doc_id": d, "text": TEXT})
    matrix = np.ones((len(meta), 2), dtype="float32")
    qvec = np.array([1.0, 1.0], dtype="float32")
    return meta, matrix, qvec


def test_run_hybrid_caps_blocks_per_document():
    """候选充足时（5 文档 × 3 块，k=5，限额 2）：
    **每文档不超过 2 块、总数仍为 k、且名额让给了多个文档**。"""
    meta, matrix, qvec = _corpus(n_docs=5, per_doc=3)
    order, _, _ = run_hybrid(Q, matrix, meta, k=5, query_vec=qvec, judge=None, max_per_doc=2)
    docs = [meta[i]["doc_id"] for i in order]
    assert len(order) == 5, "应返回 k=5 块，实得 %d" % len(order)
    assert docs.count(1) <= 2, "doc#1 超过 2 块：%s" % docs
    assert len(set(docs)) >= 3, "应覆盖 ≥3 个文档（多样性），实得 %s" % docs
    assert docs == [1, 1, 2, 2, 3], "期望按限额顺序取，实得 %s" % docs


def test_run_hybrid_no_cap_when_none():
    """`max_per_doc=None` ⇒ **行为与旧版完全一致**（向后兼容，也是 A/B 的对照组）。"""
    meta, matrix, qvec = _corpus(n_docs=5, per_doc=3)
    order, _, _ = run_hybrid(Q, matrix, meta, k=5, query_vec=qvec, judge=None, max_per_doc=None)
    docs = [meta[i]["doc_id"] for i in order]
    assert docs == [1, 1, 1, 2, 2], ("无限额时应按原 RRF 顺序取前 k（本例前 5 个索引 = "
                                     "doc#1 的 3 块 + doc#2 的 2 块），实得 %s" % docs)


def test_run_hybrid_backfills_when_compliant_blocks_insufficient():
    """**候选不足时必须回填**：2 文档（5+1 块）、k=5、限额 2 ⇒ 合规块只有 3 块 < 5
    ⇒ 必须回填到 5（否则多样性会以"返回不足"为代价，那会**降召回**，是更糟的交换）。
    """
    meta = [{"chunk_id": i, "doc_id": 1, "text": TEXT} for i in range(1, 6)]
    meta.append({"chunk_id": 99, "doc_id": 2, "text": TEXT})
    matrix = np.ones((len(meta), 2), dtype="float32")
    qvec = np.array([1.0, 1.0], dtype="float32")
    order, _, _ = run_hybrid(Q, matrix, meta, k=5, query_vec=qvec, judge=None, max_per_doc=2)
    docs = [meta[i]["doc_id"] for i in order]
    assert len(order) == 5, "应回填到 k=5，实得 %d" % len(order)
    assert docs[:3] == [1, 1, 2], "前 3 块应是合规块（doc1×2 + doc2），实得 %s" % docs
    assert docs[3:] == [1, 1], "余下 2 块应是回填的 doc1 块，实得 %s" % docs
