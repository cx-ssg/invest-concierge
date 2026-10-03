# -*- coding: utf-8 -*-
"""证据充分性判据回归锁（M1 · 取代 `max_sim` 绝对阈值）。

**为什么换掉 `max_sim`（2026-09-16 两份外部评审 + 主 Agent 实测对账）**：
- `max_sim` 是**极值统计量**：语料越大，"意外近邻"越多 → 误放行概率单调上升
  （实测 HOLDOUT 组区间倒挂：无关 0.6902 > 相关 0.6668）；
- 且它**衡量话题风格接近度，不衡量"这块里有没有答案"** —— 「怎么写一封求职信」
  命中《关于聘请》一节就是典型（语义邻近≠可答）。

**替代方案（SAR + V1，均为语料相对量、尺度无关）**：
- `SAR = bm25_max / (Σ_{t∈q} idf(t) · (k1+1))` —— Lucene maxScore/WAND 用的解析上界归一化。
  ⚠️ **不能写"两组都干净可分"**（本行旧文案如此，已被推翻）：
  只有 **HOLDOUT** 单组呈（REL 0.1791~0.3651 vs IRR 0.0000~0.0490）；
  **TUNING 组区间是倒挂的** —— 正例上限 **0.1540** < 负例上限 **0.2134**（2026-09-18 复核，
  见 `docs/M1_EVAL_REPORT.md` §4g 与第六轮审计二的独立复核）。
  ⇒ **strong 边界在现有两个池子上都无法无泄漏标定**，别把它当干净分界。
- `V1 = |{t ∈ q : 0 < df(t) < N·ρ}| / |{t ∈ q : df(t) < N·ρ}|` —— 特征词存在率，
  第二票，对"词表外的查询"给出确定性信号（`ρ = FEATURE_DF_FRACTION`，2026-10-03 F0b 由 0.05 收紧到 0.02）

⚠️ **当前阈值不在此写死**（F-R1 · 审计 F5/F11 抓出：本行曾写 `0.08 / 0.45`，而生产是
`0.075 / 0.45` ⇒ 引用者按错值复算，且与同句"以 `utils/rag/evidence.py` 为准"自相矛盾）。
唯一事实源 = `utils/rag/evidence.py` 的 `SAR_NONE` / `V1_NONE` / `FEATURE_DF_FRACTION`
（本文件已 `import SAR_NONE, V1_NONE`，需要断言时直接断言模块值）。
2026-10-03 F0b 的三候选并列实测与取舍理由见 `report-F0b.md`；两条必须记住的性质：
1. （历史）`SAR_STRONG = 0.15` 曾是**在 holdout 上选出的拟合值** —— 该档已撤下，本行仅作留档；
2. `V1_STRONG = 0.0` 使 `v1 >= V1_STRONG` **恒真** —— strong 档的活性护栏是 `v1 > 0`
   （第六轮审计一 P1：`feature` 含 `df == 0`，跨界 OOV bigram 可绕过它）。
"""
import pytest

from utils.rag.evidence import EvidenceJudge, SAR_NONE, V1_NONE
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


def test_level_weak_for_in_domain(judge):
    """域内可答查询 → level=**weak**，不是 none（A3c：不得误弃权）。

    ⚠️ 2026-09-18 **撤下 `strong` 档**后，本用例由「应为 strong」改为「应为 weak」——
    两档语义下 `weak` = 「判据未通过、但结果仍返回（且**一律带警示**）」；
    它**不再表示"委派给判官"**（判官从未实现）。这里真正要防的退化是**误判 none**。
    """
    assert judge.assess("茅台上半年营业收入").level == "weak"


def test_no_strong_tier_is_produced(judge):
    """**撤下 `strong` 档**：分档只出 `none` / `weak`（锁住 2026-09-18 的撤档决定）。

    背景（第六轮审计二 P2-1）：`strong` 与 `weak` 在生产里返回的 `results` **完全相同**，
    唯一差别是 `message` 一句话；而应消费该差别的 **LLM 判官从未实现**
    （全仓 grep `llm_judge|judge_evidence` → 0 命中）⇒ 该档位是纯装饰，
    却一直在消耗最稀缺的资源（干净数据、测试、说服力）。
    **本测试防止有人无依据地把三档加回来。**
    """
    import utils.rag.evidence as ev_mod
    assert not hasattr(ev_mod, "LEVEL_STRONG"), "strong 档已撤下，不应再定义 LEVEL_STRONG"
    assert not hasattr(ev_mod, "SAR_STRONG"), "strong 档已撤下，不应再定义 SAR_STRONG"
    assert not hasattr(ev_mod, "V1_STRONG"), "strong 档已撤下，不应再定义 V1_STRONG"
    for q in ("茅台上半年营业收入", "量子计算最新进展", "营业收入同比增长测试"):
        assert judge.assess(q).level in ("none", "weak"), \
            "分档只允许 none / weak（问题：%s）" % q


