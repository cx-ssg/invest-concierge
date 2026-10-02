# -*- coding: utf-8 -*-
"""A2-R1 引用编号规范 —— **生成侧注入** 回归锁（任务书 `task-A2R1.md` §2）。

## 为什么需要（A2 真跑暴露的问题）

A2 已打通「检索来源 → `tool_end.sources` → 前端来源卡 + 正文 `[n]` 可点上标」。
但 A2 的真跑证明：**真实回答里没有任何 `[n]`** —— 模型改用「（2026-07-18）」这类日期引用
（`report-A2.md` §5 原文：`回答里出现的引用编号：（无）`）⇒ 前端上标通路**永远不会被触发**。
设计依据：`docs/COVERAGE_DESIGN.md` §3.2 生成层明文要求「回答必须带引用编号 **[1][2]** + 来源 URL/文件名 + 日期」。

## 本文件的断言纪律

- 「**该有的有**」：非 `none` 且**有结果**的分支，`message` 必须带引用规范（且逐字等于
  `messages.CITATION_NOTE` —— 单一事实源，防止第三处各改一半）。
- 「**不该有的没有**」：`none` 档（已剥 `url`/`title`，**不可引用**）与无结果档
  （`NO_HIT_MESSAGE`）**都不得**出现引用规范 —— 在不可引用的档位要求引用 = 逼模型编造凭据。
  只加不删（无条件追加）也会被这一侧抓住。
- 「**生产路径可达**」：本仓连踩三次「单测全绿 ≠ 生产路径可达」，故本文件除纯函数层外，
  还有一条走**真实 `agent_run` + 真实 `execute_ai_tool_v2`** 的锁（常量必须真的进模型上下文）。
"""
import json

import pytest

from utils import agent_core, ai_helper
from utils.rag import messages as rag_messages
from utils.rag import store as rag_store
from utils.rag.retrieve import retrieve_docs

# 弱档查询（实测 tmp 语料判弱档，url/title 不被剥）+ 强向量
CITABLE_QUERY = "贵州茅台上半年营业收入同比增长"
CITABLE_VEC = [1.0, 0.0]
# 弃权档查询（实测判 none，url/title 被 retrieve.py 剥掉）+ 向量
NONE_QUERY = "茅台明天的股价是多少"
NONE_VEC = [0.6, 0.8]

# 引用规范必须表达的**三件事**（任务书 §2.1 的 1/2/3），以及它作为「随检索结果注入」的标记词
NOTE_TOKENS = ("[1]", "[2]", "顺序", "日期")
NOTE_MARKER = "引用规范"


