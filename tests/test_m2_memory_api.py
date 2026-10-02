# -*- coding: utf-8 -*-
"""M2 长期记忆 API 契约测试（**前端契约**，供设置页「长期记忆」区块绑定）。

为什么另开一个文件：`tests/test_m2_memory.py` 已用真实 HTTP 覆盖全部 10 个端点
（`git grep -n "api/memory" -- tests/` 可查），但断言集中在**行为语义**；
本文件只锁**前端要绑定的字段形态**（字段改名/缺失会让设置页静默显示空白），
以及 UI 对用户的两条承诺：
  ① 总开关关闭 ⇒ 召回预览为空串、三类计数全 0（不注入、不谎报）；
  ② 删除某条 ⇒ 它不再可能被召回（B4 的审计面）。

原则（对齐 `tests/test_m2_memory.py`）：
- 打**真实 app**（fastapi `TestClient`）+ 真实 SQLite（`tmp_path` 隔离，绝不碰生产库）；
- **不 monkeypatch 被测对象**；唯一打桩处是 LLM 抽取边界（模拟无 Key 的真实降级 ⇒ 规则兜底）。
"""
import pytest

from data import database
from server.main import app
from utils import long_memory as lm

#: 设置页「记忆列表」每条要读的字段（缺一个即渲染成空/undefined）
LIST_ITEM_FIELDS = {"id", "key", "content", "meta", "source", "session_id", "created_at", "updated_at"}
#: 设置页「待确认候选」每条要读的字段
PENDING_ITEM_FIELDS = {"id", "kind", "key", "content", "session_id", "status", "created_at"}
#: 召回预览三类计数键（前端逐项展示命中数）
COUNT_KEYS = {"preferences", "facts", "experiences"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离库 + 真实 TestClient（每个用例独立 tmp 库）。"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "m2_api.db"))
    database.init_db()
    from fastapi.testclient import TestClient

    return TestClient(app)


# ==================== 1. GET /api/memory（列表与分组） ====================

def test_contract_list_shape_and_empty_groups(client):
    """三类分组**恒存在**：设置页按 `kinds` 逐类渲染，缺键会直接崩。"""
    b = client.get("/api/memory").json()
    assert b["ok"] is True
    assert b["total"] == 0
    assert set(b["groups"]) == {"preference", "fact", "experience"}, sorted(b["groups"])
    assert all(b["groups"][k] == [] for k in ("preference", "fact", "experience"))
    assert b["kinds"] == ["preference", "fact", "experience"]
    assert isinstance(b["enabled"], bool)

    ids = {}
    for kind, content in (("preference", "不碰杠杆"),
                          ("fact", "600519 成本 1450"),
                          ("experience", "8 月加仓回撤 12%")):
        r = client.post("/api/memory", json={"kind": kind, "content": content})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        ids[kind] = r.json()["id"]

    b2 = client.get("/api/memory").json()
    assert b2["total"] == 3
    for kind in ("preference", "fact", "experience"):
        items = b2["groups"][kind]
        assert len(items) == 1, (kind, items)
        assert LIST_ITEM_FIELDS <= set(items[0]), (kind, sorted(items[0]))
        assert items[0]["id"] == ids[kind]
        # 前端「来源：手动/显式」分支
        assert items[0]["source"] == "explicit"
        assert isinstance(items[0]["meta"], dict)


# ==================== 2. GET/POST /api/memory/settings（总开关语义） ====================

def test_contract_settings_switch_off_means_no_recall(client):
    """UI 文案承诺「关闭后 AI 不读取长期记忆」⇒ HTTP 面必须真的空召回。"""
    s = client.get("/api/memory/settings").json()
    assert {"ok", "enabled"} <= set(s)
    assert s["enabled"] is True, "默认应为开（与「允许 AI 读取持仓」一致）"

    client.post("/api/memory", json={"kind": "preference", "content": "不碰杠杆"})
    on = client.get("/api/memory/recall-preview", params={"question": "推荐个标的"}).json()
    assert on["counts"]["preferences"] == 1 and "不碰杠杆" in on["block"]

    off = client.post("/api/memory/settings", json={"enabled": False}).json()
    assert off["ok"] is True and off["enabled"] is False
    assert client.get("/api/memory/settings").json()["enabled"] is False

    prev = client.get("/api/memory/recall-preview", params={"question": "推荐个标的"}).json()
    assert prev["block"] == "", prev
    assert prev["counts"] == {"preferences": 0, "facts": 0, "experiences": 0}, prev

    assert client.post("/api/memory/settings", json={"enabled": True}).json()["enabled"] is True
    assert "不碰杠杆" in client.get(
        "/api/memory/recall-preview", params={"question": "推荐个标的"}).json()["block"]


# ==================== 3. /api/memory/pending（候选确认） ====================

def test_contract_pending_create_list_accept_reject(client):
    created = client.post("/api/memory/pending", json={
        "kind": "fact", "content": "600519 成本 1450",
        "key": "stock:600519", "meta": {"code": "600519"}}).json()
    assert created["ok"] is True and isinstance(created["id"], int)
    pid = created["id"]

    items = client.get("/api/memory/pending").json()["items"]
    assert len(items) == 1
    assert PENDING_ITEM_FIELDS <= set(items[0]), sorted(items[0])
    assert items[0]["status"] == "pending" and items[0]["kind"] == "fact"
    assert client.get("/api/memory").json()["total"] == 0, "候选不得直接进正式表"

    acc = client.post("/api/memory/pending/%d" % pid, json={"action": "accept"}).json()
    assert acc["ok"] is True and acc["memory"]["source"] == "implicit"
    assert client.get("/api/memory").json()["total"] == 1
    assert all(i["id"] != pid for i in client.get("/api/memory/pending").json()["items"])

    p2 = client.post("/api/memory/pending",
                     json={"kind": "preference", "content": "偏好低波动"}).json()["id"]
    rej = client.post("/api/memory/pending/%d" % p2, json={"action": "reject"}).json()
    assert rej["ok"] is True and rej.get("rejected") is True
    assert client.post("/api/memory/pending/%d" % p2, json={"action": "reject"}).json()["ok"] is False
    assert client.post("/api/memory/pending/%d" % p2, json={"action": "maybe"}).json()["ok"] is False


# ==================== 4. POST/DELETE /api/memory（手动新增 / 删除） ====================

def test_contract_add_delete_and_error_shape(client):
    bad = client.post("/api/memory", json={"kind": "nonsense", "content": "x"}).json()
    assert bad["ok"] is False and isinstance(bad["error"], str) and bad["error"]
    empty = client.post("/api/memory", json={"kind": "fact", "content": "   "}).json()
    assert empty["ok"] is False and isinstance(empty["error"], str)
    assert client.post("/api/memory", json={"content": "x"}).status_code == 422

    mid = client.post("/api/memory", json={
        "kind": "fact", "content": "600519 成本 1450", "key": "stock:600519"}).json()["id"]
    d = client.delete("/api/memory/%d" % mid).json()
    assert d["ok"] is True and d["id"] == mid
    assert client.get("/api/memory").json()["total"] == 0

    missing = client.delete("/api/memory/999999").json()
    assert missing["ok"] is False and isinstance(missing["error"], str) and missing["error"]


def test_contract_deleted_memory_is_no_longer_recalled(client):
    """UI 文案承诺「删除即时生效」⇒ 删完的召回预览里不得再出现（审计面）。"""
    mid = client.post("/api/memory", json={"kind": "preference", "content": "不碰杠杆"}).json()["id"]
    before = client.get("/api/memory/recall-preview", params={"question": "给点建议"}).json()
    assert "不碰杠杆" in before["block"], "前置条件：删除前应能召回"

    assert client.delete("/api/memory/%d" % mid).json()["ok"] is True
    after = client.get("/api/memory/recall-preview", params={"question": "给点建议"}).json()
    assert after["block"] == "", after
    assert after["counts"]["preferences"] == 0, after


# ==================== 5. GET /api/memory/recall-preview（审计入口） ====================

def test_contract_recall_preview_shape_and_code_filter(client):
    client.post("/api/memory", json={"kind": "preference", "content": "不碰杠杆"})
    client.post("/api/memory", json={
        "kind": "fact", "content": "600519 成本 1450",
        "key": "stock:600519", "meta": {"code": "600519"}})

    b = client.get("/api/memory/recall-preview",
                   params={"question": "600519 怎么样", "codes": "600519"}).json()
    assert {"ok", "counts", "block"} <= set(b)
    assert set(b["counts"]) == COUNT_KEYS, sorted(b["counts"])
    assert isinstance(b["block"], str) and b["block"].startswith("## 长期记忆")
    assert b["counts"]["preferences"] == 1 and b["counts"]["facts"] >= 1
    assert "不碰杠杆" in b["block"] and "1450" in b["block"]

    other = client.get("/api/memory/recall-preview",
                       params={"question": "300750 怎么样", "codes": "300750"}).json()
    assert "1450" not in other["block"], "无关标的不得召回该事实（否则退化成全局注入）"

    # 两个 query 都有默认值：设置页「问题」为空时也不得 422
    assert client.get("/api/memory/recall-preview").json()["ok"] is True


# ==================== 6. POST /api/memory/summarize（按会话抽取候选） ====================

def test_contract_summarize_extracts_candidates(client, monkeypatch):
    """抽取入口在**无 Key** 时走规则兜底（真实降级路径），候选仍可被前端确认。"""
    from utils import agent_memory

    sid = agent_memory.ensure_session(None, title="契约测试会话")
    agent_memory.record_message(sid, "user", "记住 我不碰可转债")
    agent_memory.record_message(sid, "assistant", "好的，已记下")

    def _no_llm(_messages):
        raise RuntimeError("无 Key（模拟真实降级）")

    monkeypatch.setattr(lm, "_default_llm_extract", _no_llm)

    r = client.post("/api/memory/summarize", json={"session_id": sid}).json()
    assert r["ok"] is True and r["added"] >= 1, r
    assert isinstance(r["pending"], list) and len(r["pending"]) >= 1

    items = client.get("/api/memory/pending").json()["items"]
    assert any("可转债" in i["content"] for i in items), items
    assert all(PENDING_ITEM_FIELDS <= set(i) for i in items)
