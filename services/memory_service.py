# -*- coding: utf-8 -*-
"""长期记忆服务层（M2 · §4 的对外接口）。

只做**参数校验 + 形状整形**，业务规则全在 `utils/long_memory.py`（单一事实源）。
响应统一 `{"ok": True, ...}` / `{"ok": False, "error": ...}`，与项目其余服务层一致。
"""
from typing import Any, Dict, List, Optional

from utils import long_memory as lm
from utils.long_memory import VALID_KINDS


def list_grouped() -> Dict[str, Any]:
    """列出全部记忆（**按 kind 分组**，供设置页"可审计"展示）。"""
    rows = lm.list_all()
    groups: Dict[str, List[Dict[str, Any]]] = {k: [] for k in VALID_KINDS}
    for r in rows:
        groups.setdefault(r["kind"], []).append({
            "id": r["id"], "key": r["key"], "content": r["content"],
            "meta": r.get("meta") or {}, "source": r["source"],
            "session_id": r.get("session_id"),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        })
    return {"ok": True, "total": len(rows), "groups": groups,
            "kinds": list(VALID_KINDS), "enabled": lm.memory_enabled()}


def add(kind: str, content: str, key: str = "",
        meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """手动新增（等价用户在对话里说「记住…」）。"""
    try:
        mid = lm.add(kind, content, key=key, meta=meta, source="explicit")
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": mid, "memory": lm.get(mid)}


def remove(memory_id: int) -> Dict[str, Any]:
    """删除单条；**必须真生效**（B4）。"""
    ok = lm.delete(memory_id)
    if not ok:
        return {"ok": False, "error": "记忆不存在：{}".format(memory_id)}
    return {"ok": True, "id": int(memory_id)}


def create_pending(kind: str, content: str, key: str = "",
                   meta: Optional[Dict[str, Any]] = None,
                   session_id: Optional[int] = None) -> Dict[str, Any]:
    """**创建**一条隐式候选（F2：原先只有 list/accept/reject，产线无法产生候选）。"""
    try:
        pid = lm.propose(kind, content, key=key, meta=meta, session_id=session_id)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": pid}


def summarize_session(session_id: Optional[int] = None,
                      messages: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """按会话抽取隐式候选（F2：给产品一个可调用的抽取入口）。返回新增条数。"""
    try:
        n = lm.summarize_to_candidates(session_id, messages=messages)
    except Exception as e:  # noqa: BLE001 - 抽取失败不该让接口 500
        return {"ok": False, "error": "抽取失败：{}".format(e)}
    return {"ok": True, "added": n, "pending": lm.list_pending()}


def list_pending() -> Dict[str, Any]:
    return {"ok": True, "items": lm.list_pending()}


def resolve_pending(pending_id: int, action: str) -> Dict[str, Any]:
    """接受/拒绝隐式候选（§4.2：AI 不自行写记忆）。"""
    act = str(action or "").strip().lower()
    if act in ("accept", "accepted", "yes"):
        mid = lm.accept_pending(pending_id)
        if mid is None:
            return {"ok": False, "error": "候选不存在或已处理：{}".format(pending_id)}
        return {"ok": True, "id": mid, "memory": lm.get(mid)}
    if act in ("reject", "rejected", "no"):
        if not lm.reject_pending(pending_id):
            return {"ok": False, "error": "候选不存在或已处理：{}".format(pending_id)}
        return {"ok": True, "id": int(pending_id), "rejected": True}
    return {"ok": False, "error": "action 必须是 accept 或 reject"}


def get_settings() -> Dict[str, Any]:
    return {"ok": True, "enabled": lm.memory_enabled()}


def set_settings(enabled: bool) -> Dict[str, Any]:
    ok = lm.set_memory_enabled(bool(enabled))
    return {"ok": bool(ok), "enabled": lm.memory_enabled()}


def recall_preview(question: str, codes: Optional[List[str]] = None) -> Dict[str, Any]:
    """预览「当前问题会召回哪些记忆」（便于用户审计 AI 到底看到了什么）。"""
    block, counts = lm.build_recall_block(question or "", stock_codes=codes or None)
    return {"ok": True, "counts": counts, "block": block}
