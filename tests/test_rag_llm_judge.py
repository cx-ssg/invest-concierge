# -*- coding: utf-8 -*-
"""B1 · LLM 判官（异步 + 仅 weak 触发 + 引文确定性校验）—— 契约锁。

任务书：`task-B1.md` §1.1/§1.2/§3 + §2 的 V1 必测项。本文件的断言分四层：

1. **纯逻辑层**（`utils/rag/llm_judge.py`）：引文确定性校验 / 裁剪 / 诚实降级；
2. **降级层**：超时、解析失败、异常、无 LLM ⇒ `uncertain` 且**绝不抛异常**；
3. **触发条件层**（`services/judge_service.py`）：仅 `evidence_level == "weak"` 才调 LLM
   （`none` 档**一次都不许调**，用计数断言）；
4. **可达性层**：**真** `agent_run` → **真** SSE `stream_events` 路径（不是只测纯函数）。

⚠️ 本仓连续三次同族事故：评测不经过被测对象 / 薄封装引用不存在的模块 / 打桩只测映射不测可达分支
（`task-B1.md` §2 V1 末条）。因此第 4 层用的是**真** `retrieve_docs`（临时 kb.db）+ 事件顺序断言。

⚠️ 「指标必须能随目标退化而变红」：`test_quote_check_reverse_control_turns_red_when_neutered`
把校验函数打桩成**恒真**——该用例在那种情况下**必须红**（它自己会失败）。这是刻意设计的反证。
"""
import ast
import json
import os
import re
import threading
import time
from concurrent.futures import Future

import pytest

from services import agent_service, judge_service
from utils import agent_core, ai_helper
from utils.rag import llm_judge
from utils.rag import store as rag_store
from utils.rag.retrieve import retrieve_docs

# ==================== 语料/查询（与 tests/test_rag_sources.py 同口径，实测 weak / none） ====================
WEAK_QUERY = "贵州茅台上半年营业收入同比增长"
WEAK_VEC = [1.0, 0.0]
NONE_QUERY = "茅台明天的股价是多少"
NONE_VEC = [0.6, 0.8]

MAOTAI_TEXT = "贵州茅台上半年营业收入同比增长百分之十五，净利润增速略低于营收增速。"
FORGED_QUOTE = "这段引文在候选片段里根本不存在绝不可能逐字命中"
#: `_payload()` 造出的假工具返回里的正文（≥12 字 ⇒ 逐字引文校验可达）
PAYLOAD_TEXT = "贵州茅台上半年营业收入同比增长百分之十五。"

#: `evidence_judged` 事件的**完整**键集（协议锁；多一个少一个都算协议变更）
JUDGE_EVENT_KEYS = {"type", "query", "level", "items", "checked", "latency_ms", "reason"}


