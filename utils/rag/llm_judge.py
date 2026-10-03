# -*- coding: utf-8 -*-
"""LLM 判官 —— 纯逻辑层（引文确定性校验 + 输入裁剪 + 诚实降级）。

设计依据：`docs/COVERAGE_DESIGN.md` §3.3 判据部分 + 黑板 M1 段「④ LLM 判官
（异步 + 仅 weak + 引文确定性校验），判据落在 `judge_fp`（不在 `weak_fp`）」。
任务书：`task-B1.md` §1.1。

## 为什么必须做（实测，不是推测）

字面判据（SAR/V1）在**结构上**无法区分「域内不可答」与「相关」：`sar<0.15` 时
**83% 的相关查询被误弃权**、`sar<0.20` 时 **100%**（`docs/M1_EVAL_REPORT.md` §4b）。
`near_miss` 的词面全是合法域内词汇，缺的不是词而是**言语行为** ⇒ 只有 LLM 能判。
`weak` 档因此是判官的**入口**（`none` 档**不触发**，见下）。

## 三条硬规则（任务书 §1.1）

1. **引文确定性校验（本任务的核心防线）**：判 `relevant` 必须附 **≥12 字逐字引文**；
   代码把引文与候选 `text` 都做**跨度归一化**（`normalize_span`：CJK 之间的空白是排版伪影、
   直接删；其余连续空白 → 单空格）后做 substring 校验 —— **引用不存在 ⇒ 该条降级为
   `uncertain` 并置 `quote_rejected=True`**（不允许"看起来像"）。
   ⚠️ 这条校验**只能证明"非凭空编造"，不能证明"足以回答"**（B-F10：≥12 字阈值几乎
   没有鉴别力；B-F6：难例实测放行且引文全部通过校验）—— 详见 `quote_verified` docstring。
2. **输入裁剪**：每块只喂 `title + text[:per_chunk_chars]`；总 prompt 有上限。
   原型 43.7s 里 **90% 是 prefill** ⇒ 裁剪是主杠杆（实测裁剪后 2.9s，`probe-judge.json`）。
3. **诚实降级**：无 LLM / 超时 / JSON 解析失败 / 模型乱答 ⇒ `level="uncertain"`,
   `checked=False`，**绝不抛异常**。判官**永远不能**把不可信结果伪装成 `relevant`。

## F1（2026-10-03 · A3b 攻坚）改了什么

A3b 判据（`docs/M1_EVAL_REPORT.md` L147：`judge_fp ≤0.10` / `judge_fn ≤0.20` /
`span_valid ≥0.95`；⚠️ `judge_fn` 门槛于 **2026-10-03 由 0.15 调整为 0.20**（用户拍板），
依据见该处「判据门槛修订说明」：判官只看前 5 条候选时**地板 = 4/26 = 0.1538 > 0.15**，算术不可达）
在 B1 之后**未达成**（`judge_fp` 中位 0.308）。本轮逐条实测三种手段后：

| 手段 | 实测（tuning，weak 负 58 / weak 正 13，12–24 采样） | 处置 |
|---|---|---|
| ① 抽取式作答（**证明**能回答，而非**声称**） | `judge_fp` 0.293 → **0.086**，`judge_fn` 0.000 → 0.077 | ✅ **采用**（`PROMPT_TMPL`） |
| ② 要素覆盖（引文必须覆盖被问要素） | `judge_fp` → 0.034 **但** `judge_fn` → **0.308**（"分红总额" vs "派发现金红利…元" 这类同义改写被误杀） | ❌ **否决**（净负作用） |
| ②' 只留"形状"子检查（数值/因果/时间） | 对 `judge_fp` **零效果** | ❌ 否决 |
| ③ 实体边界（主体必须同一） | 提示词内指令：`judge_fp` 0.086 → 0.069；**确定性闸门** `subject_mismatch`：在①之上 0.0776 → 0.0690（配对 更好 1 / 更差 0 / 相同 23） | ✅ 采用（**边际**，理由是它唯一确定性） |

另修一处**判罚质量缺陷**：`quote_verified` 原用 `normalize_ws`（空白→单空格），把
"PDF 换行落在词中"的正确引文误判为不存在（实测 111 条失败引文里 110 条属此类）
⇒ 13% 的正确判罚被降级成 `uncertain`，直接抬高 `judge_fn`、压低 `span_valid`。
现改用 `normalize_span`（见其 docstring；B-F10 的**拉丁文**跨空白反例仍被拒）。

## 实测边界（`probe-judge-batch.json`，n 小，勿过度推断）

在真实 holdout 上：`out_of_domain` 放行 **0/10**，但
`in_domain_unanswerable` + `near_miss` 放行 **4/14 = 0.286**，且那 4 条的引文
**全部通过逐字校验** ⇒ **引文校验挡不住"断章取义"**：模型能引用真实存在、
但**不足以回答问题**的片段。相关查询误杀 1/5。**判官是增益，不是保证** ——
任何对外表述不得写成"判官能保证不误用"。

⚠️ 上面这组是 **B1 旧提示词**下的数字，保留作对照；F1 换提示词 + 修跨度归一化后的
**实测值见本文件 `PROMPT_TMPL` 上方的表**（tuning，12–24 采样，中位/区间）。
`in_domain_unanswerable` 仍是**最难**的一类（tuning 上 3/30 漏网、`near_miss` 1/25）。

## 与产线的边界（硬约束，任务书 §3）

- **不参与检索排序/过滤**：本模块只产出「可信度标注」，`retrieve.py` / `hybrid.py`
  对本模块**零引用**（回归锁：`tests/test_rag_llm_judge.py::test_judge_does_not_participate_in_retrieval`）。
- **不改 `none` 档行为**：触发判定在 `services/judge_service.py`，只认 `weak`。
- **不新建 client**：`llm_fn` 缺省时复用 `utils.ai_helper.call_llm`（晚绑定，可 patch）。
"""
import json
import re
import threading
import time

