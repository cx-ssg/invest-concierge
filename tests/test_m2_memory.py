# -*- coding: utf-8 -*-
"""M2 长期记忆层回归锁（`docs/M2_MEMORY_PLAN.md` Step 2，P2 门禁 B1–B4 的基础）。

覆盖四类必需行为（§4.3 B3：写入 / 召回 / 去重 / 删除）+ 降级 + 隐私开关。

⚠️ 隔离：每个用例把 `data.database.DB_FILE` 指到 `tmp_path` ⇒ **绝不碰生产库**。
⚠️ 本文件对**尚未实现**的 `utils.long_memory` 写契约 ⇒ 首跑应全红（TDD RED）。
"""
import json
import sqlite3
import sys

import pytest

sys.path.insert(0, ".")

import data.database as db
from utils import long_memory as lm


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """把库指到临时文件并建表。"""
    dbfile = str(tmp_path / "m2.db")
    monkeypatch.setattr(db, "DB_FILE", dbfile)
    db.init_db()
    return dbfile


# ======================================================================
# 1. 写入（三类记忆）
# ======================================================================
def test_add_and_list_three_kinds(iso):
    pid = lm.add(lm.KIND_PREFERENCE, "风险承受低，不碰杠杆", key="risk_tolerance")
    fid = lm.add(lm.KIND_FACT, "2026-08 买入 600519，成本 1450",
                 key="stock:600519", meta={"code": "600519"})
    eid = lm.add(lm.KIND_EXPERIENCE, "8 月加仓 600519，回撤 12%")

    assert all(isinstance(x, int) and x > 0 for x in (pid, fid, eid)), (pid, fid, eid)
    rows = lm.list_all()
    assert len(rows) == 3
    kinds = {r["kind"] for r in rows}
    assert kinds == {lm.KIND_PREFERENCE, lm.KIND_FACT, lm.KIND_EXPERIENCE}
    assert lm.list_all(lm.KIND_FACT)[0]["content"].startswith("2026-08")


def test_invalid_kind_rejected(iso):
    for bad in ("", "unknown", "PREFERENCE", None):
        with pytest.raises((ValueError, TypeError)):
            lm.add(bad, "内容")


def test_meta_roundtrip(iso):
    mid = lm.add(lm.KIND_FACT, "关注 300750", key="stock:300750",
                 meta={"code": "300750", "tags": ["新能源"]})
    row = lm.get(mid)
    assert row["meta"]["code"] == "300750"
    assert row["meta"]["tags"] == ["新能源"]


def test_empty_content_rejected(iso):
    with pytest.raises(ValueError):
        lm.add(lm.KIND_PREFERENCE, "   ", key="x")


# ======================================================================
# 2. 去重（§4 的「记忆写入必须可审计」⇒ 同一件事不该堆成多条）
# ======================================================================
def test_preference_dedup_overwrites(iso):
    a = lm.add(lm.KIND_PREFERENCE, "风险承受低", key="risk_tolerance")
    b = lm.add(lm.KIND_PREFERENCE, "风险承受低，且不碰杠杆", key="risk_tolerance")
    assert a == b, "同 key 应覆盖更新并返回同一 id"
    rows = lm.list_all(lm.KIND_PREFERENCE)
    assert len(rows) == 1
    assert "杠杆" in rows[0]["content"], "内容应被新值覆盖"
    assert rows[0]["source"] == "explicit"


def test_fact_dedup_by_stock_key(iso):
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519")
    lm.add(lm.KIND_FACT, "600519 成本 1500", key="stock:600519")
    rows = lm.list_all(lm.KIND_FACT)
    assert len(rows) == 1 and "1500" in rows[0]["content"]


def test_experience_dedup_by_fingerprint(iso):
    a = lm.add(lm.KIND_EXPERIENCE, "8 月加仓 600519，回撤 12%")
    b = lm.add(lm.KIND_EXPERIENCE, "8 月加仓   600519，回撤 12%")   # 仅空白差异
    assert a == b, "归一化后相同的内容应判为同一条"
    assert len(lm.list_all(lm.KIND_EXPERIENCE)) == 1

    c = lm.add(lm.KIND_EXPERIENCE, "9 月减仓，躲过下跌")
    assert c != a
    assert len(lm.list_all(lm.KIND_EXPERIENCE)) == 2