@pytest.fixture()
def kb(tmp_path):
    """真实形态的临时知识库：1 篇公告 / 3 个块（含真向量）。与 test_rag_sources 同构。"""
    db = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台2026年半年度报告",
        "url": "https://example.com/maotai-h1", "published_at": "2026-08-15",
    })
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": MAOTAI_TEXT, "is_table": False},
        {"seq": 1, "text": "公司表示直销渠道占比继续提升，i茅台平台贡献显著。", "is_table": False},
        {"seq": 2, "text": "比亚迪新能源汽车七月销量创新高，海外出口同比增长明显。", "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    conn.close()
    return db


# ==================== 工具函数 ====================


def _cands():
    return [{"chunk_id": 1, "title": "贵州茅台2026年半年度报告", "text": MAOTAI_TEXT}]


def _llm_text(payload, wrapper=True):
    """构造一个假 `llm_fn` 返回值。

    ⚠️ `wrapper=True` 复刻 `utils/ai_helper.call_llm` 的**真实返回结构**：
    `{"type": "text", "content": "<模型文本>", "usage": {...}}` —— 任务书 §1.0 末行实测：
    不取 `.content` 会解析到外层对象 ⇒ 判官形同失效。本文件的接受用例因此**必须**走包装态。
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if wrapper:
        return {"type": "text", "content": text, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return text


def _first_chunk_text(prompt, cid):
    """从判官 prompt 里取出 `[chunk_id=cid]` 那一块的正文（假模型"照抄原文"用）。"""
    seg = prompt.split("[chunk_id=%s]" % cid, 1)[1]
    seg = seg.split("[chunk_id=", 1)[0]
    if "\n" in seg:                       # 去掉 "title=..." 头行
        seg = seg.split("\n", 1)[1]
    return seg.strip()


def _judge_reply(prompt, *, first_verdict="relevant", forged=False, omit_others=False):
    """按 prompt 里的真实 chunk_id 生成一份"模型回复"（第一条按参数，其余判 irrelevant）。"""
    ids = re.findall(r"\[chunk_id=([^\]]+)\]", prompt)
    items = []
    for i, cid in enumerate(ids):
        if i == 0:
            quote = FORGED_QUOTE if forged else _first_chunk_text(prompt, cid)[:24]
            items.append({"chunk_id": cid, "verdict": first_verdict, "quote": quote})
        elif not omit_others:
            items.append({"chunk_id": cid, "verdict": "irrelevant", "quote": ""})
    return _llm_text({"items": items})


# ==================== 第 1 层：引文确定性校验（本任务的核心防线） ====================


def test_verbatim_quote_is_accepted_and_marked_relevant():
    """≥12 字逐字引文 ⇒ 该条 `relevant` 且 `quote_rejected=False`。"""
    out = llm_judge.judge_candidates(
        "茅台上半年营收怎么样", _cands(),
        llm_fn=lambda prompt: _judge_reply(prompt))
    assert out["checked"] is True
    assert out["level"] == "relevant"
    assert out["items"][0]["verdict"] == "relevant"
    assert out["items"][0]["quote_rejected"] is False
    assert out["latency_ms"] >= 0


def test_whitespace_normalized_quote_is_accepted():
    """空白折叠后命中即可 —— **原文里本来就有空白**时允许换形态（换行 ↔ 空格）。

    ⚠️ B-R1（审计 B-F10）**语义已收窄**：旧实现删掉**全部**空白；现在只把连续空白
    折叠成单个空格 ⇒ 原文里**没有**空白的位置，引文里补上空白不再放行
    （见 `test_cross_whitespace_concat_quote_is_rejected`）。
    """
    text = "贵州茅台上半年营业收入\n同比增长百分之十五，净利润略降。"

    def fake(prompt):
        quote = "贵州茅台上半年营业收入 同比增长百分之十五"   # 换行 → 空格（原文有空白的那个位置）
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant", "quote": quote}]})

    out = llm_judge.judge_candidates(
        "营收", [{"chunk_id": 1, "title": "T", "text": text}], llm_fn=fake)
    assert out["items"][0]["quote_rejected"] is False
    assert out["items"][0]["verdict"] == "relevant"


def test_normalize_ws_collapses_runs_instead_of_deleting_them():
    """B-F10：归一化 = 连续空白 → 单空格（+ 去首尾），**不再删光空白**。"""
    assert llm_judge.normalize_ws("a b") == "a b"
    assert llm_judge.normalize_ws(" a\t\tb\nc ") == "a b c"
    assert llm_judge.normalize_ws("ab") == "ab"
    # 关键：折叠后 "a b" 与 "ab" **不相等**（旧实现里两者相等 ⇒ 跨空白拼接可过校验）
    assert llm_judge.normalize_ws("a b") != llm_judge.normalize_ws("ab")


def test_cross_whitespace_concat_quote_is_rejected():
    """B-F10：原文**不连续**的两段，不能靠"删空白"拼成一条通过校验的引文。

    这条用例的反向对照：旧口径（删光空白）下同一条引文**会命中** —— 所以本断言
    在旧实现上必红（红/绿留证见 report-B-R1.md §B-F10）。
    """
    text = "aabbccddeeff"
    quote = "aa bb cc dd ee ff"
    assert llm_judge.quote_verified(quote, text) is False
    # 旧口径（`re.sub(r"\s+", "", s)`）确实会放行同一条引文 ⇒ 证明本用例测的是真行为
    legacy = re.sub(r"\s+", "", quote)
    assert legacy in text, "前置：这条引文只在'删光空白'的旧口径下命中"


def test_cjk_linebreak_quote_is_accepted_and_keeps_verdict():
    """F1（2026-10-03）：**CJK 之间的空白是排版伪影**（PDF 换行落在词中）⇒ 不该否掉引文。

    语料是 PDF 提取的公告，`薪酬分\\n案。` / `任\\n期激励` 是常态；模型逐字抄回那句话时
    会自然地把词内换行去掉。旧的 `normalize_ws`（空白 → 单空格）把这类**真实存在、
    逐字一致**的引文判成"不存在"。

    **红/绿留证**：把 `quote_verified` 换回 `normalize_ws` 口径 ⇒ 本用例必红
    （实测 tuning 上 111 条被拒引文里 **110 条**属此类，直接抬高 `judge_fn`、压低 `span_valid`）。
    """
    text = "贵州茅台上半年营业收入\n同比增长百分之十五，净利润略降。"

    def fake(prompt):
        # 模型把原文里那个**词内换行**去掉（人也不会写"营业收入 同比增长"）
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant",
                                     "quote": "贵州茅台上半年营业收入同比增长百分之十五"}]})

    out = llm_judge.judge_candidates(
        "营收", [{"chunk_id": 1, "title": "T", "text": text}], llm_fn=fake)
    item = out["items"][0]
    assert item["quote_rejected"] is False
    assert item["verdict"] == llm_judge.LEVEL_RELEVANT
    assert out["level"] == llm_judge.LEVEL_RELEVANT


def test_normalize_span_drops_cjk_inner_whitespace_only():
    """`normalize_span` 的**边界**：只吃 CJK 之间的空白，拉丁文词间空白保留（B-F10 性质）。"""
    assert llm_judge.normalize_span("薪酬分\n案。") == "薪酬分案。"
    assert llm_judge.normalize_span("净利润 15% 增长") == "净利润 15% 增长"  # 拉丁/数字侧不删
    assert llm_judge.normalize_span(" aa\t\tbb ") == "aa bb"
    # B-F10 的原始反例：拉丁字母之间的空白**有语义** ⇒ 两串仍不相等
    assert llm_judge.normalize_span("aa bb cc dd ee ff") != llm_judge.normalize_span("aabbccddeeff")
    # `normalize_ws` 本体**未被改动**（B-F10 的锁仍锁在它身上）
    assert llm_judge.normalize_ws("薪酬分\n案。") == "薪酬分 案。"


def test_latin_cross_whitespace_concat_quote_is_still_downgraded():
    """B-F10 的性质在**拉丁文**上原样保留：删掉词间空白 = 跨空白拼接 ⇒ 降级 + 留痕。

    与 `test_cjk_linebreak_quote_is_accepted_and_keeps_verdict` 是同一条规则的两侧：
    F1 放宽的**只有** CJK 排版伪影，拉丁文那侧的锁**没有**被放宽。
    """
    text = "revenue aa bb cc dd ee ff growth reported here"
    assert llm_judge.quote_verified("revenue aabbccddeeff growth", text) is False

    def fake(prompt):
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant",
                                     "quote": "revenue aabbccddeeff growth"}]})

    out = llm_judge.judge_candidates(
        "revenue", [{"chunk_id": 1, "title": "T", "text": text}], llm_fn=fake)
    item = out["items"][0]
    assert item["quote_rejected"] is True
    assert item["verdict"] == llm_judge.LEVEL_UNCERTAIN
    assert out["level"] == llm_judge.LEVEL_UNCERTAIN


def test_forged_quote_is_rejected_and_downgraded():
    """**伪造引文** ⇒ 该条 `verdict != relevant` 且 `quote_rejected=True`，整体 level=uncertain。

    「不允许看起来像」—— 判官**永远不能**把不可信结果伪装成 relevant。
    """
    out = llm_judge.judge_candidates(
        "茅台上半年营收怎么样", _cands(),
        llm_fn=lambda prompt: _judge_reply(prompt, forged=True))
    item = out["items"][0]
    assert item["verdict"] != "relevant"
    assert item["verdict"] == llm_judge.LEVEL_UNCERTAIN
    assert item["quote_rejected"] is True
    assert out["level"] == "uncertain"


def test_short_quote_is_rejected():
    """引文 < 12 字 ⇒ 同样降级（防"拼一个字"绕过逐字校验）。"""
    def fake(prompt):
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant", "quote": "营业收入"}]})

    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=fake)
    assert out["items"][0]["quote_rejected"] is True
    assert out["items"][0]["verdict"] != "relevant"


# ==================== F1 · approach 3：主体边界检查（可机器校验） ====================
#: 母公司/子公司语境：问题问"集团"，候选块属于上市公司
GROUP_TEXT = "贵州茅台酒股份有限公司2026年上半年实现营业收入八百亿元，同比增长百分之十五。"


def test_query_subject_extracts_org_chain():
    """主体串抽取：第一个「的」/疑问词之前那一段，去掉尾部时间词与头部虚词。"""
    q = llm_judge.query_subject
    assert q("贵州茅台集团2025年的营业收入是多少") == "贵州茅台集团"
    assert q("贵州遵义茅台机场去年的旅客吞吐量是多少") == "贵州遵义茅台机场"
    assert q("贵州茅台（集团）生态农业产业发展有限公司的销售额是多少") == \
        "贵州茅台（集团）生态农业产业发展有限公司"
    assert q("贵州茅台的长期战略是否清晰") == "贵州茅台"
    assert q("公司净利润下降的原因是什么？") == "净利润下降"
    assert llm_judge._distinctive_suffix("贵州茅台集团财务有限公司") == "集团"
    assert llm_judge._distinctive_suffix("贵州遵义茅台机场") == "机场"


def test_subject_mismatch_flags_group_vs_listed_company():
    """问题问"贵州茅台**集团**"，引文讲的是上市公司 ⇒ **不一致**（母公司的子公司 ≠ 同一主体）。"""
    assert llm_judge.chunk_company("贵州茅台:贵州茅台2026年半年度报告") == "贵州茅台"
    assert llm_judge.subject_mismatch("贵州茅台集团2025年的营业收入是多少",
                                      "贵州茅台:贵州茅台2026年半年度报告", GROUP_TEXT) is True
    # 引文**点名**了那个主体 ⇒ 不算不一致
    assert llm_judge.subject_mismatch(
        "贵州茅台集团2025年的营业收入是多少",
        "贵州茅台:贵州茅台2026年半年度报告",
        "中国贵州茅台酒厂（集团）有限责任公司2025年营业收入为一千亿元。") is False


def test_subject_mismatch_does_not_fire_on_same_or_unrelated_subject():
    """**不该触发**的三种情形（防误杀）：主体同一 / 主体是产品词 / 问题没点名别的标的。"""
    title = "贵州茅台:贵州茅台2026年半年度报告"
    # ① 主体 == 公司名
    assert llm_judge.subject_mismatch("贵州茅台的营业收入是多少", title, GROUP_TEXT) is False
    # ② 产品名（茅台酒）没有组织机构后缀 ⇒ 不是"另一个主体"
    assert llm_judge.subject_mismatch("这次茅台酒的价格上调了多少", title, GROUP_TEXT) is False
    # ③ 问题点名的是**别的标的**（本块无责任，检索侧已按标的收窄池）⇒ 不干预
    assert llm_judge.subject_mismatch("五粮液的营业收入是多少", title, GROUP_TEXT) is False
    # ④ 标题取不到公司名（非 `公司名:标题` 形态）⇒ 保守不干预
    assert llm_judge.subject_mismatch("贵州茅台集团2025年的营业收入是多少", "无名标题", GROUP_TEXT) is False


def test_subject_mismatch_downgrades_judge_verdict_without_quote_reject():
    """判官层面：引文逐字命中但主体不一致 ⇒ 降 `uncertain`；**不**置 `quote_rejected`。"""
    def fake(prompt):
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant",
                                     "quote": "2026年上半年实现营业收入八百亿元"}]})

    out = llm_judge.judge_candidates(
        "贵州茅台集团2025年的营业收入是多少",
        [{"chunk_id": 1, "title": "贵州茅台:贵州茅台2026年半年度报告", "text": GROUP_TEXT}],
        llm_fn=fake)
    item = out["items"][0]
    assert item["quote_rejected"] is False, "引文本身逐字命中，被否的是主体"
    assert item["verdict"] == llm_judge.LEVEL_UNCERTAIN
    assert out["level"] == llm_judge.LEVEL_UNCERTAIN


def test_subject_mismatch_reverse_control_turns_red_when_neutered(monkeypatch):
    """**反向对照**：把 `subject_mismatch` 打成恒假 ⇒ 上面那条判官用例**必须红**（证明它有牙）。"""
    monkeypatch.setattr(llm_judge, "subject_mismatch", lambda q, t, c: False)

    def fake(prompt):
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant",
                                     "quote": "2026年上半年实现营业收入八百亿元"}]})

    out = llm_judge.judge_candidates(
        "贵州茅台集团2025年的营业收入是多少",
        [{"chunk_id": 1, "title": "贵州茅台:贵州茅台2026年半年度报告", "text": GROUP_TEXT}],
        llm_fn=fake)
    assert out["level"] == llm_judge.LEVEL_RELEVANT, (
        "闸门被打桩成恒假后仍判 none/uncertain ⇒ 说明判官根本没调用 subject_mismatch")



def test_missing_quote_is_rejected():
    """判 relevant 却不给引文 ⇒ 降级（不得因"没给"而放行）。"""
    def fake(prompt):
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant", "quote": ""}]})

    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=fake)
    assert out["items"][0]["quote_rejected"] is True


def test_quote_check_reverse_control_turns_red_when_neutered(monkeypatch):
    """**反向对照（任务书 §2 V1 明确要求）**：把校验函数改成恒真 ⇒ 伪造引文会被**放行**。

    这条不是"断言缺陷"，而是**证明 `test_forged_quote_is_rejected_and_downgraded` 有牙**：
    它把 `quote_verified` 打桩成恒真，然后论证"若生产实现被改成恒真/被绕过，
    那条拒绝用例必然变红"。两个方向合起来才是完整的自检：

    - 生产 `quote_verified` 被改成 `return True` ⇒ **拒绝用例红**（本报告有实跑记录）；
    - `judge_candidates` 绕过 `quote_verified`（内联一条平行校验）⇒ **本用例红**
      （伪造引文不会被放行，`verdict` 仍是 uncertain）。

    ⚠️ 看到本用例红 = 有人在为"引文校验"装上恒真实现，或改走了平行实现。**不要**改这个断言。
    """
    monkeypatch.setattr(llm_judge, "quote_verified", lambda quote, text, min_chars=None: True)
    out = llm_judge.judge_candidates(
        "茅台上半年营收怎么样", _cands(),
        llm_fn=lambda prompt: _judge_reply(prompt, forged=True))
    assert out["items"][0]["verdict"] == "relevant", (
        "校验被旁路后伪造引文**没有**被放行 ⇒ 说明 judge_candidates 没有调用 quote_verified"
        "（走了一条平行实现），此时拒绝用例其实测不到真实的校验链路")
    assert out["level"] == "relevant"


def test_quote_verified_is_actually_consulted(monkeypatch):
    """正向锁：校验函数被调用过（而不是靠正则内联出一条平行实现）。"""
    calls = []
    real = llm_judge.quote_verified

    def spy(quote, text, min_chars=None):
        calls.append((quote, text))
        return real(quote, text, min_chars) if min_chars is not None else real(quote, text)

    monkeypatch.setattr(llm_judge, "quote_verified", spy)
    llm_judge.judge_candidates("营收", _cands(), llm_fn=lambda prompt: _judge_reply(prompt))
    assert calls, "judge_candidates 必须经 quote_verified 做引文校验（可被反向对照打桩）"


# ==================== 第 2 层：诚实降级（绝不抛异常） ====================


def test_timeout_degrades_to_uncertain_without_raising():
    """假 `llm_fn` sleep > `timeout_s` ⇒ `uncertain` / `checked=False`，不抛、不阻塞。"""
    def slow(prompt):
        time.sleep(1.5)
        return _llm_text({"items": []})

    t0 = time.time()
    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=slow, timeout_s=0.2)
    wall = time.time() - t0
    assert out["checked"] is False
    assert out["level"] == "uncertain"
    assert out["reason"] == "timeout"
    assert wall < 1.2, "超时判定必须**有界**（实测 %.2fs）" % wall


def test_parse_failure_degrades_to_uncertain():
    """`llm_fn` 返回垃圾 ⇒ `uncertain`（不是把垃圾当 relevant）。"""
    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=lambda p: _llm_text("这不是 JSON"))
    assert out["checked"] is False
    assert out["level"] == "uncertain"
    assert out["reason"] == "parse_error"


def test_llm_exception_degrades_to_uncertain():
    """`llm_fn` 抛异常 ⇒ 吞掉并降级（判官是旁路，不得打断主链路）。"""
    def boom(prompt):
        raise RuntimeError("上游 500")

    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=boom)
    assert out["checked"] is False and out["level"] == "uncertain"


def test_no_key_demo_text_degrades_to_uncertain(monkeypatch):
    """真实降级形态：无 Key 时 `call_llm` 直接返回提示文本（不是 JSON）⇒ `uncertain`。

    这条**经过真实的默认 llm 解析**（`utils.ai_helper.call_llm` 晚绑定），不是传假函数。
    """
    monkeypatch.setattr(ai_helper, "call_llm",
                        lambda *a, **k: {"type": "text", "content": "⚠️ 请先在设置页配置 API Key"})
    out = llm_judge.judge_candidates("营收", _cands())
    assert out["checked"] is False and out["level"] == "uncertain"


def test_empty_candidates_do_not_call_llm():
    """没有候选 ⇒ 不调 LLM、`checked=False`（空转不花 token）。"""
    called = []
    out = llm_judge.judge_candidates("营收", [], llm_fn=lambda p: called.append(1) or _llm_text({}))
    assert out["checked"] is False and out["items"] == []
    assert called == []


def test_prompt_is_trimmed_per_chunk():
    """输入裁剪：每块只喂 `per_chunk_chars` 字（原型 43.7s 的 90% 是 prefill ⇒ 裁剪是主杠杆）。"""
    long_text = "甲" * 5000
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return _judge_reply(prompt)

    llm_judge.judge_candidates("问题", [{"chunk_id": 7, "title": "T", "text": long_text}],
                               llm_fn=fake, per_chunk_chars=120)
    assert "甲" * 120 in seen["prompt"]
    assert "甲" * 121 not in seen["prompt"]


def test_prompt_has_total_budget():
    """总 prompt 有上限：超预算的候选不喂给模型（该条降级为 uncertain，绝不假装判过）。"""
    cands = [{"chunk_id": i, "title": "T", "text": "乙" * 500} for i in range(10)]
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return _judge_reply(prompt)

    out = llm_judge.judge_candidates("问题", cands, llm_fn=fake,
                                     per_chunk_chars=500, max_total_chars=1200)
    assert len(seen["prompt"]) <= 1200 + 400, "prompt 必须受总预算约束"
    assert len(out["items"]) == 10, "每条候选都要有落点（未喂进去的 = uncertain）"
    assert any(i["verdict"] == "uncertain" for i in out["items"])


def test_llm_fn_bare_string_is_supported():
    """`llm_fn` 返回**裸字符串**也要能解析（覆盖两种上游形态）。"""
    out = llm_judge.judge_candidates(
        "营收", _cands(),
        llm_fn=lambda prompt: _judge_reply(prompt, first_verdict="irrelevant"))
    assert out["checked"] is True
    assert out["level"] == "irrelevant"


def test_items_cover_every_candidate_even_if_model_omits():
    """模型漏答的候选 ⇒ `uncertain`（不得按"没提"当无关，也不得当相关）。"""
    def fake(prompt):
        return _llm_text({"items": []})

    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=fake)
    assert len(out["items"]) == 1
    assert out["items"][0]["verdict"] == "uncertain"
    assert out["level"] == "uncertain"


# ==================== 第 3 层：触发条件（仅 weak；none 一次都不许调） ====================


def _payload(level, query="q", results=None):
    return json.dumps({
        "query": query, "code": None, "message": "m", "evidence_level": level,
        "evidence": {"sar": 0.1, "v1": 0.5},
        "results": results if results is not None else [
            {"rank": 1, "chunk_id": 11, "title": "T", "text": PAYLOAD_TEXT, "url": None,
             "source": "notice", "published_at": "2026-08-15", "code": "600519", "is_table": False},
        ],
    }, ensure_ascii=False)


def _trace(level, output=None, name="retrieve_docs"):
    return [{"name": name, "arguments": {"query": "q"}, "output": output or _payload(level)}]


def test_collect_targets_only_weak_with_results():
    """触发条件：仅 `weak` **且** 有结果。`none` / 缺档位 / 空结果 / 非检索工具 ⇒ 零目标。"""
    assert len(judge_service.collect_from_tool_trace(_trace("weak"))) == 1
    assert judge_service.collect_from_tool_trace(_trace("none")) == []
    assert judge_service.collect_from_tool_trace(_trace(None)) == []
    assert judge_service.collect_from_tool_trace(
        _trace("weak", _payload("weak", "q", results=[]))) == []
    assert judge_service.collect_from_tool_trace(
        _trace("weak", output=_payload("weak"), name="get_stock_info")) == []


def test_collect_targets_decodes_double_encoded_tool_output():
    """生产实况：`execute_ai_tool_v2` 把字符串工具结果**再 dumps 一次**（双层编码）。

    只解一层的实现会判成"无结果" ⇒ 判官在产线上**永不触发**，而喂单层 JSON 的单测全绿
    （`utils/rag/sources.py` 头注记的同族坑）。
    """
    double = json.dumps(_payload("weak"))
    targets = judge_service.collect_from_tool_trace(_trace("weak", double))
    assert len(targets) == 1
    assert targets[0]["candidates"][0]["text"] == PAYLOAD_TEXT


def test_none_payload_never_invokes_llm():
    """`none` 档：判官**根本不被调用**（假 `llm_fn` 计数断言 0 次）。"""
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return _llm_text({"items": []})

    events = judge_service.judge_tool_trace(_trace("none"), llm_fn=fake)
    assert events == []
    assert calls == [], "none 档必须零调用（不改既有行为）"


def test_weak_payload_invokes_llm_once_and_returns_event():
    """`weak` 档：调一次，事件体字段与协议一致。"""
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return _judge_reply(prompt)

    events = judge_service.judge_tool_trace(_trace("weak"), llm_fn=fake, timeout_s=5)
    assert len(calls) == 1
    assert len(events) == 1
    ev = events[0]
    assert set(ev.keys()) == JUDGE_EVENT_KEYS, "SSE 事件协议键集必须稳定"
    assert ev["type"] == "evidence_judged"
    assert ev["query"] == "q"
    assert ev["checked"] is True
    assert ev["level"] == "relevant"
    assert {i["chunk_id"] for i in ev["items"]} == {11}
    assert isinstance(ev["latency_ms"], int)
    assert ev["reason"] == ""


def test_judge_service_never_raises_on_broken_tool_trace():
    """畸形 tool_trace / 畸形输出 ⇒ 空结果，不抛（旁路不得打断主链路）。"""
    assert judge_service.collect_from_tool_trace(None) == []
    assert judge_service.collect_from_tool_trace([None, 42, {}]) == []
    assert judge_service.collect_from_tool_trace(_trace("weak", output="{不是 JSON")) == []


def test_judge_service_is_single_concurrency():
    """线程池**单并发**（避免抢 CPU）：两个并发判官不得同时在跑。"""
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake(prompt):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        return _llm_text({"items": [{"chunk_id": 11, "verdict": "irrelevant", "quote": ""}]})

    trace = _trace("weak") + [{"name": "retrieve_docs", "arguments": {"query": "q2"},
                               "output": _payload("weak", "q2")}]
    # 同一次调用内两轮串行；这里直接并发提交两个 future 验证池容量
    f1 = judge_service.submit_judge("q1", [{"chunk_id": 11, "text": "正文"}], llm_fn=fake)
    f2 = judge_service.submit_judge("q2", [{"chunk_id": 12, "text": "正文"}], llm_fn=fake)
    f1.result(timeout=5), f2.result(timeout=5)
    assert state["max"] == 1, "判官线程池必须单并发（实测峰值 %d）" % state["max"]


# ==================== 第 4 层：可达性（真 agent_run → 真 SSE） ====================


def _patch_memory(monkeypatch):
    from utils import agent_memory

    monkeypatch.setattr(agent_memory, "ensure_session", lambda *a, **k: 1)
    monkeypatch.setattr(agent_memory, "record_message", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "maybe_summarize_session", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "get_agent_messages", lambda *a, **k: [])
    monkeypatch.setattr(agent_core, "_ai_read_holdings_enabled", lambda: False)
    monkeypatch.setattr(agent_core, "_demo_mode_on", lambda: True)


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _routed_llm(seen, *, judge_hook=None, judge_reply=None):
    """按 **prompt 形态**分流：list=messages ⇒ Agent 回合；str ⇒ 判官 prompt。

    ⚠️ 一条假 `call_llm` 同时服务两条链路，才能证明判官走的是**真实的默认 llm 解析**
    （`utils.ai_helper.call_llm` 晚绑定），而不是测试塞进去的假函数。
    """
    def fake(prompt, tools=None, model=None, temperature=0.7, thinking=False):
        if isinstance(prompt, list):
            seen["agent"] = seen.get("agent", 0) + 1
            if seen["agent"] == 1:
                assert tools, "agent_run 必须把工具 schema 传给模型"
                return _tool_call("retrieve_docs", {"query": WEAK_QUERY, "code": "600519"})
            return {"type": "text", "content": "（打桩终稿）", "usage": None}
        seen["judge"] = seen.get("judge", 0) + 1
        if judge_hook is not None:
            return judge_hook(prompt)
        return judge_reply or _judge_reply(prompt)

    return fake


def test_judge_is_reachable_through_agent_run_and_sse(monkeypatch, kb):
    """**可达性**：真 `agent_run` + 真 `retrieve_docs`（临时 kb）→ SSE 出 `evidence_judged`。

    这条是任务书 §2 V1 末条要的那条「经过 agent_run / SSE 真实路径」的用例。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    seen = {}
    monkeypatch.setattr(ai_helper, "call_llm", _routed_llm(seen))
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    _patch_memory(monkeypatch)

    events = list(agent_service.stream_events(WEAK_QUERY))
    types = [e.get("type") for e in events]

    assert "done" in types, "SSE 契约：回答必须照常发出"
    assert "evidence_judged" in types, (
        "判官在真实 SSE 路径上不可达（纯函数单测全绿也测不出这条）")
    assert seen.get("judge") == 1, "weak 档必须恰好触发一次判官"
    # 事件顺序：done 先到、判官结果后到
    assert types.index("done") < types.index("evidence_judged")

    ev = [e for e in events if e["type"] == "evidence_judged"][0]
    assert set(ev.keys()) == JUDGE_EVENT_KEYS
    assert ev["checked"] is True
    assert ev["level"] == "relevant"
    # 判官引文能逐字命中真实块正文（引文校验全链路成立）
    assert ev["items"][0]["quote_rejected"] is False


def test_judge_not_triggered_for_none_level_through_sse(monkeypatch, kb):
    """`none` 档（查询判据弃权）在**真实 SSE 路径**上不得触发判官。"""
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=NONE_VEC)

    seen = {}

    def fake(prompt, tools=None, model=None, temperature=0.7, thinking=False):
        if isinstance(prompt, list):
            n = seen.get("agent", 0) + 1
            seen["agent"] = n
            if n == 1:
                return _tool_call("retrieve_docs", {"query": NONE_QUERY})
            return {"type": "text", "content": "（打桩终稿）", "usage": None}
        seen["judge"] = seen.get("judge", 0) + 1
        return _judge_reply(prompt)

    monkeypatch.setattr(ai_helper, "call_llm", fake)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    _patch_memory(monkeypatch)

    events = list(agent_service.stream_events(NONE_QUERY))
    ends = [e for e in events if e.get("type") == "tool_end"]
    assert ends and ends[0]["evidence_level"] == "none", "前置：该查询必须判 none"
    assert not [e for e in events if e.get("type") == "evidence_judged"]
    assert seen.get("judge", 0) == 0, "none 档判官零调用"