__all__ = [
    "LEVEL_RELEVANT", "LEVEL_IRRELEVANT", "LEVEL_UNCERTAIN",
    "MIN_QUOTE_CHARS", "DEFAULT_PER_CHUNK_CHARS", "DEFAULT_MAX_TOTAL_CHARS",
    "normalize_ws", "normalize_span", "quote_verified", "build_prompt", "judge_candidates",
]

LEVEL_RELEVANT = "relevant"
LEVEL_IRRELEVANT = "irrelevant"
LEVEL_UNCERTAIN = "uncertain"

#: 判 `relevant` 所需的最小引文长度（归一化后计）
MIN_QUOTE_CHARS = 12
#: 每块只喂前 N 字（裁剪 = 延迟主杠杆，实测 2.9s）
DEFAULT_PER_CHUNK_CHARS = 800
#: 整条 prompt 的字符上限（含模板与全部候选块）
DEFAULT_MAX_TOTAL_CHARS = 12000
#: 单次 LLM 调用的默认上限（秒）
DEFAULT_TIMEOUT_S = 20.0

#: 判官提示词。**只输出 JSON**（实测模型能稳定遵守，无需 function calling / JSON mode）。
#:
#: ⚠️⚠️ 2026-10-03 **F1 · A3b 攻坚（approach 1 + 3，实测选型）**：
#: 旧版只问「这些片段**能否**回答该问题」并要求一段"12 字以上逐字引文" —— 实测该问法
#: 允许模型走"话题相关"这条捷径：tuning（58 条 weak 负例 / 13 条 weak 正例，12 采样）
#: `judge_fp` 中位 **0.293 [0.276, 0.362]**，超 A3b 门槛（≤0.10）约 3 倍。
#: 新版把"声称能回答"改成"**证明能回答**"：
#: ① 要求模型先确定**答案形态**，再在片段里**逐字找出那一句答案句**（找不出 ⇒ irrelevant）
#:    —— 这直接堵「断章取义」（问原因、片段只有下降幅度）；
#: ② 要求先核对**主体是否同一个**（母公司的子公司 / 同名机构 / 行业协会都不算同一主体）
#:    —— 这堵「指代混淆」（`贵州茅台集团` / `茅台学院` / `茅台机场` ≠ `贵州茅台`）。
#: 实测（12 采样，tuning）：`judge_fp` 中位 **0.293 → 0.086**（区间 [0.052, 0.103]），
#: 代价 `judge_fn` 0.000 → **0.077**（1/13 条可答查询，上限 0.154 ≤ 0.15）。
#: ⚠️ **要素覆盖检查（approach 2）实测被否**（见 `docs/` 外的 `.f1/` 证据与报告 §对照实验）：
#: 它能把 fp 压到 0.034，但同类同义改写（问"分红总额"、答"共计派发现金红利…元"）会被误杀，
#: `judge_fn` 暴涨到 **0.308** ⇒ 净负作用，**不采用**。
#:
#: ⚠️ 2026-10-03 **H2 · `judge_fn` 达标攻关**：**试过、实测被否、已还原**（留档防重做）。
#: 定因：holdout `rel-0020`（"选举出来的职工董事是哪个车间的？"）的 gold 块**排第 1、确实喂给了判官**，
#: 8 次采样判官 **5 次判 irrelevant**（`.h2-scratch/repeat_focus.log`）。失败模式是答案句形态为
#: 「**曾任**…制曲一车间…」，模型按"不是现任 ⇒ 不算答案"拒收（提示词未区分「职务类要素」与
#: 「数值类要素」）。
#: 处置：做了 3 个候选提示词臂（原文 + 职务句算答案；只改主体段；只改答案句段），
#: tuning 上**各 12 采样**对照（`.h2-scratch/tune_{old,new,b,c}.log` / `arm_summary.txt`）：
#:   · fn 中位 0.115 → **0.077**（rel-0020 类确实被修好，holdout 上 8/8 变 relevant）
#:   · 但 `judge_fp` 中位 0.070 → **0.088**，且 fp 行次**逐采样上升 11/12（符号检验）**，
#:     多行次把 fp 推过 0.10 门槛（`.h2-scratch/arm_summary.txt`）
#: ⇒ 三臂**全部不通过**预登记门槛（fp 是硬约束、也是 A3b 的 binding gate）**未采用**；
#:   `rel-0021`（gold 块没有"时长"要素）另属 gold 标注缺陷（`report-H2.md` §4），不由提示词解决。
#: 结论：**在"判官只看前 5 条候选"的口径下，`judge_fn` 的地板是 4/26 = 0.154 > 0.15**
#: （3 条 gold 排 6/8/9 根本没进判官窗 + 1 条真未召回）—— 详见 `report-H2.md` §5。
#: ⚠️ **2026-10-03 处置（用户拍板）**：既然该门槛在现判官设计下**算术不可达**，
#: 把 `judge_fn` 门槛由 0.15 调整为 **0.20**（地板算术与 4 臂杠杆关系见
#: `docs/M1_EVAL_REPORT.md` L147 的「判据门槛修订说明」）；**`judge_fp ≤0.10` 不变**（安全性不放宽）。
PROMPT_TMPL = """你是检索质量判官。给定一个问题与若干候选文档片段，请逐条回答：**这个片段里是否有一句话，单独拿出来就能直接回答该问题？**

问题：{question}

候选片段：
{candidates}

判定方法（必须逐条执行，不得凭"话题相关"或"看起来像"就下结论）：
1. 先确定该问题的答案**应该长什么样**（一个金额 / 一个比例 / 一个日期 / 一个人名 / 一句原因 / 一项事实）。
2. 再确认**主体是否同一个**：问题点名的对象（例如"贵州茅台集团""茅台学院""茅台机场""贵州茅台医院"）
   与片段里那句话的主语必须是**同一个主体**。母公司的子公司、同名机构、行业协会都**不算**同一主体。
3. 在该片段中**逐字找出**能满足要求的那句话（答案句）：
   - 必须是片段中**原样存在**的连续文本，不得改写、不得拼接、不得跨段；
   - 该句**本身必须包含答案**，而不是只提供相关背景（例如问"原因"，片段只写了下降幅度 ⇒ 找不到答案句）。
4. 找得到且主体一致 ⇒ `verdict="relevant"`，把那句话原样放进 `quote`（≥12 字）。
   找不到、或主体不是同一个 ⇒ `verdict="irrelevant"`，`quote` 留空。
5. 只输出 JSON，不要任何解释文字。格式：
{{"items":[{{"chunk_id":<int>,"verdict":"relevant|irrelevant","quote":"<逐字答案句或空串>"}}]}}
"""