def test_none_thresholds_are_ordered():
    """none 档阈值必须有序 —— 两档语义下**只剩这一组**阈值（撤下 strong 之后）。

    ⚠️ 2026-09-18 **撤下 `strong` 档**（第六轮审计二 P2-1，用户拍板）：
    `SAR_STRONG` / `V1_STRONG` **已删**，原先的
    `test_thresholds_are_ordered` 与 `test_strong_depends_only_on_sar_after_recalibration`
    随之**失去对象**（它们锁的都是 strong 侧的取值纪律）——后者已删除，前者改成本用例。
    这里只锁**仍然存在**的约束：none 档两个阈值都落在 (0,1]，且**都不为 0**
    （为 0 会退化成"恒弃权"或"恒不弃权"）。
    """
    assert 0 < SAR_NONE < 1, "SAR_NONE 必须落在 (0,1)"
    assert 0 < V1_NONE <= 1, "V1_NONE 必须落在 (0,1]"


def test_production_none_thresholds_are_locked_to_module_values():
    """生产 `none` 档阈值的**数值锁**（F-R1 · 审计 F5/F11）。

    为什么补这一条：本文件的 docstring 曾写死「当前阈值 = `0.08 / 0.45`」，
    而生产是 `0.075 / 0.45` —— 文档与代码漂移，引用者会按错值复算。
    现在唯一事实源是 `utils/rag/evidence.py`；本用例把它的**当前取值**钉住，
    改阈值时**必须同时**改这里（强制一次有意识的更新，而不是静默漂移）。
    """
    import utils.rag.evidence as e_mod
    assert (e_mod.SAR_NONE, e_mod.V1_NONE) == (0.075, 0.45)
    assert e_mod.FEATURE_DF_FRACTION == 0.02


def test_is_none_boundaries_track_constants():
    """none 判据的**边界**必须跟着常量走 —— 抓「实现里写死字面量」。

    ⚠️ 2026-10-01（**第八轮外部审计**，Claude + Codex 双路独立实测）：把
    `EvidenceJudge` 内的门限从常量换成字面量（`v1 < 0.30` / `sar < 0.10`），
    **全量 344 条测试全绿** —— 而同屏的 `--scan` 输出会与横幅自相矛盾
    （横幅仍宣称 `sar<0.06`，表里"当前"行其实是别的点的值）。
    ⇒ 判据侧此前**没有任何测试**锁住这两个门限。

    本用例把「边界两侧的行为」钉住：实现里写死任何别的阈值都会红。
    （它锁的是**行为边界**，不是常量数值本身 —— 将来调参仍可通过改常量+重跑敏感性表完成。）
    """
    import utils.rag.evidence as e_mod
    eps = 1e-6
    assert e_mod.is_none(e_mod.SAR_NONE - eps, 0.0) is True, "低于 SAR_NONE 且 v1 低 → 应判 none"
    assert e_mod.is_none(e_mod.SAR_NONE + eps, 0.0) is False, "高于 SAR_NONE → 不应判 none"
    assert e_mod.is_none(0.0, e_mod.V1_NONE - eps) is True, "低于 V1_NONE 且 sar 低 → 应判 none"
    assert e_mod.is_none(0.0, e_mod.V1_NONE + eps) is False, "高于 V1_NONE → 不应判 none"
    # 参数化形态（敏感性表用）：**必须使用传入门限**，不得回落到常量
    assert e_mod.is_none(0.29, 0.10, 0.30, 0.35) is True, "显式门限 0.30 > 0.29 → 应 none"
    assert e_mod.is_none(0.31, 0.10, 0.30, 0.35) is False, "显式门限 0.30 < 0.31 → 不应 none"


def test_only_high_frequency_terms_yield_zero_v1():
    """查询若**全是高频词** → `feature == []` → `v1 = 0.0`（**机制**回归锁）。

    ⚠️ 2026-09-18 **撤下 `strong` 档**后，本用例**不再断言 `level != "strong"`** ——
    那是**空洞断言**（分档已无 strong 取值，恒真）。改为锁**仍然成立的机制**：
    这类查询的 `v1` 恒为 0，即「语料里一个**真正存在**的特征词都没有」。

    历史背景（仍值得记）：正因 `v1 == 0` 而 `feature` 非空（`feature` 的定义含 `df == 0`
    的**跨界 OOV bigram**，第六轮审计一 P1 实测），旧规则曾把「公司报告」这类查询判成
    `strong`、跳过警示并拿到完整 url —— **该通道已随撤档关闭**。
    """
    docs = [tokenize("公司董事会决议公告第{}号".format(i)) for i in range(100)]
    j = EvidenceJudge(docs)
    toks = set(tokenize("公司董事"))
    import utils.rag.evidence as e_mod
    feature = [t for t in toks if j.index.df.get(t, 0) <= e_mod.feature_band(j.N)]
    assert feature == [], "前提：该查询应无特征词（全高频），实得 %s" % feature
    ev = j.assess("公司董事")
    assert ev.v1 == 0.0, "无特征词 → v1 必须为 0.0"
    # 两档语义下该查询只能是 weak（有字面交集）或 none（无交集）—— **不可能是 strong**
    assert ev.level in ("none", "weak")


