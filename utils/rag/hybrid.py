# -*- coding: utf-8 -*-
"""混合检索：向量 + BM25 → RRF 融合。

复用自知识库 `co-planning/scripts/search_wiki.py:503 run_hybrid()`（RRF k=60，
与 `COVERAGE_DESIGN.md` §3.3 写定的参数逐字一致）。两处有意差异：

1. **去掉 domain/pool 白名单维度**（知识库特有，本项目 YAGNI）。
2. **新增 `query_vec` 入参** —— 让单测与批量场景复用已算好的查询向量，
   避免每次检索都调用 embedding（也让内核单测不依赖 ollama 进程）。
"""
import numpy as np

from utils.rag.bm25 import BM25Index
from utils.rag.embed import embed_texts
from utils.rag.tokenize import tokenize

RRF_K = 60
# 绝对下限：max_sim 低于它 → 判定「无关查询」直接返回空（A3 判据）。
# 依据 2026-09-15 实测（bge-m3，5 篇样例）：无关查询的 max_sim 为 0.30~0.38
# （「量子计算」=0.381），相关查询的相关块为 0.72~0.80 —— 原写 0.35 会被 0.381 漏过。
MIN_SIM = 0.45
# 相对阈值：块级取舍只保留 sim >= max_sim * MIN_SIM_RATIO。
# 实测见上：相关块 0.72+ vs 次相关块 0.45，gap 明显 → 单一固定阈值无法兼顾。
MIN_SIM_RATIO = 0.85
DEFAULT_POOL_MIN = 50


def _cosine_scores(query_vec, matrix):
    """返回每个块与查询的余弦相似度。零向量 → 全 0（不抛异常）。"""
    qn = float(np.linalg.norm(query_vec))
    if qn == 0:
        return np.zeros(len(matrix), dtype="float32")
    mn = np.linalg.norm(matrix, axis=1)
    mn[mn == 0] = 1.0
    return np.asarray((matrix @ query_vec) / (mn * qn), dtype="float32")


def _top_k(scores, k, min_score=0.0):
    """按分数降序取前 k 个**达标**下标（`min_score` 为严格下限）。

    ⚠️ 必须过滤：不过滤时池内会「补位」，把与查询毫无关系的块也塞进结果 ——
    2026-09-15 probe K3-b 实测发现（降级路径下 3 个块全部进了 top-3，含完全无关块）。
    """
    order = sorted(range(len(scores)), key=lambda i: -float(scores[i]))
    return [i for i in order[:k] if float(scores[i]) > min_score]


def run_hybrid(query, matrix, meta, k=5, pool=None, query_vec=None,
               min_sim=MIN_SIM, min_sim_ratio=MIN_SIM_RATIO):
    """返回 (order, rrf_scores)。

    - `order`：top-k 块下标（按融合分降序）
    - `rrf_scores`：{下标: RRF 分}
    - 空查询 / 空语料 / `max_sim < min_sim` → `([], {})`（A3「无关查询返回 0 条」）
    - 块级取舍（双判据，2026-09-15 实测修正）：
      语义路保留 `sim > max_sim * min_sim_ratio`；BM25 路保留 `分 > 0`；
      两路皆空同样返回 `([], {})`
    """
    if not query or not str(query).strip():
        return [], {}
    if matrix is None or len(meta) == 0:
        return [], {}

    matrix = np.asarray(matrix, dtype="float32")
    if query_vec is None:
        query_vec = np.asarray(embed_texts([query])[0], dtype="float32")
    query_vec = np.asarray(query_vec, dtype="float32")

    sims = _cosine_scores(query_vec, matrix)
    max_sim = float(np.max(sims)) if len(sims) else 0.0
    if max_sim < min_sim:
        return [], {}   # A3：无关查询不得返回勉强相关内容

    kk = min(pool, len(meta)) if pool else min(max(k * 20, DEFAULT_POOL_MIN), len(meta))
    semantic_rank = _top_k(sims, kk, min_score=max_sim * min_sim_ratio)
    docs = [tokenize(m.get("text", "")) for m in meta]
    bm25_rank = _top_k(BM25Index(docs).score(tokenize(query)), kk)

    rrf = {}
    for rank, idx in enumerate(semantic_rank):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, idx in enumerate(bm25_rank):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

    order = sorted(rrf.keys(), key=lambda i: -rrf[i])
    return order[:k], rrf