def test_done_arrives_before_judge_completes(monkeypatch, kb):
    """**回答不得等判官**：判官被卡住时，`done` 仍然先到。

    手法：判官假 LLM 阻塞在一个 `Event` 上。主线程逐条消费 SSE —— 必须**先**拿到 `done`
    （此时事件未 set、判官未返回），再放行 ⇒ 之后才拿到 `evidence_judged`。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    release = threading.Event()
    entered = threading.Event()
    seen = {}

    def judge_hook(prompt):
        entered.set()
        release.wait(timeout=10)
        return _judge_reply(prompt)

    monkeypatch.setattr(ai_helper, "call_llm", _routed_llm(seen, judge_hook=judge_hook))
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    _patch_memory(monkeypatch)

    received = []          # 消费线程与主线程共享（list.append 是原子的）
    t0 = time.time()

    def consume():
        for ev in agent_service.stream_events(WEAK_QUERY):
            received.append(ev)

    t = threading.Thread(target=consume, daemon=True)
    t.start()

    # ① 判官仍被卡住（release 未 set）时，done 必须已经到达消费端
    deadline = time.time() + 6
    while time.time() < deadline and not any(e.get("type") == "done" for e in received):
        time.sleep(0.02)
    done_seen = [e for e in received if e.get("type") == "done"]
    assert done_seen, "6s 内没等到 done —— 回答被判官拖住了"
    assert not release.is_set(), "前置失效：done 到达时判官已经返回（测不出'先到'）"
    assert time.time() - t0 < 5, "done 不得被判官拖慢（实测 %.2fs）" % (time.time() - t0)
    assert entered.wait(timeout=5), "判官应已在后台启动"

    # ② 放行判官 ⇒ 结果之后才到，且顺序恒为 done < evidence_judged
    release.set()
    t.join(timeout=10)
    types = [e.get("type") for e in received]
    assert "evidence_judged" in types, "放行后判官结果必须仍然送达"
    assert types.index("done") < types.index("evidence_judged")
    assert types[-1] == "evidence_judged", "判官事件应在 done 之后、收流之前"


def test_sse_judge_timeout_does_not_hang_the_stream(monkeypatch, kb):
    """判官超时 ⇒ 流**有界**结束，且如实标注 `checked=False`（不假装判过）。"""
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    release = threading.Event()
    seen = {}

    def judge_hook(prompt):
        release.wait(timeout=10)
        return _judge_reply(prompt)

    monkeypatch.setattr(ai_helper, "call_llm", _routed_llm(seen, judge_hook=judge_hook))
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    monkeypatch.setattr(judge_service, "JUDGE_TIMEOUT_S", 0.3)
    _patch_memory(monkeypatch)

    t0 = time.time()
    try:
        events = list(agent_service.stream_events(WEAK_QUERY))
    finally:
        release.set()
    wall = time.time() - t0

    judged = [e for e in events if e.get("type") == "evidence_judged"]
    assert wall < 5, "超时必须让流有界结束（实测 %.2fs）" % wall
    assert judged, "超时应如实上报 checked=False，而不是静默"
    assert judged[0]["checked"] is False
    assert judged[0]["level"] == "uncertain"
    assert judged[0]["reason"] == "timeout"


def test_judge_does_not_participate_in_retrieval(monkeypatch, kb):
    """硬约束：判官**不参与检索排序/过滤**。

    两层证据：① AST 层（检索链路的 import 图里零判官，注释里提到名字不算）；
    ② 行为层（判官跑过之后，同一查询的 `retrieve_docs` 返回体**逐字节不变**）。

    ⚠️ B-R1 · 审计 B-F11：AST 锁已**加固**（覆盖 `importlib.import_module` /
    `__import__` / `getattr` 的**字面量**字符串参数，即"动态导入/别名"绕过面）；
    行为锁也扩到"带 `code` 过滤 + 多次调用"。**锁不住的残余面**（计算出来的名字，
    如 `import_module("services." + "judge_service")`）在
    `test_ast_judge_lock_has_teeth_for_literal_dynamic_imports` 里**显式断言**，
    避免把"常规调用面已锁"读成"绝对锁得住"。
    """
    import utils.rag.hybrid as hybrid

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("utils/rag/retrieve.py", "utils/rag/hybrid.py", "utils/rag/evidence.py"):
        path = os.path.join(root, *rel.split("/"))
        with open(path, encoding="utf-8") as f:
            src = f.read()
        static = _static_judge_imports(src)
        dynamic = _dynamic_judge_imports(src)
        assert not static, "%s 的 import 图里出现判官 —— 判官不得进入检索链路：%s" % (rel, static)
        assert not dynamic, "%s 用动态导入把判官接进了检索链路：%s" % (rel, dynamic)
    assert not hasattr(hybrid, "llm_judge")
    assert not hasattr(hybrid, "judge_service")

    # ② 行为层：裸检索 + 带 code 过滤的检索，判官跑**多次**之后逐字节不变
    def _call(code=None):
        return retrieve_docs(WEAK_QUERY, code=code, db_path=kb, query_vec=WEAK_VEC)

    before_plain, before_code = _call(), _call("600519")
    for _ in range(2):                       # 多次调用（B-F11：旧行为锁只跑一次）
        judge_service.judge_tool_trace(
            _trace("weak", before_plain), llm_fn=lambda p: _judge_reply(p), timeout_s=5)
        judge_service.judge_tool_trace(
            _trace("weak", before_code), llm_fn=lambda p: _judge_reply(p), timeout_s=5)
    assert _call() == before_plain, "判官影响了裸检索返回 ⇒ 违反硬约束"
    assert _call("600519") == before_code, "判官影响了带 code 过滤的检索返回 ⇒ 违反硬约束"


# ==================== 第 6 层：检索隔离锁的"牙口"（B-R1 · 审计 B-F11） ====================

#: 判官模块/入口的名字片段（静态 import / 动态导入字符串命中任一即算违约）
_JUDGE_NAME_HINTS = ("llm_judge", "judge_service")


def _static_judge_imports(src):
    """静态 import 面：`import X` / `from X import Y`（**含别名**）里的判官命中。"""
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update("%s.%s" % (node.module or "", a.name) for a in node.names)
    return sorted(n for n in imported if any(h in n for h in _JUDGE_NAME_HINTS))


def _dynamic_judge_imports(src):
    """动态导入面：`importlib.import_module("…")` / `__import__("…")` /
    `getattr(mod, "…")` 的**字面量**字符串参数里的判官命中。

    ⚠️ 只能锁**字面量**：`import_module("services." + "judge_service")` /
    `getattr(mod, "judge" + "_service")` 这类**计算出来的名字**锁不住（诚实边界，
    由 `test_ast_judge_lock_has_teeth_for_literal_dynamic_imports` 显式断言）。
    """
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
        else:
            continue
        if name not in ("import_module", "__import__", "getattr", "getattr_static"):
            continue
        for arg in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(h in arg.value for h in _JUDGE_NAME_HINTS):
                    hits.append("%s(%r)" % (name, arg.value))
    return hits


def test_ast_judge_lock_has_teeth_for_aliased_and_dynamic_imports():
    """B-F11：攻击样本喂给锁 ⇒ 必须命中（证明加固后的锁有牙）。

    红/绿留证（report-B-R1.md §B-F11）：加固前旧锁只扫 `ast.Import`/`ImportFrom`
    ⇒ 静态面（含 `as` 别名）**能**命中，但**动态面 0 命中**
    （`importlib.import_module("services.judge_service")` 完全无感）；加固后三种
    动态形态（`import_module` / `__import__` / `getattr` 字面量）全部命中。
    """
    sample = (
        "from services import judge_service as js\n"
        "import utils.rag.llm_judge as lj\n"
        "import importlib\n"
        "m = importlib.import_module('services.judge_service')\n"
        "j = __import__('utils.rag.llm_judge')\n"
        "k = getattr(hybrid, 'judge_service', None)\n"
    )
    static = _static_judge_imports(sample)
    assert "services.judge_service" in static, "别名静态导入必须被锁住"
    assert "utils.rag.llm_judge" in static, "别名静态导入必须被锁住"
    dynamic = _dynamic_judge_imports(sample)
    assert any("import_module" in h for h in dynamic), "importlib.import_module 必须被锁住"
    assert any("__import__" in h for h in dynamic), "__import__ 必须被锁住"
    assert any("getattr" in h for h in dynamic), "getattr 字面量必须被锁住"


def test_ast_judge_lock_documents_its_blind_spot():
    """B-F11（诚实边界）：**计算出来的**模块名锁不住 —— 断言这一点，防止过度信任。

    这不是"待修缺陷"：静态 AST 锁的语义就是"常规调用面"，动态拼名字等价于刻意规避，
    需要运行时钩子/import 审计（超 B-R1 范围）。锁 + 行为锁 + 人工评审三者才是完整防线。
    """
    evasive = ("import importlib\n"
               "m = importlib.import_module('services.' + 'judge_service')\n"
               "n = getattr(mod, 'judge' + '_service', None)\n")
    assert _dynamic_judge_imports(evasive) == []
    assert _static_judge_imports(evasive) == []


# ==================== 第 5 层：评测口径（`judge_fp` 落在 weak 子集，且只认**通过校验**的放行） ====================


def test_judge_metrics_fp_counts_only_verified_relevance(monkeypatch, kb):
    """`scripts/rag_eval.py::judge_metrics` 的口径锁。

    ① 分母是 **weak 档负例**（`none` 档不触发判官 ⇒ 不进分母）；
    ② 「放行」= `level == "relevant"`，而**伪造引文**的那条已被判官降级为 `uncertain`
       ⇒ **不计放行**（否则 `judge_fp` 会虚高，而防御本身已经生效）。
    """
    import numpy as np

    from scripts.rag_eval import judge_metrics

    rows_rel = [{"id": "rel-1", "query": WEAK_QUERY, "answer_chunk_ids": []}]
    rows_irr = [
        {"id": "irr-forged", "kind": "near_miss", "query": WEAK_QUERY},
        {"id": "irr-weak", "kind": "near_miss", "query": WEAK_QUERY},
    ]
    qvecs = np.asarray([WEAK_VEC, WEAK_VEC, WEAK_VEC], dtype="float32")

    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _judge_reply(prompt)                    # rel：逐字引文 ⇒ relevant（不误杀）
        if calls["n"] == 2:
            return _judge_reply(prompt, forged=True)       # irr：伪造引文 ⇒ 必须降级
        return _judge_reply(prompt)                        # irr：逐字引文 ⇒ 真放行

    m = judge_metrics(rows_rel, rows_irr, qvecs, db_path=kb, llm_fn=fake, timeout_s=5)
    assert m["n_triggered"] == 3, "三条查询都应落 weak 档并触发判官"
    assert m["n_weak_irr"] == 2
    assert m["judge_fp"] == 0.5, (
        "伪造引文那条不得计放行：实测 %.3f（=1/2 才对）" % m["judge_fp"])
    assert m["judge_kill_rate"] == 0.0, "rel 用逐字引文不应被判 irrelevant"
    assert m["judge_fp_by_kind"]["near_miss"] == {"n": 2, "passed": 1}
    assert m["n_latency"] == 3


def test_judge_metrics_trigger_rate_excludes_none_level(kb):
    """`none` 档查询**不进触发率**（判官根本不跑）⇒ 触发率只统计 weak 且有结果的查询。"""
    import numpy as np

    from scripts.rag_eval import judge_metrics

    rows_rel = [{"id": "rel-1", "query": WEAK_QUERY, "answer_chunk_ids": []}]
    rows_irr = [{"id": "irr-none", "kind": "out_of_domain", "query": NONE_QUERY}]
    qvecs = np.asarray([WEAK_VEC, NONE_VEC], dtype="float32")

    calls = []

    def fake(prompt):
        calls.append(prompt)
        return _judge_reply(prompt)

    m = judge_metrics(rows_rel, rows_irr, qvecs, db_path=kb, llm_fn=fake, timeout_s=5)
    assert m["n_triggered"] == 1, "none 档负例不得触发判官"
    assert len(calls) == 1
    assert m["n_weak_irr"] == 0
    assert m["judge_fp"] == 0.0
    assert m["judge_fp_all_irr"] == 0.0


def test_judge_metrics_reports_fn_and_span_valid(kb):
    """F1：A3b 判据的另外两个量（`judge_fn` / `span_valid`）**此前本工具压根没实现**。

    口径：`judge_fn` = weak 档正例里未被确认相关的比例（含 `uncertain`）；
    `span_valid` = 判官**提出**的 relevant 里引文逐字通过的占比（被拒的那批要进分母，
    否则"拒得多"反而让 span_valid 虚高）。
    """
    import numpy as np

    from scripts.rag_eval import judge_metrics

    rows_rel = [{"id": "rel-1", "query": WEAK_QUERY, "answer_chunk_ids": []}]
    rows_irr = [{"id": "irr-forged", "kind": "near_miss", "query": WEAK_QUERY}]
    qvecs = np.asarray([WEAK_VEC, WEAK_VEC], dtype="float32")

    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        if calls["n"] == 1:                       # 正例：伪造引文 ⇒ 被判官降级 ⇒ 计入 judge_fn
            return _judge_reply(prompt, forged=True)
        return _judge_reply(prompt, forged=True)  # 负例：同样被拒 ⇒ 不计放行

    m = judge_metrics(rows_rel, rows_irr, qvecs, db_path=kb, llm_fn=fake, timeout_s=5)
    assert m["judge_fn"] == 1.0, "正例未被确认相关 ⇒ judge_fn 必须是 1.0"
    assert m["judge_fn_n"] == 1
    assert m["judge_fp"] == 0.0, "伪造引文不得计放行"
    assert m["span_proposed"] == 2 and m["span_rejected"] == 2
    assert m["span_valid"] == 0.0, "两条提出的引文都被拒 ⇒ span_valid = 0"


def test_judge_metrics_span_valid_counts_rejected_quotes(monkeypatch, kb):
    """`span_valid` 的**判别力**：把引文校验打桩成恒真 ⇒ 被拒条目消失 ⇒ span_valid 变 1.0。

    这条是反向对照：若实现改成"只数通过的"，两种情形都会是 1.0，本用例就抓不到差异。
    """
    import numpy as np

    from scripts.rag_eval import judge_metrics

    rows_rel = [{"id": "rel-1", "query": WEAK_QUERY, "answer_chunk_ids": []}]
    rows_irr = []
    qvecs = np.asarray([WEAK_VEC], dtype="float32")

    def fake(prompt):
        return _judge_reply(prompt, forged=True)

    m = judge_metrics(rows_rel, rows_irr, qvecs, db_path=kb, llm_fn=fake, timeout_s=5)
    assert m["span_valid"] == 0.0 and m["span_rejected"] == 1

    monkeypatch.setattr(llm_judge, "quote_verified", lambda q, t, min_chars=None: True)
    m2 = judge_metrics(rows_rel, rows_irr, qvecs, db_path=kb, llm_fn=fake, timeout_s=5)
    assert m2["span_valid"] == 1.0 and m2["span_rejected"] == 0, (
        "校验恒真后仍报 span_valid < 1 ⇒ 说明 span_valid 的分母没算被拒条目")


# ==================== 第 7 层：总预算 / 兜底降级 / 超时取消（B-R1 审计 B-F1/B-F2/B-F3） ====================


def _reqs(n, text=PAYLOAD_TEXT):
    """n 个 weak 轮次的判官请求（与 `collect_from_tool_trace` 的产物同构）。"""
    return [{"query": "q%d" % i,
             "candidates": [{"chunk_id": 100 + i, "title": "T", "text": text}]}
            for i in range(n)]


def _all_irrelevant(prompt):
    """一份合法的"全无关"模型回复（用于不关心档位的用例）。"""
    ids = re.findall(r"\[chunk_id=([^\]]+)\]", prompt)
    return _llm_text({"items": [{"chunk_id": cid, "verdict": "irrelevant", "quote": ""}
                                for cid in ids]})


def _drain_judge_pool(timeout=10):
    """等单并发池排空 —— 防止上一条用例遗留的在跑任务影响下一条。"""
    fut = judge_service.submit_judge("drain", [{"chunk_id": 0, "text": PAYLOAD_TEXT}],
                                     llm_fn=_all_irrelevant, timeout_s=5)
    fut.result(timeout=timeout)


def test_total_budget_bounds_the_whole_batch():
    """**B-F1**：`total_budget_s` 是**整批**上限，不是"每轮各等一次"。

    3 轮 × 每轮 0.4s 的假 LLM + 总预算 0.5s ⇒ 墙钟必须 < 1.0s（没有 deadline 逻辑
    则是 1.2s+），且第 2/3 条**如实降级**（`checked=False` / `uncertain` / `timeout`）。
    红/绿留证：把 `judge_rounds` 的 deadline 改成 `None` ⇒ 本用例红（report-B-R1 §B-F1）。
    """
    started = []

    def slow(prompt):
        started.append(time.monotonic())
        time.sleep(0.4)
        return _all_irrelevant(prompt)

    t0 = time.monotonic()
    events = judge_service.judge_rounds(_reqs(3), llm_fn=slow, timeout_s=5.0,
                                        total_budget_s=0.5, max_rounds=3)
    wall = time.monotonic() - t0
    _drain_judge_pool()

    assert len(events) == 3, "每一轮都要有落点（不许静默丢掉）"
    assert wall < 1.0, "总预算必须把整批锁在 1.0s 内（实测 %.2fs；无总预算 = 1.2s+）" % wall
    assert events[0]["checked"] is True, "第 1 轮在预算内完成，应如实上报结果"
    for i, ev in enumerate(events[1:], start=2):
        assert ev["checked"] is False, "第 %d 轮超预算后不得假装判过" % i
        assert ev["level"] == "uncertain"
        assert ev["reason"] == "timeout"
        assert ev["items"] == []
    assert len(started) <= 2, (
        "总预算耗尽后不得再向池里投递 LLM 调用（实测投递 %d 次）" % len(started))


def test_total_budget_holds_through_sse_and_done_stays_first(monkeypatch, kb):
    """**B-F1（SSE 端）**：3 个 weak 轮 + 判官每轮 0.4s + `JUDGE_TIMEOUT_S=0.5`
    ⇒ `done` **先到**、流墙钟有界、第 2/3 条标注如实 `checked=False`。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    rounds = {"n": 0}

    def fake(prompt, tools=None, model=None, temperature=0.7, thinking=False):
        if isinstance(prompt, list):
            rounds["n"] += 1
            if rounds["n"] <= 3:
                return _tool_call("retrieve_docs", {"query": WEAK_QUERY},
                                  call_id="c%d" % rounds["n"])
            return {"type": "text", "content": "（打桩终稿）", "usage": None}
        time.sleep(0.4)
        return _all_irrelevant(prompt)

    monkeypatch.setattr(ai_helper, "call_llm", fake)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    monkeypatch.setattr(judge_service, "JUDGE_TIMEOUT_S", 0.5)
    _patch_memory(monkeypatch)

    t0 = time.time()
    events = list(agent_service.stream_events(WEAK_QUERY))
    wall = time.time() - t0
    _drain_judge_pool()

    types = [e.get("type") for e in events]
    judged = [e for e in events if e.get("type") == "evidence_judged"]
    assert rounds["n"] >= 3, "前置：agent 必须真的调了至少 3 轮 retrieve_docs"
    assert len(judged) == 3, "3 个 weak 轮都应产出判官事件（实测 %d）" % len(judged)
    assert wall < 1.0, "判官最多让流多活 JUDGE_TIMEOUT_S（实测 %.2fs）" % wall
    assert types.index("done") < types.index("evidence_judged"), "回答不得等判官"
    assert judged[0]["checked"] is True
    for ev in judged[1:]:
        assert ev["checked"] is False, "第 2/3 轮超预算后不得假装判过"
        assert ev["level"] == "uncertain"
        assert ev["reason"] == "timeout"


