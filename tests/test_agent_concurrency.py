# -*- coding: utf-8 -*-
"""H1 · `AGENT_POOL_SIZE` 的**可达性**回归锁：并发流上限 + 超限 503 + 各路径释放。

背景（`task-H1.md` §1）：`AGENT_POOL_SIZE = 4` 与「超限 503」长期**只有声明没有实现**
（B-R1 审计 B-F5 如实标注为"既有偏差"，`report-B-R1.md`）。本文件是该声明的回归锁：

1. 名额上限真的挡得住第 5 个并发流 —— 而且是 **503**，不是静默放行、不是排队；
2. **释放**在四条路径上都成立：正常收流 / 生成器抛异常 / 调用方提前 `close()`
   （客户端断开的等价形态）/ 响应 `background` 兜底；凭据幂等（重复释放不减穿计数）。
   ⚠️ 漏释放 = 越用越"满"，最终所有请求 503 —— 正是任务书点名要防的失败形态。

⚠️ 上限**只作用于 HTTP SSE 入口**：本文件里直接调 `stream_events()` 的存量用例
（`test_m0_api.py::test_sse_stream_event_protocol` 等）不占名额，见模块 docstring。
"""
import json
from unittest.mock import patch

import httpx
import pytest

from data import database
from server.main import app
from services import agent_service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """独立临时库（与 test_m0_api 同款），防污染真实 fund_agent.db。"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t.db"))
    database.init_db()
    yield


@pytest.fixture(autouse=True)
def pool_clean():
    """用例前后计数必须归零：漏释放会污染下一个用例、让断言假绿。"""
    assert agent_service.active_stream_count() == 0, "进入用例时名额计数非 0（上个用例泄漏）"
    yield
    assert agent_service.active_stream_count() == 0, "用例结束时名额计数非 0（本用例泄漏）"


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _hold_all():
    """占满全部名额，返回凭据列表（调用方负责释放）。"""
    tickets = [agent_service.acquire_agent_slot()
               for _ in range(agent_service.AGENT_POOL_SIZE)]
    assert all(t is not None for t in tickets)
    return tickets


# ==================== ① 名额上限与幂等释放 ====================


def test_pool_limit_blocks_fifth_and_release_is_idempotent():
    """第 5 个并发流占不到名额；`release()` 幂等（重复调用不把计数减穿）。"""
    tickets = _hold_all()
    assert agent_service.active_stream_count() == agent_service.AGENT_POOL_SIZE
    assert agent_service.acquire_agent_slot() is None, "满员时第 5 个名额必须失败"

    assert tickets[0].release() is True
    assert tickets[0].release() is False, "同一凭据第二次 release 必须是无操作"
    assert agent_service.active_stream_count() == agent_service.AGENT_POOL_SIZE - 1

    # 释放一个后，第 5 个请求又能进来（上限是"同时并发数"，不是"总量"）
    extra = agent_service.acquire_agent_slot()
    assert extra is not None
    assert agent_service.acquire_agent_slot() is None

    for t in tickets[1:] + [extra]:
        t.release()


def test_open_stream_raises_agent_pool_full_when_full():
    """`open_stream()` 满员抛 `AgentPoolFull`（路由层据此转 503）。"""
    tickets = _hold_all()
    try:
        with pytest.raises(agent_service.AgentPoolFull) as ei:
            agent_service.open_stream("占满后第 5 个请求")
        assert "上限" in str(ei.value)
    finally:
        for t in tickets:
            t.release()


# ==================== ② 释放路径（这是任务书点名的风险点） ====================


def _fake_stream(task, session_id=None, context=None):
    yield {"type": "status", "state": "running"}
    yield {"type": "done", "session_id": 1, "content": "ok", "tool_trace": []}


def test_open_stream_releases_on_normal_completion(monkeypatch):
    monkeypatch.setattr(agent_service, "stream_events", _fake_stream)
    ticket, it = agent_service.open_stream("q")
    assert agent_service.active_stream_count() == 1, "open_stream 调用即占名额（不是首次 next）"
    assert list(it)[-1]["type"] == "done"
    assert agent_service.active_stream_count() == 0, "正常收流后名额必须归还"
    assert ticket.release() is False, "守卫生成器已释放 → 兜底再释放必须无操作"


def test_open_stream_releases_on_exception(monkeypatch):
    """worker 侧炸了（异常穿出生成器）也必须归还名额。"""
    def boom(task, session_id=None, context=None):
        yield {"type": "status", "state": "running"}
        raise RuntimeError("模拟 SSE worker 崩溃")

    monkeypatch.setattr(agent_service, "stream_events", boom)
    ticket, it = agent_service.open_stream("q")
    with pytest.raises(RuntimeError):
        list(it)
    assert agent_service.active_stream_count() == 0
    assert ticket.release() is False


def test_open_stream_releases_on_early_close(monkeypatch):
    """调用方提前 `close()`（= 客户端断开、生成器被 aclose）也必须归还名额。"""
    monkeypatch.setattr(agent_service, "stream_events", _fake_stream)
    ticket, it = agent_service.open_stream("q")
    assert next(it)["type"] == "status"
    it.close()                                   # 不等收流就断开
    assert agent_service.active_stream_count() == 0
    assert ticket.release() is False


# ==================== ③ 端到端：503 + 释放 ====================


@pytest.mark.anyio
async def test_router_returns_503_when_pool_full(tmp_db):
    """HTTP 入口：名额满 → **503**（响应头之前），不是 200 也不是 500。"""
    tickets = _hold_all()
    try:
        async with _client() as c:
            r = await c.post("/api/agent/chat/stream", json={"task": "第 5 个并发对话"})
        assert r.status_code == 503, "满员请求必须 503（实测 {}）".format(r.status_code)
        assert "引擎忙" in r.json()["detail"]
        assert "4" in r.json()["detail"]
    finally:
        for t in tickets:
            t.release()


def _fake_agent_run(task, context=None, memory=False, session_id=None, tools=None,
                    model=None, temperature=0.7, max_tool_rounds=8,
                    continue_question=False, on_progress=None, structured_progress=False):
    """确定性 mock（与 test_m0_api 同款）：走**真** SSE 路由 + 真 worker 线程。"""
    if on_progress:
        on_progress("writing", "组织回答")
    return {"type": "text", "content": "上证指数平稳。", "tool_trace": [], "session_id": 42}


@pytest.mark.anyio
async def test_router_releases_slot_after_stream_completes(tmp_db):
    """走真路由收完一条流后名额归零（回归锁：否则第 5 条对话起全部 503）。"""
    async with _client() as c:
        with patch("utils.agent_core.agent_run", side_effect=_fake_agent_run):
            async with c.stream("POST", "/api/agent/chat/stream",
                                json={"task": "查大盘"}) as r:
                assert r.status_code == 200
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
    types = [json.loads(l[6:])["type"] for l in buf.decode("utf-8").splitlines()
             if l.startswith("data: ")]
    assert types[-1] == "done"
    assert agent_service.active_stream_count() == 0, "流结束后名额必须归还"


@pytest.mark.anyio
async def test_two_sequential_streams_both_get_slots(tmp_db):
    """顺序两条流都能跑通 —— 第 1 条没归还名额的话，第 2 条会 503（泄漏的可见症状）。"""
    for _ in range(2):
        async with _client() as c:
            with patch("utils.agent_core.agent_run", side_effect=_fake_agent_run):
                async with c.stream("POST", "/api/agent/chat/stream",
                                    json={"task": "查大盘"}) as r:
                    assert r.status_code == 200
                    async for _chunk in r.aiter_bytes():
                        pass
        assert agent_service.active_stream_count() == 0
