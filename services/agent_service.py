# -*- coding: utf-8 -*-
"""
Agent 服务（M0）：会话 CRUD + 同步 chat + SSE 流。

SSE 通道（FRONTEND_PLAN §5.2）：agent_run 后台线程跑，进度回调写
queue.Queue，asyncio 生成器 asyncio.to_thread 读出逐条 yield——
agent_run 是同步阻塞多轮循环（单轮 LLM 可 30s+），绝不能直接在
事件循环里跑。

并发纪律（⚠️ B-R1 实测：**下面这条声明目前没有实现**）：原设计为全局
ThreadPoolExecutor(max_workers=4) 限流、超限 503「引擎忙」，以防多个标签页
并发打爆 DeepSeek 配额。但 `AGENT_POOL_SIZE` 与 503 在**全仓没有任何调用点**
（`git grep` 只命中本文件与 `docs/FRONTEND_PLAN.md` 的声明）—— 这是**既有偏差**
（`git log -S AGENT_POOL_SIZE` 指向 M0 `da334d5`，**不是 B1 引入**），
本轮按审计要求**只标注不实现**（见 `report-B-R1.md` B-F5）：
凡「SSE 已限流 / 超限会 503」的表述都不成立。
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
    from utils.ai_helper import _is_demo_mode, _has_key, _chat_model
    from services.llm_config import get_llm_config
    cfg = get_llm_config()
    return {
        "api_key_configured": _has_key(),
        "chat_model": cfg["model"] or DEEPSEEK_MODEL,
        "reasoner_model": cfg["reasoner_model"] or DEEPSEEK_REASONER_MODEL,
        # v1.1：demo 状态进 config——对话页已订阅 ['agent-config']，
        # 设置页开关 invalidate 后跨页即时可见（修"刷新才看得见"）
        "demo_mode": _is_demo_mode(),
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
        {"type": "writing", "text": "组织回答"}
        {"type": "done", "session_id": ..., "content": ..., "tool_trace": [...]}
        {"type": "evidence_judged", "query": ..., "level": ..., "items": [...],
         "checked": bool, "latency_ms": int, "reason": ""}   # **B1 新增，done 之后**
        {"type": "error", "message": "..."}

    ## B1 LLM 判官：**回答不等判官**（task-B1.md §1.2）

    - 触发条件：**仅当本轮检索的 `evidence_level == "weak"`**（判定单点在
      `services/judge_service.collect_from_tool_trace`；`none` 档逻辑不变、零调用）。
    - 顺序保证：`done` **先**入队（生成器为此先 yield 出去），判官结果**后到** ⇒
      回答时延不含判官（回归锁：tests/test_rag_llm_judge.py::test_done_arrives_before_judge_completes）。
    - 有界：判官在**独立单并发线程池**里跑，本 worker 只做有界等待（总预算
      `judge_service.JUDGE_TIMEOUT_S`）；超时/失败 ⇒ 事件如实带 `checked=False` +
      `reason`，**绝不抛、绝不假装判过**。
    - 判官**不参与检索排序/过滤**：这里只把结论作为事件发出去。
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
            # ★ 判官在 done **之后**：回答已经发出，判官只补充可信度标注。
            _emit_judge(res.get("tool_trace"))
        except Exception as e:  # noqa: BLE001 - worker 在线程里，异常必须走队列
            _emit({"type": "error", "message": str(e)})
        finally:
            _emit(_SENTINEL)

    def _emit_judge(tool_trace):
        """把判官结论送进 SSE 队列（旁路：任何失败都不得影响已发出的 done）。

        ⚠️ `total_budget_s` **必须**显式传：判官最多让**流**多活 `JUDGE_TIMEOUT_S`
        （不是每轮各等一次）—— 「超时不得拖住流」在参数层就锁死。

        ⚠️ B-R1 · 审计 B-F4（**显式取舍，带数字**）：判官在本 worker 线程内**同步**
        做有界等待 ⇒ `done` 之后 SSE 连接最多再多保持 `JUDGE_TIMEOUT_S=20s`
        （其间 `PING_INTERVAL=15s` 可能插一条 `: ping`）。实测 p50≈0.9s / p90≈1.2s
        （n=47，`scripts/rag_eval.py --judge llm`）⇒ 典型额外占用 ≈1s，上界 20s。
        **接受现状**：① `done` 已发出，回答时延不含判官；② 判官结论只能随本连接下发，
        拆独立端点属协议变更（超 B-R1 范围）。代价：并发流连接占用上界各 +20s，
        且并发流数**无上限**（既有偏差，见模块 docstring 的 `AGENT_POOL_SIZE` 标注）。
        """
        try:
            from services import judge_service as js
            for ev in js.judge_tool_trace(tool_trace,
                                          timeout_s=js.JUDGE_TIMEOUT_S,
                                          total_budget_s=js.JUDGE_TIMEOUT_S):
                _emit(ev)
        except Exception:  # noqa: BLE001 - 判官是增强项，不是依赖项
            pass

    t = threading.Thread(target=_worker, daemon=True, name="agent-sse-worker")
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item
