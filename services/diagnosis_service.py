# -*- coding: utf-8 -*-
"""
诊断服务（M0）：/api/stocks/{code}/diagnosis 数据源。

冷启 15-40s（财报引擎无缓存）→ 服务端不塞 ThreadPoolExecutor 里的
同步等待进事件循环：router 层用 run_in_threadpool 调本模块。
响应统一过 to_jsonable（诊断 payload 含 DataFrame/Timestamp/NaN）。

**M3（2026-10-02）**：新增 `ORCHESTRATOR=graph` 编排路径（`utils/orchestrator`）。
- 默认 `legacy` ⇒ 本模块行为**与改动前完全一致**（§5.2-1 feature flag 并存）；
- `graph` ⇒ 改走 LangGraph 图（检查点 / 人审 `interrupt()` / 断点续跑），
  但**响应契约保持一致**（同一个 6 引擎 payload 形状 + `ok`），只额外挂一个
  `_orchestrator` 元信息键供观测（前端无需改动）。
"""

from services._json import to_jsonable

#: 图编排用的**长生命周期** checkpointer 单例。
#: ⚠️ 必须跨请求复用：否则每次诊断都新开连接，C3 的「断点续跑」就没有落点。
#: `SqliteSaver` 连接用 `check_same_thread=False`（router 层走 run_in_threadpool）。
_CHECKPOINTER = None
_CHECKPOINTER_CLOSER = None


def _checkpointer():
    global _CHECKPOINTER, _CHECKPOINTER_CLOSER
    if _CHECKPOINTER is None:
        from utils.orchestrator.graph import make_checkpointer

        _CHECKPOINTER, _CHECKPOINTER_CLOSER = make_checkpointer()
    return _CHECKPOINTER


def _validate(stock_code):
    code = str(stock_code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        return None, {"ok": False, "error": "股票代码须为 6 位数字（如 600519）"}
    return code, None


def get(stock_code):
    """GET /api/stocks/{code}/diagnosis：6 引擎 payload（data.diagnosis 24h TTL 缓存）。"""
    code, err = _validate(stock_code)
    if err:
        return err

    from utils.orchestrator.flags import use_graph

    if use_graph():
        return _get_via_graph(code)
    return _get_legacy(code)


def _get_legacy(code):
    """原有线性编排路径（**默认路径，行为不变**）。"""
    from data.diagnosis import build_diagnosis_payload
    try:
        payload = build_diagnosis_payload(code)
    except Exception as e:  # noqa: BLE001 - 单引擎失败已在 payload.errors 内，这里是编排级兜底
        return {"ok": False, "error": "诊断构建失败：{}".format(e), "code": code}

    payload["ok"] = True
    return to_jsonable(payload)


def _get_via_graph(code):
    """M3 图编排路径（`ORCHESTRATOR=graph`）。

    ⚠️ 契约一致性：从图状态里取回 `analyze` 节点产出的**完整 payload**，
    形状与 legacy 完全一致 ⇒ 前端零改动。图特有的可观测信息只放在额外键 `_orchestrator`。
    """
    from utils.orchestrator.graph import run_diagnosis_graph

    try:
        state = run_diagnosis_graph(code, thread_id="diagnosis:{}".format(code),
                                    checkpointer=_checkpointer())
    except Exception as e:  # noqa: BLE001 - 图本身失败也要给出可读错误，不能 500
        return {"ok": False, "error": "图编排失败：{}".format(e), "code": code}

    # ⚠️ A7 修复（2026-10-02 hermes 审计）：以 **legacy 的键骨架**为基底再合并图产出，
    #    这样无论走 analyze 还是 fallback，响应键集都与 legacy 逐字一致 ——
    #    否则 fallback（当前生产的唯一可达分支）只返回 5 个键，前端 13 个引擎字段全缺。
    from data.diagnosis import empty_diagnosis_payload

    payload = empty_diagnosis_payload(code)
    engines = state.get("engines")
    if isinstance(engines, dict):
        for k, v in engines.items():
            payload[k] = v                       # 只覆盖骨架里已有的键 + errors 等
    else:
        payload["stock_info"] = state.get("quote") or payload["stock_info"]
    for e in (state.get("errors") or []):
        if e not in payload["errors"]:
            payload["errors"].append(e)

    payload["ok"] = True
    payload["_orchestrator"] = {
        "mode": "graph",
        "branch": state.get("branch"),
        "trace": list(state.get("trace") or []),
        "review_status": state.get("review_status"),
        "review_round": state.get("review_round", 0),
        "evidence_count": len(state.get("evidence") or []),
        "report_chars": len(state.get("report") or ""),
    }
    return to_jsonable(payload)
