# -*- coding: utf-8 -*-
"""M1 内核切片 · TDD 回归锁（RED 阶段先写）。

契约来源：`docs/M1_KERNEL_SPEC.md` §3（接口签名）/ §4（切块器规格）/ §8（测试清单）。
覆盖四类：中文命中 / RRF 融合顺序 / 空结果 / 表格不切碎。

为什么是这几条（对应 §7 证据）：
- FTS5 中文实测不可用 → 中文命中必须有回归锁，否则又退回"测绿现实坏"。
- RRF 两路必须都进池：只测单路的话，退化成纯 BM25/纯向量也照样绿。
- 空查询返回空：这是 A3「无关查询返回 0 条」的内核级前置。
"""
import numpy as np
import pytest

from utils.rag.bm25 import BM25Index
from utils.rag.chunker import chunk_document
from utils.rag.hybrid import run_hybrid
from utils.rag.tokenize import tokenize


# ==================== 1. 中文 bigram 分词 ====================

def test_tokenize_chinese_bigram():
    """中文连续片段按 bigram 切（这是绕开 FTS5 中文缺陷的核心）。"""
    assert tokenize("贵州茅台") == ["贵州", "州茅", "茅台"]


def test_tokenize_keeps_ascii_words_lowercased():
    """英文/数字词保留并小写；不产生单字噪声。"""
    toks = tokenize("EPS 与 ROE 对比")
    assert "eps" in toks and "roe" in toks
    assert all(len(t) >= 2 for t in toks)


# ==================== 2. BM25 排序 ====================

def test_bm25_ranks_relevant_doc_first():
    docs = [tokenize("贵州茅台上半年营业收入同比增长"), tokenize("比亚迪新能源汽车销量")]
    idx = BM25Index(docs)
    scores = idx.score(tokenize("茅台营收"))
    assert scores[0] > scores[1]


# ==================== 3. RRF 融合（两路都必须进池）====================

def test_rrf_fusion_merges_both_retrievers():
    """精确断言 RRF = 两路贡献之和 —— 纯向量或纯 BM25 实现必然失败。

    ⚠️ 历史：旧版断言 `set(order[:2]) == {0,2}` **在「只保留向量路」时同样成立**，
    无法区分真融合与退化（critic 审计 2026-09-15 指出的自证陷阱，已加固）。

    ⚠️ 2026-09-16 判据重构：语义路**不再做相关性闸门**（只过滤 score==0），
    故两块的融合分对称。
    """
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], dtype="float32")
    meta = [
        {"text": "贵州茅台营业收入"},
        {"text": "比亚迪汽车销量"},
        {"text": "茅台酒毛利率"},
    ]
    order, rrf, ev = run_hybrid(
        "茅台", matrix, meta, k=2,
        query_vec=np.array([1.0, 0.0], dtype="float32"),
    )
    # 语义路：[0(1.0), 2(0.707)]（idx1 sim=0 被过滤）；BM25 路：[2, 0]（idx1 无「茅台」得 0 分）
    # → idx0 = 语义第1 + BM25 第2；idx2 = 语义第2 + BM25 第1 → 两者对称
    # 三种实现的期望值互不相同，故仍可区分：
    #   纯向量 → (1/61, 1/62)；纯 BM25 → (1/62, 1/61)；真融合 → 两者皆 1/61+1/62
    assert rrf[0] == pytest.approx(1 / 61 + 1 / 62), "RRF 必须是两路贡献之和"
    assert rrf[2] == pytest.approx(1 / 62 + 1 / 61)
    assert order == [0, 2]
    assert 1 not in rrf, "两路皆无命中的块必须被彻底排除，不得靠池内补位进结果"
    assert ev is None, "未传 judge 时不应产生 evidence"


def test_irrelevant_chunks_are_excluded():
    """语义为 0 且 BM25 无命中的块不得进入结果。

    回归锁：曾因「池内补位」把与查询毫无关系的块塞进 top-n（2026-09-15 probe K3-b 实测）。
    """
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    meta = [{"text": "贵州茅台营业收入"}, {"text": "比亚迪汽车销量"}]
    order, rrf, _ = run_hybrid("茅台", matrix, meta, k=5,
                               query_vec=np.array([1.0, 0.0], dtype="float32"))
    assert order == [0]
    assert 1 not in rrf


def test_empty_query_returns_empty():
    """空查询 → 空结果（不得凭向量相似度硬凑相关内容）。"""
    matrix = np.array([[1.0, 0.0]], dtype="float32")
    meta = [{"text": "贵州茅台"}]
    order, rrf, _ = run_hybrid("", matrix, meta, k=5,
                               query_vec=np.zeros(2, dtype="float32"))
    assert order == [] and rrf == {}