def test_degraded_when_total_budget_exhausted_without_calling_llm():
    """**B-F2a**：`total_budget_s=0` ⇒ 一轮都不提交，如实 `uncertain`/`checked=False`/`timeout`。

    核心安全性质：「失败一律降级 uncertain、绝不编造 relevant」。
    """
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return _all_irrelevant(prompt)

    events = judge_service.judge_rounds(_reqs(2), llm_fn=fake, total_budget_s=0.0)
    assert len(events) == 2
    assert calls == [], "预算为 0 时**一次 LLM 都不许调**"
    for ev in events:
        assert set(ev.keys()) == JUDGE_EVENT_KEYS
        assert ev["checked"] is False
        assert ev["level"] == "uncertain"
        assert ev["reason"] == "timeout"
        assert ev["items"] == []


def test_degraded_when_submit_future_times_out(monkeypatch):
    """**B-F2b**：future 级超时 ⇒ `uncertain`/`checked=False`/`timeout`。"""
    monkeypatch.setattr(judge_service, "submit_judge", lambda *a, **k: Future())
    events = judge_service.judge_rounds(_reqs(1), llm_fn=_all_irrelevant, timeout_s=0.2)
    assert events[0]["checked"] is False
    assert events[0]["level"] == "uncertain"
    assert events[0]["reason"] == "timeout"