def test_experience_key_is_fingerprint_not_empty(iso):
    """⚠️ 锁死设计定稿：experience 的 key **必须是内容指纹**，不能是 ''。

    根因：`UNIQUE(kind, key)` 下多条 `key=''` 会互相冲突（Step 1 已实测确证）。
    """
    mid = lm.add(lm.KIND_EXPERIENCE, "某条经验")
    row = lm.get(mid)
    assert row["key"], "experience 的 key 不得为空"
    assert row["key"] == lm.fingerprint("某条经验")


def test_fingerprint_normalizes_whitespace():
    assert lm.fingerprint("a b   c") == lm.fingerprint("a  b c")
    assert lm.fingerprint("x") != lm.fingerprint("y")
    assert len(lm.fingerprint("任意内容")) <= 32


# ======================================================================
# 3. 召回（三类各自策略，§4.1）
# ======================================================================
def test_recall_preferences_returns_all(iso):
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    lm.add(lm.KIND_PREFERENCE, "偏好宽基指数", key="style")
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519")

    prefs = lm.recall_preferences()
    assert len(prefs) == 2
    assert all(p["kind"] == lm.KIND_PREFERENCE for p in prefs)


def test_recall_facts_filters_by_stock(iso):
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519", meta={"code": "600519"})
    lm.add(lm.KIND_FACT, "300750 关注中", key="stock:300750", meta={"code": "300750"})
    lm.add(lm.KIND_FACT, "长期计划：三年定投", key="plan")          # 无标的

    only_600519 = lm.recall_facts(["600519"])
    contents = [r["content"] for r in only_600519]
    assert any("600519" in c for c in contents)
    assert not any("300750" in c for c in contents), "无关标的不得召回"
    assert any("定投" in c for c in contents), "无标的的长期事实应始终相关"

    # 不传标的 ⇒ 只返回无标的的通用事实（避免注入无关个股信息）
    generic = lm.recall_facts(None)
    assert all("600519" not in r["content"] for r in generic)


def test_recall_experiences_respects_top_k(iso):
    for i in range(5):
        lm.add(lm.KIND_EXPERIENCE, f"第{i}次操作的经验：回撤 {i}%")
    got = lm.recall_experiences("回撤", top_k=3)
    assert len(got) == 3


def test_recall_experiences_degrades_without_embedding(iso, monkeypatch):
    """⚠️ 无 Ollama / 向量不可用时：**仍要返回结果**（按时间倒序）且**如实标注**，不得抛。"""
    monkeypatch.setattr(lm, "embed_text", lambda text: None)
    lm.add(lm.KIND_EXPERIENCE, "早期经验")
    lm.add(lm.KIND_EXPERIENCE, "最近经验")

    got = lm.recall_experiences("任意查询", top_k=2)
    assert len(got) == 2
    assert got[0]["content"] == "最近经验", "降级时应按时间倒序（最新在前）"
    assert all("embedded" in r for r in got), "必须标注是否走了向量召回"
    assert all(r["embedded"] is False for r in got)


# ======================================================================
# 4. 删除（B4：删完必须**真的**不再体现）
# ======================================================================
def test_delete_really_removes(iso):
    mid = lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    assert lm.delete(mid) is True
    assert lm.get(mid) is None
    assert lm.list_all() == []
    assert lm.recall_preferences() == [], "删除后召回必须为空（B4 的核心）"


def test_delete_by_key_and_idempotent(iso):
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519")
    assert lm.delete_by_key(lm.KIND_FACT, "stock:600519") is True
    assert lm.list_all(lm.KIND_FACT) == []
    assert lm.delete(999999) is False, "删不存在的 id 应返回 False 而非抛"


