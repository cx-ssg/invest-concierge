# -*- coding: utf-8 -*-
"""M0 API 集成测试：FastAPI 路由契约（CRUD 往返 + SSE 事件协议），全离线 mock。

SSE 用 httpx.AsyncClient + ASGITransport（逐块真实流式读取，不像 TestClient
那样缓冲到完成——TestClient 测不出流式语义，M0 实测踩过）。
"""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from data import database
from server.main import app


# 每个测试用独立临时库，防污染真实 fund_agent.db
@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t.db"))
    database.init_db()
    yield


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


# ==================== core：health / nav ====================


@pytest.mark.anyio
async def test_health_and_nav(tmp_db):
    async with _client() as c:
        r = await c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert {"api_key_configured", "version", "ts"} <= set(body)

        r = await c.get("/api/nav")
        assert r.status_code == 200
        tracks = {t["track"]: t for t in r.json()["tracks"]}
        assert set(tracks) == {"fund", "stock", "common"}
        assert [p["key"] for p in tracks["fund"]["pages"]] == ["dashboard", "portfolio", "diary"]
        assert tracks["stock"]["pages"][0]["key"] == "stock_diagnosis"


# ==================== holdings CRUD 往返 ====================


@pytest.mark.anyio
async def test_holdings_crud_roundtrip(tmp_db):
    async with _client() as c:
        r = await c.post("/api/holdings/funds", json={
            "code": "000961", "name": "沪深300联接", "amount": 10000,
            "cost_nav": 1.65, "hold_shares": 6060.61})
        assert r.status_code == 200 and r.json()["ok"] is True

        r = await c.get("/api/holdings/funds")
        assert r.status_code == 200
        funds = r.json()
        assert len(funds) == 1 and funds[0]["code"] == "000961"

        r = await c.put("/api/holdings/funds/000961", json={"amount": 12000})
        assert r.status_code == 200 and r.json()["ok"] is True
        r = await c.get("/api/dashboard/summary")
        assert r.json()["total_invest"] == 12000

        r = await c.delete("/api/holdings/funds/000961")
        assert r.json()["ok"] is True
        assert (await c.get("/api/holdings/funds")).json() == []

        # 校验：负数金额 → 422（pydantic gt=0）
        r = await c.post("/api/holdings/funds", json={
            "code": "x", "name": "", "amount": -1, "cost_nav": 0, "hold_shares": 0})
        assert r.status_code == 422


# ==================== diary CRUD 往返 ====================


@pytest.mark.anyio
async def test_diary_crud_roundtrip(tmp_db):
    async with _client() as c:
        r = await c.post("/api/diary", json={
            "fund_code": "161725", "fund_name": "白酒",
            "action": "买入", "amount": 2000, "note": "测试"})
        assert r.status_code == 200
        entry = r.json()
        assert entry["ok"] is True and entry["id"] > 0

        r = await c.get("/api/diary")
        assert len(r.json()) == 1

        r = await c.delete("/api/diary/{}".format(entry["id"]))
        assert r.json()["ok"] is True
        # 再删同一条 → 明确报不存在
        r = await c.delete("/api/diary/{}".format(entry["id"]))
        assert r.json()["ok"] is False


# ==================== agent sessions CRUD ====================


@pytest.mark.anyio
async def test_agent_sessions_lifecycle(tmp_db):
    async with _client() as c:
        r = await c.post("/api/agent/sessions", json={"title": "测试会话"})
        sid = r.json()["session_id"]
        assert r.json()["ok"] is True and sid > 0

        r = await c.patch("/api/agent/sessions/{}".format(sid),
                          json={"action": "pin"})
        assert r.json()["pinned"] is True

        r = await c.patch("/api/agent/sessions/{}".format(sid),
                          json={"action": "rename", "title": "改名后"})
        assert r.json()["ok"] is True

        r = await c.patch("/api/agent/sessions/{}".format(sid),
                          json={"action": "archive"})
        assert r.json()["archived"] is True

        # 归档的不进默认列表，进 archived 视图
        r = await c.get("/api/agent/sessions")
        assert all(s["id"] != sid for s in r.json())
        r = await c.get("/api/agent/sessions?archived=true")
        assert any(s["id"] == sid and s["title"] == "改名后" for s in r.json())

        # 未归档列表置顶排序逻辑已由 test_memory 覆盖；这里验证端点连通
        r = await c.get("/api/agent/sessions/{}/messages".format(sid))
        assert r.status_code == 200 and r.json() == []

        r = await c.delete("/api/agent/sessions/{}".format(sid))
        assert r.json()["ok"] is True


# ==================== SSE 契约：mock agent_run 发结构化事件 ====================


def _fake_agent_run(task, context=None, memory=False, session_id=None, tools=None,
                    model=None, temperature=0.7, max_tool_rounds=8,
                    continue_question=False, on_progress=None, structured_progress=False):
    """确定性 mock：reasoning → tool_start/tool_end → writing，模拟真实 on_progress 流"""
    if on_progress:
        on_progress("reasoning", "思考中...")
        on_progress("tool", "get_market_index()")
        if structured_progress:
            on_progress("tool_start", {"name": "get_market_index", "arguments": {}})
            on_progress("tool_end", {"name": "get_market_index", "ok": True, "elapsed_ms": 12})
        on_progress("writing", "组织最终回答")
    return {"type": "text", "content": "上证指数平稳。",
            "tool_trace": [{"name": "get_market_index", "arguments": {}, "output": "ok"}],
            "session_id": 42}


@pytest.mark.anyio
async def test_sse_stream_event_protocol(tmp_db):
    """SSE 契约：status 先行 → 结构化事件对 → done 收官；data: 行全部可解析"""
    collected = []
    async with _client() as c:
        with patch("utils.agent_core.agent_run", side_effect=_fake_agent_run):
            async with c.stream("POST", "/api/agent/chat/stream",
                                json={"task": "查大盘"}) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
    for line in buf.decode("utf-8").splitlines():
        if not line.startswith("data: "):
            continue
        collected.append(json.loads(line[6:]))

    types = [e["type"] for e in collected]
    assert types[0] == "status" and collected[0]["state"] == "running"
    assert "reasoning" in types and "tool_start" in types and "tool_end" in types
    assert types[-1] == "done"
    tool_start = next(e for e in collected if e["type"] == "tool_start")
    tool_end = next(e for e in collected if e["type"] == "tool_end")
    assert tool_start["name"] == "get_market_index"
    assert tool_end["ok"] is True and isinstance(tool_end["elapsed_ms"], int)
    done = collected[-1]
    assert done["session_id"] == 42 and done["content"] == "上证指数平稳。"
    assert isinstance(done["tool_trace"], list) and done["tool_trace"][0]["name"] == "get_market_index"


@pytest.mark.anyio
async def test_sse_empty_task_400(tmp_db):
    async with _client() as c:
        r = await c.post("/api/agent/chat/stream", json={"task": "  "})
        assert r.status_code == 400
