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

# ---- 阈值（⚠️ 改前必须跑 `python scripts/rag_eval.py --scan --split tuning`）----
# ⚠️ 2026-09-18 第六轮审计二 P2-2：本行原写「改前必须先重跑 scripts/rag_threshold_probe.py」，
#   但**那个工具从不扫 `SAR_STRONG`**（硬编码 4+4 条老查询，只输出 REL 误弃权 / IRR 误放行）
#   ⇒ **是死指针**。现改指 `rag_eval.py --scan`（本轮已补 strong 档维度）。
#   **这正是「0.15 只能在 holdout 上选」的根因**：标定工具缺失 → 被迫用验收池。
#
# ⚠️⚠️ 阈值口径订正（第五轮两份审计独立判定违规后）：
#   下面这些「实测分界」**全部来自 holdout** —— 而 holdout **已被用于选择 `SAR_STRONG`**，
#   故它**不再是干净验收组**，应称「**已用于选择 strong 阈值的拟合集**」。
#   ⇒ **不得据此声明 strong 档可达性**。换新数据时应预期：`strong_fp` **0~0.09**（非 0.000）、
#     正例 strong 率 **0.10~0.23**（非 0.381）—— 见第六轮审计一的交叉验证表。
#   历史记录（保留以示来源，**勿再引用为证据**）：
#     `sar>=0.10 且 v1>=0.45`（旧）→ 正例 **2/21** 判 strong、负例 0/50  ← 档位基本失效
#     `sar>=0.15`（新，**去掉 V1 条件**）→ 正例 **8/21**、负例 **0/50**
#     `sar>=0.12` → 正例 11/21、负例 3/50（开始放水，不取）

#   **为什么去掉 V1 条件**：V1 在 strong 侧**没有区分度** —— holdout 正例 V1 中位 0.222
#   vs 负例 0.182（**分布重叠**）。字级回退修法已实测证伪：V1 被普遍抬高到 ~0.82 →
#   strong_fp 从 0.000 恶化到 0.100、弃权能力几乎归零。故 strong 只保留 SAR。
#   收益：正例委派率 0.810 → **0.524**（判官成本大降），而 `strong_fp` / `none` 分布不变。
#
# ⚠️⚠️ 2026-09-18 **`strong` 档已撤下**（用户拍板，采纳第六轮审计二 P2-1 的**首选建议**）。
#   **理由（审计二实测，我复核）**：`strong` 与 `weak` 在生产里返回的 `results` **完全相同**
#   （同一批正文 + `url` + `title`），唯一差别是 `message` 那一句话；而
#   **LLM 判官从未实现**（全仓 grep `llm_judge|judge_evidence` → 0 命中）。
#   ⇒ 第四轮为"提高 strong 可达性"付出的全部代价（`V1_STRONG→0`、`SAR_STRONG→0.15`、
#     **用掉唯一干净的 holdout**）换到的产物是**一句提示文案的切换**；
#     `delegated_rate 0.810→0.524` 是**对一个不存在的组件的成本估算**。
#   撤下后：**任何非 none 的结果都带 `WEAK_EVIDENCE_NOTE`** —— **不再有"跳过警示"的通道**。
#   这同时关掉了第五、六两轮反复攻击的那条路（weak→strong 越级 + 跳过警示 + 拿到完整引用凭据）。
#   **曾用常量（已删，勿再引用）**：`SAR_STRONG`（holdout 拟合值）、`V1_STRONG`、`LEVEL_STRONG`。
SAR_NONE = 0.06       # 实测 IRR 上限 0.0490 → 留 margin
V1_NONE = 0.35        # 实测 IRR 上限 0.286  → 留 margin（保留；实测 none 侧几乎由 SAR 承担）

LEVEL_NONE = "none"       # 弃权：确定性证据缺失（A3a）
# `weak` = **非弃权**（判据未通过 ⇒ 返回结果 + 警示，由模型自行核验）。
# ⚠️ `strong` 档已撤下（见上）—— `LEVEL_*` 现在**只有两档**。
LEVEL_WEAK = "weak"


