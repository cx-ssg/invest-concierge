# -*- coding: utf-8 -*-
"""
设置服务（M0）：/api/settings——当前只读聚合（demo 开关 + agent 配置镜像）。

不含真实凭据读写（key 只从环境变量来，M0 §0 安全红线）。
"""

from config import API_KEY
from services import status_service


def get_settings():
    """GET /api/settings：React 设置页只读态"""
    from utils.ai_helper import _is_demo_mode
    return {
        "ok": True,
        "api_key_configured": bool(API_KEY),
        "version": status_service.VERSION,
        "demo_mode_available": True,
        # v1.1 修复：返回演示模式当前值——否则设置页刷新后开关跳回"关"，
        # 用户以为没开成（"演示模式开不了"的真身：开了不显示）
        "demo_mode": _is_demo_mode(),
        "ai_read_holdings": get_ai_read_holdings(),
    }


def set_demo_mode(enabled):
    """POST /api/settings/demo {enabled: bool}：进程级演示模式开关"""
    from utils.ai_helper import set_demo_mode
    set_demo_mode(bool(enabled))
    from utils.ai_helper import _is_demo_mode
    return {"ok": True, "demo_mode": _is_demo_mode()}


# ==================== v1.1 隐私开关：允许 AI 读取我的持仓 ====================

PRIVACY_AI_READ_HOLDINGS = "ai_read_holdings"


def get_ai_read_holdings():
    """记忆显性化的持仓注入前置条件（默认开）。库不可用/异常时按默认（开）处理。"""
    try:
        from data.database import get_setting
        return get_setting(PRIVACY_AI_READ_HOLDINGS, "1") != "0"
    except Exception:  # noqa: BLE001 - 开关读取失败按默认（开），不阻断对话
        return True


def set_ai_read_holdings(enabled):
    """开关持久化到 SQLite app_settings；关闭后 agent 不再注入持仓（记忆显性化 C-4）。"""
    from data.database import set_setting
    ok = set_setting(PRIVACY_AI_READ_HOLDINGS, "1" if enabled else "0")
    return {"ok": bool(ok), "ai_read_holdings": bool(enabled)}
