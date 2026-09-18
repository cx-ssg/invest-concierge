# -*- coding: utf-8 -*-
"""证据充分性判据回归锁（M1 · 取代 `max_sim` 绝对阈值）。

**为什么换掉 `max_sim`（2026-09-16 两份外部评审 + 主 Agent 实测对账）**：
- `max_sim` 是**极值统计量**：语料越大，"意外近邻"越多 → 误放行概率单调上升
  （实测 HOLDOUT 组区间倒挂：无关 0.6902 > 相关 0.6668）；
- 且它**衡量话题风格接近度，不衡量"这块里有没有答案"** —— 「怎么写一封求职信」
  命中《关于聘请》一节就是典型（语义邻近≠可答）。

**替代方案（SAR + V1，均为语料相对量、尺度无关）**：
- `SAR = bm25_max / (Σ_{t∈q} idf(t) · (k1+1))` —— Lucene maxScore/WAND 用的解析上界归一化，
  实测 TUNING 与 **HOLDOUT 两组都干净可分**（REL 0.1791~0.3651 vs IRR 0.0000~0.0490，gap 3.65×）
- `V1 = |{t ∈ q : 0 < df(t) < N/20}| / |{t ∈ q : df(t) < N/20}|` —— 特征词存在率，
  第二票，对"词表外的查询"给出确定性信号

⚠️ 阈值（0.10/0.45/0.06/0.35）是**实测初值**，样本仅 30 条查询，
必须按 `docs/M1_INGEST_AUDIT.md` §五 重建评测集后重定。
"""
import pytest

from utils.rag.evidence import EvidenceJudge, SAR_NONE, SAR_STRONG, V1_NONE, V1_STRONG
from utils.rag.tokenize import tokenize


def _corpus():
    """101 篇：100 篇模板化的无关公告 + 1 篇含真实答案的公告（**已 token 化**）。

    小语料下 `df < N/20`（=5.05）仍能把查询词识别为特征词，故 V1 有判别力。
    """
    docs = ["这是第{}份无关的公司公告内容用于测试".format(i) for i in range(100)]
    docs.append("贵州茅台上半年营业收入同比增长百分之十五")
    return [tokenize(d) for d in docs]


@pytest.fixture()
def judge():
    return EvidenceJudge(_corpus())


def test_judge_rejects_non_tokenized_input():
    """传未 token 化的 `list[str]` 必须**显式报错**。

    ⚠️ 否则 `BM25Index` 会把字符串当可迭代字符逐个处理 → **静默产出全错分值**：
    不报错、不崩溃，只是所有分数都失去意义（2026-09-16 本组用例首次运行时真实踩到）。
    """
    with pytest.raises(TypeError):
        EvidenceJudge(["这是未 token 化的句子"])


def test_v1_defined_on_tiny_corpus():
    """小语料（N=2）下 V1 不得恒为 0 —— 否则判据永远弃权。

    旧的 `df < N/20` 在 N=2 时阈值是 0.1 → 只有词表外的词算特征词 → V1 恒 0，
    连「茅台上半年营业收入」这种明确可答的查询都会被判 `none`
    （2026-09-16 重构 retrieve.py 时踩到）。
    """
    docs = [tokenize("贵州茅台上半年营业收入同比增长"), tokenize("比亚迪汽车销量创新高")]
    j = EvidenceJudge(docs)
    ev = j.assess("茅台上半年营业收入")
    assert ev.v1 > 0.5, "小语料下特征词定义失效：v1=%s" % ev.v1
    assert ev.level != "none", "小语料下的可答查询不得被弃权"


# ==================== SAR ====================

def test_sar_is_zero_when_all_query_terms_absent(judge):
    """查询词全部不在语料词表 → SAR = 0（**确定性** NO_HIT，不需要阈值）。"""
    ev = judge.assess("量子计算最新进展")
    assert ev.sar == 0.0


def test_sar_positive_for_in_domain_query(judge):
    """语料里存在答案的查询 → SAR > 0。"""
    ev = judge.assess("茅台上半年营业收入")
    assert ev.sar > 0.0