class Evidence:
    """一次查询的证据评估结果。"""

    __slots__ = ("sar", "v1", "level", "bm25_scores")

    def __init__(self, sar, v1, level, bm25_scores=None):
        self.sar = sar
        self.v1 = v1
        self.level = level
        # ⚠️ 2026-09-18 第六轮审计二 U8：**不再做 `or []` 兜底**。
        # 那会让 `[]`（"没有分数信息"）与「**明确零字面交集**」**不可区分**，
        # 于是 `hybrid.run_hybrid` 的**分层硬停**会对任何**没传 scores 的构造点误触发**
        # （测试替身、未来的缓存层、任何 `Evidence(...)` 简写都算）。
        # 新语义：**`None` = 未知 → 不参与硬停**；`[]` / 非空列表 = 明确的字面交集事实。
        self.bm25_scores = bm25_scores

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

        # ⚠️ 2026-09-18（审计二 P2-2）：`V1_STRONG = 0.0` 使 `v1 >= V1_STRONG` **恒真**，
        # 于是顺带**移除了「无特征词」这道护栏**：若 query 的 token 全是高频词（df ≥ 4）
        # → `feature == []` → `v1 = 0.0` → 旧规则 `0.0 >= 0.45` 挡住，新规则**放行**。
        # 实测（75 块语料）：「公司董事」(`sar=0.81`) /「公司股份」(`0.82`) /「董事会议」(`0.89`)
        # 等 **8 例**从 weak 翻成 strong，且产线返回 5 条**带 url** 的结果
        # —— 跳过 `WEAK_EVIDENCE_NOTE` 的警示、拿到完整引用凭据。
        # 这类查询词面上**毫无判别力**（只匹配最常见块），不该算「证据充分」。
        # ⇒ 显式保留「**必须有特征词**」这道护栏 —— 这是删条件时最容易漏的副作用。
        #
        # ⚠️⚠️ 2026-09-18 第六轮审计一 P1：**上一轮的修复没修干净**。
        # 原本写的是 `feature` 非空，但 `feature` 的定义（上行 130-131）**含 `df == 0`**
        # —— 语料里**根本不存在**的词也算"特征词"。而分词器在词缝处会产出**跨界 bigram**：
        #     `公司报告` → ['公司', '司报', '报告']，其中 `司报` **df=0 → 算 feature**
        # 于是 `feature=['司报']` 非空、`v1 = 0/1 = 0.0` → **旧规则照样放行**。
        # 实测（第六轮审计一发现，我已原样复现）：`公司报告`(sar=0.1777) / `公司的报告`(0.300) /
        # `公司员工`(0.1889) / `公司投资`(0.1911) / `公司利润`(0.1941) / `公司收入`(0.2284) /
        # `公司报告期内`(0.4164) / `酒厂集团`(0.2938) —— **13 条里 10 条 strong，其中 9 条 `v1 == 0`**，
        # 且产线返回 5 条**带 url** 的结果。**这正是上一轮花力气关掉的那条路。**
        #
        # ⇒ 判准应是「**存在至少一个语料里真的有的特征词**」，即 **`v1 > 0`**。
        #   `v1 > 0` **蕴含** `feature != []`（feature 为空 ⇒ v1 恒为 0），故严格更优。
        # 实测代价：holdout 的 8 条 strong **0 条**受影响；tuning 的 3 条 strong 中 1 条
        # （「注销之后公司的股份总数是多少？」—— 措辞全用常用词、但**确实可答**）降为 weak。
        # 这是词面判据的**固有张力**（无法区分「措辞通俗但可答」与「纯高频词凑数」），
        # 记为**取舍**而非缺陷。
        #
        # ⚠️⚠️ 2026-09-18 **`strong` 档已撤下**（用户拍板，采纳第六轮审计二 P2-1 的首选建议）。
        # 分档现在**只有两档**：
        #   `none` = 判据断定「没有可用证据」（弃权）
        #   `weak` = **其余全部** —— 判据未通过，但结果仍返回，**并一律附带警示语**
        # 被删掉的旧逻辑：`if v1 > 0 and sar >= SAR_STRONG and v1 >= V1_STRONG: strong`
        # 撤它的理由**不是"这个条件写得不好"，而是"它闸的东西不存在"**：
        # `strong` 与 `weak` 返回的 `results` 完全相同，唯一差别是 `message` 一句话，
        # 而应当消费这个差别的 **LLM 判官从未实现**（全仓 grep `llm_judge|judge_evidence` 0 命中）。
        # ⇒ 撤档的真实收益是**行为层面**的：**"跳过警示"这条通道消失了** ——
        #   非 none 的结果一律带 `WEAK_EVIDENCE_NOTE`，第五、六两轮反复攻击的
        #   "weak→strong 越级 + 跳过警示 + 拿到完整 url/title"不再可能发生。
        # ⚠️ 副作用（**语义变更，必须记住**）：`delegated_rate` 从「**委派成本**」
        #   变成「**未通过判据、但仍返回给模型的结果占比**」—— 判官并不存在，
        #   所以它现在描述的是**风险面**，不再是成本。
        if sar < SAR_NONE and v1 < V1_NONE:
            level = LEVEL_NONE
        else:
            level = LEVEL_WEAK
        return Evidence(sar, v1, level, scores)