# ==================== F0b：特征词带的尺度不变性（2026-10-03）====================
# 背景：A3a 0.922 → 0.725 的定因是**特征词带随语料膨胀**（pre-E1 14.05 → 现语料 84.55）
# ⇒ 中频词（`数据` df 18→60）落进带内、域外查询的 v1 被抬高到阈值之上 ⇒ 弃权失效。
# 修法：带宽**只依赖 df/N**（`feature_band = max(1, N·ρ)`，ρ 由 0.05 收紧到 0.02）。
# 下面两条锁住这个决定的**结构**，防止将来被"固定 df 上限 / df 分位数"等方案静默替换。


def test_feature_band_is_scale_invariant_under_pure_duplication():
    """零新内容对照的**单元级**版本：同一语料原样复制 6 份 → 每条查询 `v1` 逐位不变。

    ⚠️ 为什么必须锁：这条性质是"带宽取 df/N 形式"的**全部理由**。
    实测（F0b，98 条 tuning 查询）：固定 df 上限会让 **76 条** 在纯复制下改变 `v1`；
    df 分位数在词表由 df=1 词主导时退化（100 篇语料上带=100 → 全高频查询 v1 由 0.00 变 1.00）。
    `scripts/e_r1_kb_dup6b.py` 是本性质的**端到端**版本（真实语料 + 真实查询）。
    """
    base = [tokenize("公司公告第{}号 贵州茅台 营业收入 同比增长 百分之{}".format(i, i % 7))
            for i in range(60)]
    assert 0.02 * 60 > 1.0, "前提：带宽必须高于下限 1.0（否则 max(1,·) 会破坏倍率关系）"
    j1, j6 = EvidenceJudge(base), EvidenceJudge(base * 6)
    assert j6.N == j1.N * 6
    assert j6.feature_df_cap == pytest.approx(j1.feature_df_cap * 6), \
        "带宽没有随语料同倍放大 —— 纯复制下准入词集合会变"
    for q in ("茅台上半年营业收入", "量子计算最新进展", "公司公告内容测试", "百分比是多少"):
        e1, e6 = j1.assess(q), j6.assess(q)
        assert e1.v1 == pytest.approx(e6.v1), \
            "纯复制改变了 v1（%r: %.3f -> %.3f）—— 特征词带不再是尺度不变的" % (q, e1.v1, e6.v1)


def test_feature_band_value_and_mid_frequency_exclusion():
    """带宽取值锁 + 定因锚点：现语料尺寸下 `df=60`（`数据`）必须**落在带外**。

    `数据` 是 E 阶段两路审计点名的越界词（df 18→60 ⇒ v1 0.083→0.462）。
    旧带宽在现语料上是 84.55（`数据` 在带内）；F0b 的 33.82 把它排除。
    ⚠️ 这里写死 `1691 / 60` 是**故意的**：它把"回归发生时的那个语料尺寸 + 那个词"
    钉成可复现的锚点；语料换代时应连同 `report-F0b.md` 一起复核。
    """
    import utils.rag.evidence as e_mod
    assert e_mod.FEATURE_DF_FRACTION == 0.02, "ρ 被改动了 —— 需同步重跑三候选并列实测"
    assert e_mod.feature_band(1691) == pytest.approx(33.82)
    assert e_mod.feature_band(0) == 1.0, "空语料带宽不得为 0（否则 feature 恒空、v1 恒 0）"
    assert 60 > e_mod.feature_band(1691), "df=60 的中频词又进带内了 —— v1 会被它抬高"
    assert 60 < max(1.0, 1691 * 0.05), "对照：旧带宽（0.05N）下它在带内 —— 这正是定因"


# ==================== 与旧判据的对照（防回归到 max_sim）====================

def test_judge_does_not_expose_max_sim_gate(judge):
    """新判据**不得**再依赖整查询的向量 max（结构性缺陷，已废弃）。"""
    ev = judge.assess("茅台上半年营业收入")
    assert not hasattr(ev, "max_sim"), "evidence 不应携带 max_sim —— 它已不是判据"
