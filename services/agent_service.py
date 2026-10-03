# -*- coding: utf-8 -*-
"""
Agent 服务（M0）：会话 CRUD + 同步 chat + SSE 流。

SSE 通道（FRONTEND_PLAN §5.2）：agent_run 后台线程跑，进度回调写
queue.Queue，asyncio 生成器 asyncio.to_thread 读出逐条 yield——
agent_run 是同步阻塞多轮循环（单轮 LLM 可 30s+），绝不能直接在
事件循环里跑。

并发纪律（H1 · 2026-10-03 **已落地，声明与实现一致**）：`AGENT_POOL_SIZE = 4`
是 **HTTP SSE 入口**（`server/routers/agent.py::chat_stream`）的并发流上限 ——
占不到名额的请求在**响应开始前**就 503「引擎忙」，不会进入 `agent_run`
（多个标签页并发打爆 DeepSeek 配额的防护由此生效）。

- 名额 = 模块级计数 + `threading.Lock`（`acquire_agent_slot()`）；凭据
  `AgentPoolTicket.release()` **幂等**（重复释放不会把计数减穿）。
- **释放走两条独立路径**（H1 审计要点：漏释放 = 越用越"满"）：
  ① `open_stream()` 的守卫生成器 `finally` —— 正常收流 / 异常 / 调用方提前
     `close()`（客户端断开）都会执行；
  ② `StreamingResponse(background=BackgroundTask(ticket.release))` 兜底 ——
     生成器未被及时 `aclose()` 时由响应收尾兜底。凭据幂等 ⇒ 两条路径不会双减。
- ⚠️ **范围**：上限只作用于 HTTP SSE 入口。脚本/测试**直接调用** `stream_events()`
  不占名额；同步 `/api/agent/chat`（`run_chat`）也不占 —— 它没有"流"要占。
  （`git log -S AGENT_POOL_SIZE` 指向 M0 `da334d5`；B-R1 曾如实标注"声明无调用点"，
  见 `report-B-R1.md` B-F5；本文件 H1 起该偏差已消除。）
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

# agent_run 是重活（多轮 LLM + 判官），HTTP SSE 限 4 并发；超限由路由层 503「引擎忙」
AGENT_POOL_SIZE = 4

#: 并发流名额的锁与计数（模块级：一个进程内共享）。只经由下方 3 个函数读写。
_pool_lock = threading.Lock()
_pool_active = 0


class AgentPoolFull(Exception):
    """并发流名额已满 —— 路由层把它转成 503「引擎忙」。"""


class AgentPoolTicket:
    """一次并发流的占位凭据。`release()` **幂等**：重复调用只真正释放一次。

    幂等是硬要求：释放有两条路径（守卫生成器 `finally` + 响应 `background`），
    客户端断开时两条都可能在同一次流上触发 —— 非幂等的 `-= 1` 会把计数减穿，
    反而把上限从"限制"变成"形同虚设"。
    """

    __slots__ = ("_released",)

    def __init__(self):
        self._released = False

    def release(self):
        """释放名额。返回 True = 本次真的释放；False = 之前已释放过。"""
        global _pool_active
        with _pool_lock:
            if self._released:
                return False
            self._released = True
            if _pool_active > 0:
                _pool_active -= 1
            return True


def acquire_agent_slot():
    """尝试占一个并发流名额；满员返回 None（调用方负责 503）。"""
    global _pool_active
    with _pool_lock:
        if _pool_active >= AGENT_POOL_SIZE:
            return None
        _pool_active += 1
        return AgentPoolTicket()


def active_stream_count():
    """当前持名的并发流数（观测/测试用；不要拿它做业务判断）。"""
    with _pool_lock:
        return _pool_active


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
    """GET /api/agent/sessions/{id}/messages：历史回放（user/assistant 文本 + 来源/判官）。

    G1-2：助手消息若落库时带了 `meta`（`{"sources": [...], "judge": {...}}`），
    这里**透传**成同名的 `sources` / `judge` 键 —— 前端历史分支据此复用新消息的
    `SourceList`（来源卡 + 判官标注 + `[n]` 上标回跳）。

    ⚠️ 协议纪律（沿用 A-R2/F5「与 sources 同条件」）：**没有**就不出现该键 ——
    老会话 `meta IS NULL` ⇒ 返回体仍是 `{role, content}`（前端不显示来源区，
    也不会出现"无来源"占位）。既有消费方只读 role/content，增键不影响。
    """
    raw = get_agent_messages(session_id, limit=limit)
    out = []
    for m in raw:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content:
            item = {"role": role, "content": content}
            meta = m.get("meta")
            if isinstance(meta, dict):
                sources = meta.get("sources")
                if isinstance(sources, list) and sources:
                    item["sources"] = sources
                judge = meta.get("judge")
                if isinstance(judge, dict) and judge:
                    item["judge"] = judge
            out.append(item)
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
            _emit_judge(res.get("tool_trace"), res.get("assistant_message_id"))
        except Exception as e:  # noqa: BLE001 - worker 在线程里，异常必须走队列
            _emit({"type": "error", "message": str(e)})
        finally:
            _emit(_SENTINEL)

    def _emit_judge(tool_trace, assistant_message_id=None):
        """把判官结论送进 SSE 队列（旁路：任何失败都不得影响已发出的 done）。

        G1-2 写入点③：判官结论与 sources **同一 `meta` 列** —— 事件照发的同时，
        把 `{chunk_id: 结论}` 合并回**该轮助手消息**的 meta（read-modify-write，
        不会抹掉已落库的 sources）。回填失败只影响"回放能看到判官标注"，不影响对话。

        ⚠️ `total_budget_s` **必须**显式传：判官最多让**流**多活 `JUDGE_TIMEOUT_S`
        （不是每轮各等一次）—— 「超时不得拖住流」在参数层就锁死。

        ⚠️ B-R1 · 审计 B-F4（**显式取舍，带数字**）：判官在本 worker 线程内**同步**
        做有界等待 ⇒ `done` 之后 SSE 连接最多再多保持 `JUDGE_TIMEOUT_S=20s`
        （其间 `PING_INTERVAL=15s` 可能插一条 `: ping`）。实测 p50≈0.9s / p90≈1.2s
        （n=47，`scripts/rag_eval.py --judge llm`）⇒ 典型额外占用 ≈1s，上界 20s。
        **接受现状**：① `done` 已发出，回答时延不含判官；② 判官结论只能随本连接下发，
        拆独立端点属协议变更（超 B-R1 范围）。代价：并发流连接占用上界各 +20s
        （H1 起并发流数由 HTTP 入口的 `AGENT_POOL_SIZE`/`open_stream()` 限为 4，
        超限 503；本函数所在的 SSE worker 占的就是那个名额）。
        """
        try:
            from services import judge_service as js
            events = []
            for ev in js.judge_tool_trace(tool_trace,
                                          timeout_s=js.JUDGE_TIMEOUT_S,
                                          total_budget_s=js.JUDGE_TIMEOUT_S):
                events.append(ev)
                _emit(ev)
            # G1-2：落库（在事件全部发出之后，纯附加动作）
            _persist_judge(assistant_message_id, events)
        except Exception:  # noqa: BLE001 - 判官是增强项，不是依赖项
            pass

    def _persist_judge(assistant_message_id, events):
        """把判官结论并入助手消息 meta（失败静默：回放少个标注 ≠ 对话出错）。"""
        try:
            from data.database import merge_agent_message_meta
            from services import judge_service as js

            judge = js.judge_items_map(events)
            if judge and assistant_message_id:
                merge_agent_message_meta(assistant_message_id, {"judge": judge})
        except Exception:  # noqa: BLE001 - 旁路
            pass

    t = threading.Thread(target=_worker, daemon=True, name="agent-sse-worker")
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item


def open_stream(task, session_id=None, context=None):
    """占并发名额并返回 `(ticket, stream_events 的守卫生成器)` —— 路由层的唯一 SSE 入口。

    与直接调用 `stream_events()` 的差别：
      ① 名额满 ⇒ 抛 `AgentPoolFull`（路由层转 503「引擎忙」）；
      ② 生成器无论**正常收流 / 抛异常 / 调用方提前 `close()`**，`finally` 都释放名额。

    ⚠️ 本函数**故意不是生成器函数**（函数体里没有 yield）：调用即占名额，
    所以 503 能在 HTTP 响应开始前发出。若写成生成器函数，占名额会被推迟到第一次
    `next()`（那时 200 + SSE 响应头已经发出）—— 503 就没机会返回了。
    """
    ticket = acquire_agent_slot()
    if ticket is None:
        raise AgentPoolFull(
            "引擎忙：并发对话已达上限（{}），请稍后重试".format(AGENT_POOL_SIZE))

    def _guarded():
        try:
            for item in stream_events(task, session_id, context):
                yield item
        finally:
            ticket.release()

    return ticket, _guarded()
