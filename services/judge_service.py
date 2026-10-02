# -*- coding: utf-8 -*-
"""LLM 判官的**异步通道**（线程池 + 有界等待 + 事件体）—— 任务书 `task-B1.md` §1.2。

## 为什么必须异步

判官同步 inline 不可交付：外部评审侧原型**单次 43.7 秒**（90% 是 prefill）。
本仓探针把输入裁剪到 `per_chunk_chars=800` 后降到 **2.9s**（`probe-judge.json`），
但那仍是**一次额外的 LLM 往返** ⇒ 它绝不能挂在回答生成的关键路径上。

## 分工（与 `utils/rag/llm_judge.py` 的边界）

- `llm_judge.py`：纯逻辑（提示词 / 引文校验 / 降级），**可单测**；
- 本模块：**触发判定** + 单并发线程池 + 有界等待 + `evidence_judged` 事件体。
- `services/agent_service.py`：在 `done` **之后**调用本模块，把事件送进 SSE 队列。

## 触发条件（硬约束）

**仅当本轮检索的 `evidence_level == "weak"` 才触发**：`none` 档既有行为不变
（分层硬停 + 剥 url/title），也**不触发**判官；缺档位 / 空结果 / 非检索工具一律跳过。
触发判定在 `collect_from_tool_trace()` 里单点实现（评测与 SSE 共用它，
避免"评测自己写一份判定"导致口径分叉）。

## 线程池纪律

- **单并发**（`max_workers=1`）：判官是额外开销，不得与 Agent 工具链抢 CPU/配额；
- **有界等待**：`judge_rounds` 用**总预算 deadline**（不是每轮各等一次）⇒
  判官最多让**流**多活 `JUDGE_TIMEOUT_S`，而 `done` 早已发出（回答不等判官）；
- 超时/异常/解析失败 ⇒ 事件体里如实写 `checked=False` + `reason`，**不假装判过**；
- 本模块**绝不抛异常**：它是对话主链路的旁路，任何失败不得打断回答。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from utils.rag.llm_judge import (
    DEFAULT_PER_CHUNK_CHARS,
    LEVEL_UNCERTAIN,
    judge_candidates,
)

__all__ = [
    "JUDGE_ENABLED", "JUDGE_TIMEOUT_S", "JUDGE_MAX_ROUNDS", "JUDGE_MAX_CANDIDATES",
    "submit_judge", "collect_from_tool_trace", "judge_rounds", "judge_tool_trace",
]

#: 总开关（脚本做「开/关判官」对照用；默认开）。读取发生在**调用时**，可 patch。
JUDGE_ENABLED = True
#: 判官的总时间预算（秒）—— 同时也是 SSE 流为判官多等的最长时间。
JUDGE_TIMEOUT_S = 20.0
#: 单轮 Agent 运行里最多判几轮检索（成本上限：一次运行最多 N 次判官 LLM 调用）
JUDGE_MAX_ROUNDS = 3
#: 单轮最多判几条候选（与 `retrieve_docs` 默认 `top_n=5` 对齐）
JUDGE_MAX_CANDIDATES = 5

_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _executor():
    """单并发线程池（惰性创建：从不触发判官的进程不会多出任何线程）。"""
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                               thread_name_prefix="rag-judge")
    return _EXECUTOR


def submit_judge(question, candidates, *, llm_fn=None,
                 per_chunk_chars=DEFAULT_PER_CHUNK_CHARS, timeout_s=None):
    """把一次判官提交到单并发池，返回 `Future`（= 结果占位，由调用方有界等待）。

    `timeout_s` 是本轮判官内部的 LLM 调用上限（缺省 `JUDGE_TIMEOUT_S`）。
    """
    budget = JUDGE_TIMEOUT_S if timeout_s is None else timeout_s
    return _executor().submit(
        judge_candidates, question, candidates,
        llm_fn=llm_fn, per_chunk_chars=per_chunk_chars, timeout_s=budget)


def _decode_payload(output):
    """解开 `execute_ai_tool_v2` 的**双层编码**（详见 `utils/rag/sources.py` 头注）。

    只解一层的实现会把生产载荷判成"无结果" ⇒ 判官在产线上**永不触发**，
    而喂单层 JSON 的单测全绿（本仓「单测全绿 ≠ 生产路径可达」的经典形态）。
    """
    if isinstance(output, (bytes, bytearray)):
        try:
            output = output.decode("utf-8")
        except Exception:  # noqa: BLE001 - 畸形输入是预期分支
            return None
    data = output
    for _ in range(2):
        if not isinstance(data, str):
            break
        try:
            data = json.loads(data)
        except Exception:  # noqa: BLE001
            return None
    return data if isinstance(data, dict) else None


def collect_from_tool_trace(tool_trace):
    """从 `agent_run` 的 `tool_trace` 里抽出**需要判官**的检索轮（只认 weak）。

    返回 `[{"query": str, "candidates": [{"chunk_id", "title", "text"}]}, ...]`，顺序即调用顺序。

    触发条件是**派发点唯一实现**：`none` / 缺档位 / 空结果 / 非 `retrieve_docs` 一律跳过。
    绝不抛异常（畸形 `tool_trace` ⇒ `[]`）。
    """
    out = []
    if not isinstance(tool_trace, (list, tuple)):
        return out
    for entry in tool_trace:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != "retrieve_docs":
            continue
        data = _decode_payload(entry.get("output"))
        if data is None:
            continue
        # ★ 触发条件：**仅 weak**（none 档判官零调用；缺档位同样不触发）
        if data.get("evidence_level") != "weak":
            continue
        results = data.get("results")
        if not isinstance(results, list) or not results:
            continue
        cands = []
        for row in results[:JUDGE_MAX_CANDIDATES]:
            if not isinstance(row, dict):
                continue
            text = row.get("text")
            if not isinstance(text, str) or not text:
                continue
            cands.append({"chunk_id": row.get("chunk_id"),
                          "title": row.get("title"), "text": text})
        if not cands:
            continue
        query = data.get("query")
        if not isinstance(query, str) or not query:
            args = entry.get("arguments")
            query = args.get("query") if isinstance(args, dict) else ""
        out.append({"query": query or "", "candidates": cands})
    return out


def _item_payload(item):
    """事件体里每条判官结论（保持 `judge_candidates` 的字段集）。"""
    return {
        "chunk_id": item.get("chunk_id"),
        "verdict": item.get("verdict") or LEVEL_UNCERTAIN,
        "quote": item.get("quote") or "",
        "quote_rejected": bool(item.get("quote_rejected")),
    }


def _event(query, level, items, checked, latency_ms, reason):
    """`evidence_judged` 事件体（协议键集固定：多一个少一个都算协议变更）。"""
    return {
        "type": "evidence_judged",
        "query": query,
        "level": level,
        "items": items,
        "checked": bool(checked),
        "latency_ms": int(latency_ms or 0),
        "reason": reason or "",
    }


def _degraded(query, reason, latency_ms=0):
    """未判/判不出 ⇒ 如实标注（不静默、不伪装成 relevant）。"""
    return _event(query, LEVEL_UNCERTAIN, [], False, latency_ms, reason)


def judge_rounds(requests, *, llm_fn=None, timeout_s=None, total_budget_s=None,
                 max_rounds=None):
    """逐轮判定（单并发 + 有界等待 + 永不抛）。

    `requests` 来自 `collect_from_tool_trace`。返回与输入等长（截断后）的
    `evidence_judged` 事件体列表。三个"表盘"：

    - `timeout_s`：**每轮** LLM 调用上限（缺省 `JUDGE_TIMEOUT_S`）；
    - `total_budget_s`：**整批**总预算（缺省 None = 不设总预算，逐轮各自有界）。
      SSE 侧显式传 `JUDGE_TIMEOUT_S` ⇒ 判官最多让**流**多活这么久；评测侧不传
      ⇒ 每行独立有界（否则整批评测会被一个总预算掐掉）。
    - `max_rounds`：最多判几轮（缺省 `JUDGE_MAX_ROUNDS`；评测按行数放开）。
    """
    per_round = JUDGE_TIMEOUT_S if timeout_s is None else timeout_s
    cap = JUDGE_MAX_ROUNDS if max_rounds is None else max_rounds
    rounds = list(requests or [])[:max(0, int(cap))]
    deadline = (None if total_budget_s is None
                else time.monotonic() + max(0.0, float(total_budget_s)))
    events = []
    for req in rounds:
        query = req.get("query") or ""
        t_round = time.monotonic()
        try:
            remaining = per_round if deadline is None else min(per_round, deadline - t_round)
            if remaining <= 0:
                events.append(_degraded(query, "timeout"))
                continue
            fut = submit_judge(query, req.get("candidates") or [],
                               llm_fn=llm_fn, timeout_s=remaining)
            try:
                res = fut.result(timeout=remaining)
            except FutureTimeoutError:
                # ⚠️ 有界等待：只放弃等待，**不杀线程**（Python 无法强杀）；
                #    被放弃的那次调用会自行结束，其结果按"未确认"上报。
                events.append(_degraded(query, "timeout",
                                        int((time.monotonic() - t_round) * 1000)))
                continue
            except Exception:  # noqa: BLE001 - 池内异常同样降级
                events.append(_degraded(query, "llm_error",
                                        int((time.monotonic() - t_round) * 1000)))
                continue
            if not isinstance(res, dict):
                events.append(_degraded(query, "llm_error",
                                        int((time.monotonic() - t_round) * 1000)))
                continue
            events.append(_event(
                query,
                res.get("level") or LEVEL_UNCERTAIN,
                [_item_payload(i) for i in (res.get("items") or []) if isinstance(i, dict)],
                res.get("checked"),
                res.get("latency_ms"),
                res.get("reason")))
        except Exception:  # noqa: BLE001 - 本模块绝不抛
            events.append(_degraded(query, "llm_error",
                                    int((time.monotonic() - t_round) * 1000)))
    return events


def judge_tool_trace(tool_trace, *, llm_fn=None, timeout_s=None,
                     total_budget_s=None, max_rounds=None):
    """SSE 侧入口：`tool_trace` → `evidence_judged` 事件体列表（无触发 ⇒ `[]`，永不抛）。

    调用方（`services/agent_service.py`）**必须**在 `done` 之后调用它 —— 回答不等判官。
    """
    try:
        if not JUDGE_ENABLED:
            return []
        requests = collect_from_tool_trace(tool_trace)
        if not requests:
            return []
        return judge_rounds(requests, llm_fn=llm_fn, timeout_s=timeout_s,
                            total_budget_s=total_budget_s, max_rounds=max_rounds)
    except Exception:  # noqa: BLE001 - 旁路失败不得打断主链路
        return []