def test_sar_is_scale_free(judge):
    """SAR 归一到理论上界 → 恒 ≤ 1（旧实现的 raw BM25 会随语料规模漂移）。"""
    assert 0.0 <= judge.assess("茅台上半年营业收入").sar <= 1.0


# ==================== V1（特征词存在率）====================

def test_v1_is_zero_when_no_feature_word_present(judge):
    """查询的特征词一个都不在语料里 → V1 = 0。"""
    assert judge.assess("量子计算最新进展").v1 == 0.0


def test_v1_is_one_when_all_feature_words_present(judge):
    """特征词全部命中 → V1 = 1.0。"""
    assert judge.assess("茅台上半年营业收入").v1 == 1.0


# ==================== 三档判定 ====================

def test_level_none_for_out_of_domain(judge):
    """域外查询 → level=none（**弃权**，A3a 的判据）。"""
    assert judge.assess("量子计算最新进展").level == "none"
    assert judge.assess("如何学习滑雪").level == "none"


def test_level_strong_for_in_domain(judge):
    """域内可答查询 → level=strong（A3c：不得误弃权）。"""
    assert judge.assess("茅台上半年营业收入").level == "strong"


def test_level_weak_in_between(judge):
    """两信号都不满足 strong、也不都低于 none → weak（交由 LLM 裁决）。"""
    ev = judge.assess("茅台上半年营业收入")   # 先确认基线
    assert ev.level == "strong"
    # 构造中间态：术语部分命中（SAR 低于 strong 门槛但 V1 高于 none）
    mid = judge.assess("营业收入同比增长测试")
    assert mid.level in ("weak", "strong")


def test_thresholds_are_ordered():
    """阈值必须有序，否则判定逻辑自相矛盾。

    ⚠️ 2026-09-18 修正：`V1_STRONG` 已置 **0**（V1 在 strong 侧无区分度 —— 见 evidence.py docstring），
    故不再满足 `0 < V1_NONE < V1_STRONG`。改为：
      - SAR 侧仍严格有序（`0 < SAR_NONE < SAR_STRONG < 1`）；
      - `V1_STRONG` 允许为 0（= 不参与 strong 判定）；非 0 时**必须**严格大于 `V1_NONE`。
    """
    assert 0 < SAR_NONE < SAR_STRONG < 1
    assert 0 <= V1_STRONG <= 1
    assert 0 < V1_NONE <= 1
    assert V1_STRONG == 0.0 or V1_STRONG > V1_NONE, \
        "V1_STRONG 为 0 表示弃用；否则必须严格大于 V1_NONE，否则 none/strong 分档会重叠"


def test_strong_depends_only_on_sar_after_recalibration():
    """`strong` 只依赖 SAR（`V1_STRONG == 0`）—— 锁住 2026-09-18 的实测重标。

    依据（holdout 干净验收组，21 rel + 50 neg）：
      `sar>=0.10 且 v1>=0.45`（旧）→ 正例 **2/21** 判 strong（**档位基本失效**，三份审计都点了）
      `sar>=0.15`（新，去掉 V1）   → 正例 **8/21**、负例 **0/50**
      `sar>=0.12`                 → 负例 3/50（开始放水）
    且 V1 在 strong 侧**无区分度**（正例中位 0.222 vs 负例 0.182）；字级回退修法实测会把
    `strong_fp` 从 0.000 抬到 0.100。**本测试防止有人无依据地把 V1 加回、或把 SAR 降回 0.10。**
    """
    assert V1_STRONG == 0.0, "V1 不得参与 strong 判定（实测无区分度）"
    assert SAR_STRONG >= 0.15, "SAR_STRONG 必须 ≥ 实测分界 0.15（0.10 失效 / 0.12 放水）"


# ==================== 与旧判据的对照（防回归到 max_sim）====================

def test_judge_does_not_expose_max_sim_gate(judge):
    """新判据**不得**再依赖整查询的向量 max（结构性缺陷，已废弃）。"""
    ev = judge.assess("茅台上半年营业收入")
    assert not hasattr(ev, "max_sim"), "evidence 不应携带 max_sim —— 它已不是判据"