def test_delete_only_affects_target(iso):
    a = lm.add(lm.KIND_PREFERENCE, "A", key="a")
    lm.add(lm.KIND_PREFERENCE, "B", key="b")
    lm.delete(a)
    rows = lm.list_all(lm.KIND_PREFERENCE)
    assert len(rows) == 1 and rows[0]["content"] == "B"


# ======================================================================
# 5. 提交块（注入用）+ 隐私开关
# ======================================================================
def test_build_recall_block_marks_sources(iso):
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519", meta={"code": "600519"})
    lm.add(lm.KIND_EXPERIENCE, "8 月加仓回撤 12%")

    text, sources = lm.build_recall_block("600519 最近怎么样", stock_codes=["600519"])
    assert "不碰杠杆" in text
    assert "600519" in text
    assert sources["preferences"] == 1
    assert sources["facts"] >= 1
    assert sources["experiences"] >= 1


def test_build_recall_block_empty_when_no_memory(iso):
    """**没有任何记忆时不注入、不谎报**（沿用 v1.1 三件套 C 的惯例）。"""
    text, sources = lm.build_recall_block("600519 怎么样", stock_codes=["600519"])
    assert text == ""
    assert sources == {"preferences": 0, "facts": 0, "experiences": 0}


def test_privacy_switch_off_disables_recall(iso):
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    assert lm.memory_enabled() is True, "默认应为开（与持仓开关一致）"

    lm.set_memory_enabled(False)
    assert lm.memory_enabled() is False
    text, sources = lm.build_recall_block("随便问", stock_codes=None)
    assert text == "", "开关关闭时不得注入"
    assert sources["preferences"] == 0

    lm.set_memory_enabled(True)
    text2, _ = lm.build_recall_block("随便问", stock_codes=None)
    assert "不碰杠杆" in text2, "开关重新打开后应恢复注入"


# ======================================================================
# 6. 隐式写入候选（§4.2：AI 不自行写记忆）
# ======================================================================
def test_propose_then_accept(iso):
    pid = lm.propose(lm.KIND_PREFERENCE, "用户似乎偏好低波动", key="risk_tolerance")
    assert len(lm.list_pending()) == 1
    assert lm.list_all() == [], "候选不得直接进正式表"

    mid = lm.accept_pending(pid)
    assert isinstance(mid, int) and mid > 0
    assert lm.list_all(lm.KIND_PREFERENCE)[0]["source"] == "implicit"
    assert lm.list_pending() == [], "确认后候选应出队"


def test_propose_then_reject(iso):
    pid = lm.propose(lm.KIND_FACT, "用户可能持有 600519")
    assert lm.reject_pending(pid) is True
    assert lm.list_pending() == []
    assert lm.list_all() == [], "拒绝不得落库"


def test_accept_unknown_pending_returns_none(iso):
    assert lm.accept_pending(999999) is None
    assert lm.reject_pending(999999) is False


def test_summarize_to_candidates_rule_fallback(iso):
    """无 LLM 时用**规则兜底**抽取候选（不抛、不猜太多）。"""
    n = lm.summarize_to_candidates(
        None,
        messages=[
            {"role": "user", "content": "记住我不碰杠杆"},
            {"role": "assistant", "content": "好的，已记下。"},
            {"role": "user", "content": "今天天气不错"},
        ],
    )
    assert n == 1, f"应只从「记住…」抽 1 条候选，实际 {n}"
    pend = lm.list_pending()
    assert len(pend) == 1 and "杠杆" in pend[0]["content"]