def test_degraded_when_submit_future_raises(monkeypatch):
    """**B-F2b**：future 级异常 ⇒ `uncertain`/`checked=False`/`llm_error`。"""
    fut = Future()
    fut.set_exception(RuntimeError("上游 500"))
    monkeypatch.setattr(judge_service, "submit_judge", lambda *a, **k: fut)
    events = judge_service.judge_rounds(_reqs(1), llm_fn=_all_irrelevant, timeout_s=0.2)
    assert events[0]["checked"] is False
    assert events[0]["level"] == "uncertain"
    assert events[0]["reason"] == "llm_error"


def test_timeout_cancels_queued_round_and_burns_no_llm_call():
    """**B-F3**：判官超时后，**排队中**的轮次必须被 `cancel()` —— 不得再烧一次 LLM 调用。

    手法：用 blocker 占住唯一 worker，再把下一轮排到队尾；超时后取消 ⇒ 队列里那次
    一次都没跑（旧实现没有 `cancel()` ⇒ 它会照顺序跑完并真实调用 LLM）。
    """
    released = threading.Event()
    started = threading.Event()

    def blocker(prompt):
        started.set()
        released.wait(timeout=10)
        return _all_irrelevant(prompt)

    busy = judge_service.submit_judge("busy", _reqs(1)[0]["candidates"],
                                      llm_fn=blocker, timeout_s=10)
    assert started.wait(5), "前置：单并发 worker 已被占住"
    ran = []
    try:
        events = judge_service.judge_rounds(
            _reqs(1), llm_fn=lambda p: ran.append(1) or _all_irrelevant(p),
            timeout_s=0.2, total_budget_s=0.2)
    finally:
        released.set()
    busy.result(timeout=10)
    time.sleep(0.25)      # 给"未取消"的旧实现足够时间把队列里的任务跑起来

    assert events[0]["checked"] is False and events[0]["reason"] == "timeout"
    assert ran == [], "超时后排队中的判官轮次必须被 cancel（旧实现会跑完并真实调用 LLM）"
