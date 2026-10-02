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
import json
import re
import threading
import time

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
    """空白归一化后命中即可（模型抄回来时常改换行/空格）。"""
    def fake(prompt):
        quote = "贵州茅台上半年营业收入\n同比增长   百分之十五"
        return _llm_text({"items": [{"chunk_id": 1, "verdict": "relevant", "quote": quote}]})

    out = llm_judge.judge_candidates("营收", _cands(), llm_fn=fake)
    assert out["items"][0]["quote_rejected"] is False
    assert out["items"][0]["verdict"] == "relevant"


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
    """
    import ast
    import os

    import utils.rag.hybrid as hybrid

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("utils/rag/retrieve.py", "utils/rag/hybrid.py", "utils/rag/evidence.py"):
        path = os.path.join(root, *rel.split("/"))
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update("%s.%s" % (node.module or "", a.name) for a in node.names)
        assert not any("llm_judge" in n or "judge_service" in n for n in imported), (
            "%s 的 import 图里出现判官 —— 判官不得进入检索链路" % rel)
    assert not hasattr(hybrid, "llm_judge")

    raw_before = retrieve_docs(WEAK_QUERY, db_path=kb, query_vec=WEAK_VEC)
    judge_service.judge_tool_trace(
        _trace("weak", raw_before), llm_fn=lambda p: _judge_reply(p), timeout_s=5)
    raw_after = retrieve_docs(WEAK_QUERY, db_path=kb, query_vec=WEAK_VEC)
    assert raw_before == raw_after, "判官影响了检索返回 ⇒ 违反硬约束"


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