# ======================================================================
# 7. 注入集成（Step 4）：走真实 agent_run，验证 prompt 与 memory_used 事件
# ======================================================================
def _run_agent_for_memory(task="给我个操作建议"):
    """跑一次 agent_run，返回 (captured_messages, events)。零网络、零真实落库。"""
    from unittest.mock import patch

    from utils import agent_core, ai_helper

    events = []
    captured = []

    def _on_progress(stage, detail):
        events.append((stage, detail))

    def _call(messages, tools=None, model=None, temperature=None, **kw):
        captured.append(messages)
        return {"type": "text", "content": "回答"}

    with patch.object(ai_helper, "call_llm", _call), \
         patch("utils.agent_memory.ensure_session", side_effect=lambda sid, title="": sid or 1), \
         patch("utils.agent_memory.record_message", return_value=None), \
         patch("utils.agent_memory.maybe_summarize_session", return_value=None), \
         patch("utils.ai_helper._is_demo_mode", return_value=False), \
         patch("services.settings_service.get_ai_read_holdings", return_value=False):
        agent_core.agent_run(task, memory=True, session_id=None,
                             structured_progress=True, on_progress=_on_progress)
    msgs = captured[0] if captured else []
    system = msgs[0]["content"] if msgs else ""
    return system, events


def test_agent_run_injects_long_term_memory(iso):
    """有偏好记忆 ⇒ system 必须含「## 长期记忆」段，且 `memory_used` 事件带来源细分。"""
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆，偏好宽基指数", key="no_leverage")

    system, events = _run_agent_for_memory()

    assert "## 长期记忆" in system, "长期记忆未注入 prompt"
    assert "不碰杠杆" in system, "偏好内容未注入"
    mem = [d for s, d in events if s == "memory_used"]
    assert mem, "应发 memory_used 事件"
    src = mem[0]["sources"]
    assert "long_term" in src and "preferences" in src, f"sources 缺来源标记：{src}"


def test_agent_run_injects_stock_fact_only_when_relevant(iso):
    """事实记忆按**标的**召回：问题上没提到的标的不得注入。"""
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519", meta={"code": "600519"})
    lm.add(lm.KIND_FACT, "300750 正在观察", key="stock:300750", meta={"code": "300750"})

    system, _ = _run_agent_for_memory("600519 最近怎么样")

    assert "600519" in system
    assert "300750" not in system, "无关标的的事实被注入了"


def test_agent_run_no_memory_no_injection_no_event(iso):
    """**没有任何记忆 ⇒ 不注入、不发事件**（不暗示"我记得"）。"""
    system, events = _run_agent_for_memory()

    assert "## 长期记忆" not in system
    assert not [d for s, d in events if s == "memory_used"], "无记忆却发了 memory_used"


def test_agent_run_respects_memory_switch(iso):
    """隐私开关关闭 ⇒ 即使有记忆也不注入（B4 的前置条件：删/关都必须真生效）。"""
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    lm.set_memory_enabled(False)

    system, events = _run_agent_for_memory()

    assert "## 长期记忆" not in system
    assert "不碰杠杆" not in system
    assert not [d for s, d in events if s == "memory_used"], "开关关闭却发了 memory_used"


def test_agent_run_after_delete_stops_injecting(iso):
    """**B4 的核心**：删掉记忆后，再问同样问题 ⇒ prompt 里不得再出现该内容。"""
    mid = lm.add(lm.KIND_PREFERENCE, "不碰杠杆，偏好宽基指数", key="no_leverage")
    system_before, _ = _run_agent_for_memory("给我个操作建议")
    assert "不碰杠杆" in system_before, "前置条件：删除前应能注入"

    assert lm.delete(mid) is True
    system_after, events_after = _run_agent_for_memory("给我个操作建议")

    assert "不碰杠杆" not in system_after, "删除后仍在注入 ⇒ 删除没真生效"
    assert not [d for s, d in events_after if s == "memory_used"]


# ======================================================================
# 8. API（Step 5）：走真实 HTTP 层
# ======================================================================
def _client():
    from fastapi.testclient import TestClient
    from server.main import app
    return TestClient(app)


