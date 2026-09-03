# -*- coding: utf-8 -*-
"""
设置路由（M0）：只读设置 + demo 开关。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from services import settings_service

router = APIRouter(prefix="/api", tags=["settings"])


class DemoIn(BaseModel):
    enabled: bool


@router.get("/settings")
def get_settings():
    """设置页只读态"""
    return settings_service.get_settings()


@router.post("/settings/demo")
def set_demo(body: DemoIn):
    """进程级演示模式开关（React 勾选 → AI 用内置示例持仓）"""
    return settings_service.set_demo_mode(body.enabled)
