# -*- coding: utf-8 -*-
"""
Agent 路由（M0）：会话 CRUD + 同步 chat + SSE 流。

SSE（§5.2）：services.agent_service.stream_events 是同步生成器，
丢 run_in_threadpool/线程里逐条读，asyncio 侧只做 yield 组帧；
每 PING_INTERVAL 发 ": ping" 注释行防代理掐断。
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from typing import Optional

from services import agent_service

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatIn(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    session_id: Optional[int] = None
    context: Optional[list] = None    # 追问上下文（诊断数据/记忆摘要等）


class SessionPatchIn(BaseModel):
    action: str                       # rename / pin / unpin / archive / restore
    title: str = ""


class SessionCreateIn(BaseModel):
    title: str = ""


@router.get("/config")
def agent_config():
    """key 配置与模型名"""
    return agent_service.config()


@router.get("/sessions")
def sessions(limit: int = 20, archived: bool = False):
    """会话列表（置顶优先；archived=true 给回收站视图）"""
    return agent_service.sessions_list(limit=limit, include_archived=archived)


@router.post("/sessions")
def create_session(body: SessionCreateIn):
    """新建会话"""
    return agent_service.sessions_create(title=body.title)


@router.get("/sessions/{session_id}/messages")
def session_messages(session_id: int, limit: Optional[int] = None):
    """历史回放（只回 user/assistant 文本）"""
    return agent_service.session_messages(session_id, limit=limit)


@router.patch("/sessions/{session_id}")
def patch_session(session_id: int, body: SessionPatchIn):
    """置顶/归档/重命名（action 区分）"""
    return agent_service.session_patch(session_id, body.action, title=body.title)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int):
    """删除会话（消息级联）"""
    return agent_service.session_delete(session_id)


@router.post("/chat")
async def chat(body: ChatIn):
    """同步 chat（非流式 fallback；LLM 120s 级 → 必须出事件循环）"""
    return await run_in_threadpool(
        agent_service.run_chat,
        body.task, body.session_id, body.context,
    )


@router.post("/chat/stream")
async def chat_stream(body: ChatIn):
    """SSE 流式 chat（协议见 FRONTEND_PLAN §5.1）"""
    if not body.task.strip():
        raise HTTPException(status_code=400, detail="task 不能为空")

    async def gen():
        queue_backlog = asyncio.Queue()

        async def pump():
            """线程池里拉同步生成器的事件，转投 asyncio 队列"""
            def _next_sync(it):
                try:
                    return next(it)
                except StopIteration:
                    return None
            it = agent_service.stream_events(
                body.task, body.session_id, body.context)
            while True:
                item = await run_in_threadpool(_next_sync, it)
                if item is None:
                    await queue_backlog.put(None)
                    break
                await queue_backlog.put(item)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    # 保活窗口内无事件 → 发 ": ping" 注释行（SSE 规范，客户端忽略）
                    item = await asyncio.wait_for(
                        queue_backlog.get(), timeout=agent_service.PING_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                yield "data: {}\n\n".format(
                    json.dumps(item, ensure_ascii=False))
        finally:
            pump_task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
