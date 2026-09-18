# -*- coding: utf-8 -*-
"""证据充分性判据（SAR + V1）—— 取代 `max_sim` 绝对阈值。

**为什么废弃 `max_sim`**（2026-09-16 两份外部评审 + 主 Agent 实测对账）：
1. 它是**极值统计量**：语料越大越容易撞到高分近邻 → 误放行概率单调上升，**与语料是否同质无关**；
2. 它衡量的是**话题语言风格接近度，不是"这一块里有没有答案"**。

**替代判据**：

```
idf(t) = ln((N - df(t) + 0.5) / (df(t) + 0.5) + 1)
SAR    = bm25_max / (Σ_{t∈q} idf(t) · (k1+1))     # BM25 分数达成率（Lucene maxScore 界归一化）
V1     = |{t∈q : 0 < df(t) ≤ max(1, N·0.05)}| / |{t∈q : df(t) ≤ max(1, N·0.05)}|
```

⚠️ **实测状态（2026-09-17 更新：语料 75 块 / 评测集 98 条）**：

- **SAR 是唯一有效的字面判据**。两份外部评审各自实测了 4~6 种替代量
  （IDF 覆盖率、块内覆盖率 `cov_local`、最长连续命中 `maxrun`、OOV 占比 `oov_frac`、
  块内特征词率 `v1_local`…），**全部打不过 SAR** —— 这条路可以正式冻结，别再投入。
- ⚠️ **`V1` 的定位已修正（2026-09-18 实测）**：它此前被认为是「`strong` 档几乎不可达的**主因**」
  （归因于 bigram 跨界伪 token 稀释），但那是**半个错误**：
  - 真正的机制是 **V1 在 strong 侧没有区分度** —— holdout 正例 V1 中位 **0.222** vs 负例 **0.182**，
    分布重叠；
  - 字级回退修法（df=0 的 bigram，若其组成字都在语料里则视为存在）已**实测证伪**：
    V1 被普遍抬高到 ~0.82 → `strong_fp` 从 **0.000 恶化到 0.100**、弃权能力几乎归零
    （1001 个常用中文字几乎覆盖一切）。
  - **处置**：`strong` 只保留 SAR 条件（`V1_STRONG = 0.0`），`SAR_STRONG` 提到 **0.15**
    → 正例 strong **2/21 → 8/21**、负例仍 **0/50**；正例委派率 0.810 → **0.524**。
  - `none` 侧仍保留 V1（`V1_NONE`），但实测几乎由 SAR 承担。
- **判据的能力上界已实测清楚**（重要，决定架构）：

  | 负例类别 | 字面判据能否挡住 | 实测 |
  |---|---|---|
  | `out_of_domain`（域外） | ✅ **完全够用** | 0/6 误放行 |
  | `in_domain_unanswerable` | 🟡 可用字面换，代价不实用 | over_abstain 0.23 → 0.46 |
  | `near_miss`（近义干扰） | ❌ **结构上不可判** | 无任何工作点 |

  `near_miss` 不可判的原因：它的词面**全是合法域内词汇**（战略 df=9、治理 df=8…），
  缺的不是词而是**言语行为**（比较/排名/预测/评判），而公告体例不产生这类句子。
  → **A3b 必须走 LLM 判官**（`weak` 档即其入口），这不是退让而是唯一出路。

⚠️ **阈值是实测初值，不是普适常数**。完整数据与后续计划见 `docs/M1_EVAL_REPORT.md`。
"""
import math

from utils.rag.bm25 import BM25Index
from utils.rag.tokenize import tokenize

K1 = 1.5

# ---- 阈值（实测初值；改前必须先重跑 scripts/rag_threshold_probe.py）----
# ⚠️ 2026-09-18 按实测重标（三份外部审计都指出「strong 档几乎不可达 → strong_fp=0 缺检验力」）：
#   实测分界（holdout 干净验收组）：
#     `sar>=0.10 且 v1>=0.45`（旧）→ 正例 **2/21** 判 strong、负例 0/50  ← 档位基本失效
#     `sar>=0.15`（新，**去掉 V1 条件**）→ 正例 **8/21**、负例 **0/50**   ← 可达且仍零误放行
#     `sar>=0.12` → 正例 11/21、负例 3/50（开始放水，不取）
#   **为什么去掉 V1 条件**：V1 在 strong 侧**没有区分度** —— holdout 正例 V1 中位 0.222
#   vs 负例 0.182（**分布重叠**）。字级回退修法已实测证伪：V1 被普遍抬高到 ~0.82 →
#   strong_fp 从 0.000 恶化到 0.100、弃权能力几乎归零。故 strong 只保留 SAR。
#   收益：正例委派率 0.810 → **0.524**（判官成本大降），而 `strong_fp` / `none` 分布不变。
SAR_STRONG = 0.15     # 实测分界（旧值 0.10 对应「档位失效」）
V1_STRONG = 0.0       # **置 0**：V1 在 strong 侧无区分度（见上）；保留常量仅为可调/可追溯
SAR_NONE = 0.06       # 实测 IRR 上限 0.0490 → 留 margin（none 侧不动）
V1_NONE = 0.35        # 实测 IRR 上限 0.286  → 留 margin（保留；实测 none 侧几乎由 SAR 承担）

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
