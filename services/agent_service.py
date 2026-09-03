# -*- coding: utf-8 -*-
"""
Agent 服务（M0）：会话 CRUD + 同步 chat + SSE 流。

SSE 通道（FRONTEND_PLAN §5.2）：agent_run 后台线程跑，进度回调写
queue.Queue，asyncio 生成器 asyncio.to_thread 读出逐条 yield——
agent_run 是同步阻塞多轮循环（单轮 LLM 可 30s+），绝不能直接在
事件循环里跑。

并发纪律：全局 ThreadPoolExecutor(max_workers=4) 限流，超限 503
「引擎忙」——本地单用户场景防多个标签页并发打爆 DeepSeek 配额。
"""

import json
import queue
import threading

from config import API_KEY, DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL
from data.database import (
    create_agent_session,
    delete_agent_session,
    get_agent_messages,
    list_agent_sessions,
    rename_agent_session,
    toggle_agent_session_archived,
    toggle_agent_session_pinned,
)
from services._json import to_jsonable

# SSE 事件队列的收官哨兵（worker 结束后放入，生成器读到即收流）
_SENTINEL = object()

# 每条 SSE 事件的间隔保活：15s 发一次 ": ping" 注释行防代理掐断（§5.1）
PING_INTERVAL = 15.0

# agent_run 是重活（多轮 LLM），限 4 并发；超限由调用方返回 503
AGENT_POOL_SIZE = 4


def config():
    """GET /api/agent/config"""
    return {
        "api_key_configured": bool(API_KEY),
        "chat_model": DEEPSEEK_MODEL,
        "reasoner_model": DEEPSEEK_REASONER_MODEL,
    }


def sessions_list(limit=20, include_archived=False):
    """GET /api/agent/sessions：会话侧栏（置顶优先，未归档）"""
    rows = list_agent_sessions(limit=limit, include_archived=include_archived)
    return to_jsonable(rows)


def sessions_create(title=""):
    """POST /api/agent/sessions：新建会话"""
    sid = create_agent_session(title=str(title or "")[:30])
    return {"ok": bool(sid), "session_id": sid}


def session_messages(session_id, limit=None):
    """GET /api/agent/sessions/{id}/messages：历史回放（只回 user/assistant 文本）"""
    raw = get_agent_messages(session_id, limit=limit)
    out = []
    for m in raw:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def session_patch(session_id, action, title=""):
    """PATCH /api/agent/sessions/{id}：rename / pin / archive 三合一

    action: "rename" | "pin" | "unpin" | "archive" | "restore"
    """
    sid = int(session_id)
    if action == "rename":
        name = str(title or "").strip()
        if not name:
            return {"ok": False, "error": "会话名称不能为空"}
        ok = rename_agent_session(sid, name[:60])
        return {"ok": bool(ok)}
    if action == "pin":
        return {"ok": True, "pinned": toggle_agent_session_pinned(sid)}
    if action == "unpin":
        return {"ok": True, "pinned": toggle_agent_session_pinned(sid)}
    if action == "archive":
        return {"ok": True, "archived": toggle_agent_session_archived(sid)}
    if action == "restore":
        return {"ok": True, "archived": toggle_agent_session_archived(sid)}
    return {"ok": False, "error": "未知操作：{}".format(action)}


def session_delete(session_id):
    """DELETE /api/agent/sessions/{id}：会话+消息级联删除"""
    delete_agent_session(int(session_id))
    return {"ok": True}


def _pick_result(res):
    """done 事件只带前端需要的字段（tool_trace 可含大体积工具输出，原样带全）"""
    return {
        "session_id": res.get("session_id"),
        "content": res.get("content") or "",
        "tool_trace": to_jsonable(res.get("tool_trace") or []),
    }


def run_chat(task, session_id=None, context=None):
    """POST /api/agent/chat：同步 JSON（非流式 fallback，120s 级）

    返回 {"ok": bool, ...}；无 key 时 agent_run 内部走 demo 降级路径。
    """
    from utils.agent_core import agent_run
    try:
        res = agent_run(
            str(task),
            context=context,
            memory=True,
            session_id=session_id,
            continue_question=bool(session_id),
        )
    except Exception as e:  # noqa: BLE001 - 顶层兜底：SSE 之外的同步路径也要返 JSON
        return {"ok": False, "error": "Agent 执行失败：{}".format(e)}
    return {"ok": True, **_pick_result(res)}


def stream_events(task, session_id=None, context=None):
    """POST /api/agent/chat/stream 的生成器（同步 yield 事件 dict，SSE 包装在 router 层）。

    事件协议（§5.1）：
        {"type": "status", "state": "running"}
        {"type": "reasoning", "text": "..."}          # 模型原生思考流（可多次）
        {"type": "tool_start", "name": ..., "arguments": {...}}
        {"type": "tool_end", "name": ..., "ok": bool, "elapsed_ms": int}
        {"type": "writing", "text": "组织最终回答"}
        {"type": "done", "session_id": ..., "content": ..., "tool_trace": [...]}
        {"type": "error", "message": "..."}
    """
    q = queue.Queue(maxsize=500)

    def _emit(item):
        try:
            q.put_nowait(item)
        except queue.Full:  # 消费端断开/卡死：丢进度事件，不炸 worker
            pass

    def _on_progress(stage, detail):
        # structured_progress=True 时 detail 是 dict（tool_start/tool_end）
        if isinstance(detail, dict):
            _emit({"type": stage, **detail})
        else:
            # 字符串进度（reasoning/tool/writing）按类型补字段名
            if stage == "reasoning":
                _emit({"type": "reasoning", "text": str(detail)})
            elif stage == "tool":
                _emit({"type": "tool", "text": str(detail)})
            else:
                _emit({"type": "writing", "text": str(detail)})

    def _worker():
        from utils.agent_core import agent_run
        try:
            _emit({"type": "status", "state": "running"})
            res = agent_run(
                str(task),
                context=context,
                memory=True,
                session_id=session_id,
                continue_question=bool(session_id),
                structured_progress=True,
                on_progress=_on_progress,
            )
            _emit({"type": "done", **_pick_result(res)})
        except Exception as e:  # noqa: BLE001 - worker 在线程里，异常必须走队列
            _emit({"type": "error", "message": str(e)})
        finally:
            _emit(_SENTINEL)

    t = threading.Thread(target=_worker, daemon=True, name="agent-sse-worker")
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item
