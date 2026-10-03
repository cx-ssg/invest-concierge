# -*- coding: utf-8 -*-
"""`utils/rag/query_scope.py` 单元锁（F0a · 查询侧标的识别）。

定因（`report-F0a.md` 第一步）：真实链路上 LLM **时带时不带** `code`
（5 条带明确标的问题里 2 条没带，其中「五粮液今年一季度的营业收入是多少」
top-5 混入 600519/000568）⇒ 属任务书**情形 B**，修检索链路。

本文件锁住「识别规则」本身；工具层的两条必补回归（带标的查询不污染 /
无标的查询仍全库）在 `tests/test_rag_retrieve.py`。
"""
from utils.rag.query_scope import company_names, detect_targets, lead_name

#: 最小语料：两条标的，标题用 `scripts/rag_ingest.py` 的 `公司名:标题` 形态
META = [
    {"code": "600519", "title": "贵州茅台:2025年度分红派息实施公告",
     "text": "贵州茅台2025年度分红派息实施方案：每10股派发现金红利276.24元。"},
    {"code": "600519", "title": "贵州茅台:2026年半年度报告",
     "text": "贵州茅台白酒业务毛利率保持稳定。"},
    {"code": "000568", "title": "泸州老窖:2025年度分红派息实施公告",
     "text": "泸州老窖2025年度分红派息实施方案：每10股派发现金红利13.0元。"},
    {"code": "000568", "title": "泸州老窖:2026年半年度报告",
     "text": "泸州老窖白酒业务毛利率同比提升。"},
    {"code": "000858", "title": "五粮液:2025年度分红派息实施公告",
     "text": "五粮液2025年度分红派息实施方案：每10股派发现金红利25.0元。"},
]


# ==================== ① 公司名推导 ====================


def test_lead_name_only_accepts_colon_titles():
    """只认 `公司名:标题`（ingest 层唯一形态）。

    非冒号标题（交易所原始文件名，如 `000568泸州老窖投资者关系管理信息20260914`）
    前缀**不是**公司名 —— 取它会污染众数（实测：冒号形态 13 条 vs 非冒号 2 条，
    取全部会把众数推向 `泸州老窖投资`）。拿不到名字只是「不收窄」的保守退化。
    """
    assert lead_name("贵州茅台:2026年半年度报告") == "贵州茅台"
    assert lead_name("贵州茅台：全角冒号也要认") == "贵州茅台"
    assert lead_name("000568泸州老窖投资者关系管理信息20260914") == ""
    assert lead_name("关于召开业绩说明会的公告") == ""


def test_company_names_take_mode_per_code():
    """逐 code 取众数：同一标的的多种标题前缀不得把公司名带偏。"""
    meta = META + [
        {"code": "000568", "title": "000568泸州老窖投资者关系管理信息20260914",
         "text": "泸州老窖投资者关系活动记录。"},
        {"code": "000568", "title": "泸州老窖:董事会决议公告",
         "text": "泸州老窖董事会决议。"},
    ]
    names = company_names(meta)
    assert names == {"600519": "贵州茅台", "000568": "泸州老窖", "000858": "五粮液"}


# ==================== ② 识别规则 ====================


def test_detect_targets_full_name_and_short_alias():
    """全名与**短别名**（公司名后缀）都要能认出来 —— 用户口语说「茅台」而非「贵州茅台」。"""
    assert detect_targets("贵州茅台的分红方案是什么", META) == ["600519"]
    assert detect_targets("茅台的分红方案是什么", META) == ["600519"]
    assert detect_targets("山西汾酒最近有哪些公告", META) == []          # 语料里没有 600809
    assert detect_targets("泸州老窖的分红方案", META) == ["000568"]
    assert detect_targets("老窖的分红方案", META) == ["000568"]


def test_detect_targets_explicit_code_wins():
    """查询里出现语料已知的 6 位代码 ⇒ 直接认它（不必依赖名称）。"""
    assert detect_targets("600519 的分红方案", META) == ["600519"]
    assert detect_targets("看看 000858 的公告", META) == ["000858"]
    assert detect_targets("看看 999999 的公告", META) == []              # 非语料 code 不猜