def test_hybrid_abstains_when_judge_says_none():
    """judge 判定 `none` → 不返回任何结果（A3a 在**检索层**的落地）。

    这是 2026-09-16 判据重构的核心行为：**弃权由 EvidenceJudge（SAR+V1）决定，
    不再由向量 `max_sim` 决定** —— 故此处故意给满向量相似度，验证它不再能左右闸门。
    """
    from utils.rag.evidence import EvidenceJudge
    from utils.rag.tokenize import tokenize

    judge = EvidenceJudge([tokenize("贵州茅台营业收入")])
    matrix = np.array([[1.0, 0.0]], dtype="float32")
    meta = [{"text": "贵州茅台营业收入"}]
    order, rrf, ev = run_hybrid(
        "量子计算最新进展", matrix, meta, k=5,
        query_vec=np.array([1.0, 0.0], dtype="float32"),   # 向量相似度故意给满
        judge=judge,
    )
    assert ev.level == "none"
    assert order == [] and rrf == {}, "证据为 none 时不得返回任何块"


# ==================== 4. 切块器：表格保护 + 硬上限 ====================

def test_chunker_table_not_split():
    """表格整块不切碎（财报数字被切断会串错行）。"""
    rows = ["| 项目 | 数值 |", "| --- | --- |"] + \
           ["| 指标{} | {} |".format(i, i * 100) for i in range(8)]
    chunks = chunk_document("\n".join(rows))
    tables = [c for c in chunks if c["is_table"]]
    assert len(tables) == 1, "10 行连续表格必须合成 1 个表格块"
    assert tables[0]["text"].count("\n") >= 9, "表格行不得被截断"


def test_chunker_oversize_table_splits_and_repeats_header():
    """超长表格（> hard_max*3 = 3600 字符）必须按行拆分，且**每段重复表头**。

    依据 M1_KERNEL_SPEC §4 第 2 条：表格可整块超 target，但不能无限；
    分段后每段仍须自解释（否则片段无法判断列含义）。
    """
    header, sep = "| 项目 | 金额 |", "| --- | --- |"
    rows = ["| 项目编号{} | 金额{} |".format(i, i) for i in range(400)]
    chunks = chunk_document("\n".join([header, sep] + rows), target=600, hard_max=1200)
    tables = [c for c in chunks if c["is_table"]]
    assert len(tables) > 1, "超长表格必须拆分（400 行 ≈ 4800 字符 > 3600）"
    for t in tables:
        assert t["text"].startswith(header), "每段必须重复表头"
        assert len(t["text"]) <= 1200 * 3


def test_chunker_long_paragraph_hard_max():
    """超长无空行段落必须按 hard_max 硬拗断。"""
    chunks = chunk_document("茅" * 3000, target=600, hard_max=1200)
    assert chunks
    assert all(len(c["text"]) <= 1200 for c in chunks)


def test_chunker_aggregates_short_paragraphs():
    """短段落必须**聚合**成块，不得每段单独成块。

    ⚠️ 实测（2026-09-16）：公告正文是「一行一句 + 空行分隔」格式，
    旧实现每段独立成块 → 814 块的中位长度只有 **28 字符**（p25=12、min=1），
    而设计目标是 600 字。碎片块同时损害检索质量 / BM25 长度归一化 / 向量语义。
    """
    chunks = chunk_document("段落一。\n\n段落二。\n\n段落三。")
    assert len(chunks) == 1, "600 字内的短段落应聚合成 1 块（实际 %d 块）" % len(chunks)
    assert chunks[0]["seq"] == 0


def test_chunker_flushes_at_target():
    """聚合到 target 附近必须结算，不能无限累积；每块不得超 hard_max。"""
    para = "这是一个用于测试聚合行为的段落。" * 5          # ≈ 75 字
    chunks = chunk_document("\n\n".join([para] * 20), target=600, hard_max=1200)
    assert len(chunks) >= 3, "≈1500 字应按 target 切成多块（实际 %d）" % len(chunks)
    assert all(len(c["text"]) <= 1200 for c in chunks)


def test_chunker_seq_increments_and_unique():
    """seq 从 0 递增且文档内唯一（store 落库依赖它）。"""
    para = "内容段落。" * 40                                # ≈200 字
    chunks = chunk_document("\n\n".join([para] * 10))
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    assert len({c["seq"] for c in chunks}) == len(chunks)


# ==================== 5. embedding 失败必须给可操作指引（K3）====================

def test_embed_failure_raises_with_guidance(monkeypatch):
    """ollama 不可达 → RuntimeError 且消息含 ollama 指引，不得静默返回空向量。"""
    from utils.rag import embed as embed_mod

    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", _boom)
    with pytest.raises(RuntimeError) as ei:
        embed_mod.embed_texts(["测试文本"])
    assert "ollama" in str(ei.value).lower()


def test_embed_empty_input_returns_empty_list():
    """空输入不得触发网络调用。"""
    from utils.rag import embed as embed_mod
    assert embed_mod.embed_texts([]) == []
