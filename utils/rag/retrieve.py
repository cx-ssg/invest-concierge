# -*- coding: utf-8 -*-
"""`retrieve_docs`：M1 私域知识层的 Agent 工具实现。

契约：`docs/COVERAGE_DESIGN.md` §3.2 接入层（第 24 个工具）、§3.3 第 4 条
「无引用 = 不算回答：检索为空时明确回『未找到相关公告』，不允许模型凭空补」。

**2026-09-16 判据重构 + 产线 bug 修复**：
- 证据判据改由 `EvidenceJudge`（SAR + V1）给出，**不再用向量 `max_sim`**；
- ⚠️ **修复产线 bug**：`judge` 必须用**全库**构建。旧实现在按 `code` 过滤 matrix **之后**
  才让 `max_sim` 闸门跑在子集上，而阈值是在全库上定的 → 单标的检索池变小会系统性过度弃权、
  接入第二个标的时反向失真（外部评审读码指出，2026-09-16 确认）。

**2026-09-17 补 `chunk_id`**：返回体此前缺块唯一标识 → ① 前端无法把引用 `[1]` 链回具体段落
（设计 §3.2 的引用渲染）；② 离线评测无法判定 gold 是否落在 top-k（33 条正例被全判「未召回」）。
`chunk_id` 是这两件事的唯一锚点，故补入每条结果。

⚠️ `chunk_id` 就是 `chunks.id`（库内自增），**只在同一个 kb.db 构建内稳定** —— 语料重建后会变。
它可用于**同一次构建内的引用回跳与离线评测**，**不可**跨构建持久化（前端若缓存引用，需带构建标识）。

返回 **JSON 字符串**（与项目既有 23 个工具的统一契约一致）。
"""
import json

from utils.rag import store as rag_store
from utils.rag.evidence import LEVEL_NONE, LEVEL_WEAK, EvidenceJudge
from utils.rag.hybrid import run_hybrid
from utils.rag.tokenize import tokenize

# 无命中时的显式提示：把「没有」这件事说清楚，模型才不会拿训练数据硬编
NO_HIT_MESSAGE = "未找到相关公告或研报 —— 请如实告知用户知识库中没有相关内容，不要凭记忆编造"
# 证据不足档（A3b 的安全网）：结果照给，但明确要求模型谨慎
WEAK_EVIDENCE_NOTE = ("检索到的内容与问题只有字面弱相关（证据不足档）—— 引用前请自行核验；"
                      "若无法支撑回答，请如实说明未找到，不要据此推断")


def retrieve_docs(query, code=None, top_n=5, db_path=None, query_vec=None):
    """检索私域语料（公告 / 研报 / 财报），返回 JSON 字符串。

    - `code`：限定标的（None = 全库检索）。**只影响返回哪些块，不影响判据**（判据恒用全库）
    - `query_vec`：可注入查询向量（单测 / 批量场景复用，避免重复调用 embedding）
    - 返回体含 `evidence_level`（none / weak / strong）与 `evidence`（sar / v1）
    """
    conn = rag_store.get_conn(db_path)
    try:
        meta_all, matrix = rag_store.load_index(conn)
    finally:
        conn.close()

    if not meta_all or matrix is None:
        return _payload(query, code, [], NO_HIT_MESSAGE, LEVEL_NONE)

    # ① 判据：**全库**构建 —— 不受 code 过滤影响（旧实现 bug 的修复点）
    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta_all])

    # ② 检索池：按 code 过滤，决定「从哪些块里挑」
    pool_meta, pool_matrix = meta_all, matrix
    bm25_index = judge.index          # 池 == 全库时可复用，省一次 O(N·L) 构建
    if code:
        keep = [i for i, m in enumerate(meta_all) if m.get("code") == code]
        if not keep:
            return _payload(query, code, [], NO_HIT_MESSAGE, LEVEL_NONE)
        pool_meta = [meta_all[i] for i in keep]
        pool_matrix = matrix[keep]
        # 池 ≠ 全库 → 排序用的 BM25 必须在**池内**重建，否则分数与 meta 索引错位
        bm25_index = None

    order, rrf, evidence = run_hybrid(
        query, pool_matrix, pool_meta, k=top_n, query_vec=query_vec,
        judge=judge, bm25_index=bm25_index,
    )

    results = []
    for rank, idx in enumerate(order, 1):
        m = pool_meta[idx]
        results.append({
            "rank": rank,
            "score": round(float(rrf[idx]), 6),
            "chunk_id": m.get("chunk_id"),
            "text": m.get("text"),
            "title": m.get("title"),
            "url": m.get("url"),
            "source": m.get("source"),
            "published_at": m.get("published_at"),
            "code": m.get("code"),
            "is_table": bool(m.get("is_table")),
        })

    ev_level = evidence.level if evidence is not None else None
    if not results:
        message = NO_HIT_MESSAGE
    elif ev_level == LEVEL_WEAK:
        message = WEAK_EVIDENCE_NOTE
    else:
        message = "命中 {} 条".format(len(results))
    return _payload(query, code, results, message, ev_level, evidence)


def _payload(query, code, results, message, evidence_level=None, evidence=None):
    return json.dumps({
        "query": query,
        "code": code,
        "results": results,
        "message": message,
        "evidence_level": evidence_level,
        "evidence": ({"sar": round(evidence.sar, 4), "v1": round(evidence.v1, 3)}
                     if evidence is not None else None),
    }, ensure_ascii=False, default=str)
