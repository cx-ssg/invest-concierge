# -*- coding: utf-8 -*-
"""长期记忆路由（M2）—— /api/memory/*。

设计依据 `docs/COVERAGE_DESIGN.md` §4 + `docs/M2_MEMORY_PLAN.md` §4。
原则：**记忆必须可审计、可删除**（§4.2）⇒ 列表/预览/删除/开关全部有入口。
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import memory_service

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryIn(BaseModel):
    kind: str = Field(..., description="preference | fact | experience")
    content: str = Field(..., description="记忆正文")
    key: str = Field("", description="去重键（experience 会忽略并改用内容指纹）")
    meta: Optional[Dict[str, Any]] = Field(None, description="结构化信息，如 {'code': '600519'}")


class PendingIn(BaseModel):
    action: str = Field(..., description="accept | reject")


class SettingsIn(BaseModel):
    enabled: bool = Field(..., description="是否允许 AI 使用长期记忆")


@router.get("")
def list_memories():
    """列出全部记忆（按 kind 分组）—— 用户可审计。"""
    return memory_service.list_grouped()


@router.post("")
def add_memory(body: MemoryIn):
    """手动新增一条记忆（等价对话里说「记住…」）。"""
    return memory_service.add(body.kind, body.content, body.key, body.meta)


@router.delete("/{memory_id}")
def delete_memory(memory_id: int):
    """删除单条（B4：删完再问不得再体现）。"""
    return memory_service.remove(memory_id)


@router.get("/pending")
def list_pending():
    """待确认的隐式候选（§4.2：AI 不自行写记忆）。"""
    return memory_service.list_pending()


@router.post("/pending/{pending_id}")
def resolve_pending(pending_id: int, body: PendingIn):
    """接受或拒绝候选。"""
    return memory_service.resolve_pending(pending_id, body.action)


@router.get("/settings")
def get_settings():
    return memory_service.get_settings()


@router.post("/settings")
def set_settings(body: SettingsIn):
    return memory_service.set_settings(body.enabled)


@router.get("/recall-preview")
def recall_preview(question: str = "", codes: str = ""):
    """预览当前问题会召回哪些记忆（审计用：让用户看到 AI 到底看到什么）。"""
    code_list = [c.strip() for c in str(codes or "").split(",") if c.strip()]
    return memory_service.recall_preview(question, code_list)
