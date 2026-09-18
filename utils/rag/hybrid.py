# -*- coding: utf-8 -*-
"""混合检索：向量 + BM25 → RRF 融合；**证据充分性判据走 EvidenceJudge（SAR + V1）**。

## 2026-09-16 判据重构（两份外部评审 + 主 Agent 实测对账后拍板）

**删除了什么**：`max_sim < min_sim` 整查询闸门。
理由（可复现）：`max_sim` 是**极值统计量** —— 语料越大越容易撞到高分近邻，
误放行概率单调上升；且它衡量的是「话题语言风格接近度」，不是「这块里有没有答案」。
实测 HOLDOUT 组**区间倒挂**（无关 0.6902 > 相关 0.6668）。

**换成什么**：证据判据由 `utils/rag/evidence.py` 的 `EvidenceJudge` 判定
（`SAR` = BM25 分数达成率 + `V1` = 特征词存在率，均为尺度无关量），
实测 TUNING 与 HOLDOUT **两组都干净可分**（3.65× gap）。

**向量退回纯排序**（方案 A 原意）：语义路只提供排名，不再做闸门。

**返回值改为三元组** `(order, rrf_scores, evidence)` —— 调用方据 `evidence.level` 分档：
`none`（弃权）/ `weak`（返回 + 标注证据不足，交 LLM 裁决）/ `strong`（正常返回）。
"""
import numpy as np

from utils.rag.bm25 import BM25Index
from utils.rag.embed import embed_texts
from utils.rag.evidence import LEVEL_NONE  # noqa: F401  （保留供调用方/文档引用）
from utils.rag.tokenize import tokenize

RRF_K = 60
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
    """按分数降序取前 k 个**严格高于** min_score 的下标。

    注意：这里的过滤只用于「不把零分块塞进池里」，
    **不再**承担"相关性闸门"职责（该职责已移交 EvidenceJudge）。
    """
    order = sorted(range(len(scores)), key=lambda i: -float(scores[i]))
    return [i for i in order[:k] if float(scores[i]) > min_score]


def run_hybrid(query, matrix, meta, k=5, pool=None, query_vec=None,
               judge=None, bm25_index=None):
    """返回 `(order, rrf_scores, evidence)`。

    - `judge`：`EvidenceJudge` 实例（**必须用全库构建**，见其 docstring）；None = 跳过判据
    - `bm25_index`：可复用的 `BM25Index`（避免每次查询重建，扩容后是 O(N·L) 的纯 Python 循环）
    - `evidence.level == "none"` → **仍返回候选块**，只标注证据不足（2026-09-17 改，见上）
    - 空查询 / 空语料 → `([], {}, None)`
    """
    if not query or not str(query).strip():
        return [], {}, None
    if matrix is None or len(meta) == 0:
        return [], {}, None

    evidence = judge.assess(query) if judge is not None else None

    # ① 2026-09-17：`none` 档**整体不再清空结果**（原 `return [], {}, evidence` 已删）。
    #    独立审计实测：holdout 21 条正例走裸检索 Recall@5 = **21/21**，但 `rel-0015` 的 gold
    #    排在 **rank 1** 仍被判 `none` → 闸门把**已经拿到的正确证据物理丢掉**。
    #
    # ② 2026-09-18 **分层硬停**（审计 A 提出、我独立复算后采纳）：把「机器强制」找回一部分。
    #    背景：① 之后 `agent_core.AGENT_SYSTEM_PROMPT` 的防幻觉守则（触发条件=「空数据」）
    #    对域外查询**不再触发**，而 `evidence_level` 又没有任何下游消费者 →
    #    A3a 从「机器强制」退化成「模型自觉」，且没有任何指标能检测这种退化。
    #    条件 = `level == none` **且** 与全库**零 bigram 交集**（`bm25max == 0`）。
    #    实测（holdout，无需 embedding）：域外 **5/20** 命中该条件、**误杀正例 0/21**；
    #    `rel-0014`(bm25max=4.57) / `rel-0015`(9.21) 都 > 0 → **不会被硬停**，17 日的修复不回退。
    #    ⚠️ 对 `in_domain_unanswerable` / `near_miss` 无效（各 0/15，词面全是域内词）
    #    —— 那两类只能靠 LLM 判官，不属本闸门职责。
    #    ⚠️ 2026-09-18 第六轮审计二 U8：本条件有**两处**要记住：
    #    ① `level == LEVEL_NONE` 这一项**恒被后一项蕴含** —— `bm25max == 0` ⇒ 所有 token
    #       `df == 0` ⇒ `v1 == 0` 且 `sar == 0` ⇒ level 必为 none。所以「分层硬停」实际
    #       只有**一层**（纯字面交集检查），且它**完全绕开判据**。这里保留 level 判断是
    #       **防御式冗余**：若将来 sar 公式变化使「sar>0」与「bm25max>0」不再等价，这层仍能挡。
    #    ② `is not None` 是必需的（`Evidence.bm25_scores` 默认 **None = 无分数信息**）——
    #       否则没传 scores 的构造点（测试替身/缓存层）会被**误硬停**。
    if (evidence is not None and evidence.bm25_scores is not None
            and not any(evidence.bm25_scores)):
        return [], {}, evidence          # 零字面交集 → 恢复机器强制（A3a）

    matrix = np.asarray(matrix, dtype="float32")
    if query_vec is None:
        query_vec = np.asarray(embed_texts([query])[0], dtype="float32")
    query_vec = np.asarray(query_vec, dtype="float32")

    kk = min(pool, len(meta)) if pool else min(max(k * 20, DEFAULT_POOL_MIN), len(meta))
    sims = _cosine_scores(query_vec, matrix)
    semantic_rank = _top_k(sims, kk, min_score=0.0)     # 只排序，不做闸门

    if bm25_index is None:
        bm25_index = BM25Index([tokenize(m.get("text", "")) for m in meta])
    bm25_rank = _top_k(bm25_index.score(tokenize(query)), kk, min_score=0.0)

    rrf = {}
    for rank, idx in enumerate(semantic_rank):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, idx in enumerate(bm25_rank):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

    order = sorted(rrf.keys(), key=lambda i: -rrf[i])
    return order[:k], rrf, evidence