def test_detect_targets_returns_empty_for_untargeted_query():
    """**无标的查询必须返回空** —— 上游据此保持全库，否则「白酒行业对比」会被锁死。

    这是任务书 §2 情形 B 的硬要求：「无法识别时保持全库」。
    """
    assert detect_targets("白酒行业上市公司有哪些", META) == []
    assert detect_targets("白酒行业对比", META) == []
    assert detect_targets("", META) == []
    assert detect_targets("茅台分红方案", []) == []                      # 空语料不抛


def test_detect_targets_multi_target_keeps_both():
    """跨标的查询识别出**两个**标的 ⇒ 两个都要在（并集池仍能跨标的返回）。"""
    got = detect_targets("茅台和五粮液的分红方案对比", META)
    assert set(got) == {"600519", "000858"}, got


def test_detect_targets_rejects_indiscriminative_alias():
    """**判别性**门：短别名若在其它标的的块里大量出现 ⇒ 不得当标的用。

    反例（真实存在的一类）：公司名「东方财富」的后缀「财富」是通用词，
    用它做别名会把「财富管理怎么配置」这类无关查询错误收窄到该公司。
    这里用「茅台」构造同一形态：让它在**别的标的**的块里高频出现
    （3 块 > 容忍度 `max(2, 5% × 2)=2`）。
    """
    poisoned = list(META) + [
        {"code": "000858", "title": "五粮液:2026年半年度报告",
         "text": "五粮液在报告中多次提及茅台的市场表现与竞争格局。{}".format("茅台" * 30)},
        {"code": "000568", "title": "泸州老窖:2026年半年度报告",
         "text": "泸州老窖同样提及茅台的价格带占位。{}".format("茅台" * 30)},
        {"code": "000568", "title": "泸州老窖:2025年度权益分派实施公告",
         "text": "泸州老窖与茅台同为高端白酒标的。{}".format("茅台" * 30)},
    ]
    # 其它标的命中块数 3 > max(2, 5% × 2) = 2 ⇒ **不判别** ⇒ 短别名失效
    assert detect_targets("茅台的分红方案", poisoned) == []
    # 但全名仍是**强**信号：不受短别名判别性影响
    assert detect_targets("贵州茅台的分红方案", poisoned) == ["600519"]


def test_detect_targets_prefers_longest_alias():
    """同名不同长度的匹配取**最长**命中（避免短别名抢走更精确的标的）。"""
    got = detect_targets("贵州茅台和泸州老窖的对比", META)
    assert set(got) == {"600519", "000568"}, got


# ==================== F-R1（审计 F7）：边界与短路 ====================


def test_detect_targets_boundary_substring_hits_are_pinned():
    """**边界公司名会被子串匹配收窄** —— 如实钉住，不假装没有（F-R1 · 审计 F7）。

    同名机构 / 地名 / 校区等含公司名或其别名的查询，会被判成该上市公司：
    `贵州茅台集团…` / `茅台学院` / `茅台机场` / `贵州茅台医院` / `贵州茅台镇` ⇒ `600519`。
    这是名称匹配的固有代价（探针实测见 `report-F-R1.md` §F-R7）；
    本轮**不加"机构后缀排除表"**——它会在反方向误伤 `贵州茅台集团财务有限公司`
    这类真实关联主体。危害在现语料有界（不会返回错标的的公告），换大语料才是召回盲区。
    """
    for q in ("贵州茅台集团的营收是多少", "茅台学院有哪些专业",
              "茅台机场的航班时刻表", "贵州茅台医院怎么样",
              "贵州茅台镇有什么特产"):
        assert detect_targets(q, META) == ["600519"], q


def test_detect_targets_explicit_code_no_longer_short_circuits():
    """F-R1（审计 F7）：显式代码与名称命中**取并集**，显式代码排在前。

    旧实现在 `explicit` 非空时直接 `return` ⇒ 「600519 和五粮液的分红对比」
    只收窄到 600519，**同一句里点名的五粮液被丢掉**（跨标的查询被单标的锁定）。
    """
    got = detect_targets("600519 和五粮液的分红对比", META)
    assert set(got) == {"600519", "000858"}, got
    assert got[0] == "600519", "显式代码应保持在前（优先序不变）"
    # 单标的/无标的场景行为不变
    assert detect_targets("600519 的分红方案", META) == ["600519"]
    assert detect_targets("看看 999999 的公告", META) == []
