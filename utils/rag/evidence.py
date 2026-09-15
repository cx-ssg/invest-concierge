# -*- coding: utf-8 -*-
"""证据充分性判据（SAR + V1）—— 取代 `max_sim` 绝对阈值。

**为什么废弃 `max_sim`**（2026-09-16 两份外部评审 + 主 Agent 实测对账）：
1. 它是**极值统计量**：语料越大越容易撞到高分近邻 → 误放行概率单调上升，**与语料是否同质无关**；
2. 它衡量的是**话题语言风格接近度，不是"这一块里有没有答案"** —— 「怎么写一封求职信」
   命中《关于聘请》一节即典型（语义邻近 ≠ 可答）。

**替代判据（两个信号，均为语料相对量、尺度无关）**：

```
idf(t) = ln((N - df(t) + 0.5) / (df(t) + 0.5) + 1)              # df=0 也良定义
SAR    = bm25_max / (Σ_{t∈q} idf(t) · (k1+1))                    # 分数达成率（Lucene maxScore/WAND 的界）
V1     = |{t∈q : 0 < df(t) < N/20}| / |{t∈q : df(t) < N/20}|     # 特征词存在率
```

实测（814 块真实公告语料，30 条查询，TUNING 与 HOLDOUT **两组独立复核**）：

| 组 | SAR: REL vs IRR | 可分 |
|---|---|---|
| TUNING | 0.1103~0.2816 vs 0.0000~0.0422 | ✅ |
| **HOLDOUT** | **0.1791~0.3651 vs 0.0000~0.0490**（gap 3.65×） | ✅ |

对比：同一批查询下 `max_sim` 在 HOLDOUT 组**区间倒挂**（无关 0.6902 > 相关 0.6668）。

⚠️ **阈值是实测初值，不是普适常数**：SAR 是上界量，真实查询永远取不到 1（实测 0.11~0.37）。
样本仅 30 条查询 → 必须按 `docs/M1_INGEST_AUDIT.md` §五 重建评测集后重定。
"""
import math

from utils.rag.bm25 import BM25Index
from utils.rag.tokenize import tokenize

K1 = 1.5

# ---- 阈值（实测初值；改前必须先重跑 scripts/rag_threshold_probe.py）----
SAR_STRONG = 0.10     # 实测 REL 下限 0.1103 / IRR 上限 0.0490
V1_STRONG = 0.45      # 实测 REL 下限 0.500  / IRR 上限 0.286
SAR_NONE = 0.06       # 实测 IRR 上限 0.0490 → 留 margin
V1_NONE = 0.35        # 实测 IRR 上限 0.286  → 留 margin

LEVEL_NONE = "none"       # 弃权：确定性证据缺失（A3a）
LEVEL_WEAK = "weak"       # 返回结果 + 标注证据不足，交 LLM 裁决（A3b 的安全网）
LEVEL_STRONG = "strong"   # 正常返回


class Evidence:
    """一次查询的证据评估结果。"""

    __slots__ = ("sar", "v1", "level", "bm25_scores")

    def __init__(self, sar, v1, level, bm25_scores=None):
        self.sar = sar
        self.v1 = v1
        self.level = level
        self.bm25_scores = bm25_scores or []

    def __repr__(self):
        return "Evidence(sar=%.4f, v1=%.3f, level=%s)" % (self.sar, self.v1, self.level)


class EvidenceJudge:
    """在**完整语料**上评估「这批语料里有没有答案」。

    ⚠️ 必须用**全库**（而非按 code 过滤后的子集）构建：否则 `idf`/`df` 的统计基准
    会随检索池大小变化，判据随之漂移 —— 这正是 2026-09-16 读码发现的产线 bug 根因
    （`retrieve.py` 先按 code 过滤 matrix，再让闸门在子集上算 `max_sim`）。
    """

    def __init__(self, docs_tokens):
        docs_tokens = list(docs_tokens or [])
        # 防御：传 list[str]（未 token 化）时 BM25Index 会把**字符串当字符**迭代 →
        # 静默产出全错分值（不报错、不崩溃）。宁可响亮地失败。
        # 2026-09-16 实测踩到：测试 fixture 漏了 tokenize，4 条用例全红。
        if docs_tokens and isinstance(docs_tokens[0], str):
            raise TypeError(
                "EvidenceJudge 需要 token 化的语料 list[list[str]]，收到 list[str]；"
                "请先经 utils.rag.tokenize.tokenize 处理"
            )
        self.docs = docs_tokens
        self.index = BM25Index(docs_tokens)
        self.N = len(docs_tokens)

    def idf(self, t):
        """与 BM25Index 同式；df=0（词表外）时取最大 idf。"""
        df = self.index.df.get(t, 0)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)

    def assess(self, query):
        """返回 `Evidence(sar, v1, level, bm25_scores)`。"""
        toks = set(tokenize(query))
        if not toks:
            return Evidence(0.0, 0.0, LEVEL_NONE, [])

        total_idf = sum(self.idf(t) for t in toks)
        scores = self.index.score(sorted(toks))
        bm25_max = max(scores) if scores else 0.0
        # 分子＝实测最高分；分母＝该查询的理论上界（tf→∞、长度归一取均值）→ SAR 恒 ≤ 1
        sar = (bm25_max / (total_idf * (K1 + 1))) if total_idf > 0 else 0.0

        # 特征词：`df <= max(1, N*0.05)`。
        # ⚠️ 下限 1 是**必需**的：N<20 时 `N/20 < 1` 会让所有词都不算特征词 →
        # V1 恒为 0 → 判据永远弃权（小语料/单测立刻踩到）。
        # 大语料下 `N*0.05` 与旧式 `N/20` 等价，实测数值不变。
        feature = [t for t in toks
                   if self.index.df.get(t, 0) <= max(1.0, self.N * 0.05)]
        if feature:
            v1 = len([t for t in feature if self.index.df.get(t, 0) > 0]) / len(feature)
        else:
            v1 = 0.0

        if sar >= SAR_STRONG and v1 >= V1_STRONG:
            level = LEVEL_STRONG
        elif sar < SAR_NONE and v1 < V1_NONE:
            level = LEVEL_NONE
        else:
            level = LEVEL_WEAK
        return Evidence(sar, v1, level, scores)