@pytest.fixture()
def kb(tmp_path):
    """真实形态的临时知识库：1 篇公告 / 3 个块（含真向量），与 `scripts/rag_ingest.py` 落库形态一致。"""
    db = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台2026年半年度报告",
        "url": "https://example.com/maotai-h1", "published_at": "2026-08-15",
    })
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": "贵州茅台上半年营业收入同比增长百分之十五，净利润增速略低于营收增速。",
         "is_table": False},
        {"seq": 1, "text": "公司表示直销渠道占比继续提升，i茅台平台贡献显著。", "is_table": False},
        {"seq": 2, "text": "比亚迪新能源汽车七月销量创新高，海外出口同比增长明显。", "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    conn.close()
    return db


def _citation_note():
    """引用规范常量。用 `getattr` 而非模块级 `from ... import`：常量名是契约的一部分，
    但缺失时应让失败落在**断言**上（而不是整文件 collection error）—— RED 输出才可读。"""
    return getattr(rag_messages, "CITATION_NOTE", "")


# ==================== ① 常量本身（单一事实源 + 三要素） ====================


def test_citation_note_constant_defined_with_three_requirements():
    """`utils/rag/messages.py` 必须新增 `CITATION_NOTE`，且文案覆盖任务书 §2.1 的三件事：
    ① 用 `[1][2]` 编号 ② 编号与列表顺序一致（第 1 条 = `[1]`）③ 注明来源日期。
    """
    note = _citation_note()
    assert note, "utils/rag/messages.py 必须新增引用规范常量 CITATION_NOTE（task-A2R1 §2.1）"
    for token in NOTE_TOKENS:
        assert token in note, "引用规范必须表达「{}」（task-A2R1 §2.1）".format(token)
    assert "1" in note and "条" in note, "必须点明「第 1 条 = [1]」的顺序锚点"


# ==================== ② 该有的有：非 none + 有结果 ====================


def test_citation_note_reaches_message_when_results_citable(kb):
    """弱档（有结果、凭据未被剥）的 `message` 必须带引用规范 —— 这正是 A2 缺失的一环。"""
    out = json.loads(retrieve_docs(CITABLE_QUERY, db_path=kb, query_vec=CITABLE_VEC))
    assert out["results"], "前置：该查询必须命中"
    assert out["evidence_level"] != "none", "前置：该查询不得判弃权档（否则凭据被剥，不可引用）"

    note = _citation_note()
    assert note, "引用规范常量未落地"
    assert note in out["message"], \
        "非 none 且有结果的分支必须挂载引用规范（A2 真跑里回答零个 [n] 的根因就是这里没给指令）"
    assert NOTE_MARKER in out["message"], "引用规范必须以可辨识的标记词出现，便于模型执行"
    assert "[1]" in out["message"], "message 里必须出现编号示例，否则模型仍不会用 [n]"


def test_citation_note_survives_no_hit_guard_ordering(kb):
    """挂载点必须与 `WEAK_EVIDENCE_NOTE` **同一分支**，且不得覆盖警示语（两个都要在）。"""
    out = json.loads(retrieve_docs(CITABLE_QUERY, db_path=kb, query_vec=CITABLE_VEC))
    assert rag_messages.WEAK_EVIDENCE_NOTE in out["message"], \
        "引用规范不得替换掉弱相关警示语（安全网必须仍在）"


# ==================== ③ 不该有的没有：none 档 / 无结果档 ====================


def test_citation_note_absent_in_none_level(kb):
    """`none` 档**不得**加引用规范。

    该档位已由 `retrieve.py` 剥掉 `url`/`title`（候选不可引用，设计 §3.3「无引用 = 不算回答」的
    机器强制）—— 此时要求编号 = 逼模型编造凭据。`results` 仍非空（2026-09-17 起不再物理清空），
    所以本条**不能**靠「results 为空」侥幸通过：它测的正是「有候选但不可引用」这一档。
    """
    out = json.loads(retrieve_docs(NONE_QUERY, db_path=kb, query_vec=NONE_VEC))
    assert out["evidence_level"] == "none", "前置：该查询应判弃权档"
    assert out["results"], "前置：none 档仍返回候选（否则本条退化成「无结果」场景）"
    assert all(r["url"] is None and r["title"] is None for r in out["results"]), \
        "前置：none 档候选已剥引用凭据"

    note = _citation_note()
    assert note not in out["message"], \
        "none 档不得要求引用（候选已剥凭据，要求引用只会催生编造）"
    assert "[1]" not in out["message"], "none 档 message 里不得出现编号示例"
    assert NOTE_MARKER not in out["message"], "none 档 message 里不得出现引用规范标记词"


def test_citation_note_absent_when_no_results(kb):
    """无结果档（零字面交集 → 物理回空）**不得**加引用规范：没有任何东西可引。"""
    out = json.loads(retrieve_docs("量子计算最新进展", db_path=kb, query_vec=[0.6, 0.8]))
    assert out["results"] == [], "前置：零 bigram 交集应触发分层硬停"
    assert "未找到" in out["message"], "无条件无结果提示必须保留（零回归）"

    note = _citation_note()
    assert note not in out["message"], "无结果档不得要求引用"
    assert "[1]" not in out["message"], "无结果档 message 里不得出现编号示例"
    assert NOTE_MARKER not in out["message"]


def test_citation_note_absent_on_empty_index(tmp_path):
    """空索引（尚未 ingest）同样不得出现引用规范。"""
    db = str(tmp_path / "empty.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    conn.close()

    out = json.loads(retrieve_docs("茅台", db_path=db, query_vec=[1.0, 0.0]))
    assert out["results"] == []
    note = _citation_note()
    assert note not in out["message"]
    assert "[1]" not in out["message"]


# ==================== ④ 可达性：常量必须真的进模型上下文 ====================


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _double_decode(output):
    """解开 `execute_ai_tool_v2` 对字符串工具的**再编码**（生产实况，见 test_rag_sources.py 的说明）。"""
    data = json.loads(output)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def test_citation_note_reaches_agent_tool_context(monkeypatch, kb):
    """**可达性锁**：引用规范必须经 `execute_ai_tool_v2`（resolve → _truncate → 序列化）真的
    进到 `agent_run` 的工具消息里 —— 而不是只存在于被单测直接调用的 `retrieve_docs` 返回值里。

    本仓历史教训：`extract_sources` 曾因双层编码而在生产路径上永远拿不到 sources，纯函数单测却全绿。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=CITABLE_VEC)

    seen = {"round": 0}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen["round"] += 1
        if seen["round"] == 1:
            assert tools, "agent_run 必须把工具 schema 传给模型"
            return _tool_call("retrieve_docs", {"query": CITABLE_QUERY, "code": "600519"})
        return {"type": "text", "content": "（打桩终稿）", "usage": None}

    monkeypatch.setattr(ai_helper, "call_llm", fake_llm)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)

    res = agent_core.agent_run("贵州茅台最近公告说了什么", memory=False, model="fake-model",
                               structured_progress=True)
    trace = res.get("tool_trace") or []
    assert trace, "agent_run 必须记录 tool_trace（模型实际读到的工具输出）"

    payload = _double_decode(trace[0]["output"])
    note = _citation_note()
    assert note, "引用规范常量未落地"
    assert note in payload["message"], \
        "引用规范没进 agent 的工具消息 ⇒ 生产路径上模型仍拿不到编号指令（单测全绿也没用）"
