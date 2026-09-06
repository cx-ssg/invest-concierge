# -*- coding: utf-8 -*-
"""周报路由（v1.1 粘性三件套 B）。生成走 AI 可达 60-120s → 前端 180s 超时。"""

from fastapi import APIRouter

from services import report_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/weekly")
def generate_weekly():
    """生成本周周报（同周幂等返回缓存；force 参数走后端内部，暂不暴露）"""
    return report_service.generate_weekly()


@router.get("/weekly")
def latest_weekly():
    """最近一期周报（只读，不触发生成）"""
    return report_service.weekly_cached()