def test_api_memory_add_list_delete(iso):
    """B4 的 API 面：新增 → 列表可见 → 删除 → 列表不再含。"""
    c = _client()

    r = c.post("/api/memory", json={"kind": "preference", "content": "不碰杠杆",
                                    "key": "no_leverage"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and isinstance(body["id"], int)
    mid = body["id"]

    g = c.get("/api/memory").json()
    assert g["ok"] is True and g["total"] == 1
    assert len(g["groups"]["preference"]) == 1
    assert g["groups"]["preference"][0]["content"] == "不碰杠杆"
    assert g["kinds"] == ["preference", "fact", "experience"]

    d = c.delete(f"/api/memory/{mid}").json()
    assert d["ok"] is True
    assert c.get("/api/memory").json()["total"] == 0, "API 删除未真生效"


def test_api_memory_invalid_kind(iso):
    c = _client()
    r = c.post("/api/memory", json={"kind": "nonsense", "content": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False and "kind" in r.json()["error"]


def test_api_memory_empty_content(iso):
    c = _client()
    r = c.post("/api/memory", json={"kind": "fact", "content": "   "})
    assert r.json()["ok"] is False


def test_api_memory_missing_fields_422(iso):
    """缺必填字段 ⇒ 422（pydantic 拦在服务层之前）。"""
    c = _client()
    assert c.post("/api/memory", json={"content": "x"}).status_code == 422
    assert c.post("/api/memory", json={}).status_code == 422


def test_api_delete_missing_returns_error(iso):
    c = _client()
    r = c.delete("/api/memory/999999").json()
    assert r["ok"] is False and "不存在" in r["error"]


def test_api_pending_accept_flow(iso):
    """隐式候选：propose → 列表可见（且**不进**正式表）→ accept → 进表。"""
    c = _client()
    pid = lm.propose(lm.KIND_PREFERENCE, "用户似乎偏好低波动", key="risk_tolerance")

    pend = c.get("/api/memory/pending").json()
    assert pend["ok"] is True and len(pend["items"]) == 1
    assert c.get("/api/memory").json()["total"] == 0, "候选不得直接进正式表"

    r = c.post(f"/api/memory/pending/{pid}", json={"action": "accept"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["memory"]["source"] == "implicit"
    assert c.get("/api/memory/pending").json()["items"] == []
    assert c.get("/api/memory").json()["total"] == 1


def test_api_pending_reject_flow(iso):
    c = _client()
    pid = lm.propose(lm.KIND_FACT, "用户可能持有 600519")
    r = c.post(f"/api/memory/pending/{pid}", json={"action": "reject"})
    assert r.json()["ok"] is True
    assert c.get("/api/memory").json()["total"] == 0


def test_api_pending_bad_action(iso):
    c = _client()
    pid = lm.propose(lm.KIND_FACT, "x")
    r = c.post(f"/api/memory/pending/{pid}", json={"action": "maybe"}).json()
    assert r["ok"] is False and "accept" in r["error"]


def test_api_settings_toggle(iso):
    c = _client()
    assert c.get("/api/memory/settings").json()["enabled"] is True

    r = c.post("/api/memory/settings", json={"enabled": False}).json()
    assert r["ok"] is True and r["enabled"] is False
    assert lm.memory_enabled() is False, "开关未真正落到设置"

    assert c.post("/api/memory/settings", json={"enabled": True}).json()["enabled"] is True


def test_api_recall_preview(iso):
    """审计用：预览当前问题会召回什么（让用户看到 AI 到底看到了什么）。"""
    c = _client()
    lm.add(lm.KIND_PREFERENCE, "不碰杠杆", key="no_leverage")
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519", meta={"code": "600519"})

    r = c.get("/api/memory/recall-preview", params={"question": "600519 怎么样",
                                                    "codes": "600519"}).json()
    assert r["ok"] is True
    assert r["counts"]["preferences"] == 1 and r["counts"]["facts"] >= 1
    assert "不碰杠杆" in r["block"] and "600519" in r["block"]

    # 无关标的 ⇒ 不召回该事实
    r2 = c.get("/api/memory/recall-preview", params={"question": "怎么样"}).json()
    assert "1450" not in r2["block"]


# ======================================================================
# 9. 审计整改回归锁（F1 / F2 / F3 / F4 / F5）
# ======================================================================
def test_embed_text_accepts_list_of_floats(iso, monkeypatch):
    """⚠️ F1 读侧锁：真实 `embed_texts_batched` 返回 `list[list[float]]`。

    原实现要求 numpy 的 `.tobytes` ⇒ 恒返回 None ⇒ 向量召回**永远降级**。
    这里只桩 Ollama 边界（`embed_texts_batched`），**不桩 `embed_text`**。
    """
    import utils.rag.embed as re_mod

    monkeypatch.setattr(re_mod, "embed_texts_batched", lambda texts: [[0.1, 0.2, 0.3]])
    out = lm.embed_text("任意文本")
    assert isinstance(out, bytes), f"真实形态未被接受：{out!r}"
    assert len(out) == 3 * 4, f"float32 3 维应为 12 字节，实际 {len(out)}"


def test_embed_text_accepts_ndarray_too(iso, monkeypatch):
    """两种真实形态（list / ndarray）都要能吃。"""
    import numpy as np
    import utils.rag.embed as re_mod

    monkeypatch.setattr(re_mod, "embed_texts_batched",
                        lambda texts: [np.asarray([0.5, 0.5], dtype="float32")])
    assert isinstance(lm.embed_text("x"), bytes)


def test_experience_stores_embedding_column(iso, monkeypatch):
    """⚠️ F1 写侧锁：experience 必须**真的**把向量落进 `memories.embedding`。

    原实现的 `INSERT` 语句里根本没有 embedding 列 ⇒ 写侧从不产向量。
    """
    monkeypatch.setattr(lm, "embed_text", lambda t: b"\x00" * 16)
    mid = lm.add(lm.KIND_EXPERIENCE, "某条经验")

    conn = db.get_conn()
    try:
        raw = conn.execute("SELECT embedding FROM memories WHERE id = ?", (mid,)).fetchone()[0]
    finally:
        conn.close()
    assert raw is not None, "experience 未落向量（写侧仍有缺陷）"
    assert lm.get(mid)["embedded"] is True


def test_recall_experiences_uses_vectors_when_available(iso, monkeypatch):
    """⚠️ F1 核心锁：**有向量时必须走向量**（`embedded=True`），而不是永远时间倒序。"""
    import numpy as np

    vecs = {"甲": [1.0, 0.0], "乙": [0.0, 1.0]}
    monkeypatch.setattr(lm, "embed_text",
                        lambda t: np.asarray(vecs.get(str(t).strip(), [0.0, 0.0]),
                                             dtype="float32").tobytes())
    lm.add(lm.KIND_EXPERIENCE, "甲")
    lm.add(lm.KIND_EXPERIENCE, "乙")

    got = lm.recall_experiences("甲", top_k=1)
    assert len(got) == 1
    assert got[0]["content"] == "甲", f"向量排序未生效：{[g['content'] for g in got]}"
    assert got[0]["embedded"] is True, "应标注为已向量化"


def test_blank_key_facts_do_not_overwrite(iso):
    """⚠️ F3 锁：两条**不同内容**的无 key fact 必须共存（原实现静默覆盖 = 数据丢失）。"""
    a = lm.add(lm.KIND_FACT, "事实甲")
    b = lm.add(lm.KIND_FACT, "事实乙")
    assert a != b, "不同内容被静默覆盖成同一条"
    rows = lm.list_all(lm.KIND_FACT)
    assert len(rows) == 2, f"应共存 2 条，实际 {len(rows)}"
    assert {r["content"] for r in rows} == {"事实甲", "事实乙"}


def test_same_blank_key_content_still_dedups(iso):
    """同内容（都无 key）重复写 ⇒ 仍是同一条（指纹相同）。"""
    a = lm.add(lm.KIND_FACT, "事实甲")
    b = lm.add(lm.KIND_FACT, "  事实甲  ")
    assert a == b
    assert len(lm.list_all(lm.KIND_FACT)) == 1


def test_blank_key_pending_accept_does_not_overwrite(iso):
    """⚠️ F3 锁（候选路径）：两条无 key 候选逐条 accept ⇒ 必须共存。"""
    p1 = lm.propose(lm.KIND_PREFERENCE, "偏好甲")
    p2 = lm.propose(lm.KIND_PREFERENCE, "偏好乙")
    m1 = lm.accept_pending(p1)
    m2 = lm.accept_pending(p2)
    assert m1 != m2, "候选 accept 时互相覆盖"
    assert len(lm.list_all(lm.KIND_PREFERENCE)) == 2


def test_fact_recalled_by_code_in_key(iso):
    """⚠️ F5 锁：key 里带代码但**没有 meta** 的事实，必须按标的召回。"""
    lm.add(lm.KIND_FACT, "600519 成本 1450", key="stock:600519")
    assert any("1450" in r["content"] for r in lm.recall_facts(["600519"])), "未按 key 召回"
    assert not any("1450" in r["content"] for r in lm.recall_facts(["300750"])), \
        "无关标的仍被注入（退化成全局事实）"


def test_fund_and_etf_codes_are_recognized(iso):
    """⚠️ F4 锁：基金/ETF（1/5 开头）也要能被标的抽取识别。"""
    import re
    pat = re.compile(r"(?<!\d)([0134568]\d{5})(?!\d)")
    for code in ("161725", "510300", "600519", "000001", "300750"):
        assert pat.findall(code) == [code], f"{code} 未被识别"
    # 年份类短数字与长数字串不得误匹配
    assert pat.findall("2026 年") == []
    assert pat.findall("12345678901") == []


def test_api_can_create_pending(iso):
    """⚠️ F2 锁：产线必须能**产生**候选（原实现只有 list/accept/reject ⇒ pending 恒空）。"""
    c = _client()
    r = c.post("/api/memory/pending", json={"kind": "preference",
                                            "content": "用户似乎偏好低波动"})
    assert r.status_code == 200 and r.json()["ok"] is True
    pid = r.json()["id"]
    assert len(c.get("/api/memory/pending").json()["items"]) == 1
    assert c.post(f"/api/memory/pending/{pid}", json={"action": "accept"}).json()["ok"] is True
    assert c.get("/api/memory").json()["total"] == 1


def test_api_summarize_extracts(iso, monkeypatch):
    """⚠️ F2 锁：抽取端点可被产品调用（用桩 llm_fn 控制结果，链路是真的）。"""
    c = _client()
    monkeypatch.setattr(lm, "_default_llm_extract",
                        lambda messages: [{"kind": "preference", "content": "不碰杠杆",
                                           "key": "no_leverage"}])
    r = c.post("/api/memory/summarize", json={"session_id": None,
                                              "messages": None}).json()
    assert r["ok"] is True
    assert r["added"] == 1, r
    assert len(r["pending"]) == 1


def test_agent_run_extracts_candidates_at_summary_point(iso, monkeypatch):
    """⚠️ F2 接线锁：`agent_run` 在**会话摘要触发点**（每 8 轮）顺带抽取候选。"""
    from unittest.mock import patch

    from utils import agent_core, ai_helper

    calls = {"n": 0}

    def _fake_summarize(session_id, messages=None, llm_fn=None):
        calls["n"] += 1
        lm.propose(lm.KIND_PREFERENCE, "从会话抽到的偏好")
        return 1

    def _call(messages, tools=None, model=None, temperature=None, **kw):
        return {"type": "text", "content": "回答"}

    with patch.object(ai_helper, "call_llm", _call), \
         patch("utils.agent_memory.ensure_session", side_effect=lambda sid, title="": sid or 1), \
         patch("utils.agent_memory.record_message", return_value=None), \
         patch("utils.agent_memory.maybe_summarize_session", return_value=8), \
         patch("utils.ai_helper._is_demo_mode", return_value=False), \
         patch("services.settings_service.get_ai_read_holdings", return_value=False), \
         patch("utils.long_memory.summarize_to_candidates", _fake_summarize):
        agent_core.agent_run("你好", memory=True, session_id=1,
                             structured_progress=False)

    assert calls["n"] == 1, "摘要触发点未接线抽取（F2 回归）"
    assert len(lm.list_pending()) == 1
