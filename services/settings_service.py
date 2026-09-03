# -*- coding: utf-8 -*-
"""
设置服务（M0）：/api/settings——当前只读聚合（demo 开关 + agent 配置镜像）。

不含真实凭据读写（key 只从环境变量来，M0 §0 安全红线）。
"""

from config import API_KEY
from services import status_service


def get_settings():
    """GET /api/settings：React 设置页只读态"""
    return {
        "ok": True,
        "api_key_configured": bool(API_KEY),
        "version": status_service.VERSION,
        "demo_mode_available": True,
    }


def set_demo_mode(enabled):
    """POST /api/settings/demo {enabled: bool}：进程级演示模式开关"""
    from utils.ai_helper import set_demo_mode
    set_demo_mode(bool(enabled))
    from utils.ai_helper import _is_demo_mode
    return {"ok": True, "demo_mode": _is_demo_mode()}