_WS_RE = re.compile(r"\s+")
#: 「表意文字 / 全角标点」类字符（CJK 统一表意文字 + 假名 + 全角标点 + CJK 符号）
_CJK_CLASS = (r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
#: 夹在两个 CJK 字符之间的空白（PDF/公告落库时的换行伪影，如 `薪酬分\n案`、`任\n期激励`）
_CJK_WS_RE = re.compile(r"(?<=%s)\s+(?=%s)" % (_CJK_CLASS, _CJK_CLASS))


class _LLMTimeout(Exception):
    """判官单次 LLM 调用超时（有界等待，不杀线程）。"""


def normalize_ws(s):
    """空白归一化（引文比对用）：把**连续空白折叠成单个空格**，并去掉首尾空白。

    ⚠️ 2026-10-03（B-R1 · 审计 B-F10）**语义已收窄**：旧实现是 `re.sub(r"\\s+", "", s)`
    —— 删掉**全部**空白 ⇒ 原文里被空白隔开的两段可以拼成一条"逐字"引文：

        quote_verified("aabbccddeeff", "aa bb cc dd ee ff")   # 旧实现 True，而原文并不连续

    现在只折叠连续空白（`"a  b"` → `"a b"`，`"ab"` 仍是 `"ab"`）⇒ **跨空白拼接会被拒**。
    代价（如实记录，属保守侧）：模型把原文里**不存在**的空白插进引文（原文
    "贵州茅台上半年"、引文 "贵州茅台 上半年"）会判不通过 —— 该方向只会把 `relevant`
    降级为 `uncertain`，**不会放行伪造内容**。换行 ↔ 空格这类"原文本来就有空白"的
    形态变化仍然容忍（模型抄回原文的常见行为）。
    """
    return _WS_RE.sub(" ", s or "").strip()


def normalize_span(s):
    """**跨度归一化**（引文校验专用，2026-10-03 F1 新增）：CJK 感知的空白处理。

    与 `normalize_ws` 的差别只有一条：**夹在两个表意文字/全角标点之间的空白直接删掉**，
    其余空白仍折叠成单个空格。

    ## 为什么必须这样（实测，不是推理）

    语料是 PDF 提取的公告/财报，**换行落在词中是常态**：`薪酬分\n案。` / `任\n期激励` /
    `第\n三次会议`。模型逐字抄回那句话时会自然地把词内换行去掉（人也不会写"薪酬分 案"），
    于是旧的 `normalize_ws`（空白 → 单空格）把**真实存在、逐字一致**的引文判成"不存在"。

    `.f1/span_diag.py` 在 tuning 的 5 个提示词臂 × 采样上取 **700 条模型提出的 relevant**：
    旧口径失败 **111** 条，其中 **110 条**是这种空白形态差异（另 1 条是**真·张冠李戴**：
    模型把 chunk 109 的句子挂到 chunk 63 上）；换成 `normalize_span` 后失败降到 **7 条**
    （5 条是引文短于 `MIN_QUOTE_CHARS`，1 条张冠李戴，1 条同为过短）。
    ⇒ 旧口径把 13% 的正确判罚**误降级成 uncertain**，直接抬高 `judge_fn`、压低 `span_valid`。

    ## 与 B-F10（审计 · `normalize_ws` 的收窄）的关系

    B-F10 关掉的是「**删光全部空白**」，理由是跨空白拼接（`quote_verified("aabbccddeeff",
    "aa bb cc dd ee ff")` 会命中）。本条**不恢复**那条路：
    **拉丁文/数字之间的空白仍然有意义** —— 上例里两串归一化后仍不相等（前者原样、
    后者折叠成 `aa bb cc dd ee ff`），B-F10 的锁（`tests/test_rag_llm_judge.py::
    test_cross_whitespace_concat_quote_is_rejected`）**保持绿灯**。
    被放宽的只有一种形态：**CJK 之间多一个/少一个空白**（排版伪影，不携带语义）。
    代价如实记录：CJK 文本里**确实**被空白分开的两段（如两栏排版）现在也能拼成一条引文；
    但字符本身仍必须**原序、连续**（空白之外的字符一个都不能少），伪造内容依旧过不了。
    """
    s = s or ""
    return _WS_RE.sub(" ", _CJK_WS_RE.sub("", s)).strip()


def quote_verified(quote, text, min_chars=None):
    """引文是否**跨度归一化后逐字**出现在候选正文里（substring）。

    ## 这条校验**能证明**什么

    - 引文（≥ `min_chars` 字）确实是候选正文里的**一段连续文本**（`normalize_span` 后逐字命中）；
    - ⇒ 判 `relevant` 的那条**不是凭空编造**的：模型没有虚构一段原文里不存在的话。

    ## **不能**证明什么（对外表述必读，审计 B-F6/B-F10）

    - **不证明引文足以回答问题**：模型可以引一段真实存在、但**断章取义**的片段
      （实测 4/14 难例被放行且引文全部通过校验，`probe-judge-batch.json`）；
    - **不证明结论正确**：`MIN_QUOTE_CHARS=12` 几乎不构成鉴别力 —— 任何 ≥12 字的原文
      子串都能过（它是"非编造"闸门，**不是"质量"判据**）
      ⚠️ F1 实测：过短的引文确实会**误伤**（`.f1/` 里 7 条失败引文有 5 条是 <12 字，
      例如 `二、董事会会议审议情况`（11 字）本身是对的答案句）—— 但该阈值是审计过的
      反伪造下限，本轮**不动**，如实记为已知代价；
    - **不覆盖改写**：同义改写会被拒（拒绝方向，不影响安全性质）；
    - **CJK 之间的空白形态不再影响判定**（2026-10-03 F1，见 `normalize_span`）。

    参数：`min_chars` 缺省 = `MIN_QUOTE_CHARS`（12）——太短的引文没有鉴别力，且"拼一个字"
    会绕过校验。**绝不抛异常**；非字符串按空串处理。
    """
    if min_chars is None:
        min_chars = MIN_QUOTE_CHARS
    q = normalize_span(quote if isinstance(quote, str) else "")
    if len(q) < max(1, int(min_chars or 0)):
        return False
    return q in normalize_span(text if isinstance(text, str) else "")


# ==================== 主体边界检查（F1 · approach 3，机器可校验） ====================
#: 组织机构后缀 —— 问题主体串以它结尾，才可能"点的是**另一个**主体"。
_ORG_SUFFIXES = ("集团", "公司", "学院", "机场", "医院", "酒业", "银行", "酒店",
                 "旅行社", "基地", "中心", "厂", "学校", "协会", "专卖店", "门店")
#: 候选块标题的「公司名:…」前缀（与 `utils/rag/query_scope.lead_name` 同一形态）
_TITLE_NAME_RE = re.compile(r"^\s*([\u4e00-\u9fa5A-Za-z]{2,10}?)[：:]")
#: 主体串的右边界：第一个「的」或疑问词
_SUBJECT_CUT_RE = re.compile(r"的|是多少|有多少|有多大|有多高|有多长|有多久|多少|如何|怎么样|是否|吗")
#: 主体串尾部要去掉的时间/指示词
_SUBJECT_TAIL_RE = re.compile(
    r"(去年|今年|明年|上年|本年|本年度|前年|最近|现在|目前|一年|[0-9]{4}\s*年)+$")
#: 主体串头部要去掉的虚词
_SUBJECT_HEAD_RE = re.compile(r"^(请问|那么|这次|这批|该|本|这个|那个|上述|公司)+")


def chunk_company(title):
    """候选块所属的公司名（`公司名:标题` 前缀）；取不到返回 `""`（保守：不干预）。"""
    m = _TITLE_NAME_RE.match((title or "").strip())
    return m.group(1) if m else ""


def query_subject(query):
    """问题点名的**主体串**（第一个「的」/疑问词之前那一段，去掉首尾时间与虚词）。"""
    q = re.sub(r"[^\u4e00-\u9fff0-9（）()]", "", query or "")
    cut = len(q)
    for m in _SUBJECT_CUT_RE.finditer(q):
        cut = min(cut, m.start())
    return _SUBJECT_HEAD_RE.sub("", _SUBJECT_TAIL_RE.sub("", q[:cut]))


def subject_mismatch(query, title, quote):
    """**主体边界检查**：问题点名的对象是否**不是**本块的主体（机器可校验，绝不抛）。

    判定「不一致」需三条同时成立：
    1. 主体串以**组织机构后缀**结尾（`…集团` / `…学院` / `…公司` / `…机场` / `…医院`）；
    2. 主体串**含本块公司名或其 2 字别名** —— 否则问题问的是别的标的（检索侧已按标的收窄池），
       本块无责任，不干预；
    3. 主体串 **≠ 公司名**，且**没有逐字出现在引文里**。

    只在**判 `relevant` 之后**调用；命中 ⇒ 降 `uncertain`（**不**置 `quote_rejected`：
    引文本身逐字命中，被否的是"主体不是同一个"）。

    ⚠️ **实测效果（tuning，24 次采样，CJK 口径）**：在**新版提示词之上**它只让
    `judge_fp` 中位 0.0776 → **0.0690**，配对「更好 1 / 更差 0 / 相同 23」——**边际量级**。
    保留它的理由不是分数，而是它是这套判罚里**唯一确定性的**一环（不依赖模型是否听话）；
    在旧提示词上配对「更好 7 / 更差 0 / 相同 5」（n=12）。如实记录，不夸大。
    """
    try:
        subj = query_subject(query)
        if len(subj) < 4 or not subj.endswith(_ORG_SUFFIXES):
            return False
        name = chunk_company(title)
        if not name:
            return False
        if name not in subj and name[-2:] not in subj:
            return False
        if subj == name:
            return False
        qq = normalize_span(quote)
        if subj in qq or re.sub(r"[^\u4e00-\u9fff]", "", subj) in re.sub(r"[^\u4e00-\u9fff]", "", qq):
            return False
        # 引文没用**逐字全名**、但点名了同一个主体（如全称"中国贵州茅台酒厂（集团）有限责任公司"
        # 内含别名"茅台" + 判别后缀"集团"）⇒ 也算同一主体，不干预。
        alias, suffix = name[-2:], _distinctive_suffix(subj)
        if suffix and alias in qq and suffix in qq:
            return False
        return True
    except Exception:  # noqa: BLE001 - 判官是旁路：任何异常都不得影响判定
        return False


def _distinctive_suffix(subj):
    """主体串里**最有判别力**的组织后缀（`贵州茅台集团财务有限公司` → `集团` 而非 `公司`）。"""
    for s in ("集团", "学院", "机场", "医院", "酒业", "银行", "酒店", "旅行社",
              "基地", "中心", "专卖店", "门店", "厂", "学校", "协会"):
        if s in subj:
            return s
    for s in _ORG_SUFFIXES:
        if subj.endswith(s):
            return s
    return ""


def _extract_json_object(text):
    """从模型文本里取第一个**括号配平**的 JSON 对象；找不到返回 None。

    比 `re.search(r"\\{[\\s\\S]*\\}")`（原型写法）稳：模型在 JSON 后补充说明、
    或引文里带 `{`/`}` 时，贪心正则会把尾巴一起吞进来导致解析失败。
    逐层扫描并**逐个起点重试**，任一候选串坏掉不影响后面的起点。
    """
    if not text:
        return None
    s = str(text)
    start = s.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:  # noqa: BLE001 - 该起点不是合法 JSON，试下一个
                        break
        start = s.find("{", start + 1)
    return None


def _extract_text(raw):
    """从 `llm_fn` 的返回里取出**模型文本**。

    ⚠️ 实测（`task-B1.md` §1.0 末行）：`utils.ai_helper.call_llm` 返回的是**包了一层**的
    `{"type": "text", "content": "<模型文本>", "usage": {...}}`。**必须取 `.content`**；
    否则会把外层对象当模型输出 → 解析到 0 条 → 判官形同失效（监督者首探针就踩过）。
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str):
            return content
        return json.dumps(raw, ensure_ascii=False, default=str)
    return "" if raw is None else str(raw)


def _resolve_llm(llm_fn):
    """解析实际调用的 LLM 函数：显式 `llm_fn` > `utils.ai_helper.call_llm`（晚绑定）。

    晚绑定（函数内 import + 取属性）让 `monkeypatch.setattr(ai_helper, "call_llm", ...)`
    与产线同路径生效；解析失败返回 None ⇒ 调用方降级（**不新起 client**，硬约束）。
    """
    if llm_fn is not None:
        return llm_fn if callable(llm_fn) else None
    try:
        from utils import ai_helper
        fn = getattr(ai_helper, "call_llm", None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 - 判官是旁路，解析失败一律降级
        return None


def _call_bounded(fn, prompt, timeout_s):
    """有界等待地调用 `fn(prompt)`；超时抛 `_LLMTimeout`（线程不杀，daemon 自行收尾）。"""
    if not timeout_s or timeout_s <= 0:
        return fn(prompt)
    box = {}

    def _run():
        try:
            box["value"] = fn(prompt)
        except BaseException as e:  # noqa: BLE001 - 原样带回主线程再降级
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True, name="rag-judge-llm")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise _LLMTimeout("判官 LLM 调用超过 %.1fs" % timeout_s)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def build_prompt(question, candidates, per_chunk_chars=DEFAULT_PER_CHUNK_CHARS,
                 max_total_chars=DEFAULT_MAX_TOTAL_CHARS):
    """组装判官 prompt（返回 str）。

    - 每块只喂 `title + text[:per_chunk_chars]`（规则 2：裁剪）；
    - 整条 prompt（含模板）不超过 `max_total_chars`；超预算的候选**不喂**，
      由 `judge_candidates` 标成 `uncertain`（绝不假装判过）。
    """
    skeleton = PROMPT_TMPL.format(question=question, candidates="")
    budget = max(0, int(max_total_chars) - len(skeleton))
    blocks, used = [], 0
    for c in candidates:
        cid = c.get("chunk_id")
        title = c.get("title") or ""
        text = (c.get("text") or "")
        if not isinstance(text, str):
            text = str(text)
        block = "[chunk_id=%s] title=%s\n%s\n" % (cid, title, text[:per_chunk_chars])
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    return PROMPT_TMPL.format(question=question, candidates="\n".join(blocks))


def judge_candidates(question, candidates, *, llm_fn=None,
                     per_chunk_chars=DEFAULT_PER_CHUNK_CHARS,
                     timeout_s=DEFAULT_TIMEOUT_S,
                     max_total_chars=DEFAULT_MAX_TOTAL_CHARS):
    """判官主入口（纯逻辑，可单测）。返回：

    ```
    {"level": "relevant"|"irrelevant"|"uncertain",
     "items": [{"chunk_id": ..., "verdict": ..., "quote": ..., "quote_rejected": bool}],
     "checked": bool, "reason": str, "latency_ms": int}
    ```

    语义：
    - `checked=False` ⇒ 判官**没有**产出可信结论（无候选 / 无 LLM / 超时 / 解析失败 /
      上游异常），`items` 为空，`reason` 说明原因；
    - `checked=True` ⇒ 模型输出已解析；`items` **对每条候选都有落点**
      （模型漏答 / 超预算 / 引文被拒 ⇒ `uncertain`，不得当"无关"也不得当"相关"）；
    - `level` 是整体档位：任一 `relevant` ⇒ `relevant`；否则有 `uncertain` ⇒ `uncertain`；
      全 `irrelevant` ⇒ `irrelevant`。

    **绝不抛异常**。
    """
    t0 = time.time()
    out = {"level": LEVEL_UNCERTAIN, "items": [], "checked": False,
           "reason": "", "latency_ms": 0}
    cands = [c for c in (candidates or []) if isinstance(c, dict)]
    if not cands:
        out["reason"] = "no_candidates"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    fn = _resolve_llm(llm_fn)
    if fn is None:
        out["reason"] = "no_llm"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    prompt = build_prompt(question, cands, per_chunk_chars, max_total_chars)
    try:
        raw = _call_bounded(fn, prompt, timeout_s)
    except _LLMTimeout:
        out["reason"] = "timeout"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out
    except Exception:  # noqa: BLE001 - 上游任何异常都降级，不打断主链路
        out["reason"] = "llm_error"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    parsed = _extract_json_object(_extract_text(raw))
    model_items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(model_items, list):
        out["reason"] = "parse_error"
        out["latency_ms"] = int((time.time() - t0) * 1000)
        return out

    by_id = {}
    for mi in model_items:
        if not isinstance(mi, dict):
            continue
        key = str(mi.get("chunk_id"))
        if key not in by_id:          # 重复 id 取首次
            by_id[key] = mi

    items = []
    for c in cands:
        cid = c.get("chunk_id")
        text = c.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        mi = by_id.get(str(cid))
        if mi is None:
            # 模型漏答 / 超预算未喂 ⇒ 未确认（不替模型编结论）
            items.append({"chunk_id": cid, "verdict": LEVEL_UNCERTAIN,
                          "quote": "", "quote_rejected": False})
            continue
        verdict = str(mi.get("verdict") or "").strip().lower()
        if verdict not in (LEVEL_RELEVANT, LEVEL_IRRELEVANT):
            verdict = LEVEL_UNCERTAIN
        quote = mi.get("quote") if isinstance(mi.get("quote"), str) else ""
        rejected = False
        if verdict == LEVEL_RELEVANT and not quote_verified(quote, text):
            # ★ 核心防线：引文不存在 ⇒ 降级 + 留痕，不允许"看起来像"
            verdict = LEVEL_UNCERTAIN
            rejected = True
        elif verdict == LEVEL_RELEVANT and subject_mismatch(question, c.get("title"), quote):
            # ★ F1 · approach 3：引文逐字命中，但**主体不是同一个**（母公司/子公司/同名机构）
            #   ⇒ 降级为 uncertain。不置 `quote_rejected`（引文本身没问题，被否的是主体）。
            verdict = LEVEL_UNCERTAIN
        items.append({"chunk_id": cid, "verdict": verdict,
                      "quote": quote, "quote_rejected": rejected})

    if any(i["verdict"] == LEVEL_RELEVANT for i in items):
        level = LEVEL_RELEVANT
    elif any(i["verdict"] == LEVEL_UNCERTAIN for i in items):
        level = LEVEL_UNCERTAIN
    else:
        level = LEVEL_IRRELEVANT

    out.update({"level": level, "items": items, "checked": True, "reason": ""})
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out
