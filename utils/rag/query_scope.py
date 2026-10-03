# -*- coding: utf-8 -*-
"""查询侧**标的识别**（F0a，2026-10-03）：从用户/模型的查询里认出「语料里已知的标的」。

## 为什么需要它（F0a 第一步实测的定因结论）

`report-E-R1.md` 的污染证据全部是 `code=None` 测的，但**不确定产线是否真的不传 `code`**。
F0a 走**真实链路**（`services.agent_service.stream_events`，真 LLM + 真工具）问了 7 个问题，
逐条取 `tool_start.arguments` 原文：

===========================  =============================================  =============
问题                          LLM 实际传入的 retrieve_docs 参数               top-5 codes
===========================  =============================================  =============
贵州茅台的分红方案是什么      `{"query": "贵州茅台 分红方案 利润分配", "code": "600519"}`  600519×5
泸州老窖最近的公告说了什么    `{"query": "泸州老窖 公告", "code": "000568", ...}`          000568×5
五粮液今年一季度的营业收入是多少 `{"query": "五粮液 一季度 营业收入"}`（**无 code**）      000858×3 + **600519 + 000568**
山西汾酒最近有哪些公告        `{"query": "山西汾酒 公告"}`（**无 code**）                  600809×5（侥幸干净）
贵州茅台上半年的营业收入是多少 `{"query": "贵州茅台上半年营业收入", "code": "600519"}`     600519×5
===========================  =============================================  =============

⇒ **LLM 时带时不带**（3/5 带、2/5 不带）⇒ 属任务书**情形 B**：产线**有真问题**，要修检索链路。
且不带 code 的那条（五粮液）**实际就污染了**（top-5 混入 600519/000568）。

## 规则（本模块的全部逻辑）

1. **显式代码参与识别**：查询里出现 6 位数字且是语料已知 code ⇒ 认它（如「600519 的分红」）。
   ⚠️ **F-R1（审计 F-R7）：显式代码不再短路名称匹配**。旧实现在 `explicit` 非空时**直接 `return`**
   ⇒ 「600519 和五粮液的分红对比」只收窄到 600519，**点名了的五粮液被丢掉**。
   现改为 **`explicit ∪ 名称命中`**（显式代码排在前，名称命中按最长别名排序）——
   「识别到就收窄、识别不到就全库」的语义不变，只是**不再漏掉同一句里点名的另一个标的**。
2. **公司名**：语料 `documents.title` 的「公司名:公告标题」前缀**众数**（`贵州茅台` / `五粮液` /
   `泸州老窖` / `山西汾酒`）—— **数据驱动**，新标的入库即自动获得别名，无需改代码。
3. **短别名**：公司名的**后缀**（`贵州茅台`→`茅台`、`山西汾酒`→`汾酒`、`泸州老窖`→`老窖`），
   但必须**在语料内可判别**：该别名在**其它标的**的块里基本不出现
   （判据 `other ≤ max(2, 5% × own)`）。否则丢弃 —— 防止把通用词当标的
   （反例：某公司叫「东方财富」时，后缀「财富」会误伤「财富管理」类查询）。
   ⚠️ **边界（F-R1 实测，如实记录，不假装没有）**：名称匹配是**子串**匹配 ⇒
   `贵州茅台集团…` / `茅台学院` / `茅台机场` / `贵州茅台医院` / `贵州茅台镇` 都会被收窄到
   `600519`（探针实测见 `report-F-R1.md` §F-R7）；`汾酒行业怎么样` 同样收窄到 `600809`。
   这是名称匹配的固有代价：现语料下这些查询的危害有界（不会返回错标的的公告），
   但换更大的语料后是**真实召回盲区**（同名机构/地名/品类词的主题会被判成上市公司主题）。
   **本轮不修**（加"同名机构后缀排除表"会在反方向出错：`贵州茅台集团财务有限公司` 是
   真实关联主体，排除它会把合法主题一起丢掉）；先在单测里把这三种边界**钉住**。
4. **只在识别到标的时收窄**：识别到 ≥1 个 ⇒ 检索池 = 这些标的的块（多标的取**并集**，
   所以「茅台和五粮液对比」仍能跨标的）；**一个都没识别到 ⇒ 保持全库**
   （「白酒行业上市公司有哪些」必须能跨标的返回，不得被硬过滤锁死）。

## 不用「阈值 / 缩小 top-k」的原因

任务书 §3 明令禁止用牺牲召回的手段冒充修复：本模块**不开新阈值、不动 top-k**，
只把「查询已点名的标的范围」显式化 —— 与模型自己传 `code` 时走的是**同一条过滤路径**
（`retrieve.py` 的池过滤），因此 `Recall@5` 的池稀释结论（E-R1 §4.3）原样适用。
"""
import re
from collections import Counter

#: 6 位股票代码（前后不接数字，避免把「2026100」这种串切出代码）
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
#: 标题里的公司名（`公司名:标题`；兼容半/全角冒号）
_NAME_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z]{2,10}?)[：:]")
#: 语料内判别容忍度：其它标的的命中块数 ≤ max(_OTHER_FLOOR, _OTHER_RATIO × 本标的命中块数)
_OTHER_FLOOR = 2
_OTHER_RATIO = 0.05


def lead_name(title):
    """`documents.title` → 公司名（只认 `公司名:标题` 形态，否则返回 ""）。

    ⚠️ **只认冒号前缀**：语料由 `scripts/rag_ingest.py` 落库，title 恒为
    `"{name}:{announcementTitle}"`（实测 65 篇全部如此）。非冒号标题（交易所原始
    文件名形态，如 `000568泸州老窖投资者关系管理信息20260914`）前缀**不是**公司名，
    取它会污染众数，故直接弃权 —— 拿不到名字只是「不收窄」的保守退化，不会误伤。
    """
    m = _NAME_RE.match(re.sub(r"^\d{4,6}", "", (title or "").strip()))
    return m.group(1) if m else ""


def company_names(meta):
    """语料推导 `{code: 公司名}`（取该 code 下冒号标题前缀的**众数**）。"""
    votes = {}
    for m in meta or []:
        code, name = m.get("code"), lead_name(m.get("title"))
        if code and name:
            votes.setdefault(code, Counter())[name] += 1
    return {code: c.most_common(1)[0][0] for code, c in votes.items()}


def _is_discriminative(alias, code, meta):
    """短别名是否**只在**该标的的块里出现（其它标的块数 ≤ 容忍度）。"""
    own = other = 0
    for m in meta or []:
        text = m.get("text") or ""
        if alias in text:
            if m.get("code") == code:
                own += 1
            else:
                other += 1
    if own == 0:
        return False                    # 别名在自家语料里都不出现 ⇒ 不是本标的的别名
    return other <= max(_OTHER_FLOOR, _OTHER_RATIO * own)


def detect_targets(query, meta):
    """返回查询里识别到的标的 code 列表（按匹配强度降序）；未识别 → `[]`。

    - 显式 6 位代码（且是语料已知 code）与名称匹配**取并集**（显式在前）——
      F-R1 修复：旧实现命中显式代码即 `return`，会漏掉同一句里点名的其它标的；
    - 名称匹配取**最长命中**：全名 > 长后缀 > 短后缀；
    - 只返回**语料里真实存在**的 code（不猜、不查外部接口）。
    """
    q = query or ""
    known = {m.get("code") for m in (meta or []) if m.get("code")}
    if not known:
        return []

    explicit = []
    for c in _CODE_RE.findall(q):
        if c in known and c not in explicit:
            explicit.append(c)

    names = company_names(meta)
    scored = {}
    cache = {}
    for code, name in names.items():
        if code in explicit:
            continue                                # 已由显式代码命中，不参与名称排序
        if name in q:
            scored[code] = len(name)
            continue
        for L in range(len(name) - 1, 1, -1):       # 短别名：长度 ≥2 的后缀
            alias = name[-L:]
            if alias not in q:
                continue
            key = (alias, code)
            if key not in cache:
                cache[key] = _is_discriminative(alias, code, meta)
            if cache[key]:
                scored[code] = L
                break
    return explicit + [c for c, _ in sorted(scored.items(), key=lambda kv: -kv[1])]
